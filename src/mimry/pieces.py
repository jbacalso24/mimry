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

import sqlite3
from pathlib import Path

from .intent import query_terms
from .scanner import body_terms

PIECES_FTS_CREATE = (
    "create virtual table if not exists pieces_fts using fts5(file_id unindexed, "
    "line_start unindexed, line_end unindexed, name, body)"
)
# Ranges listed per result: enough to point at the code, few enough to
# keep the context pack short.
RANGES_PER_FILE = 2
# The most lines above a symbol that a piece takes in as its comments,
# decorators or attributes.
ATTACHED_LINES = 15


def file_pieces(data: bytes, symbols: list[dict]) -> list[list]:
    """``[line_start, line_end, name, body terms]`` for each symbol
    spanning lines of this file.
    """
    lines = data.decode("utf-8", errors="ignore").splitlines()
    pieces = []
    for symbol in symbols:
        start, end = symbol.get("line_start"), symbol.get("line_end")
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end:
            continue
        end = min(end, len(lines))
        if start > end:
            continue
        # Doc comments, decorators and attributes sit directly above a
        # symbol; a piece starts at the top of that attached block.
        top = start
        while top > 1 and start - top < ATTACHED_LINES and lines[top - 2].strip():
            top -= 1
        start = top
        body = body_terms("\n".join(lines[start - 1 : end]).encode("utf-8"))
        pieces.append([start, end, str(symbol.get("name") or ""), body])
    return pieces


def piece_matches(idx: Path, q: str) -> dict[str, list[tuple[float, int, int, str]]]:
    """Matching pieces by file id, best first, as ``(rank, start, end,
    name)``; a lower rank is a better BM25 match.
    """
    terms = query_terms(q)
    match = " OR ".join(f'"{term}"*' for term in terms if term)
    if not match:
        return {}
    try:
        con = sqlite3.connect(Path(idx) / "mimry.sqlite")
        try:
            rows = con.execute(
                "select file_id, line_start, line_end, name, "
                "bm25(pieces_fts, 0.0, 0.0, 0.0, 1.4, 1.0) as rank "
                "from pieces_fts where pieces_fts match ? "
                "order by rank, file_id, line_start limit 400",
                (match,),
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


def best_ranges(pieces: list[tuple[float, int, int, str]]) -> list[dict]:
    """The best non-overlapping pieces of one file, in file order."""
    chosen: list[tuple[float, int, int, str]] = []
    for piece in pieces:
        _rank, start, end, _name = piece
        if any(start <= c_end and c_start <= end for _r, c_start, c_end, _n in chosen):
            continue
        chosen.append(piece)
        if len(chosen) == RANGES_PER_FILE:
            break
    return [
        {"start": start, "end": end, "name": name}
        for _rank, start, end, name in sorted(chosen, key=lambda p: p[1])
    ]
