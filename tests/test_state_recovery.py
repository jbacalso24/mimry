from __future__ import annotations

import json
import os
import errno
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry import state
from mimry.commands import cmd_cache_wipe, cmd_feedback
from mimry.freshness import index_freshness
from mimry.indexer import _cleanup_generations, write_index
from mimry.mcp_server import mimry_find
from mimry.paths import idx_path
from mimry.storage import active_index_pointer, load_pointer, load_root_registry, register_root, save_pointer

from test_mimry_cli import copy_fixture, run_cli


def _pointer(root: Path, root_id: str) -> dict[str, object]:
    return {
        "rootId": root_id,
        "rootPath": str(root),
        "rootType": "repo",
        "indexPath": str(root / "index"),
        "createdAt": "2026-01-01T00:00:00+00:00",
        "lastIndexedAt": None,
        "schemaVersion": "0.1.0",
    }


def _register_in_process(cache: str, pointer: dict[str, object]) -> None:
    os.environ["MIMRY_CACHE_HOME"] = cache
    register_root(pointer)


def test_atomic_write_failure_preserves_previous_file_and_cleans_temp(tmp_path: Path, monkeypatch):
    target = tmp_path / "pointer.json"
    target.write_text('{"rootId": "last-known-good"}\n', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("injected rename failure")

    monkeypatch.setattr(state.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected rename failure"):
        state.atomic_write_json(target, {"rootId": "new"})

    assert json.loads(target.read_text(encoding="utf-8"))["rootId"] == "last-known-good"
    assert list(tmp_path.glob(".pointer.json.*.tmp")) == []


def test_corrupt_pointer_without_backup_is_actionable_and_has_no_traceback(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    pointer = repo / ".mimry" / "pointer.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text('{"rootId":', encoding="utf-8")

    result = run_cli(repo, tmp_path / "cache", "status")

    assert result.returncode == 2
    assert "MIMRY state error" in result.stderr
    assert str(pointer) in result.stderr
    assert "last-known-good backup" in result.stderr
    assert "Preserve the corrupt file" in result.stderr
    assert "Traceback" not in result.stderr


def test_status_repairs_corrupt_pointer_from_last_known_good_backup(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    pointer = repo / ".mimry" / "pointer.json"
    expected = json.loads(pointer.read_text(encoding="utf-8"))
    pointer.write_text("{not-json", encoding="utf-8")

    result = run_cli(repo, cache, "status")

    assert result.returncode in {0, 2}
    assert "Recovered corrupt MIMRY state" in result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(pointer.read_text(encoding="utf-8")) == expected


def test_registry_recovery_restores_valid_backup(tmp_path: Path, monkeypatch, capsys):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    register_root(_pointer(tmp_path / "repo", "root-1"))
    registry_path = cache / "roots.json"
    expected = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_path.write_text("[truncated", encoding="utf-8")

    actual = load_root_registry()

    assert actual == expected
    assert json.loads(registry_path.read_text(encoding="utf-8")) == expected
    assert "Recovered corrupt MIMRY state" in capsys.readouterr().err


def test_registry_deduplicates_canonical_paths_and_prefers_current_pointer(tmp_path: Path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    root = tmp_path / "projects" / "repo"
    root.mkdir(parents=True)
    old = _pointer(root, "old-id")
    old["lastIndexedAt"] = "2026-01-02T00:00:00+00:00"
    duplicate = _pointer(root / ".." / "repo", "duplicate-id")
    duplicate["lastIndexedAt"] = "2026-01-03T00:00:00+00:00"
    registry_path = cache / "roots.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps({"roots": [old, duplicate]}), encoding="utf-8")

    current = _pointer(root.resolve(), "current-id")
    register_root(current)
    registry = load_root_registry()

    assert len(registry["roots"]) == 1
    assert registry["roots"][0]["rootId"] == "current-id"
    assert registry["roots"][0]["rootPath"] == str(root.resolve())
    assert json.loads(registry_path.read_text(encoding="utf-8")) == registry


def test_register_root_rebinds_root_id_to_authoritative_pointer(tmp_path: Path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    old_root = tmp_path / "old" / "repo"
    current_root = tmp_path / "current" / "repo"
    old_root.mkdir(parents=True)
    current_root.mkdir(parents=True)
    register_root(_pointer(old_root, "shared-id"))

    current = _pointer(current_root, "shared-id")
    register_root(current)

    registry = load_root_registry()
    assert [(entry["rootId"], entry["rootPath"]) for entry in registry["roots"]] == [
        ("shared-id", str(current_root.resolve()))
    ]


def test_moved_root_fails_closed_when_recorded_path_is_missing(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    initialized = run_cli(repo, cache, "init", "--skip-graph")
    assert initialized.returncode == 0, initialized.stderr
    moved = tmp_path / "moved-repo"
    shutil.move(repo, moved)
    before_pointer = (moved / ".mimry" / "pointer.json").read_bytes()
    before_registry = (cache / "roots.json").read_bytes()

    refused = run_cli(moved, cache, "init", "--skip-graph")

    assert refused.returncode == 2
    assert "Moved-root rebinding requires an explicit recovery workflow" in refused.stderr
    assert (moved / ".mimry" / "pointer.json").read_bytes() == before_pointer
    assert (cache / "roots.json").read_bytes() == before_registry


def test_init_refuses_copied_pointer_while_recorded_root_exists(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    initialized = run_cli(repo, cache, "init", "--skip-graph")
    assert initialized.returncode == 0, initialized.stderr
    copied = tmp_path / "copied-repo"
    shutil.copytree(repo, copied)
    before_pointer = (copied / ".mimry" / "pointer.json").read_bytes()
    before_registry = (cache / "roots.json").read_bytes()

    refused = run_cli(copied, cache, "init", "--skip-graph")

    assert refused.returncode == 2
    assert "Refusing MIMRY root identity mismatch" in refused.stderr
    assert (copied / ".mimry" / "pointer.json").read_bytes() == before_pointer
    assert (cache / "roots.json").read_bytes() == before_registry


def test_copied_pointer_blocks_index_preflight_and_mcp_without_mutating_original(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    copied = tmp_path / "copied-repo"
    shutil.copytree(repo, copied)
    original_pointer = (repo / ".mimry" / "pointer.json").read_bytes()
    original_status = run_cli(repo, cache, "status")
    assert original_status.returncode == 0, original_status.stderr

    for command in (("index",), ("preflight", "copied root must fail closed"), ("status",)):
        refused = run_cli(copied, cache, *command)
        assert refused.returncode == 2
        assert "MIMRY root identity error" in refused.stderr
        assert "Traceback" not in refused.stderr

    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    payload = mimry_find("auth", str(copied))
    assert payload["returncode"] == 2
    assert payload["error"]["code"] == "root_identity_mismatch"
    assert (repo / ".mimry" / "pointer.json").read_bytes() == original_pointer
    after_status = run_cli(repo, cache, "status")
    assert after_status.returncode == 0, after_status.stderr


def test_roots_labels_missing_and_duplicate_root_ids_without_repairing_registry(tmp_path: Path):
    cache = tmp_path / "cache"
    registry_path = cache / "roots.json"
    registry_path.parent.mkdir(parents=True)
    roots = [
        _pointer(tmp_path / "missing-a", "shared-id"),
        _pointer(tmp_path / "missing-b", "shared-id"),
    ]
    registry_path.write_text(json.dumps({"roots": roots}), encoding="utf-8")
    before = registry_path.read_bytes()

    result = run_cli(tmp_path / "command-root", cache, "roots")

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("missing root") == 2
    assert result.stdout.count("duplicate root ID") == 2
    assert registry_path.read_bytes() == before


def test_concurrent_registry_updates_do_not_drop_roots(tmp_path: Path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    pointers = [_pointer(tmp_path / "repos" / f"repo-{index}", f"root-{index}") for index in range(32)]

    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_register_in_process, str(cache), pointer) for pointer in pointers]
        for future in futures:
            future.result()

    registry = load_root_registry()
    assert {entry["rootId"] for entry in registry["roots"]} == {f"root-{index}" for index in range(32)}
    assert len(registry["roots"]) == 32
    assert json.loads((cache / "roots.json").read_text(encoding="utf-8")) == registry


def _initialized_repo(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    return repo, cache


def test_generation_publish_failure_between_artifacts_keeps_previous_generation(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    before = load_pointer(repo)
    original = state.atomic_write_json

    def fail_on_graph(path, payload, **kwargs):
        if Path(path).name == "graph.json":
            raise OSError("injected crash between artifacts")
        return original(path, payload, **kwargs)

    monkeypatch.setattr("mimry.indexer.atomic_write_json", fail_on_graph)
    with pytest.raises(OSError, match="injected crash"):
        write_index(repo, before)

    after = load_pointer(repo)
    assert after["generationId"] == before["generationId"]
    assert index_freshness(repo, after)["state"] == "current"
    assert not list((idx_path(before["rootId"]) / "generations").glob(".*.staging"))


def test_kill_after_generation_then_restart_keeps_lkg_and_next_index_migrates(tmp_path: Path):
    repo, cache = _initialized_repo(tmp_path)
    before = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "MIMRY_CACHE_HOME": str(cache),
            "MIMRY_FAULT_POINT": "after-generation",
        }
    )
    killed = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), "index"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert killed.returncode == 91
    assert (
        json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))["generationId"]
        == before["generationId"]
    )
    status = run_cli(repo, cache, "status")
    assert status.returncode == 0, status.stderr
    rebuilt = run_cli(repo, cache, "index")
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert (
        json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))["generationId"]
        != before["generationId"]
    )


def test_pointer_recovery_cannot_overwrite_newer_writer(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    pointer_path = repo / ".mimry" / "pointer.json"
    current = load_pointer(repo)
    pointer_path.write_text("{broken", encoding="utf-8")
    entered_recovery = threading.Event()
    release_recovery = threading.Event()
    original = state.atomic_write_json

    def pause_recovery(path, payload, **kwargs):
        if Path(path) == pointer_path and payload.get("generationId") != "newer-generation":
            entered_recovery.set()
            assert release_recovery.wait(5)
        return original(path, payload, **kwargs)

    monkeypatch.setattr(state, "atomic_write_json", pause_recovery)
    loaded = []
    loader = threading.Thread(target=lambda: loaded.append(load_pointer(repo)))
    loader.start()
    assert entered_recovery.wait(5)
    newer = {**current, "generationId": "newer-generation", "indexPath": current["indexPath"]}
    writer = threading.Thread(target=lambda: save_pointer(repo, newer))
    writer.start()
    time.sleep(0.05)
    assert writer.is_alive(), "writer must wait for pointer recovery lock"
    release_recovery.set()
    loader.join(5)
    writer.join(5)
    assert json.loads(pointer_path.read_text(encoding="utf-8"))["generationId"] == "newer-generation"


def test_concurrent_pointer_backup_writes_remain_valid_json(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    base = _pointer(repo, "root")
    save_pointer(repo, base)
    barrier = threading.Barrier(16)

    def write(number):
        barrier.wait()
        save_pointer(repo, {**base, "lastIndexedAt": str(number)})

    threads = [threading.Thread(target=write, args=(number,)) for number in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    primary = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    backup = json.loads((repo / ".mimry" / "pointer.json.bak").read_text(encoding="utf-8"))
    assert primary["rootId"] == backup["rootId"] == "root"
    assert primary["lastIndexedAt"] != backup["lastIndexedAt"]


def test_mixed_generation_sidecar_is_corrupt_and_all_direct_mcp_tools_structure_error(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    first = load_pointer(repo)
    write_index(repo, first)
    second = load_pointer(repo)
    (Path(second["indexPath"]) / "files.jsonl").write_bytes(
        (Path(first["indexPath"]) / "files.jsonl").read_bytes() + b"\n"
    )

    with pytest.raises(state.StateCorruptionError, match="checksum does not match generation"):
        index_freshness(repo, second)
    payload = mimry_find("auth", str(repo))
    assert payload["returncode"] == 2
    assert payload["error"]["code"] == "state_corruption"
    assert payload["error"]["path"].endswith("files.jsonl")


def test_mixed_generation_sqlite_is_never_reported_current(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    first = load_pointer(repo)
    write_index(repo, first)
    second = load_pointer(repo)
    (Path(second["indexPath"]) / "mimry.sqlite").write_bytes((Path(first["indexPath"]) / "mimry.sqlite").read_bytes())

    with pytest.raises(state.StateCorruptionError, match="SQLite generation does not match"):
        index_freshness(repo, second)
    payload = mimry_find("auth", str(repo))
    assert payload["returncode"] == 2
    assert payload["error"]["code"] == "state_corruption"
    assert payload["error"]["path"].endswith("mimry.sqlite")


def test_concurrent_index_writers_publish_one_complete_generation(tmp_path: Path):
    repo, cache = _initialized_repo(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "MIMRY_CACHE_HOME": str(cache),
        }
    )
    command = [sys.executable, "-m", "mimry.cli", "--root", str(repo), "index"]
    writers = [
        subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)
    ]
    results = [writer.communicate(timeout=30) + (writer.returncode,) for writer in writers]
    assert all(returncode == 0 for _stdout, _stderr, returncode in results), results
    status = run_cli(repo, cache, "status")
    assert status.returncode == 0, status.stderr
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    assert (Path(pointer["indexPath"]) / "generation.json").is_file()


def test_legacy_index_layout_migrates_on_next_index(tmp_path: Path, monkeypatch):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    legacy = _pointer(repo, "legacy-root")
    legacy["indexPath"] = str(idx_path("legacy-root"))
    Path(legacy["indexPath"]).mkdir(parents=True)
    (Path(legacy["indexPath"]) / "files.jsonl").write_text("", encoding="utf-8")
    save_pointer(repo, legacy)

    stats = write_index(repo, legacy)
    migrated = load_pointer(repo)
    assert migrated["generationId"] == stats["generation"]
    assert Path(migrated["indexPath"]).parent.name == "generations"
    assert index_freshness(repo, migrated)["layout"] == "generation"


def test_directory_fsync_only_suppresses_explicitly_unsupported_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        state.os, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported"))
    )
    state._fsync_directory(tmp_path)
    monkeypatch.setattr(
        state.os, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError(errno.EIO, "disk error"))
    )
    with pytest.raises(OSError) as raised:
        state._fsync_directory(tmp_path)
    assert raised.value.errno == errno.EIO


def test_wipe_and_publication_share_lock_and_publication_never_dangles(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    pointer = load_pointer(repo)
    original_rmtree = shutil.rmtree
    wipe_entered = threading.Event()
    allow_wipe = threading.Event()

    def paused_rmtree(path, *args, **kwargs):
        if Path(path).name == "generations" and not wipe_entered.is_set():
            wipe_entered.set()
            assert allow_wipe.wait(5)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("mimry.commands.shutil.rmtree", paused_rmtree)
    wipe_result: list[int] = []
    wipe = threading.Thread(
        target=lambda: wipe_result.append(cmd_cache_wipe(SimpleNamespace(root=str(repo), all=False)))
    )
    wipe.start()
    assert wipe_entered.wait(5)

    publish_result: list[dict] = []
    publisher = threading.Thread(target=lambda: publish_result.append(write_index(repo, pointer)))
    publisher.start()
    time.sleep(0.05)
    assert publisher.is_alive(), "publisher must wait for the cache wipe's per-root index lock"
    allow_wipe.set()
    wipe.join(10)
    publisher.join(30)

    assert wipe_result == [0]
    assert publish_result
    published = load_pointer(repo)
    assert Path(published["indexPath"]).is_dir()
    assert index_freshness(repo, published)["state"] == "current"


def test_cache_wipe_current_leaves_truthful_missing_state_and_normal_index_rebuilds(tmp_path: Path):
    repo, cache = _initialized_repo(tmp_path)
    before = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))

    wiped = run_cli(repo, cache, "cache", "wipe", "--current")
    assert wiped.returncode == 0, wiped.stderr
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    assert "generationId" not in pointer
    assert pointer["lastIndexedAt"] is None
    assert Path(pointer["indexPath"]) == cache / "indexes" / before["rootId"]
    status = run_cli(repo, cache, "status")
    assert status.returncode == 2, status.stderr
    assert "Index: missing" in status.stdout

    rebuilt = run_cli(repo, cache, "index")
    assert rebuilt.returncode == 0, rebuilt.stderr
    published = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    assert published.get("generationId")
    assert Path(published["indexPath"]).is_dir()


def test_feedback_waiting_for_publication_resolves_and_persists_to_new_generation(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    old = load_pointer(repo)
    publishing = threading.Event()
    release = threading.Event()
    original_save = save_pointer

    def pause_before_publish(root, pointer):
        if pointer.get("generationId") != old.get("generationId"):
            publishing.set()
            assert release.wait(5)
        return original_save(root, pointer)

    monkeypatch.setattr("mimry.indexer.save_pointer", pause_before_publish)
    publisher = threading.Thread(target=lambda: write_index(repo, old))
    publisher.start()
    assert publishing.wait(5)

    feedback_result: list[int] = []
    args = SimpleNamespace(
        root=str(repo),
        feedback_action=None,
        query="publication race",
        context=None,
        suggested=None,
        opened="src/app.py",
        changed=None,
        missed=None,
        ignored=None,
        verification=None,
        outcome="passed",
        notes=None,
        json=None,
    )
    feedback = threading.Thread(target=lambda: feedback_result.append(cmd_feedback(args)))
    feedback.start()
    time.sleep(0.05)
    assert feedback.is_alive(), "feedback must wait for the publication operation lock"
    release.set()
    publisher.join(30)
    feedback.join(10)

    assert feedback_result == [0]
    current = load_pointer(repo)
    assert current["generationId"] != old["generationId"]
    with sqlite3.connect(Path(current["indexPath"]) / "mimry.sqlite") as con:
        assert con.execute("select query from feedback where query = ?", ("publication race",)).fetchone()


def test_reader_holds_generation_lifetime_across_two_publications_and_gc(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    original = load_pointer(repo)
    reader_entered = threading.Event()
    release_reader = threading.Event()
    observed: list[str] = []

    def reader():
        with active_index_pointer(repo) as pointer:
            reader_entered.set()
            observed.append((Path(pointer["indexPath"]) / "files.jsonl").read_text(encoding="utf-8"))
            assert release_reader.wait(5)
            observed.append((Path(pointer["indexPath"]) / "files.jsonl").read_text(encoding="utf-8"))

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    assert reader_entered.wait(5)
    publishers = [threading.Thread(target=lambda: write_index(repo, original)) for _ in range(2)]
    for publisher in publishers:
        publisher.start()
    time.sleep(0.05)
    assert all(publisher.is_alive() for publisher in publishers)
    assert Path(original["indexPath"]).is_dir()

    release_reader.set()
    reader_thread.join(10)
    for publisher in publishers:
        publisher.join(30)

    assert observed[0] == observed[1]
    current = load_pointer(repo)
    state.validate_generation(current)
    assert len(list((idx_path(current["rootId"]) / "generations").iterdir())) <= 2


def test_hard_crash_staging_and_orphan_growth_is_collected_on_restart(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "MIMRY_CACHE_HOME": str(cache),
        }
    )
    command = [sys.executable, "-m", "mimry.cli", "--root", str(repo), "index"]
    pointer = load_pointer(repo)
    generations = idx_path(pointer["rootId"]) / "generations"

    orphaned = subprocess.run(command, env={**env, "MIMRY_FAULT_POINT": "after-generation"}, check=False)
    assert orphaned.returncode == 91
    assert len(list(generations.iterdir())) == 2
    rebuilt = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert len(list(generations.iterdir())) <= 2

    staged = subprocess.run(command, env={**env, "MIMRY_FAULT_POINT": "after-sidecars"}, check=False)
    assert staged.returncode == 91
    assert any(path.name.endswith(".staging") for path in generations.iterdir())
    rebuilt = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert not any(path.name.endswith(".staging") for path in generations.iterdir())
    assert len(list(generations.iterdir())) <= 2


def test_repeated_successful_publications_retain_current_and_last_known_good(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    for _ in range(5):
        write_index(repo, load_pointer(repo))

    current = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    previous = json.loads((repo / ".mimry" / "pointer.json.bak").read_text(encoding="utf-8"))
    generations = idx_path(current["rootId"]) / "generations"
    retained = {path.name for path in generations.iterdir()}
    assert retained == {current["generationId"], previous["generationId"]}
    state.validate_generation(current)
    state.validate_generation(previous)


def test_semantic_generation_swap_is_detected_as_corrupt(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    first = load_pointer(repo)
    write_index(repo, first)
    second = load_pointer(repo)
    target = sqlite3.connect(Path(second["indexPath"]) / "mimry.sqlite")
    try:
        target.execute("attach database ? as swapped", (str(Path(first["indexPath"]) / "mimry.sqlite"),))
        with target:
            target.execute("delete from semantic_chunks")
            target.execute("insert into semantic_chunks select * from swapped.semantic_chunks")
            target.execute("delete from semantic_metadata")
            target.execute("insert into semantic_metadata select * from swapped.semantic_metadata")
        target.execute("detach database swapped")
    finally:
        target.close()

    with pytest.raises(state.StateCorruptionError, match="semantic metadata does not match"):
        state.validate_generation(second)


def test_lock_timeout_is_bounded_and_actionable(tmp_path: Path):
    lock = tmp_path / "index.lock"
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with state.exclusive_file_lock(lock):
            entered.set()
            assert release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(5)
    started = time.monotonic()
    with pytest.raises(state.StateLockTimeoutError) as raised:
        with state.exclusive_file_lock(lock, timeout=0.08, poll_interval=0.01):
            pass
    elapsed = time.monotonic() - started
    release.set()
    thread.join(5)

    assert elapsed < 0.5
    assert str(lock) in str(raised.value)
    assert "another running `mimry` process" in str(raised.value)
    assert f'"pid": {os.getpid()}' in raised.value.holder


def test_windows_shared_operation_requests_fall_back_to_exclusive_locking():
    assert state._effective_shared_lock(True, platform="posix") is True
    assert state._effective_shared_lock(True, platform="nt") is False
    assert state._effective_shared_lock(False, platform="nt") is False


def test_cache_wipe_all_refuses_without_touching_cache_while_root_writer_is_active(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    pointer = load_pointer(repo)
    base = idx_path(pointer["rootId"])
    entered = threading.Event()
    release = threading.Event()

    def writer():
        with state.exclusive_file_lock(base / "operation.lock"):
            entered.set()
            assert release.wait(5)

    thread = threading.Thread(target=writer)
    thread.start()
    assert entered.wait(5)
    result = cmd_cache_wipe(SimpleNamespace(root=str(repo), all=True))
    release.set()
    thread.join(5)

    assert result == 2
    assert Path(pointer["indexPath"]).is_dir()
    assert (cache / "roots.json").is_file()


def test_generation_cleanup_unlinks_staging_symlink_and_preserves_pointer_generations(tmp_path: Path, monkeypatch):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    write_index(repo, load_pointer(repo))
    current = load_pointer(repo)
    previous = json.loads((repo / ".mimry" / "pointer.json.bak").read_text(encoding="utf-8"))
    base = idx_path(current["rootId"])
    generations = base / "generations"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (generations / ".hostile.staging").symlink_to(outside, target_is_directory=True)
    orphan = generations / "orphan"
    orphan.mkdir()
    (orphan / "junk").write_text("junk", encoding="utf-8")

    with state.exclusive_file_lock(base / "index.lock"):
        _cleanup_generations(repo, base, current)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (generations / ".hostile.staging").exists()
    assert not orphan.exists()
    assert Path(current["indexPath"]).is_dir()
    assert Path(previous["indexPath"]).is_dir()


def test_cache_wipe_reports_incomplete_removal_nonzero(tmp_path: Path, monkeypatch, capsys):
    repo, cache = _initialized_repo(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    original = shutil.rmtree

    def fail_generation(path, *args, **kwargs):
        if Path(path).name == "generations":
            raise OSError("injected removal failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr("mimry.commands.shutil.rmtree", fail_generation)
    result = cmd_cache_wipe(SimpleNamespace(root=str(repo), all=False))
    output = capsys.readouterr().out
    assert result == 2
    assert "Cache wipe incomplete" in output
    assert "injected removal failure" in output
