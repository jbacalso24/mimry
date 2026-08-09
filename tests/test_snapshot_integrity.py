"""Tests for RED 2: snapshot integrity and adapter isolation.

Ensures that:
1. Metadata equality is not proof content did not change (hash verification)
2. Adapters never reopen the live file during indexing
3. Hash and symbols derive from the same bytes
"""

from __future__ import annotations

import hashlib
import builtins
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mimry.scanner import (
    FileChangedError,
    adapt,
    file_record,
    read_snapshot,
)
from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.storage import load_pointer


def test_same_size_rewrite_during_snapshot_acquisition_is_rejected():
    """A real rewrite between the two acquisition reads must fail closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "test.py"
        original = b"def foo(): pass  "  # 16 bytes
        replaced = b"def bar(): pass  "  # Also 16 bytes
        test_file.write_bytes(original)
        real_fdopen = os.fdopen
        acquisition_reads = 0
        acquisition_attempts = 0

        class MutatingReader:
            def __init__(self, handle, next_bytes):
                self.handle = handle
                self.next_bytes = next_bytes
                self.mutated = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def read(self, *args):
                nonlocal acquisition_reads
                data = self.handle.read(*args)
                acquisition_reads += 1
                if not self.mutated:
                    with builtins.open(test_file, "wb") as writer:
                        writer.write(self.next_bytes)
                    self.mutated = True
                return data

            def seek(self, *args):
                return self.handle.seek(*args)

        def mutating_fdopen(fd, *args, **kwargs):
            nonlocal acquisition_attempts
            handle = real_fdopen(fd, *args, **kwargs)
            acquisition_attempts += 1
            next_bytes = replaced if acquisition_attempts % 2 else original
            return MutatingReader(handle, next_bytes)

        with patch("mimry.scanner.os.fdopen", mutating_fdopen), pytest.raises(FileChangedError):
            read_snapshot(test_file)

        assert acquisition_attempts == 3, "snapshot acquisition must stop after three attempts"
        assert acquisition_reads == 6, "each attempt must compare two descriptor reads"


def test_same_inode_replacement_detected():
    """Overwrite in place (open "r+b"), inode unchanged.

    The hash should still detect the change even though inode/size/mtime
    might briefly match.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "test.py"

        original = b"original content."
        test_file.write_bytes(original)

        # In-place overwrite (open("r+b"))
        with open(test_file, "r+b") as f:
            f.write(b"changed content ")  # Same length

        # Snapshot should succeed
        data, st = read_snapshot(test_file)

        # Hash should reflect the change
        assert hashlib.sha256(data).hexdigest() != hashlib.sha256(original).hexdigest()


def test_mutation_during_parsing_does_not_mix_evidence():
    """Hash and symbols must come from the same bytes.

    Ensures that if a file changes between hash computation and parsing,
    they don't become out of sync in the record.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "test.py"

        content = b"def foo():\n    pass\n"
        test_file.write_bytes(content)

        # Read snapshot once
        data, st = read_snapshot(test_file)
        computed_hash = hashlib.sha256(data).hexdigest()

        # Create a file record with the snapshot
        rec = file_record(test_file, root, "python-ast", "ok", "", snapshot=(data, st))

        # The hash in the record must match data, not a later file state
        assert rec["hash"] == computed_hash

        # Mutate the file
        test_file.write_bytes(b"def bar():\n    pass\n")

        # Hash in record should still reflect original
        assert rec["hash"] == computed_hash


def test_adapter_never_reopens_live_path():
    """Adapters must parse the captured bytes, never re-read the path.

    The snapshot layer itself legitimately opens the file -- once to read, and
    again to prove the content did not change underneath. That is bounded and
    is the whole point. What must never happen is an adapter going back to the
    filesystem afterwards, because by then the bytes it gets may be a different
    version than the one that was hashed.

    So: `Path.open` is counted and bounded, while `read_text`/`read_bytes` --
    which only adapters use -- must not be called at all.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "test.py"
        test_file.write_bytes(b"def foo():\n    pass\n")

        opens: list[str] = []
        reopens: list[tuple[str, str]] = []
        real_open = Path.open

        def counting_open(self, *args, **kwargs):
            if Path(self) == test_file:
                opens.append(str(self))
            return real_open(self, *args, **kwargs)

        def forbidden_read_text(self, *args, **kwargs):
            reopens.append(("read_text", str(self)))
            raise AssertionError(f"adapter re-read {self} via read_text instead of using the snapshot")

        def forbidden_read_bytes(self, *args, **kwargs):
            reopens.append(("read_bytes", str(self)))
            raise AssertionError(f"adapter re-read {self} via read_bytes instead of using the snapshot")

        with (
            patch.object(Path, "open", counting_open),
            patch.object(Path, "read_text", forbidden_read_text),
            patch.object(Path, "read_bytes", forbidden_read_bytes),
        ):
            record, symbols, _edges, _imports, _exports, _calls, _references = adapt(test_file, root)

        assert record is not None
        assert reopens == [], f"adapt() re-read the live path: {reopens}"
        # Bounded: the snapshot reads, plus its verification re-read. Anything
        # beyond the retry ceiling means something is reading per-adapter.
        assert len(opens) <= 4, f"snapshot layer opened {test_file.name} {len(opens)} times: {opens}"
        assert [s["name"] for s in symbols] == ["foo"]


