#!/usr/bin/env python3
"""Write the contributors grid and image into the assembled site.

The GitHub Pages deploy runs this after it assembles _site/, so the
list is fresh on every deploy (and weekly) without committing anything.
It writes:

- the homepage grid, between the contributors markers in
  _site/index.html;
- _site/contributors.svg, one image with every avatar embedded as a
  data URI, for the README (GitHub's image proxy blocks avatars loaded
  from inside an SVG).

Any API or network error exits non-zero and writes nothing, so the
deploy fails and the previous site stays live rather than publishing a
partial list.

    python scripts/build_contributors.py _site

Standard library only. GITHUB_TOKEN is optional and only raises the API
rate limit.
"""

from __future__ import annotations

import base64
import html
import json
import os
import sys
import urllib.request
from http.client import HTTPMessage
from pathlib import Path

API_URL = "https://api.github.com/repos/jbacalso24/mimry/contributors?per_page=100"
USER_AGENT = "mimry-build-contributors"
START_MARKER = "<!-- contributors:start -->"
END_MARKER = "<!-- contributors:end -->"
COLUMNS = 10
CELL_WIDTH = 120
AVATAR = 64
PAD = 8
LABEL_GAP = 6
LABEL_ASCENT = 12
LABEL_DESCENT = 3
CELL_HEIGHT = PAD + AVATAR + LABEL_GAP + LABEL_ASCENT + LABEL_DESCENT + PAD
LABEL_MAX = 19
SVG_NS = "http://www.w3.org/2000/svg"


def contributors(api_items: list[dict]) -> list[dict]:
    """Keep human contributors, in API order (most contributions first).

    Bots are dropped by type and by a "[bot]" login suffix.
    """
    people = []
    for item in api_items:
        if item.get("type") != "User" or item["login"].endswith("[bot]"):
            continue
        people.append(
            {
                "login": item["login"],
                "avatar_url": item["avatar_url"],
                "html_url": item["html_url"],
            }
        )
    return people


def _sized(url: str, size: int) -> str:
    return f"{url}&s={size}" if "?" in url else f"{url}?s={size}"


def render_html(people: list[dict]) -> str:
    items = []
    for person in people:
        login = html.escape(person["login"])
        href = html.escape(person["html_url"])
        src = html.escape(_sized(person["avatar_url"], 96))
        items.append(
            f'<li><a href="{href}"><img src="{src}" width="48" height="48" '
            f'loading="lazy" alt="{login}"><span>{login}</span></a></li>'
        )
    return "\n".join(['<ul class="contributors">', *items, "</ul>"])


def _label(login: str) -> str:
    """Shorten a long login for the SVG text.

    The <title> of the person's group keeps the full login.
    """
    return login if len(login) <= LABEL_MAX else login[: LABEL_MAX - 1] + "\u2026"


def render_svg(people: list[dict], avatars: dict[str, tuple[str, bytes]]) -> str:
    rows = (len(people) + COLUMNS - 1) // COLUMNS
    columns = min(COLUMNS, len(people))
    width, height = columns * CELL_WIDTH, rows * CELL_HEIGHT
    defs, items = [], []
    for index, person in enumerate(people):
        login = person["login"]
        mime, data = avatars[login]
        row, column = divmod(index, COLUMNS)
        left = column * CELL_WIDTH + (CELL_WIDTH - AVATAR) // 2
        top = row * CELL_HEIGHT + PAD
        center = column * CELL_WIDTH + CELL_WIDTH // 2
        clip_id = f"avatar-{index}"
        encoded = base64.b64encode(data).decode("ascii")
        defs.append(
            f'<clipPath id="{clip_id}"><circle cx="{left + AVATAR // 2}" '
            f'cy="{top + AVATAR // 2}" r="{AVATAR // 2}"/></clipPath>'
        )
        baseline = top + AVATAR + LABEL_GAP + LABEL_ASCENT
        items.append(
            f"<g><title>{html.escape(login)}</title>"
            f'<image href="data:{mime};base64,{encoded}" x="{left}" y="{top}" '
            f'width="{AVATAR}" height="{AVATAR}" clip-path="url(#{clip_id})"/>'
            f'<text x="{center}" y="{baseline}" text-anchor="middle" '
            'font-family="system-ui, -apple-system, Segoe UI, sans-serif" '
            f'font-size="12" fill="#8b949e">{html.escape(_label(login))}</text></g>'
        )
    return "\n".join(
        [
            f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img">',
            "<title>MIMRY contributors</title>",
            f"<defs>{''.join(defs)}</defs>",
            *items,
            "</svg>",
        ]
    )


def replace_between_markers(page: str, html: str) -> str:
    for marker in (START_MARKER, END_MARKER):
        count = page.count(marker)
        if count != 1:
            raise ValueError(f"expected one {marker} in the page, found {count}")
    start = page.index(START_MARKER) + len(START_MARKER)
    end = page.index(END_MARKER)
    if end < start:
        raise ValueError("the contributors end marker comes before the start marker")
    line_start = page.rfind("\n", 0, end) + 1
    indent = page[line_start:end]
    if indent.strip() or line_start < start:
        indent = ""
    return page[:start] + "\n" + html + "\n" + indent + page[end:]


def _get(url: str, accept: str | None, token: bool) -> tuple[bytes, HTTPMessage]:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if token and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=30
    ) as response:
        return response.read(), response.headers


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        url_part, _, params = part.partition(";")
        if 'rel="next"' in params:
            return url_part.strip().strip("<>")
    return None


def fetch_contributors() -> list[dict]:
    api_items: list[dict] = []
    url: str | None = API_URL
    while url:
        body, headers = _get(url, "application/vnd.github+json", token=True)
        page = json.loads(body)
        if not isinstance(page, list):
            raise ValueError(f"unexpected response from {url}")
        api_items.extend(page)
        url = _next_link(headers.get("Link", ""))
    return contributors(api_items)


def fetch_avatar(url: str) -> tuple[str, bytes]:
    body, headers = _get(url, None, token=False)
    mime = headers.get_content_type()
    if mime not in ("image/png", "image/jpeg"):
        raise ValueError(f"avatar {url} is {mime}, expected PNG or JPEG")
    return mime, body


def main(site_dir: Path) -> int:
    index = site_dir / "index.html"
    svg_path = site_dir / "contributors.svg"
    try:
        people = fetch_contributors()
        if not people:
            raise ValueError("GitHub returned no contributors")
        avatars = {p["login"]: fetch_avatar(_sized(p["avatar_url"], AVATAR)) for p in people}
        page = replace_between_markers(index.read_text(encoding="utf-8"), render_html(people))
        svg = render_svg(people, avatars)
    except (OSError, ValueError, KeyError) as error:
        print(f"build_contributors: {error}", file=sys.stderr)
        return 1
    index.write_text(page, encoding="utf-8", newline="\n")
    svg_path.write_text(svg, encoding="utf-8", newline="\n")
    print(f"Wrote {len(people)} contributors to {index} and {svg_path} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/build_contributors.py <site_dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
