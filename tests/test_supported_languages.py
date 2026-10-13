"""The supported-languages list has one source of truth.

``supported_languages()`` in ``core/languages.py`` drives the README
"Supported languages" table. The language count stated in the README,
the website and docs/how-it-works.md must match it. When a language
is added, add its label to ``LANGUAGE_LABELS`` and update the table
and the counts.
"""

from __future__ import annotations

import re
from pathlib import Path

from mimry.core.languages import EXTENSION_LANGUAGE, LANGUAGE_LABELS, supported_languages

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SITE = ROOT / "site" / "index.html"
HOW_IT_WORKS = ROOT / "docs" / "how-it-works.md"
COUNTED_FILES = [README, SITE, HOW_IT_WORKS]


def _readme_table_rows() -> list[tuple[str, list[str]]]:
    text = README.read_text(encoding="utf-8")
    section = re.search(
        r"^## Supported languages\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE
    )
    assert section, "README.md has no '## Supported languages' section; add it after '## Features'"
    rows = []
    for line in section.group(1).splitlines():
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] == "Language":
            continue
        rows.append((cells[0], re.findall(r"`([^`]+)`", cells[1])))
    return rows


def _names(text: str) -> list[str]:
    """Names in a 'A, B, C and D' list."""
    return re.split(r", | and ", text.strip())


def _site_names() -> list[str]:
    match = re.search(r'<p class="langs">([^<]*)</p>', SITE.read_text(encoding="utf-8"))
    assert match, 'site/index.html has no <p class="langs"> language list to update'
    return _names(match.group(1))


def _how_it_works_names() -> list[str]:
    text = HOW_IT_WORKS.read_text(encoding="utf-8")
    match = re.search(r"^\d+ languages parsed: (.+)\.$", text, re.MULTILINE)
    assert match, "docs/how-it-works.md has no '<count> languages parsed: ...' line to update"
    return _names(match.group(1))


def test_every_language_has_a_label():
    missing = sorted(set(EXTENSION_LANGUAGE.values()) - set(LANGUAGE_LABELS))
    assert not missing, (
        f"add a LANGUAGE_LABELS entry in src/mimry/core/languages.py for: {missing}"
    )


def test_readme_table_matches_supported_languages():
    assert _readme_table_rows() == supported_languages(), (
        "the README '## Supported languages' table differs from supported_languages(); "
        "update its rows to match src/mimry/core/languages.py"
    )


def test_site_and_how_it_works_list_labels_in_order():
    labels = [label for label, _ in supported_languages()]
    for name, found in [
        ("site/index.html", _site_names()),
        ("docs/how-it-works.md", _how_it_works_names()),
    ]:
        assert found == labels, (
            f"{name} lists languages as {found}; it must list the labels from "
            f"supported_languages() in order: {labels}"
        )


def test_language_counts_match_supported_languages():
    count = len(supported_languages())
    for path in COUNTED_FILES:
        rel = path.relative_to(ROOT)
        match = re.search(r"(\d+) languages", path.read_text(encoding="utf-8"))
        assert match, f"{rel} states no language count; write '{count} languages' next to the list"
        assert int(match.group(1)) == count, (
            f"{rel} says {match.group(1)} languages but supported_languages() has {count}; "
            "update that count"
        )
