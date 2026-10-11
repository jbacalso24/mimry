from __future__ import annotations

import json
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BASE = "https://jbacalso24.github.io/mimry/"
PAGES = {
    "": SITE / "index.html",
    "claude-code/": SITE / "claude-code" / "index.html",
    "codex/": SITE / "codex" / "index.html",
    "context-retrieval/": SITE / "context-retrieval" / "index.html",
}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.meta: list[dict[str, str]] = []
        self.links: list[str] = []
        self.h1_count = 0
        self._json_ld = False
        self._json_chunks: list[str] = []
        self.json_ld: list[object] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            self.meta.append(values)
        elif tag == "link":
            if values.get("rel") == "canonical":
                self.meta.append({"canonical": values.get("href", "")})
            if values.get("href"):
                self.links.append(values["href"])
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._json_ld = True
            self._json_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._json_ld:
            self.json_ld.append(json.loads("".join(self._json_chunks)))
            self._json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._json_ld:
            self._json_chunks.append(data)


def parse(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def meta_value(parser: PageParser, key: str, value: str | None = None) -> str:
    for item in parser.meta:
        if value is None and key in item:
            return item[key]
        if value is not None and item.get(key) == value:
            return item.get("content", "")
    return ""


def local_target(page_key: str, href: str) -> Path | None:
    parsed = urlparse(href)
    if parsed.scheme or href.startswith("//") or href.startswith("#"):
        return None
    rel = posixpath.normpath(posixpath.join(page_key, parsed.path))
    if parsed.path.endswith("/") or rel == ".":
        rel = posixpath.join(rel, "index.html")
    target = SITE / rel
    if target == SITE / "llms.txt":
        target = ROOT / "llms.txt"  # copied to the deployment root by pages.yml
    return target


def test_static_pages_have_unique_search_metadata_and_valid_schema() -> None:
    titles: set[str] = set()
    descriptions: set[str] = set()
    canonicals: set[str] = set()
    for key, path in PAGES.items():
        parser = parse(path)
        description = meta_value(parser, "name", "description")
        canonical = meta_value(parser, "canonical")
        assert parser.title and parser.title not in titles
        assert description and description not in descriptions
        assert canonical == BASE + key and canonical not in canonicals
        assert meta_value(parser, "property", "og:title")
        assert meta_value(parser, "property", "og:description")
        assert meta_value(parser, "property", "og:url") == canonical
        assert parser.h1_count == 1
        assert parser.json_ld
        for value in parser.json_ld:
            assert isinstance(value, dict)
            assert value.get("@context") == "https://schema.org"
            assert value.get("@type")
        titles.add(parser.title)
        descriptions.add(description)
        canonicals.add(canonical)


def test_all_internal_links_resolve_in_pages_artifact() -> None:
    failures: list[str] = []
    for key, path in PAGES.items():
        for href in parse(path).links:
            target = local_target(key, href)
            if target is not None and not target.exists():
                failures.append(f"{path.relative_to(ROOT)} -> {href} ({target.relative_to(ROOT)})")
    assert not failures, "Broken internal links:\n" + "\n".join(failures)


def test_sitemap_lists_each_canonical_and_llms_with_current_lastmod() -> None:
    tree = ElementTree.parse(SITE / "sitemap.xml")
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries = {
        item.findtext("sm:loc", namespaces=namespace): item.findtext(
            "sm:lastmod", namespaces=namespace
        )
        for item in tree.findall("sm:url", namespace)
    }
    expected = {BASE + key for key in PAGES} | {BASE + "llms.txt"}
    assert set(entries) == expected
    assert set(entries.values()) == {"2026-10-10"}


def test_robots_is_truthful_about_project_scope() -> None:
    robots = (SITE / "robots.txt").read_text(encoding="utf-8")
    assert "Allow: /" in robots
    assert f"Sitemap: {BASE}sitemap.xml" in robots
    assert "cannot publish /mimry/robots.txt only" not in robots
    assert "project can publish /mimry/robots.txt only" in robots
    assert "cannot\n# create GitHub Pages' host-root /robots.txt" in robots
