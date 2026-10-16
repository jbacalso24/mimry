#!/usr/bin/env python3
"""Write the site's changelog page from CHANGELOG.md.

Run it after a release is added to CHANGELOG.md, so the site shows it:

    uv run python scripts/build_changelog.py

The page is plain HTML written with the standard library. The reader
accepts only the Markdown that CHANGELOG.md uses and raises ValueError,
naming the line, for anything else, so a new construct cannot render
wrong without anyone noticing.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "CHANGELOG.md"
OUTPUT = ROOT / "site" / "changelog" / "index.html"
URL = "https://jbacalso24.github.io/mimry/changelog/"
DESCRIPTION = "Every MIMRY release with its notes, generated from the project changelog."
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
RELEASE = re.compile(r"## ([0-9][0-9A-Za-z.+-]*) - (\d{4}-\d{2}-\d{2})")
# Block starts this reader does not support: tables, quotes, headings
# below ###, bullets other than "- ", fences, rules and ordered lists.
UNSUPPORTED = re.compile(r"(?:[|>#]|[-*+](?:\s|$)|-{3}|`{3}|~{3}|\d+[.)](?:\s|$))")
INLINE = re.compile(r"`(?P<code>[^`]+)`|\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)\s]+)\)")

FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' heig"
    "ht='64' viewBox='0 0 64 64' fill='none' role='img' aria-label='MIMRY'%3E%3Cr"
    "ect width='64' height='64' rx='15' fill='%2314223A'/%3E%3Cpath d='M18 45V19L"
    "32 33L46 19V45' stroke='%23E3E9F1' stroke-width='8' stroke-linecap='round' s"
    "troke-linejoin='round'/%3E%3Ccircle cx='32' cy='33' r='8' fill='%23F4D35E'/%"
    "3E%3C/svg%3E"
)

BRAND_MARK = (
    '<svg viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false"><r'
    'ect width="64" height="64" rx="15" fill="#14223A"/><path d="M18 45V19L32 33L'
    '46 19V45" stroke="#E3E9F1" stroke-width="5.5" stroke-linecap="round" stroke-'
    'linejoin="round"/><g fill="#E3E9F1"><circle cx="18" cy="45" r="5"/><circle c'
    'x="18" cy="19" r="5"/><circle cx="46" cy="19" r="5"/><circle cx="46" cy="45"'
    ' r="5"/></g><circle cx="32" cy="33" r="6.5" fill="#F4D35E"/></svg>'
)
FONTS = (
    "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600"
    "&family=Schibsted+Grotesk:wght@400;500;600;700;800&display=swap"
)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MIMRY changelog</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MIMRY">
<meta property="og:title" content="MIMRY changelog">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/social-preview.png">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="640">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://raw.githubusercontent.com/jbacalso24/mimry/main/assets/social-preview.png">
<link rel="icon" href="{favicon}">
<link rel="stylesheet" href="../guide.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{fonts}" rel="stylesheet">
<script type="application/ld+json">
{json_ld}
</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="wrap top"><a class="brand" href="../">{brand_mark}MIMRY</a>
<nav aria-label="Main">
<a href="../claude-code/">Claude Code</a>
<a href="../codex/">Codex</a>
<a href="../context-retrieval/">Context retrieval</a>
<a href="../changelog/" aria-current="page">Changelog</a>
<a href="https://github.com/jbacalso24/mimry">GitHub</a>
</nav></header>
<main class="wrap" id="main">
<p class="breadcrumb"><a href="../">MIMRY</a> / Changelog</p>
<h1>Changelog</h1>
{lede}
<nav class="jump" aria-label="Jump to a release"><span>Jump to</span>{jump}</nav>
{articles}
</main>
<footer><div class="wrap"><span>MIMRY is local, open-source software.</span>
<nav aria-label="Project">
<a href="../">Home</a>
<a href="https://github.com/jbacalso24/mimry/blob/main/README.md">Full documentation</a>
<a href="../llms.txt">llms.txt</a>
</nav></div></footer>
</body>
</html>
"""


@dataclass
class Section:
    title: str
    # ("p", lines) is a paragraph. ("ul", items) is a list whose items
    # are lists of lines: one bullet and its indented continuations.
    blocks: list[tuple[str, list]] = field(default_factory=list)


@dataclass
class Release:
    version: str
    day: date
    sections: list[Section] = field(default_factory=list)


def _fail(number: int, reason: str) -> ValueError:
    return ValueError(f"CHANGELOG.md line {number}: {reason}")


