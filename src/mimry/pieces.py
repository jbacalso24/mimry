"""Say which lines of a result to read: its best-matching pieces.

An agent told only a file's path reads all of it. Pieces are the
symbols the index already has (functions, classes, Markdown sections)
with their start and end lines, searched with BM25 like whole files, so
each result can name the few lines that match the task.

Pieces do not change how files rank: on the dev token benchmark,
boosting a file by its best piece located no more tasks and read
slightly more.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .core.documents import DOCUMENT_UNITS, DocumentPart
from .intent import query_terms
from .scanner import body_terms
from .security import redact_sensitive_text

PIECES_FTS_CREATE = (
    "create virtual table if not exists pieces_fts using fts5(file_id unindexed, "
    "line_start unindexed, line_end unindexed, name, body)"
)
# Ranges listed per result: enough to point at the code, few enough to
# keep the context pack short.
RANGES_PER_FILE = 2
# A document part joins or follows the best match only when its BM25
# rank is at least this share of the best one.
MATCH_SHARE = 0.5
# Longest part title kept as a piece name; a first line can be long.
TITLE_CHARS = 80
# The most lines above a symbol that a piece takes in as its comments,
# decorators or attributes.
ATTACHED_LINES = 15
# A line above a symbol belongs to it only when it is a comment,
# decorator or attribute. Any other line is code, such as the
# signature of the function above, and must not join the piece.
ATTACHED_LINE = re.compile(r"(?:#|//|/\*|\*(?:\s|/|$)|@|--|\[[A-Z])")


def file_pieces(data: bytes, symbols: list[dict]) -> list[list]:
    """``[line_start, line_end, name, body terms]`` for each symbol
    spanning lines of this file.
    """
    lines = data.decode("utf-8", errors="ignore").splitlines()
    pieces = []
    # Symbols that share their own line range (a minified bundle can
    # put thousands on a single line) share one piece. Its body is the
    # tokens of those lines, tokenized once. Doing it per symbol is
    # quadratic on a long line and stores the same body each time. The
    # key is the symbol's range before attached lines are taken in, so a
    # class and its first method (which starts on the next line) keep
    # separate pieces.
    seen_ranges: set[tuple[int, int]] = set()
    for symbol in symbols:
        start, end = symbol.get("line_start"), symbol.get("line_end")
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end:
            continue
        end = min(end, len(lines))
        if start > end:
            continue
        if (start, end) in seen_ranges:
            continue
        seen_ranges.add((start, end))
        # Doc comments, decorators and attributes sit directly above a
        # symbol; a piece starts at the top of that attached block.
        top = start
        while (
            top > 1
            and start - top < ATTACHED_LINES
            and ATTACHED_LINE.match(lines[top - 2].strip())
        ):
            top -= 1
        start = top
        body = body_terms("\n".join(lines[start - 1 : end]).encode("utf-8"))
        pieces.append([start, end, str(symbol.get("name") or ""), body])
    return pieces


def piece_matches(
    idx: Path, q: str, file_ids: Iterable[str]
) -> dict[str, list[tuple[float, int, int, str]]]:
    """Matching pieces of the given files by file id, best first, as
    ``(rank, start, end, name)``; a lower rank is a better BM25 match.

    Only the result files are searched, and none of their pieces is
    cut. A cap over the whole repository dropped a result's own
    pieces whenever many other files matched the same terms.
    """
    terms = query_terms(q)
    match = " OR ".join(f'"{term}"*' for term in terms if term)
    ids = sorted(set(file_ids))
    if not match or not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    try:
        con = sqlite3.connect(Path(idx) / "mimry.sqlite")
        try:
            rows = con.execute(
                "select file_id, line_start, line_end, name, "
                "bm25(pieces_fts, 0.0, 0.0, 0.0, 1.4, 1.0) as rank "
                f"from pieces_fts where pieces_fts match ? and file_id in ({marks}) "
                "order by rank, file_id, line_start",
                (match, *ids),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        # An index built before pieces existed has no table; search
        # falls back to whole files until the next reindex.
        return {}
    by_file: dict[str, list[tuple[float, int, int, str]]] = {}
    for file_id, start, end, name, rank in rows:
        by_file.setdefault(file_id, []).append((rank, int(start), int(end), name))
    return by_file


def document_pieces(parts: list[DocumentPart]) -> list[list]:
    """``[number, number, title, body terms]`` for each part with text.

    A document's part number stands in for its line numbers, so the same
    pieces table and ranges serve documents and code.
    """
    pieces = []
    for part in parts:
        if not part.text.strip() or part.kind not in DOCUMENT_UNITS.values():
            continue
        title = redact_sensitive_text(part.title)[:TITLE_CHARS]
        pieces.append([part.number, part.number, title, body_terms(part.text.encode("utf-8"))])
    return pieces


def document_body(parts: list[DocumentPart]) -> str:
    """Searchable terms of a whole document: its text and section,
    sheet and slide titles. Page titles are left out, since every page
    would match the word "page".
    """
    titles = [part.title for part in parts if part.kind != "page" and part.title]
    texts = [part.text for part in parts if part.text]
    return body_terms("\n".join(titles + texts).encode("utf-8"))


def best_ranges(pieces: list[tuple[float, int, int, str]], unit: str | None = None) -> list[dict]:
    """The best non-overlapping pieces of one file, in file order.

    For a document, ``unit`` names what its parts are and each range
    carries it. Neighbouring pages that match about as well as the best
    one join it, so a run reads as one range such as pages 12-14, and a
    part far weaker than the best is not named at all.
    """
    if unit == "page":
        pieces = _join_adjacent(pieces)
    chosen: list[tuple[float, int, int, str]] = []
    for piece in pieces:
        rank, start, end, _name = piece
        if unit and chosen and not _close(rank, chosen[0][0]):
            break
        if any(start <= c_end and c_start <= end for _r, c_start, c_end, _n in chosen):
            continue
        chosen.append(piece)
        if len(chosen) == RANGES_PER_FILE:
            break
    ranges = [
        {"start": start, "end": end, "name": name}
        for _rank, start, end, name in sorted(chosen, key=lambda p: p[1])
    ]
    if unit:
        for item in ranges:
            item["unit"] = unit
    return ranges


def _close(rank: float, best: float) -> bool:
    """Whether a BM25 rank (lower is better) is at least half as good as
    the best one."""
    return rank <= best * MATCH_SHARE


def _join_adjacent(pieces: list[tuple[float, int, int, str]]) -> list[tuple[float, int, int, str]]:
    """Grow each page, best first, into the neighbouring pages that
    match about as well as it does. A run of weak matches never swallows
    a strong one. Returned best first.
    """
    by_page = {start: rank for rank, start, _end, _name in pieces}
    used: set[int] = set()
    runs: list[tuple[float, int, int, str]] = []
    for rank, start, end, name in sorted(pieces, key=lambda p: (p[0], p[1])):
        if start in used:
            continue
        first, last = start, end
        while first - 1 in by_page and first - 1 not in used and _close(by_page[first - 1], rank):
            first -= 1
        while last + 1 in by_page and last + 1 not in used and _close(by_page[last + 1], rank):
            last += 1
        used.update(range(first, last + 1))
        runs.append((rank, first, last, name))
    return runs
