from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
PAGE = ROOT / "site" / "changelog" / "index.html"
SCRIPT = ROOT / "scripts" / "build_changelog.py"
REGENERATE = "uv run python scripts/build_changelog.py"

SPEC = importlib.util.spec_from_file_location("build_changelog", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
# dataclasses look the module up in sys.modules while it is being built.
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
render = MODULE.render

RELEASE_HEADING = re.compile(r"^## ([0-9][0-9A-Za-z.+-]*) - ", re.MULTILINE)
ARTICLE_ID = re.compile(r'<article class="release" id="([^"]+)">')
LATEST = '<span class="tag">Latest</span>'


def _versions(markdown: str) -> list[str]:
    return RELEASE_HEADING.findall(markdown)


def _page(body: str) -> str:
    return f"# Changelog\n\n{body}"


def test_committed_page_matches_the_generator():
    expected = render(CHANGELOG.read_text(encoding="utf-8"))
    actual = PAGE.read_bytes().decode("utf-8") if PAGE.is_file() else ""
    assert actual == expected, f"site/changelog/index.html is out of date; run {REGENERATE}"


def test_each_release_has_an_article_in_order_and_only_the_first_is_latest():
    markdown = CHANGELOG.read_text(encoding="utf-8")
    page = render(markdown)
    versions = _versions(markdown)
    assert versions, "CHANGELOG.md has no releases"
    assert ARTICLE_ID.findall(page) == [f"v{version}" for version in versions]
    assert page.count(LATEST) == 1
    first = page.index(f'id="v{versions[0]}"')
    second = page.index(f'id="v{versions[1]}"')
    assert first < page.index(LATEST) < second


def test_continuation_lines_join_into_one_item():
    page = render(
        _page("## 1.0.0 - 2026-01-02\n\n### Notes\n\n- first part\n  second part\n  third part\n")
    )
    assert "<li>first part second part third part</li>" in page
    assert "<li>second part" not in page
    assert "<li>third part" not in page


def test_inline_code_links_and_escaping():
    page = render(
        _page(
            "## 1.0.0 - 2026-01-02\n\n### Notes\n\n"
            "- Run `mimry <name>` ([#7](https://github.com/jbacalso24/mimry/pull/7)) & more.\n"
        )
    )
    assert "<code>mimry &lt;name&gt;</code>" in page
    assert '<a href="https://github.com/jbacalso24/mimry/pull/7">#7</a>' in page
    assert "&amp; more." in page
    assert "<name>" not in page


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("| a | b |", id="table"),
        pytest.param("1. step", id="numbered-list"),
        pytest.param("```", id="fenced-block"),
        pytest.param("#### Too deep", id="four-level-heading"),
    ],
)
def test_unsupported_markdown_raises_with_the_line_number(line):
    # The bad line is line 7: title 1, release 3, section 5.
    markdown = f"# Changelog\n\n## 1.0.0 - 2026-01-02\n\n### Notes\n\n{line}\n"
    with pytest.raises(ValueError, match="line 7:"):
        render(markdown)
