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

import math
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .core.documents import DOCUMENT_UNITS, DocumentPart
from .intent import is_doc_or_plan, is_test_file, query_terms
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
# Matching pieces read for the repository-wide file ranking.
PIECE_LIMIT = 200
# A file's later pieces add to its score, each half the weight of the
# one before, over this many of them.
PIECE_TAIL = 8
# The keyword ranking keeps this many of the query's terms, and weighs a
# term that the query writes as code this much more than a plain word.
KW_TERMS = 48
IDENTIFIER_WEIGHT = 2.0
# Reciprocal rank fusion: a file scores weight / (KW_RRF_K + rank) in
# each list it is in, and a whole-file match counts double.
KW_RRF_K = 20
WHOLE_FILE_LIMIT = 100
# A code name that more symbols than this share names no file.
SYMBOL_NAME_SHARE = 4
# Path factors: tests, docs and examples rank down, __init__.py less.
TEST_FACTOR = 0.3
DOC_FACTOR = 0.3
INIT_FACTOR = 0.5
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BACKTICK_RE = re.compile(r"`([^`]*)`")


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


def code_identifiers(q: str) -> list[str]:
    """Names the query writes as code, as written: those in backticks,
    and words with an inner underscore or a lowercase letter then an
    uppercase one.
    """
    names = []
    for span in _BACKTICK_RE.findall(q):
        names.extend(_IDENTIFIER_RE.findall(span))
    for word in _IDENTIFIER_RE.findall(q):
        if "_" in word.strip("_") or re.search(r"[a-z][A-Z]", word):
            names.append(word)
    return list(dict.fromkeys(names))


def _identifier_terms(names: list[str]) -> set[str]:
    """The lowercase sub-tokens of code names, split as file text is
    (underscores and camelCase).
    """
    return {term for name in names for term in body_terms(name.encode("utf-8")).split()}


def _exact_match(terms: list[str]) -> str:
    return " OR ".join(f'"{term}"' for term in terms if term)


def _kept_terms(con, terms: list[str], identifier_terms: set[str]) -> list[str]:
    """The query terms with the most weight times rarity over the
    pieces, at most ``KW_TERMS``. A term no piece has is dropped, and
    ties keep the query's order.
    """
    con.execute(
        "create virtual table if not exists temp.mimry_pieces_vocab "
        "using fts5vocab(main, pieces_fts, 'row')"
    )
    marks = ", ".join("?" for _ in terms)
    doc_counts = dict(
        con.execute(
            f"select term, doc from temp.mimry_pieces_vocab where term in ({marks})", terms
        ).fetchall()
    )
    total = con.execute("select count(*) from pieces_fts").fetchone()[0]
    weighted = []
    for term in terms:
        df = doc_counts.get(term, 0)
        if not df:
            continue
        idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
        weight = IDENTIFIER_WEIGHT if term in identifier_terms else 1.0
        weighted.append((weight * idf, term))
    weighted.sort(key=lambda item: -item[0])
    return [term for _weight, term in weighted[:KW_TERMS]]


def _piece_scores(con, match: str, limit: int) -> dict[str, list[float]]:
    """Each file's piece scores, best first, from the top ``limit``
    pieces by BM25 (higher is better). Files keep their first
    appearance order.
    """
    rows = con.execute(
        "select file_id, line_start, bm25(pieces_fts, 0.0, 0.0, 0.0, 1.4, 1.0) as rank "
        "from pieces_fts where pieces_fts match ? "
        "order by rank, file_id, line_start limit ?",
        (match, limit),
    ).fetchall()
    by_file: dict[str, list[float]] = {}
    for file_id, _start, rank in rows:
        by_file.setdefault(file_id, []).append(-rank)
    return by_file


def _exact_symbol_files(con, identifiers: list[str]) -> list[str]:
    """Files defining a symbol named exactly by a code name in the
    query, the rarest name first. A name that more than
    ``SYMBOL_NAME_SHARE`` symbols share names no file.
    """
    names = list(dict.fromkeys(name.lower() for name in identifiers))
    if not names:
        return []
    marks = ", ".join("?" for _ in names)
    shares = dict(
        con.execute(
            f"select lower(name), count(*) from symbols where lower(name) in ({marks}) "
            "group by lower(name)",
            names,
        ).fetchall()
    )
    names = [name for name in names if 0 < shares.get(name, 0) <= SYMBOL_NAME_SHARE]
    if not names:
        return []
    marks = ", ".join("?" for _ in names)
    rows = con.execute(
        f"select file_id, lower(name) from symbols where lower(name) in ({marks})", names
    ).fetchall()
    rows.sort(key=lambda row: (shares[row[1]], row[0]))
    return list(dict.fromkeys(file_id for file_id, _name in rows))