def _parse(markdown: str) -> tuple[list[str], list[Release]]:
    intro: list[str] = []
    releases: list[Release] = []
    section: Section | None = None
    # "p" or "li" while a paragraph or bullet is open, else None.
    open_block: str | None = None
    for number, raw in enumerate(markdown.split("\n"), start=1):
        line = raw.rstrip()
        match = RELEASE.fullmatch(line)
        if not line:
            open_block = None
        elif line == "# Changelog" and not releases and not intro:
            continue  # the page has its own h1
        elif line.startswith("# "):
            raise _fail(number, "the only title is '# Changelog'")
        elif match:
            try:
                day = date.fromisoformat(match.group(2))
            except ValueError:
                raise _fail(number, f"no such date {match.group(2)}") from None
            releases.append(Release(match.group(1), day))
            section = None
            open_block = None
        elif line.startswith("## "):
            raise _fail(number, "a release heading must read '## <version> - <YYYY-MM-DD>'")
        elif line.startswith("### "):
            if not releases:
                raise _fail(number, "a section must come after a release heading")
            section = Section(line[4:].strip())
            releases[-1].sections.append(section)
            open_block = None
        elif line.startswith("#"):
            raise _fail(number, "only the title, release and section headings are supported")
        elif line.startswith("- "):
            if section is None:
                raise _fail(number, "a bullet must be inside a ### section")
            if not section.blocks or section.blocks[-1][0] != "ul":
                section.blocks.append(("ul", []))
            section.blocks[-1][1].append([line[2:]])
            open_block = "li"
        elif line.startswith("  "):
            text = line[2:]
            if open_block != "li" or text[:1].isspace() or UNSUPPORTED.match(text):
                raise _fail(number, "an indented line must continue the bullet above it")
            section.blocks[-1][1][-1].append(text)
        elif line.startswith(" ") or UNSUPPORTED.match(line):
            raise _fail(number, f"unsupported Markdown: {line.strip()!r}")
        elif open_block == "li":
            raise _fail(number, "a bullet's continuation lines must be indented two spaces")
        elif section is None:
            if releases:
                raise _fail(number, "text must be inside a ### section")
            if open_block is None and intro:
                raise _fail(number, "only one intro paragraph is supported")
            intro.append(line)
            open_block = "p"
        else:
            if open_block == "p":
                section.blocks[-1][1].append(line)
            else:
                section.blocks.append(("p", [line]))
            open_block = "p"
    if not releases:
        raise ValueError("CHANGELOG.md has no release sections")
    return intro, releases


def _inline(text: str) -> str:
    out: list[str] = []
    end = 0
    for match in INLINE.finditer(text):
        out.append(html.escape(text[end : match.start()], quote=False))
        if match.group("code") is not None:
            out.append(f"<code>{html.escape(match.group('code'), quote=False)}</code>")
        else:
            href = html.escape(match.group("url"))
            label = html.escape(match.group("label"), quote=False)
            out.append(f'<a href="{href}">{label}</a>')
        end = match.end()
    out.append(html.escape(text[end:], quote=False))
    return "".join(out)


def _blocks(section: Section) -> str:
    out: list[str] = []
    for kind, content in section.blocks:
        if kind == "p":
            out.append(f"<p>{_inline(' '.join(content))}</p>")
        else:
            items = "".join(f"<li>{_inline(' '.join(lines))}</li>" for lines in content)
            out.append(f"<ul>{items}</ul>")
    return "\n".join(out)


def _article(release: Release, latest: bool) -> str:
    day = release.day
    version = release.version  # the pattern allows only URL-safe characters
    tag = ' <span class="tag">Latest</span>' if latest else ""
    sections = "\n".join(f"<h3>{_inline(s.title)}</h3>\n{_blocks(s)}" for s in release.sections)
    return (
        f'<article class="release" id="v{version}">\n'
        '<header class="release-meta">\n'
        f"<h2>{version}</h2>\n"
        f'<p class="release-date"><time datetime="{day.isoformat()}">'
        f"{day.day} {MONTHS[day.month - 1]} {day.year}</time>{tag}</p>\n"
        '<ul class="release-links">\n'
        f'<li><a href="https://github.com/jbacalso24/mimry/releases/tag/v{version}">'
        "Release notes on GitHub</a></li>\n"
        f'<li><a href="https://pypi.org/project/mimry/{version}/">PyPI</a></li>\n'
        "</ul>\n"
        "</header>\n"
        f'<div class="release-notes">\n{sections}\n</div>\n'
        "</article>"
    )


def render(markdown: str) -> str:
    intro, releases = _parse(markdown)
    lede = f'<p class="lede">{_inline(" ".join(intro))}</p>' if intro else ""
    jump = "".join(f'<a href="#v{r.version}">{r.version}</a>' for r in releases)
    articles = "\n".join(_article(r, latest=i == 0) for i, r in enumerate(releases))
    json_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "MIMRY changelog",
            "url": URL,
            "description": DESCRIPTION,
        },
        separators=(",", ":"),
    )
    return PAGE.format(
        description=DESCRIPTION,
        url=URL,
        favicon=FAVICON,
        brand_mark=BRAND_MARK,
        fonts=FONTS,
        json_ld=json_ld,
        lede=lede,
        jump=jump,
        articles=articles,
    )


def main() -> None:
    markdown = SOURCE.read_text(encoding="utf-8")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not text mode, so Windows never writes CRLF.
    OUTPUT.write_bytes(render(markdown).encode("utf-8"))
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
