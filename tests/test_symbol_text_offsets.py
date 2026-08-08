"""Symbol names must survive a UTF-8 BOM and non-ASCII source.

tree-sitter reports *byte* offsets. Slicing the decoded str with them shifts every
name by the number of extra bytes ahead of it. A UTF-8 BOM alone costs 2 characters,
and Visual Studio writes C#/TS files with a BOM by default -- on a real 1,035-file
.NET solution this corrupted 32% of symbol labels (`formattedNumber` -> `rmattedNumber`)
while every test here passed, because fixtures written by Python are BOM-free ASCII.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mimry.core.languages import extract
from mimry.ts_ast_adapter import parse_ts_like

BOM = "﻿"


def _names(result: dict) -> set[str]:
    return {d["name"] for d in result["definitions"]}


@pytest.mark.parametrize(
    "name,suffix,source,expected",
    [
        ("bom_ts", ".ts", BOM + "export const formattedNumber = 1;\n", "formattedNumber"),
        ("bom_cs", ".cs", BOM + "namespace A { class PolicyAttribute { } }\n", "PolicyAttribute"),
        # Non-ASCII drifts offsets even without a BOM, and by more the further in you go.
        ("accented_ts", ".ts", "// café über naïve\nexport function computeTotal() {}\n", "computeTotal"),
        ("emoji_cs", ".cs", "// \U0001f680\U0001f680\nclass LaunchService { }\n", "LaunchService"),
    ],
)
def test_extract_keeps_names_intact(tmp_path: Path, name: str, suffix: str, source: str, expected: str) -> None:
    path = tmp_path / f"{name}{suffix}"
    path.write_text(source, encoding="utf-8")

    names = _names(extract(path, source))

    assert expected in names, f"expected {expected!r}, got {sorted(names)}"
    # The failure mode is a *prefix* being eaten, which leaves a plausible-looking
    # suffix rather than an obviously broken value -- assert nothing is a tail of it.
    assert not any(n != expected and expected.endswith(n) for n in names), (
        f"a truncated variant of {expected!r} survived: {sorted(names)}"
    )


def test_ts_adapter_keeps_names_intact(tmp_path: Path) -> None:
    """scanner.py routes .ts/.tsx through this adapter, which had the same defect."""
    path = tmp_path / "reducer.ts"
    path.write_text(BOM + "export const sidebarToggledHandler = () => {};\n", encoding="utf-8")

    symbols, _edges, _imports, _exports, status = parse_ts_like(path, tmp_path, {"file_id": "f1"})

    assert not status.startswith("parse_error"), status
    names = {s["name"] for s in symbols}
    assert "sidebarToggledHandler" in names, sorted(names)


@pytest.mark.parametrize(
    "suffix,source,expected",
    [
        (".cs", "class S { void M(){ schemes.Where(x => x.Name != null).ToList(); } }\n", {"Where", "ToList"}),
        (".cs", "class S { void M(){ provider.GetService<IFoo>(); } }\n", {"GetService"}),
        (".ts", "function m(){ items.filter(x => x.ok).map(y => y.id); }\n", {"filter", "map"}),
        (".go", "func m(){ helper.Run() }\n", {"Run"}),
    ],
)
def test_call_names_are_identifiers_not_whole_expressions(tmp_path, suffix, source, expected) -> None:
    """A fluent chain must yield the method name, not the entire receiver expression.

    Using the call node's first child verbatim produced multi-line blobs that no
    caller could resolve -- and one such blob pulled enough file content into the
    record to trip the secret scanner, silently dropping the file from the index.
    """
    path = tmp_path / f"calls{suffix}"
    path.write_text(source, encoding="utf-8")

    names = {c["name"] for c in extract(path, source)["calls"]}

    assert expected <= names, f"missing {expected - names}; got {sorted(names)}"
    assert not any(any(ch.isspace() for ch in n) for n in names), f"expression leaked in: {sorted(names)}"


def test_every_parsed_language_is_also_scannable_text() -> None:
    """A language we parse but omit from TEXT_EXTS gets no secret scan and no hint.

    has_sensitive_content() returns False for extensions outside TEXT_EXTS, so adding
    C#/Go/Rust to the parser without adding them here left those files unscanned for
    secrets and with an empty content hint.
    """
    from mimry.constants import TEXT_EXTS
    from mimry.core.languages import EXTENSION_LANGUAGE

    missing = sorted(set(EXTENSION_LANGUAGE) - set(TEXT_EXTS))
    assert missing == [], f"parsed but not scannable as text: {missing}"
