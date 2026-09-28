"""Git-style reuse: stat-unchanged files skip re-reading, and the output never differs."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import mimry.freshness as freshness
import mimry.indexer as indexer
from mimry.commands import cmd_init
from mimry.freshness import index_freshness
from mimry.indexer import write_index
from mimry.storage import load_pointer

ARTIFACTS = ("files.jsonl", "symbols.jsonl", "imports.jsonl", "exports.jsonl", "graph.json", "file-hashes.json")
OLD_NS = 1_600_000_000 * 10**9


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "a.py").write_text("import b\n\ndef alpha():\n    return b.beta()\n", encoding="utf-8")
    (root / "app" / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (root / "guide.md").write_text("# Demo\n\nSee [a](app/a.py).\n", encoding="utf-8")
    _age(root)
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    return root


def _age(root: Path) -> None:
    """Move every mtime well before any scan, so no file is racy."""
    for path in root.rglob("*"):
        if path.is_file() and ".mimry" not in path.parts:
            os.utime(path, ns=(OLD_NS, OLD_NS))


def _index(root: Path, **kwargs) -> dict[str, bytes]:
    write_index(root, load_pointer(root), **kwargs)
    idx = Path(load_pointer(root)["indexPath"])
    return {name: (idx / name).read_bytes() for name in ARTIFACTS}


def _count_adapts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    adapted: list[str] = []
    real = indexer.adapt

    def spy(path, root, snapshot=None):
        adapted.append(Path(path).name)
        return real(path, root, snapshot)

    monkeypatch.setattr(indexer, "adapt", spy)
    return adapted


def test_incremental_reindex_reuses_unchanged_files_and_matches_full(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    full = _index(root)
    adapted = _count_adapts(monkeypatch)

    assert _index(root) == full
    assert adapted == []

    (root / "app" / "b.py").write_text("def beta():\n    return 3\n\ndef gamma():\n    return 4\n", encoding="utf-8")
    _age(root)
    incremental = _index(root)
    assert adapted == ["b.py"]
    assert incremental == _index(root, full=True)
    assert incremental != full


def test_freshness_trusts_stat_identity_but_rereads_racy_files(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    _index(root)
    reads: list[str] = []
    real = freshness.read_snapshot

    def spy(path, *args, **kwargs):
        reads.append(Path(path).name)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(freshness, "read_snapshot", spy)
    ptr = load_pointer(root)
    assert index_freshness(root, ptr)["state"] == "current"
    assert reads == []

    # An edit that keeps the size but moves the mtime is caught without --verify.
    target = root / "app" / "b.py"
    target.write_text(target.read_text(encoding="utf-8").replace("2", "5"), encoding="utf-8")
    assert index_freshness(root, ptr)["changed"] == ["app/b.py"]

    # A file modified right before the scan is never trusted by metadata alone:
    # a second same-tick rewrite would leave its identity unchanged.
    target.write_text("def beta():\n    return 2\n", encoding="utf-8")
    _index(root)
    reads.clear()
    assert index_freshness(root, load_pointer(root))["state"] == "current"
    assert reads == ["b.py"]


def test_status_verify_rehashes_every_file(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    _index(root)
    target = root / "app" / "b.py"
    before = target.stat()
    target.write_text("def beta():\n    return 7\n", encoding="utf-8")
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    ptr = load_pointer(root)

    assert index_freshness(root, ptr)["state"] == "current"
    assert index_freshness(root, ptr, verify=True)["changed"] == ["app/b.py"]
    # A full reindex re-reads it; an incremental one would trust it, like git.
    adapted = _count_adapts(monkeypatch)
    _index(root, full=True)
    assert sorted(adapted) == ["a.py", "b.py", "guide.md"]
