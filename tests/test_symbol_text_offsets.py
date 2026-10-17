"""Symbol names must survive a UTF-8 BOM and non-ASCII source.

tree-sitter reports *byte* offsets. Slicing the decoded str with them
shifts every name by the number of extra bytes ahead of it. A UTF-8 BOM
alone costs 2 characters, and Visual Studio writes C#/TS files with a
BOM by default -- on a real 1,035-file .NET solution this corrupted 32%
of symbol labels (`formattedNumber` -> `rmattedNumber`) while every test
here passed, because fixtures written by Python are BOM-free ASCII.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.adapters.ts_ast import parse_ts_like
from mimry.commands import cmd_init
from mimry.core.languages import extract
from mimry.indexer import write_index
from mimry.scanner import adapt
from mimry.storage import load_pointer

BOM = "﻿"


def _names(result: dict) -> set[str]:
    return {d["name"] for d in result["definitions"]}


@pytest.mark.parametrize(
    "name,suffix,source,expected",
    [
        ("bom_ts", ".ts", BOM + "export const formattedNumber = 1;\n", "formattedNumber"),
        ("bom_cs", ".cs", BOM + "namespace A { class PolicyAttribute { } }\n", "PolicyAttribute"),
        # Non-ASCII drifts offsets even without a BOM, and by more the
        # further in you go.
        (
            "accented_ts",
            ".ts",
            "// café über naïve\nexport function computeTotal() {}\n",
            "computeTotal",
        ),
        ("emoji_cs", ".cs", "// \U0001f680\U0001f680\nclass LaunchService { }\n", "LaunchService"),
    ],
)
def test_extract_keeps_names_intact(
    tmp_path: Path, name: str, suffix: str, source: str, expected: str
) -> None:
    path = tmp_path / f"{name}{suffix}"
    path.write_text(source, encoding="utf-8")

    names = _names(extract(path, source))

    assert expected in names, f"expected {expected!r}, got {sorted(names)}"
    # The failure mode is a *prefix* being eaten, which leaves a
    # plausible-looking suffix rather than an obviously broken value --
    # assert nothing is a tail of it.
    assert not any(n != expected and expected.endswith(n) for n in names), (
        f"a truncated variant of {expected!r} survived: {sorted(names)}"
    )


def test_ts_adapter_keeps_names_intact(tmp_path: Path) -> None:
    """scanner.py routes .ts/.tsx through this adapter.

    It had the same defect.
    """
    path = tmp_path / "reducer.ts"
    path.write_text(BOM + "export const sidebarToggledHandler = () => {};\n", encoding="utf-8")

    symbols, _edges, _imports, _exports, status, _adapter = parse_ts_like(
        path, tmp_path, {"file_id": "f1"}
    )

    assert not status.startswith("parse_error"), status
    names = {s["name"] for s in symbols}
    assert "sidebarToggledHandler" in names, sorted(names)


@pytest.mark.parametrize(
    "suffix,source,expected",
    [
        (
            ".cs",
            "class S { void M(){ schemes.Where(x => x.Name != null).ToList(); } }\n",
            {"Where", "ToList"},
        ),
        (".cs", "class S { void M(){ provider.GetService<IFoo>(); } }\n", {"GetService"}),
        (".ts", "function m(){ items.filter(x => x.ok).map(y => y.id); }\n", {"filter", "map"}),
        (".go", "func m(){ helper.Run() }\n", {"Run"}),
    ],
)
def test_call_names_are_identifiers_not_whole_expressions(
    tmp_path, suffix, source, expected
) -> None:
    """A fluent chain must yield the method name, not the entire
    receiver expression.

    Using the call node's first child verbatim produced multi-line blobs
    that no caller could resolve -- and one such blob pulled enough file
    content into the record to trip the secret scanner, silently
    dropping the file from the index.
    """
    path = tmp_path / f"calls{suffix}"
    path.write_text(source, encoding="utf-8")

    names = {c["name"] for c in extract(path, source)["calls"]}

    assert expected <= names, f"missing {expected - names}; got {sorted(names)}"
    assert not any(any(ch.isspace() for ch in n) for n in names), (
        f"expression leaked in: {sorted(names)}"
    )


def test_every_parsed_language_is_also_scannable_text() -> None:
    """A language we parse but omit from TEXT_EXTS gets no secret scan
    and no hint.

    has_sensitive_content() returns False for extensions outside
    TEXT_EXTS, so adding C#/Go/Rust to the parser without adding them
    here left those files unscanned for secrets and with an empty
    content hint.
    """
    from mimry.constants import TEXT_EXTS
    from mimry.core.languages import EXTENSION_LANGUAGE

    missing = sorted(set(EXTENSION_LANGUAGE) - set(TEXT_EXTS))
    assert missing == [], f"parsed but not scannable as text: {missing}"


def test_python_file_saved_with_a_bom_keeps_its_symbols(tmp_path: Path, monkeypatch) -> None:
    """Windows editors write a BOM. ast.parse rejects U+FEFF, so the
    file used to lose every symbol without a warning."""
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "bom.py").write_bytes(
        BOM.encode("utf-8")
        + b"def bommed():\n    return helper_bom()\n\ndef helper_bom():\n    return 3\n"
    )
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    write_index(root, load_pointer(root))

    index = Path(load_pointer(root)["indexPath"])
    symbols = (index / "symbols.jsonl").read_text(encoding="utf-8")
    assert '"bommed"' in symbols and '"helper_bom"' in symbols


@pytest.mark.parametrize(
    ("bom", "codec"), [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")]
)
def test_utf16_python_and_markdown_keep_their_symbols(tmp_path, bom, codec):
    """PowerShell 5 writes UTF-16 with a BOM. The Python and Markdown
    branches used to decode UTF-8, so the file lost its symbols."""
    py = tmp_path / "a.py"
    py.write_bytes(bom + "def alpha_fn():\n    return 1\n".encode(codec))
    md = tmp_path / "h.md"
    md.write_bytes(bom + "# ThetaHeading\n\nbody\n".encode(codec))

    py_record, py_symbols, *_ = adapt(py, tmp_path)
    md_record, md_symbols, *_ = adapt(md, tmp_path)

    assert py_record["parse_status"] == "ok"
    assert [(s["name"], s["line_start"]) for s in py_symbols] == [("alpha_fn", 1)]
    assert [(s["name"], s["line_start"]) for s in md_symbols] == [("ThetaHeading", 1)]


def test_markdown_heading_on_line_one_after_a_utf8_bom_is_a_symbol(tmp_path):
    """The UTF-8 BOM used to stay in the text, hiding the `#`."""
    md = tmp_path / "f.md"
    md.write_bytes(b"\xef\xbb\xbf# BomHeading\n\nbody\n")

    _record, symbols, *_ = adapt(md, tmp_path)

    assert [(s["name"], s["line_start"]) for s in symbols] == [("BomHeading", 1)]
