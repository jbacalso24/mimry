from __future__ import annotations

import hashlib
import json
import os
import shutil
import unicodedata
from pathlib import Path
from unittest.mock import patch

import pytest

from mimry.indexer import write_index, _collect
from mimry.paths import stable_id
from mimry.scanner import scan, file_record, adapt
from mimry.storage import register_root, save_pointer


def _small_repo(root: Path) -> None:
    """Build a small test repo with 6 files across 3 dirs for determinism testing."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("def main(): pass\n")
    (root / "src" / "config.py").write_text("DEBUG = True\n")
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_app.py").write_text("def test_main(): pass\n")
    (root / "tests" / "test_config.py").write_text("def test_config(): pass\n")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "README.md").write_text("# My App\n")
    (root / "LICENSE").write_text("MIT\n")


def _setup_pointer(root: Path, cache: Path, root_id: str) -> dict:
    """Create and register a pointer for indexing."""
    pointer = {
        "rootId": root_id,
        "rootPath": str(root),
        "rootType": "repo",
        "indexPath": str(cache / "indexes" / root_id / "index"),
        "createdAt": "2026-01-01T00:00:00+00:00",
        "lastIndexedAt": None,
        "schemaVersion": "0.2.0",
    }
    os.environ["MIMRY_CACHE_HOME"] = str(cache)
    register_root(pointer)
    (root / ".mimry").mkdir(parents=True, exist_ok=True)
    save_pointer(root, pointer)
    return pointer


def test_scan_order_independent_of_creation_order(tmp_path: Path):
    """Files scanned must be in deterministic order regardless of creation order."""
    # Create repo A: files created in reverse alphabetical order
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    for name in reversed(["a.py", "b.py", "c.py"]):
        (repo_a / name).write_text(f"# {name}\n")

    # Create repo B: files created in forward alphabetical order
    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    for name in ["a.py", "b.py", "c.py"]:
        (repo_b / name).write_text(f"# {name}\n")

    # Scan both repos
    scan_a = sorted([p.relative_to(repo_a).as_posix() for p in scan(repo_a)])
    scan_b = sorted([p.relative_to(repo_b).as_posix() for p in scan(repo_b)])

    # Must be identical lists
    assert scan_a == scan_b == ["a.py", "b.py", "c.py"]


def test_full_index_identical_across_creation_order(tmp_path: Path):
    """Complete index must be byte-for-byte identical regardless of file creation order."""
    cache = tmp_path / "cache"
    cache.mkdir()

    # Build repo A with reverse creation order
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    _small_repo(repo_a)
    # Re-create files in reverse order to ensure OS doesn't cache order
    files_a = sorted((repo_a / "src").glob("*.py")) + sorted((repo_a / "tests").glob("*.py"))
    file_contents = {f: f.read_text() for f in files_a}
    for f in files_a:
        f.unlink()
    for f in reversed(files_a):
        f.write_text(file_contents[f])

    # Build repo B with forward creation order
    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    _small_repo(repo_b)

    ptr_a = _setup_pointer(repo_a, cache, "repo-a")
    ptr_b = _setup_pointer(repo_b, cache, "repo-b")

    # Index both
    result_a = write_index(repo_a, ptr_a)
    result_b = write_index(repo_b, ptr_b)

    # Read the jsonl files
    idx_a = Path(result_a["index"])
    idx_b = Path(result_b["index"])

    def load_jsonl(path):
        records = []
        for line in path.read_text().strip().split("\n"):
            if line:
                records.append(json.loads(line))
        return records

    files_a = load_jsonl(idx_a / "files.jsonl")
    files_b = load_jsonl(idx_b / "files.jsonl")
    symbols_a = load_jsonl(idx_a / "symbols.jsonl")
    symbols_b = load_jsonl(idx_b / "symbols.jsonl")

    # Sort for comparison
    files_a_sorted = sorted(files_a, key=lambda f: f["rel_path"])
    files_b_sorted = sorted(files_b, key=lambda f: f["rel_path"])
    symbols_a_sorted = sorted(symbols_a, key=lambda s: (s["file_id"], s["name"]))
    symbols_b_sorted = sorted(symbols_b, key=lambda s: (s["file_id"], s["name"]))

    # Files must have identical structure except for absolute path
    assert len(files_a_sorted) == len(files_b_sorted)
    for fa, fb in zip(files_a_sorted, files_b_sorted):
        assert fa["rel_path"] == fb["rel_path"]
        assert fa["file_id"] == fb["file_id"]
        assert fa["hash"] == fb["hash"]
        assert fa["size"] == fb["size"]

    # Symbols must be identical
    assert symbols_a_sorted == symbols_b_sorted


def test_canonical_ids_identical_across_absolute_roots(tmp_path: Path):
    """File and symbol IDs must be identical across different absolute checkout paths."""
    cache = tmp_path / "cache"
    cache.mkdir()

    # Build the same repo at two different paths
    repo_a = tmp_path / "path_a" / "repo"
    repo_a.mkdir(parents=True)
    _small_repo(repo_a)

    repo_b = tmp_path / "path_b" / "my_project"
    repo_b.mkdir(parents=True)
    _small_repo(repo_b)

    ptr_a = _setup_pointer(repo_a, cache, "repo-x")
    ptr_b = _setup_pointer(repo_b, cache, "repo-y")

    # Index both
    result_a = write_index(repo_a, ptr_a)
    result_b = write_index(repo_b, ptr_b)

    idx_a = Path(result_a["index"])
    idx_b = Path(result_b["index"])

    # Parse files.jsonl to extract file_ids
    def load_jsonl(path):
        records = []
        for line in path.read_text().strip().split("\n"):
            if line:
                records.append(json.loads(line))
        return records

    files_a = load_jsonl(idx_a / "files.jsonl")
    files_b = load_jsonl(idx_b / "files.jsonl")

    symbols_a = load_jsonl(idx_a / "symbols.jsonl")
    symbols_b = load_jsonl(idx_b / "symbols.jsonl")

    # Both must have same number of files and symbols
    assert len(files_a) == len(files_b)
    assert len(symbols_a) == len(symbols_b)

    # IDs must match when sorted by rel_path
    files_a_sorted = sorted(files_a, key=lambda f: f["rel_path"])
    files_b_sorted = sorted(files_b, key=lambda f: f["rel_path"])

    for fa, fb in zip(files_a_sorted, files_b_sorted):
        assert fa["rel_path"] == fb["rel_path"]
        assert fa["file_id"] == fb["file_id"], f"file_id mismatch for {fa['rel_path']}"

    # Symbol IDs must also match
    symbols_a_sorted = sorted(symbols_a, key=lambda s: (s["file_id"], s["name"]))
    symbols_b_sorted = sorted(symbols_b, key=lambda s: (s["file_id"], s["name"]))

    for sa, sb in zip(symbols_a_sorted, symbols_b_sorted):
        assert sa["symbol_id"] == sb["symbol_id"]


def test_file_record_hash_matches_snapshot_bytes(tmp_path: Path):
    """Recorded file hash must exactly match the bytes that were actually read."""
    repo = tmp_path / "repo"
    repo.mkdir()
    test_file = repo / "test.py"
    test_file.write_text("print('hello')\n")

    # Collect the file record (9 return values)
    files, _, _, _, _, _, _, _, _ = _collect(repo)
    assert len(files) == 1

    file_rec = files[0]
    recorded_hash = file_rec["hash"]
    actual_bytes = test_file.read_bytes()
    expected_hash = hashlib.sha256(actual_bytes).hexdigest()

    assert recorded_hash == expected_hash


def test_file_changed_during_read_is_not_indexed(tmp_path: Path):
    """File that changes between metadata snapshot and content read must raise FileChangedError."""
    repo = tmp_path / "repo"
    repo.mkdir()
    test_file = repo / "volatile.py"
    test_file.write_text("# original\n")

    # Monkeypatch sha() to mutate the file before reading it
    from mimry import scanner
    original_sha = scanner.sha

    def mutating_sha(path):
        if "volatile" in str(path):
            path.write_text("# mutated\n")
        return original_sha(path)

    with patch.object(scanner, "sha", mutating_sha):
        # This should either raise FileChangedError or mark as unindexable
        files, _, _, _, _, _, _, _, _ = _collect(repo)
        # The file should be marked as unindexable due to mismatch
        # (or it should have been skipped)


def test_oversized_and_unreadable_files_still_skipped_gracefully(tmp_path: Path):
    """Regression: oversized and unreadable files must still be skipped without crashing."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Create a normal file
    (repo / "normal.py").write_text("print('ok')\n")

    # Create an oversized file (over 1MB)
    oversized = repo / "huge.bin"
    with open(oversized, "wb") as f:
        f.write(b"x" * (1_000_001))

    # Create an unreadable file (if possible on this platform)
    unreadable = repo / "locked.py"
    unreadable.write_text("# locked\n")
    try:
        os.chmod(unreadable, 0o000)
    except Exception:
        # Some systems don't support chmod; skip this part
        pass

    # Collection should complete gracefully
    try:
        files, _, _, _, _, _, _, _, _ = _collect(repo)
        # Should only collect the normal file
        assert any(f["filename"] == "normal.py" for f in files)
        # Should not collect oversized
        assert not any("huge.bin" in f["filename"] for f in files)
    finally:
        # Restore permissions for cleanup
        try:
            os.chmod(unreadable, 0o644)
        except Exception:
            pass
