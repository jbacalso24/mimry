from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from mimry import state
from mimry.storage import load_root_registry, register_root

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
