from __future__ import annotations

import json
import os
import errno
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from mimry import state
from mimry.freshness import index_freshness
from mimry.indexer import write_index
from mimry.mcp_server import mimry_find
from mimry.paths import idx_path
from mimry.storage import load_pointer, load_root_registry, register_root, save_pointer

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
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
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
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
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
