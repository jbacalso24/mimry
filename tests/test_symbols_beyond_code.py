"""Tests for JS/TS modules, Markdown headings, and file name
fallback in symbol search.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_init, symbol_matches
from mimry.indexer import write_index
from mimry.scanner import markdown_heading_symbols
from mimry.storage import load_pointer


def _index(root: Path) -> dict:
    """Initialize and index a test repo."""
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)
    refreshed = load_pointer(root)
    assert refreshed is not None
    return refreshed


def test_mjs_and_cts_files_get_symbols(tmp_path: Path) -> None:
    """Test that .mjs and .cts files have their symbols indexed."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "lib").mkdir(parents=True)

    (root / "scripts" / "judge.mjs").write_text(
        "export function seal(p, o) {\n  return p;\n}\n", encoding="utf-8"
    )
    (root / "lib" / "util.cts").write_text(
        "export function helper(): number {\n  return 1;\n}\n", encoding="utf-8"
    )

    ptr = _index(root)

    seal_matches = symbol_matches(ptr, "seal")
    assert len(seal_matches) > 0
    assert seal_matches[0]["name"] == "seal"
    assert seal_matches[0]["kind"] == "function"
    assert seal_matches[0]["path"] == "scripts/judge.mjs"

    helper_matches = symbol_matches(ptr, "helper")
    assert len(helper_matches) > 0
    assert helper_matches[0]["name"] == "helper"
    assert helper_matches[0]["kind"] == "function"
    assert helper_matches[0]["path"] == "lib/util.cts"


def test_markdown_headings_are_symbols_spanning_their_section() -> None:
    """Test that markdown headings are extracted as symbols with
    correct spans.
    """
    text = """# Guide
intro
## Maturity - read this first
text
```bash
# not a heading
```
### Detail ##
more
## Next
end
"""
    symbols, edges = markdown_heading_symbols("fid", text)

    # Check the symbols have correct names, line_start, and line_end
    result = [(s["name"], s["line_start"], s["line_end"]) for s in symbols]
    assert result == [
        ("Guide", 1, 11),
        ("Maturity - read this first", 3, 9),
        ("Detail", 8, 9),
        ("Next", 10, 11),
    ]

    # Check all are headings
    for s in symbols:
        assert s["kind"] == "heading"

    # Check there's one defines edge per symbol
    assert len(edges) == len(symbols)
    for edge in edges:
        assert edge["edge_type"] == "defines"


def test_symbol_finds_markdown_heading_after_indexing(tmp_path: Path) -> None:
    """Test that markdown headings are found by symbol_matches after
    indexing.
    """
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)

    (root / "docs" / "guide.md").write_text(
        """# Guide
intro
## Maturity - read this first
text
```bash
# not a heading
```
### Detail ##
more
## Next
end
""",
        encoding="utf-8",
    )

    ptr = _index(root)

    matches = symbol_matches(ptr, "maturity")
    assert len(matches) > 0
    assert matches[0]["name"] == "Maturity - read this first"
    assert matches[0]["kind"] == "heading"
    assert matches[0]["path"] == "docs/guide.md"
    assert matches[0]["line_start"] == 3


def test_symbol_falls_back_to_file_names(tmp_path: Path) -> None:
    """Test that symbol_matches falls back to file names when no
    symbols match.
    """
    root = tmp_path / "repo"
    (root / "skills" / "verify" / "scripts").mkdir(parents=True)

    (root / "skills" / "verify" / "scripts" / "scan_standards.py").write_text(
        "def main():\n    return 0\n", encoding="utf-8"
    )

    ptr = _index(root)

    # Test fallback to file name
    matches = symbol_matches(ptr, "scan_standards")
    assert len(matches) == 1
    assert matches[0]["name"] == "scan_standards.py"
    assert matches[0]["kind"] == "file"
    assert matches[0]["language"] == ""
    assert matches[0]["path"] == "skills/verify/scripts/scan_standards.py"
    assert matches[0]["line_start"] is None

    # Test that the symbol match takes precedence
    matches = symbol_matches(ptr, "main")
    assert len(matches) > 0
    assert matches[0]["kind"] == "function"


def test_heading_with_a_secret_is_not_a_symbol() -> None:
    """Test that headings containing secrets are not extracted."""
    text = "## Token ghp_" + "a" * 36 + "\n"
    symbols, edges = markdown_heading_symbols("fid", text)
    assert symbols == []
    assert edges == []


def test_heading_lines_count_newlines_only():
    """Editors and ripgrep count \\n; a form feed or U+2028 in the
    text must not shift later headings, and CRLF must not leak into
    titles."""
    text = "# One\r\nfeed\x0cstill line two\r\n## Two ##\r\nsep same line\r\n## Three\r\n"
    symbols, _ = markdown_heading_symbols("fid", text)
    assert [(s["name"], s["line_start"], s["line_end"]) for s in symbols] == [
        ("One", 1, 5),
        ("Two", 3, 4),
        ("Three", 5, 5),
    ]