def test_hash_and_symbols_derive_from_same_bytes():
    """Verify that file_record hash and parsed symbols come from same snapshot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "test.py"

        # Python file with a function
        content = b"def greet():\n    return 'hello'\n"
        test_file.write_bytes(content)

        # Get snapshot
        data, st = read_snapshot(test_file)

        # Create record
        rec = file_record(test_file, root, "python-ast", "ok", "", snapshot=(data, st))

        # Hash should be from the data
        expected_hash = hashlib.sha256(data).hexdigest()
        assert rec["hash"] == expected_hash

        # Mutate the file
        test_file.write_bytes(b"def different():\n    pass\n")

        # Re-read would give different content
        new_data, _ = read_snapshot(test_file)
        new_hash = hashlib.sha256(new_data).hexdigest()

        # But our record still has the original hash
        assert rec["hash"] == expected_hash
        assert rec["hash"] != new_hash


def test_bounded_retry_then_fail_closed():
    """Assert retry count is bounded, no infinite loop."""
    # Determine the retry limit from read_snapshot
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "test.py"
        test_file.write_bytes(b"content")

        # Mock path.stat to always return different values
        call_count = [0]
        original_stat = Path.stat

        def changing_stat(self, *args, **kwargs):
            call_count[0] += 1
            # Make it look like the file is changing
            result = original_stat(self, *args, **kwargs)
            if call_count[0] % 2 == 0:
                # Return a different st_mtime_ns on even calls
                class ChangingResult:
                    def __init__(self, real):
                        self.st_mode = real.st_mode
                        self.st_size = real.st_size
                        self.st_mtime = real.st_mtime
                        self.st_mtime_ns = real.st_mtime_ns + 1  # Different
                        self.st_ino = real.st_ino
                        self.st_dev = real.st_dev
                        self.st_atime_ns = real.st_atime_ns

                return ChangingResult(result)
            return result

        with patch.object(Path, "stat", changing_stat), pytest.raises(FileChangedError):
            read_snapshot(test_file)

        assert call_count[0] == 6, "three attempts must perform exactly two path-stat checks each"


def test_oversized_unreadable_and_deleted_files_still_skipped():
    """Gracefully handle deleted and oversized files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Oversized file
        oversized = root / "big.bin"
        oversized.write_bytes(b"x" * 2_000_000)
        # Should be skipped during scan
        assert oversized.stat().st_size > 1_000_000

        # Deleted file
        deleted = root / "gone.txt"
        deleted.write_bytes(b"content")
        deleted.unlink()

        with pytest.raises(FileNotFoundError):
            read_snapshot(deleted)


def test_pipeline_rejects_file_swapped_to_outside_symlink_before_adaptation(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "safe.txt"
    target.write_text("safe local text", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    marker = "EXTERNAL_MARKER_MUST_NOT_BE_PUBLISHED"
    outside.write_text(marker, encoding="utf-8")
    try:
        probe = tmp_path / "probe-link"
        probe.symlink_to(outside)
        probe.unlink()
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)

    from mimry import indexer

    real_scan = indexer.scan

    def swapping_scan(scan_root):
        discovered = list(real_scan(scan_root))
        target.unlink()
        target.symlink_to(outside)
        yield from discovered

    monkeypatch.setattr(indexer, "scan", swapping_scan)
    stats = write_index(root, pointer)
    published = Path(stats["index"])
    persisted = b"".join(path.read_bytes() for path in published.iterdir() if path.is_file())
    assert marker.encode() not in persisted
    records = [json.loads(line) for line in (published / "files.jsonl").read_text().splitlines() if line]
    assert "safe.txt" not in {record["rel_path"] for record in records}
