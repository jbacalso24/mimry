"""Context normalization must mask the operational envelope and nothing else.

The determinism matrix compares context packs across permutations and
platforms. To do that it has to blank out values that legitimately differ --
the absolute root, the generation UUID, the index path, the indexed-at
timestamp -- while leaving every canonical fact intact.

The earlier implementation dropped whole lines that contained the root or the
word "Generated" and rewrote any 32-character hex token. That is broad textual
masking: it silently discarded real semantic guidance (MIMRY's own risk note
about generated/cache paths) and could erase canonical content hashes. These
tests pin the narrow, field-aware behavior instead.
"""

from __future__ import annotations

from mimry.digest import normalize_context

ENVELOPE = {
    "root": "/home/ci/work/checkout",
    "index_path": "/home/ci/.cache/mimry/indexes/abc/generations/0f9c2b1d4e5a6f7809a1b2c3d4e5f607",
    "generation_id": "0f9c2b1d4e5a6f7809a1b2c3d4e5f607",
    "indexed_at": "2026-08-09T06:43:45.933916+00:00",
}


def test_absolute_root_is_replaced_without_dropping_the_line():
    line = "- Root: `/home/ci/work/checkout` (files: 102; symbols: 593)"
    out = normalize_context(line, **ENVELOPE)
    assert "<root>" in out
    # The canonical counts on the same line must survive.
    assert "files: 102; symbols: 593" in out
    assert "/home/ci/work/checkout" not in out


def test_generation_id_and_timestamp_are_replaced_in_place():
    line = f"- Index: current (last indexed: {ENVELOPE['indexed_at']}; generation {ENVELOPE['generation_id']})"
    out = normalize_context(line, **ENVELOPE)
    assert "<generation>" in out and "<timestamp>" in out
    assert "- Index: current (last indexed:" in out


def test_semantic_guidance_containing_generated_is_preserved():
    """MIMRY's own risk note is canonical guidance, not an operational field."""
    line = "- Generated/cache paths (`.mimry/`, `.git/`, caches, build outputs) are support artifacts; do not edit them as source fixes."
    assert normalize_context(line, **ENVELOPE) == line


def test_unrelated_32_char_hex_token_is_preserved():
    """A content hash is canonical state; only the known generation id is masked."""
    other = "d41d8cd98f00b204e9800998ecf8427e"
    line = f"- Content hash: {other}"
    assert normalize_context(line, **ENVELOPE) == line


def test_unrelated_timestamp_is_preserved():
    line = "- Migration 2026-07-01T00:00:00+00:00 adds session expiry"
    assert normalize_context(line, **ENVELOPE) == line


def test_root_like_substring_that_is_not_the_root_is_preserved():
    line = "- See /home/ci/work/checkout-plan.md for the design"
    out = normalize_context(line, **ENVELOPE)
    # The real root is a path prefix of this string but not this path; the
    # trailing "-plan.md" must not be orphaned onto a "<root>" stem.
    assert "checkout-plan.md" in out


def test_blank_lines_are_dropped_but_content_lines_are_not():
    text = "alpha\n\n   \nbeta"
    assert normalize_context(text, **ENVELOPE).splitlines() == ["alpha", "beta"]


def test_index_path_is_replaced_before_generation_id():
    """The index path contains the generation id; masking must not leave a stub."""
    line = f"- Index path: {ENVELOPE['index_path']}"
    out = normalize_context(line, **ENVELOPE)
    assert out == "- Index path: <indexpath>"


def test_windows_style_root_is_replaced():
    envelope = {**ENVELOPE, "root": r"C:\Users\ci\work\checkout"}
    line = r"- Root: `C:\Users\ci\work\checkout` (files: 3)"
    out = normalize_context(line, **envelope)
    assert "<root>" in out and "files: 3" in out