def _path_factor(rel_path: str) -> float:
    """Tests, docs and examples rank down, and package __init__ files
    less. Where several apply, the strongest one counts.
    """
    factors = [1.0]
    if is_test_file(rel_path):
        factors.append(TEST_FACTOR)
    if is_doc_or_plan(rel_path) or {"example", "examples"} & set(rel_path.lower().split("/")):
        factors.append(DOC_FACTOR)
    if rel_path.rsplit("/", 1)[-1] == "__init__.py":
        factors.append(INIT_FACTOR)
    return min(factors)


def piece_file_ranking(idx: Path, q: str, limit: int = PIECE_LIMIT) -> list[dict]:
    """Files of the whole repository, fused from four ranked lists: the
    query's kept terms over pieces, its identifier terms over pieces,
    whole files, and files defining a symbol the query names exactly.

    Each list ranks files by reciprocal rank fusion, the path factor
    scales the sum, and the top ``limit`` files come in fused order with
    ``kw_pos`` and ``kw_score``. A file in the pieces list also keeps
    ``piece_pos``, ``piece_best``, ``piece_sum`` and ``piece_hits``, its
    best piece's BM25 score and a decaying sum of its next pieces.
    Recorded as ranking parts only, so it does not reorder results.
    """
    terms = list(dict.fromkeys(query_terms(q)))
    identifiers = code_identifiers(q)
    try:
        con = sqlite3.connect(Path(idx) / "mimry.sqlite")
        try:
            return _fused_ranking(con, terms, identifiers, limit)
        finally:
            con.close()
    except sqlite3.Error:
        return []


def _fused_ranking(con, terms: list[str], identifiers: list[str], limit: int) -> list[dict]:
    identifier_terms = _identifier_terms(identifiers)
    kept = _kept_terms(con, terms, identifier_terms) if terms else []
    if not kept:
        return []
    by_piece = _piece_scores(con, _exact_match(kept), limit)
    kept_identifiers = [term for term in kept if term in identifier_terms]
    by_identifier = (
        list(_piece_scores(con, _exact_match(kept_identifiers), limit)) if kept_identifiers else []
    )
    whole_files = [
        file_id
        for file_id, _rank in con.execute(
            "select file_id, bm25(files_fts, 0.0, 1.0, 1.4, 0.4, 0.8, 0.8, 0.8) as rank "
            "from files_fts where files_fts match ? "
            "order by rank, rel_path, file_id limit ?",
            (_exact_match(kept), WHOLE_FILE_LIMIT),
        ).fetchall()
    ]
    symbol_files = _exact_symbol_files(con, identifiers)

    fused: dict[str, float] = {}
    lists = ((list(by_piece), 1.0), (by_identifier, 1.0), (whole_files, 2.0), (symbol_files, 1.0))
    for ranked, weight in lists:
        for position, file_id in enumerate(ranked, start=1):
            fused[file_id] = fused.get(file_id, 0.0) + weight / (KW_RRF_K + position)
    marks = ", ".join("?" for _ in fused)
    paths = dict(
        con.execute(
            f"select file_id, rel_path from files where file_id in ({marks})", list(fused)
        ).fetchall()
    )
    scored = sorted(
        (
            (
                round(score * _path_factor(paths[file_id]) if file_id in paths else score, 6),
                file_id,
            )
            for file_id, score in fused.items()
        ),
        key=lambda item: (-item[0], item[1]),
    )[:limit]

    piece_position = {file_id: position for position, file_id in enumerate(by_piece, start=1)}
    ranking = []
    for kw_pos, (kw_score, file_id) in enumerate(scored, start=1):
        entry: dict = {"file_id": file_id}
        if file_id in by_piece:
            scores = by_piece[file_id]
            tail = sum(s * 0.5 ** (i + 1) for i, s in enumerate(scores[1 : PIECE_TAIL + 1]))
            entry.update(
                {
                    "piece_pos": piece_position[file_id],
                    "piece_best": round(scores[0], 4),
                    "piece_sum": round(scores[0] + 0.5 * tail, 4),
                    "piece_hits": len(scores),
                }
            )
        entry.update({"kw_pos": kw_pos, "kw_score": kw_score})
        ranking.append(entry)
    return ranking


def piece_files(idx: Path, q: str, limit: int = 30) -> list[dict]:
    """The first ``limit`` files of ``piece_file_ranking`` with their
    paths, for the localization benchmark's diagnosis.
    """
    ranking = piece_file_ranking(idx, q)
    ids = [entry["file_id"] for entry in ranking]
    if not ids:
        return []
    marks = ", ".join("?" for _ in ids)
    try:
        con = sqlite3.connect(Path(idx) / "mimry.sqlite")
        try:
            paths = dict(
                con.execute(
                    f"select file_id, rel_path from files where file_id in ({marks})", ids
                ).fetchall()
            )
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [
        {**entry, "path": paths[entry["file_id"]]}
        for entry in ranking
        if entry["file_id"] in paths
    ][:limit]


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
