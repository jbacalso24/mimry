from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_contributors.py"
PAGE = ROOT / "site" / "index.html"
SVG_NS = "{http://www.w3.org/2000/svg}"

SPEC = importlib.util.spec_from_file_location("build_contributors", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

# The three people committed as the homepage fallback, as the
# API returned them.
COMMITTED = [
    {
        "login": "jbacalso24",
        "avatar_url": "https://avatars.githubusercontent.com/u/38557979?v=4",
        "html_url": "https://github.com/jbacalso24",
    },
    {
        "login": "abhinavrastogi2627",
        "avatar_url": "https://avatars.githubusercontent.com/u/49560440?v=4",
        "html_url": "https://github.com/abhinavrastogi2627",
    },
    {
        "login": "KeuqS",
        "avatar_url": "https://avatars.githubusercontent.com/u/50778230?v=4",
        "html_url": "https://github.com/KeuqS",
    },
]


def _person(login: str) -> dict[str, str]:
    return {
        "login": login,
        "avatar_url": f"https://avatars.githubusercontent.com/u/{len(login)}?v=4",
        "html_url": f"https://github.com/{login}",
    }


def _api_item(login: str, kind: str = "User") -> dict[str, object]:
    return {
        "login": login,
        "type": kind,
        "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
        "html_url": f"https://github.com/{login}",
        "contributions": 1,
    }


def test_contributors_drops_bots_and_keeps_api_order() -> None:
    items = [
        _api_item("alice"),
        _api_item("dependabot[bot]", kind="Bot"),
        _api_item("github-actions[bot]", kind="User"),
        _api_item("bob"),
        _api_item("some-org", kind="Organization"),
    ]
    people = MODULE.contributors(items)
    assert [person["login"] for person in people] == ["alice", "bob"]
    assert set(people[0]) == {"login", "avatar_url", "html_url"}


def test_render_html_links_avatars_and_escapes_logins() -> None:
    people = [
        {
            "login": "a<b",
            "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
            "html_url": "https://github.com/a%3Cb",
        }
    ]
    markup = MODULE.render_html(people)
    assert '<a href="https://github.com/a%3Cb">' in markup
    assert 'src="https://avatars.githubusercontent.com/u/1?v=4&amp;s=96"' in markup
    assert 'width="48" height="48" loading="lazy" alt="a&lt;b"' in markup
    assert "<span>a&lt;b</span>" in markup
    assert "a<b" not in markup


def test_render_svg_embeds_one_image_per_person_and_parses_as_xml() -> None:
    people = [_person("alice"), _person("bo<b")]
    avatars = {"alice": ("image/png", b"alice-png"), "bo<b": ("image/png", b"bob-png")}
    root = ElementTree.fromstring(MODULE.render_svg(people, avatars))
    images = list(root.iter(f"{SVG_NS}image"))
    assert len(images) == 2
    assert all(image.get("href", "").startswith("data:image/png;base64,") for image in images)
    assert [text.text for text in root.iter(f"{SVG_NS}text")] == ["alice", "bo<b"]
    assert root.get("role") == "img"
    assert root.find(f"{SVG_NS}title").text == "MIMRY contributors"


def test_render_svg_grows_a_second_row_after_ten_people() -> None:
    def svg_height(count: int) -> int:
        people = [_person(f"user{index}") for index in range(count)]
        avatars = {person["login"]: ("image/jpeg", b"jpeg") for person in people}
        root = ElementTree.fromstring(MODULE.render_svg(people, avatars))
        assert len(list(root.iter(f"{SVG_NS}image"))) == count
        return int(root.get("viewBox").split()[3])

    one_row = svg_height(10)
    assert svg_height(11) == 2 * one_row


def test_render_svg_shortens_a_long_login_and_titles_the_full_one() -> None:
    long_login = "abcdefghijklmnopqrstuvwxyz0123456789ABC"
    fits = "abcdefghijklmnopqrs"  # 19 characters, shown in full
    assert len(long_login) == 39 and len(fits) == 19
    people = [_person(long_login), _person(fits)]
    avatars = {person["login"]: ("image/png", b"png") for person in people}
    root = ElementTree.fromstring(MODULE.render_svg(people, avatars))
    long_group, fits_group = list(root.iter(f"{SVG_NS}g"))
    assert long_group.find(f"{SVG_NS}title").text == long_login
    assert long_group.find(f"{SVG_NS}text").text == long_login[:18] + "\u2026"
    assert long_login not in [text.text for text in root.iter(f"{SVG_NS}text")]
    assert fits_group.find(f"{SVG_NS}text").text == fits
    assert fits_group.find(f"{SVG_NS}title").text == fits


def test_render_svg_has_equal_top_and_bottom_padding() -> None:
    people = [_person("alice")]
    root = ElementTree.fromstring(MODULE.render_svg(people, {"alice": ("image/png", b"p")}))
    image = next(root.iter(f"{SVG_NS}image"))
    text = next(root.iter(f"{SVG_NS}text"))
    height = int(root.get("viewBox").split()[3])
    top_gap = int(image.get("y"))
    # The label's descenders sit about 3px under its baseline.
    bottom_gap = height - (int(text.get("y")) + MODULE.LABEL_DESCENT)
    assert top_gap == bottom_gap == MODULE.PAD


def test_replace_between_markers_changes_only_the_region() -> None:
    page = "before\n  <!-- contributors:start -->\n  old\n  <!-- contributors:end -->\nafter"
    assert MODULE.replace_between_markers(page, "NEW") == (
        "before\n  <!-- contributors:start -->\nNEW\n  <!-- contributors:end -->\nafter"
    )


@pytest.mark.parametrize(
    "page",
    [
        "no markers at all",
        "<!-- contributors:start --> only a start",
        "only an end <!-- contributors:end -->",
        "<!-- contributors:end --> <!-- contributors:start -->",
    ],
)
def test_replace_between_markers_rejects_missing_or_misordered_markers(page: str) -> None:
    with pytest.raises(ValueError):
        MODULE.replace_between_markers(page, "x")


def test_committed_homepage_fallback_is_the_rendered_three() -> None:
    page = PAGE.read_text(encoding="utf-8")
    start, end = MODULE.START_MARKER, MODULE.END_MARKER
    assert page.count(start) == 1 and page.count(end) == 1
    assert page.index(start) < page.index(end)
    # Re-rendering the committed three leaves the page byte-identical.
    assert MODULE.replace_between_markers(page, MODULE.render_html(COMMITTED)) == page
