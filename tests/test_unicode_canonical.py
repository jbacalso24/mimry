"""Tests for YELLOW 1: Unicode canonicalization.

Ensures that NFC and NFD filenames produce identical records
and that the canonical path is used for all identity-bearing metadata.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
import tempfile

import pytest

from mimry.scanner import file_record
from mimry.paths import canonical_rel_path


def test_nfc_and_nfd_filenames_produce_identical_file_records():
    """Same logical filename (e.g. café.py) in NFC vs NFD produces identical records.

    MUST fail before the fix.
    """
    with tempfile.TemporaryDirectory() as tmpdir1:
        with tempfile.TemporaryDirectory() as tmpdir2:
            root1 = Path(tmpdir1)
            root2 = Path(tmpdir2)

            # Create two variants of the same logical filename
            # NFC: café (single composed character é = U+00E9)
            # NFD: cafe (e + combining accent = U+0065 U+0301)
            filename_nfc = "café.py"
            filename_nfd = "café.py"  # Decomposed form

            # Verify they're different byte sequences but same logical name
            assert filename_nfc != filename_nfd
            assert unicodedata.normalize("NFC", filename_nfc) == filename_nfc
            assert unicodedata.normalize("NFD", filename_nfd) == filename_nfd
            assert unicodedata.normalize("NFC", filename_nfd) == filename_nfc

            # Try to create both (some filesystems will normalize, that's ok)
            nfc_path = root1 / filename_nfc
            try:
                nfc_path.write_bytes(b"# coding: utf-8\ndef hello(): pass\n")
            except (OSError, ValueError):
                pytest.skip("Filesystem does not support NFC filename")

            # If NFC worked, try NFD
            nfd_path = root2 / filename_nfd
            try:
                nfd_path.write_bytes(b"# coding: utf-8\ndef hello(): pass\n")
            except (OSError, ValueError):
                pytest.skip("Filesystem does not support NFD filename")

            # Get canonical paths
            nfc_canonical = canonical_rel_path(nfc_path, root1)
            nfd_canonical = canonical_rel_path(nfd_path, root2)

            # Both should normalize to NFC
            assert nfc_canonical == nfd_canonical
            assert nfc_canonical == filename_nfc

            # Create file records
            nfc_record = file_record(nfc_path, root1, "python-ast", "ok", "hint")
            nfd_record = file_record(nfd_path, root2, "python-ast", "ok", "hint")

            # Records should have identical canonical fields
            assert nfc_record["rel_path"] == nfd_record["rel_path"] == filename_nfc
            assert nfc_record["filename"] == nfd_record["filename"]
            assert nfc_record["extension"] == nfd_record["extension"]

            # metadata_text should use canonical names
            assert nfc_record["metadata_text"] == nfd_record["metadata_text"]


def test_nfd_root_produces_same_canonical_rel_path():
    """Directory components are also NFC-normalized.

    A file at café/file.py should produce the same canonical path
    regardless of whether the directory is stored as NFC or NFD.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Try to create directories with accents
        dirname_nfc = "café"
        dirname_nfd = "café"

        dir_nfc = root / dirname_nfc
        try:
            dir_nfc.mkdir(exist_ok=True)
            file_nfc = dir_nfc / "test.py"
            file_nfc.write_bytes(b"pass\n")
        except (OSError, ValueError):
            pytest.skip("Filesystem does not support NFC directory names")

        # If NFC worked, try NFD
        dir_nfd = root / dirname_nfd
        try:
            dir_nfd.mkdir(exist_ok=True)
            file_nfd = dir_nfd / "test.py"
            file_nfd.write_bytes(b"pass\n")
        except (OSError, ValueError):
            pytest.skip("Filesystem does not support NFD directory names")

        # Get canonical paths
        nfc_canonical = canonical_rel_path(file_nfc, root)
        nfd_canonical = canonical_rel_path(file_nfd, root)

        # Both should normalize to NFC
        assert nfc_canonical == nfd_canonical
        expected = f"{dirname_nfc}/test.py"
        assert nfc_canonical == expected


def test_canonical_path_used_in_metadata():
    """The canonical (NFC) path is used in all metadata identity fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a file with a non-ASCII name
        filename = "tëst.py"  # NFD: tëst.py
        filepath = root / filename
        try:
            filepath.write_bytes(b"# test\n")
        except (OSError, ValueError):
            pytest.skip("Filesystem does not support non-ASCII filenames")

        # Create file record
        record = file_record(filepath, root, "python-ast", "ok", "hint")

        # These should all use NFC canonical forms
        assert record["rel_path"] == unicodedata.normalize("NFC", filename)
        assert record["filename"] == unicodedata.normalize("NFC", filepath.name)
        assert record["extension"] == filepath.suffix.lower()

        # metadata_text should not use raw path.name
        canonical = canonical_rel_path(filepath, root)
        assert canonical in record["metadata_text"]
