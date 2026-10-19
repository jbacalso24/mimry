from __future__ import annotations

import fnmatch
import json
import math
import re
from functools import cache
from pathlib import Path

from .core.artifacts import graph_available, graph_rows
from .core.documents import DOCUMENT_UNITS
from .core.languages import (
    EXTENSION_LANGUAGE,
    LANGUAGE_LABELS,
    file_language,
    grammars_named,
)
from .feedback import apply_feedback_to_rows
from .intent import (
    CJK_RUN_RE,
    TEST_TERMS,
    apply_exclusion_adjustment,
    apply_intent_adjustment,
    cjk_bigrams,
    excluded_query_terms,
    intent_terms,
    is_doc_or_plan,
    is_migration,
    is_supporting_code,
    is_test_file,
    query_terms,
)
from .mentions import (
    EXACT_NAME_REASON,
    exact_name_paths,
    named_boost,
    named_paths,
    unindexed_file_names,
)
from .paths import canonical_cached_rel_path
from .pieces import best_ranges, piece_file_ranking, piece_matches
from .security import filter_index_records
from .semantic import merge_semantic_rows, semantic_rows
from .storage import connect, load_jsonl, load_pointer

# `--language py` means python: extensions work as language names.
LANGUAGE_ALIASES = {ext.lstrip("."): language for ext, language in EXTENSION_LANGUAGE.items()}
# Score lift per named identifier a file defines.
NAMED_DEFINITION_LIFT = 0.1
# Most named identifiers counted for one file.
NAMED_DEFINITION_CAP = 3
# Rows the learned order reranks. Rows past the pool keep their order.
LEARNED_POOL = 50
# Rank offset in the reciprocal rank fusion of the keyword file list.
KEYWORD_FUSE_K = 5
# Weight of the keyword file list against MIMRY's own order.
KEYWORD_FUSE_WEIGHT = 0.75
# Keyword files that take part, by their place in the list.
KEYWORD_FUSE_LIST = 30
# Distinct query terms before fusion; short queries are name-like.
KEYWORD_FUSE_MIN_TERMS = 20
# Pairwise logistic regression over these parts, fitted on the SWE-bench
# train set (tasks.train.json); refit when signals change.
LEARNED_WEIGHTS = {
    "graph_label": -244,
    "graph_path": 167,
    "graph_coverage": -63,
    "graph_matches": -157,
    "graph_multi": 91,
    "graph_degree": 142,
    "graph_topology": -85,
    "graph_reached": 65,
    "graph_score": 1,
    "filename_hits": -254,
    "path_hits": 197,
    "hint_hits": 55,
    "metadata_hits": 49,
    "fts": 22,
    "named_path": 396,
    "content_score": 69,
    "content_on_graph": 287,
    "named_defs": 1501,
    "feedback": 0,
    "exact_lift": 0,
    "cluster_bonus": 0,
    "final_score": 517,
    "file_bytes": 136,
    "intent_multiplier": 183,
    "log_rank": -847,
    "has_graph": 514,
    "has_content": 323,
    "doc": -1367,
    "supporting": -818,
    "migration": 5,
    "init": -112,
    "depth": -78,
}
_QUERY_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_BACKTICK_NAME_RE = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`")


def _file_languages(record: dict) -> set[str]:
    """A file's languages by extension; TSX counts as TypeScript."""
    language = file_language(record)
    if language is None:
        return set()
    return {language, "typescript"} if language == "tsx" else {language}


def _with_reason(reason: str, extra: str) -> str:
    """Add a reason before a graph row's ``; nodes:`` list, which must
    hold node names only."""
    head, separator, nodes = reason.partition("; nodes:")
    return f"{head}, {extra}{separator}{nodes}"


def _validated_globs(values, label):
    if values is not None and not isinstance(values, (list, tuple)):
        raise ValueError(f"{label}s must be a list of strings")
    globs = []
    for raw in values or ():
        if not isinstance(raw, str):
            raise ValueError(f"{label} must be a string")
        value = raw.strip().replace("\\", "/")
        parts = value.split("/")
        if not value or value.startswith("/") or "\x00" in value or ".." in parts:
            raise ValueError(
                f"{label} must be a non-empty repository-relative glob without '..': {raw!r}"
            )
        if len(value) >= 2 and value[1] == ":":
            raise ValueError(f"{label} must be repository-relative, not absolute: {raw!r}")
        globs.append(value)
    return tuple(dict.fromkeys(globs))


def _path_glob_matches(path: str, pattern: str) -> bool:
    """Match a repository path with segment-aware ``**`` semantics."""
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")

    @cache
    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], part)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


def _grammars_for(languages) -> tuple[str, ...]:
    """Grammar names for --language values: py, Groovy, Terraform and
    the like, de-duplicated in order."""
    grammars = []
    for raw in languages or ():
        if not isinstance(raw, str):
            raise ValueError("language must be a string")
        value = raw.strip().casefold()
        if not value:
            raise ValueError("language must not be empty")
        if value not in EXTENSION_LANGUAGE.values():
            value = LANGUAGE_ALIASES.get(value, value)
        named = () if value in EXTENSION_LANGUAGE.values() else grammars_named(value)
        grammars.extend(named or (value,))
    return tuple(dict.fromkeys(grammars))


def language_display_names(languages) -> list[str]:
    """The display name of each --language value, as find's scope line
    shows it: groovy is Groovy, py is Python."""
    return list(dict.fromkeys(LANGUAGE_LABELS.get(g, g) for g in _grammars_for(languages)))


def _search_scope(*, languages=None, path_globs=None, exclude_globs=None, offset=None, limit=10):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if offset is not None and (
        isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
    ):
        raise ValueError("offset must be a non-negative integer")
    if languages is not None and not isinstance(languages, (list, tuple)):
        raise ValueError("languages must be a list of strings")
    return {
        "languages": _grammars_for(languages),
        "path_globs": _validated_globs(path_globs, "path glob"),
        "exclude_globs": _validated_globs(exclude_globs, "exclude glob"),
        "offset": 0 if offset is None else offset,
        "pagination_requested": offset is not None,
        "limit": limit,
    }


def _filter_and_page(rows, *, scope, languages_by_path):
    filtered = []
    for row in rows:
        path = row["path"]
        languages = languages_by_path.get(path, frozenset())
        if scope["languages"] and not languages.intersection(scope["languages"]):
            continue
        if scope["path_globs"] and not any(
            _path_glob_matches(path, pattern) for pattern in scope["path_globs"]
        ):
            continue
        if any(_path_glob_matches(path, pattern) for pattern in scope["exclude_globs"]):
            continue
        labels = []
        if scope["languages"]:
            labels.append(
                "language filter: " + "/".join(sorted(languages.intersection(scope["languages"])))
            )
        if scope["path_globs"]:
            labels.append("path filter: " + ", ".join(scope["path_globs"]))
        if scope["exclude_globs"]:
            labels.append("excluded paths: " + ", ".join(scope["exclude_globs"]))
        filtered.append({**row, "reason": ", ".join([row["reason"], *labels])})
    start = scope["offset"]
    return filtered[start : start + scope["limit"]]


def _fts_match_query(terms: list[str]) -> str:
    # Prefix each normalized token for identifier/path fragments; quote
    # to keep FTS syntax safe.
    clauses = []
    for term in terms:
        if not term:
            continue
        if not CJK_RUN_RE.fullmatch(term):
            clauses.append(f'"{term}"*')
            continue
        # A CJK run is one FTS word, so a substring of it matches its
        # character pairs instead (indexed by body_terms).
        grams = sorted(set(cjk_bigrams(term)))
        pairs = " AND ".join(f'"{gram}"' for gram in grams)
        clauses.append(f'("{term}"* OR ({pairs}))' if len(grams) > 1 else f'("{term}"*)')
    return " OR ".join(clauses)


LONG_QUERY_TERMS = 24


def bm25_scale(term_count: int) -> float:
    """How much to scale the BM25 boost for a query this long.

    Keyword and graph sums grow with every query term; BM25 does not.
    On an issue-length query they drowned it out, though BM25 is the
    signal that corrects for long files. Up to LONG_QUERY_TERMS terms
    the boost is unchanged; past that it grows 4x per further block.
    """
    return max(1.0, 1 + 4 * (term_count - LONG_QUERY_TERMS) / LONG_QUERY_TERMS)


def _fts_scores(idx, q: str) -> dict[str, tuple[int, str]]:
    terms = query_terms(q)
    match = _fts_match_query(terms)
    if not match:
        return {}
    try:
        con = connect(idx)
        # First weight is unindexed file_id; rel_path, filename,
        # extension, content_hint, metadata_text, body follow.
        rows = con.execute(
            "select file_id, bm25(files_fts, 0.0, 1.0, 1.4, 0.4, 0.8, 0.8, 0.8) as rank "
            "from files_fts where files_fts match ? order by rank, rel_path, file_id limit 80",
            (match,),
        ).fetchall()
        con.close()
    except Exception:
        return {}
    if not rows:
        return {}
    scores: dict[str, tuple[int, str]] = {}
    best = min(rank for _file_id, rank in rows)
    scale = bm25_scale(len(terms))
    for file_id, rank in rows:
        if best < 0:
            boost = round((8 + 72 * rank / best) * scale)
        else:
            boost = 8
        scores[file_id] = (boost, "FTS/BM25 match")
    return scores


def _piece_parts(idx, q, path_by_id) -> dict[str, dict]:
    """Each file's place in the repository-wide piece ranking, by path,
    for its row to carry as parts.
    """
    if not q:
        return {}
    # A file outside the pieces list has only the kw_* keys.
    keys = ("piece_pos", "piece_best", "piece_sum", "piece_hits", "kw_pos", "kw_score")
    return {
        path_by_id[entry["file_id"]]: {key: entry[key] for key in keys if key in entry}
        for entry in piece_file_ranking(idx, q)
        if entry["file_id"] in path_by_id
    }


def _with_piece_parts(rows, piece_by_path):
    """Add each row's piece ranking to its parts, if it has one."""
    return [
        {**row, "parts": {**row.get("parts", {}), **piece_by_path[row["path"]]}}
        if row["path"] in piece_by_path
        else row
        for row in rows
    ]


def _with_ranges(idx, q, rows, id_by_path, cut_at, minified_paths=frozenset()):
    """Name the lines to read first in each result: its best pieces.

    ``cut_at`` maps a document cut at a cap to where the cut fell, so
    its ranges say so and do not read as the whole document. A row for
    a minified file is marked, so it is never taken for an edit surface.
    """
    pieces = piece_matches(
        idx,
        q,
        [id_by_path[row["path"]] for row in rows if row["path"] in id_by_path],
    )
    out = []
    for row in rows:
        if row["path"] in minified_paths:
            row = {**row, "minified": True}
        matches = pieces.get(id_by_path.get(row["path"], ""))
        if not matches:
            out.append(row)
            continue
        # A document's ranges name its parts (pages, sections, ...).
        unit = DOCUMENT_UNITS.get(Path(row["path"]).suffix.lower())
        ranges = best_ranges(matches, unit)
        if row["path"] in cut_at:
            for item in ranges:
                item["cut_at"] = cut_at[row["path"]]
        out.append({**row, "ranges": ranges})
    return out


def score(
    f,
    q,
    fts_boost: tuple[int, str] | None = None,
    *,
    terms: list[str] | None = None,
    intent: list[str] | None = None,
    named: tuple[int, str] | None = None,
    parts: dict | None = None,
):
    # find_rows passes both lists in, computed once per query; other
    # callers get them from q. When parts is given, the raw signal
    # values are written into it; the score is the same either way.
    terms = query_terms(q) if terms is None else terms
    intent = intent_terms(q) if intent is None else intent
    h = {
        "filename": f["filename"].lower(),
        "path": f["rel_path"].lower(),
        "content hint": f.get("content_hint", "").lower(),
        "metadata": f.get("metadata_text", "").lower(),
    }
    s = 0
    reasons = []
    matched = dict.fromkeys(h, 0)
    for term in terms:
        for label, text in h.items():
            if term in text:
                matched[label] += 1
                s += {"filename": 30, "path": 20, "content hint": 10, "metadata": 6}[label]
                reason = f"{label} match"
                token_reason = f"token match: {term}"
                if reason not in reasons:
                    reasons.append(reason)
                if token_reason not in reasons:
                    reasons.append(token_reason)

    if parts is not None:
        parts.update(
            filename_hits=matched["filename"],
            path_hits=matched["path"],
            hint_hits=matched["content hint"],
            metadata_hits=matched["metadata"],
        )

    if fts_boost:
        boost, reason = fts_boost
        s += boost
        if reason not in reasons:
            reasons.append(reason)
        if parts is not None:
            parts["fts"] = boost

    if named:
        # Before the intent adjustment, so a named test file still takes
        # the test downrank on a source task.
        boost, reason = named
        s += boost
        reasons.append(reason)
        if parts is not None:
            parts["named_path"] = boost

    if s and f["adapter"] != "generic":
        s += 8
        reasons.append(f["adapter"] + " adapter")
        if parts is not None:
            parts["adapter_bonus"] = 8

    before_intent = s
    s, intent_reasons = apply_intent_adjustment(
        s, f["rel_path"], intent, minified=bool(f.get("minified"))
    )
    reasons.extend(intent_reasons)
    if parts is not None:
        parts["content_score"] = before_intent
        parts["intent_multiplier"] = round(s / before_intent, 4) if before_intent else 1.0

    return s, reasons


def _exact_names_first(rows, exact_paths):
    """Lift the files the query names exactly above every other row.

    Graph and content scores are sums that can reach the hundreds, so no
    fixed boost is safely above them. The exact files keep their order
    among themselves and move up together, so each score still matches
    its rank.
    """
    exact = [r for r in rows if r["path"] in exact_paths]
    if not exact:
        return rows
    others = [r for r in rows if r["path"] not in exact_paths]
    best_other = max((r["score"] for r in others), default=0)
    lift = max(0, best_other + 1 - min(r["score"] for r in exact))
    lifted = []
    for r in exact:
        reason = r["reason"]
        if EXACT_NAME_REASON not in reason:
            reason = ", ".join(filter(None, [reason, EXACT_NAME_REASON]))
        parts = {**r.get("parts", {}), "exact_lift": lift}
        lifted.append({**r, "score": r["score"] + lift, "reason": reason, "parts": parts})
    return lifted + others


def _named_definition_lift(rows, q, symbols, id_by_path):
    """Lift files that define an identifier the task names.

    Needs the whole query and every symbol, so it runs on merged rows
    rather than inside score(). A plain word that matches a file name
    is not lifted: commit scopes such as `fix(types)` name a folder or
    a topic, not the file to edit.
    """
    words = set(_QUERY_WORD_RE.findall(q))
    named = {m.split(".")[-1] for m in _BACKTICK_NAME_RE.findall(q)}
    named |= {w for w in words if "_" in w.strip("_") or re.search(r"[a-z][A-Z]", w)}
    names_by_file: dict[str, set[str]] = {}
    for symbol in symbols:
        name = str(symbol.get("name") or "").split(".")[-1]
        if name:
            names_by_file.setdefault(symbol.get("file_id"), set()).add(name)
    lifted = []
    for row in rows:
        path = row["path"]
        defined = len(named & names_by_file.get(id_by_path.get(path), set()))
        if not defined:
            lifted.append(row)
            continue
        multiplier = 1 + NAMED_DEFINITION_LIFT * min(defined, NAMED_DEFINITION_CAP)
        noun = "identifier" if defined == 1 else "identifiers"
        lifted.append(
            {
                **row,
                "score": int(row["score"] * multiplier),
                "reason": _with_reason(row["reason"], f"defines {defined} named {noun}"),
                "parts": {**row.get("parts", {}), "named_defs": defined},
            }
        )
    return sorted(lifted, key=lambda r: (-r["score"], r["path"]))


def _learned_score(row, rank, size_by_path):
    """The fitted ranker's score for one row at its 1-based rank."""
    parts = row.get("parts", {})
    path = row["path"]
    derived = {
        # A flat intent bonus on a tiny base score reads as a big ratio;
        # 99% of the training rows were at or under 2.
        "intent_multiplier": min(parts.get("intent_multiplier", 1.0), 2.0),
        "log_rank": math.log(rank),
        "has_graph": float("graph_score" in parts),
        "has_content": float("content_score" in parts or "content_on_graph" in parts),
        "doc": float(is_doc_or_plan(path)),
        "supporting": float(is_supporting_code(path)),
        "migration": float(is_migration(path)),
        "init": float(path.endswith("__init__.py")),
        "depth": float(path.count("/")),
    }
    total = 0.0
    for name, weight in LEARNED_WEIGHTS.items():
        if name in derived:
            value = derived[name]
        elif name == "final_score":
            value = math.log1p(max(0, row["score"]))
        elif name == "file_bytes":
            value = math.log1p(max(0, size_by_path.get(path, 0)))
        else:
            value = math.log1p(max(0, parts.get(name, 0)))
        total += round(value, 3) * weight
    return round(total, 3)


def _learned_rerank(rows, size_by_path):
    """Reorder the first LEARNED_POOL rows by the fitted ranker (#42).

    The pool's own scores are handed out again in the new order, so the
    scale stays the same and a later sort by score (feedback, semantic
    merge) keeps the learned order. Rows past the pool keep their place.
    """
    pool = []
    for rank, row in enumerate(rows[:LEARNED_POOL], start=1):
        learned = _learned_score(row, rank, size_by_path)
        parts = {**row.get("parts", {}), "learned": learned}
        pool.append((-learned, rank, {**row, "parts": parts}))
    pool.sort(key=lambda item: (item[0], item[1]))
    scores = sorted((row["score"] for _, _, row in pool), reverse=True)
    reranked = [{**row, "score": score} for (_, _, row), score in zip(pool, scores, strict=True)]
    return reranked + rows[LEARNED_POOL:]


def _keyword_fuse(rows, piece_by_path, allowed):
    """Fuse the keyword file list into the learned pool (#42).

    A pool row scores by its place in MIMRY's order. Each keyword file
    within KEYWORD_FUSE_LIST adds its own reciprocal-rank term. A
    keyword file outside the pool joins only if ``allowed`` accepts its
    path. The pool keeps its own scores, handed out in the new order.
    """
    if not piece_by_path:
        return rows
    head, tail = rows[:LEARNED_POOL], rows[LEARNED_POOL:]
    fused = {row["path"]: 1 / (KEYWORD_FUSE_K + rank) for rank, row in enumerate(head, start=1)}
    members = {row["path"]: row for row in head}
    tail_by_path = {row["path"]: row for row in tail}
    floor = head[-1]["score"] if head else 0
    for path, parts in piece_by_path.items():
        kw_pos = parts.get("kw_pos")
        if kw_pos is None or kw_pos > KEYWORD_FUSE_LIST:
            continue
        if path not in members:
            if not allowed(path):
                continue
            members[path] = tail_by_path.get(path) or {
                "path": path,
                "score": floor,
                "reason": "keyword match",
                "parts": dict(parts),
            }
        fused[path] = fused.get(path, 0) + KEYWORD_FUSE_WEIGHT / (KEYWORD_FUSE_K + kw_pos)
    pool = sorted(members.values(), key=lambda row: (-fused[row["path"]], row["path"]))
    scores = sorted((row["score"] for row in pool), reverse=True)
    pool = [
        {
            **row,
            "score": score,
            "parts": {**row.get("parts", {}), "keyword_fused": round(fused[row["path"]], 6)},
        }
        for row, score in zip(pool, scores, strict=True)
    ]
    return pool + [row for row in tail if row["path"] not in members]


def _apply_exclusions(rows, q):
    excluded_terms = excluded_query_terms(q)
    if not excluded_terms:
        return rows
    adjusted = []
    for row in rows:
        if "excluded scope downrank:" in row.get("reason", ""):
            adjusted.append(row)
            continue
        score, reasons = apply_exclusion_adjustment(row["score"], row["path"], excluded_terms)
        reason = ", ".join(filter(None, [row.get("reason", ""), *reasons]))
        parts = {**row.get("parts", {}), "exclusion": score - row["score"]}
        adjusted.append({**row, "score": score, "reason": reason, "parts": parts})
    return sorted(adjusted, key=lambda r: (-r["score"], r["path"]))


def _without_excluded(rows, excluded_paths):
    canonical_excluded = {
        canonical
        for path in excluded_paths
        if (canonical := canonical_cached_rel_path(path)) is not None
    }
    retained = []
    for row in rows:
        canonical = canonical_cached_rel_path(row.get("rel_path") or row.get("path"))
        # Imported/cached absolute or traversal-bearing paths are not
        # valid repository candidates. Fail closed instead of resolving
        # them.
        if canonical is None or canonical in canonical_excluded:
            continue
        retained.append(row)
    return retained


def _with_semantic(rows, idx, root_id, q, known_paths, limit, semantic, excluded_paths):
    if not semantic:
        return _apply_exclusions(_without_excluded(rows, excluded_paths), q)
    sem_rows, _health = semantic_rows(idx, root_id, q, limit)
    merged = merge_semantic_rows(rows, sem_rows, limit=limit)
    ranked = apply_feedback_to_rows(merged, idx, root_id, q, known_paths=known_paths)
    return _apply_exclusions(_without_excluded(ranked, excluded_paths), q)


def missing_file_names(idx, q) -> list[str]:
    """File names the query writes out that the index does not hold."""
    files, _ = filter_index_records(load_jsonl(idx / "files.jsonl"))
    return unindexed_file_names(q, [f["rel_path"] for f in files])


def _stamp_final(rows, size_by_path):
    """Record each row's score, after every adjustment, and its file
    size as parts."""
    return [
        {
            **row,
            "parts": {
                **row.get("parts", {}),
                "final_score": row["score"],
                "file_bytes": size_by_path.get(row["path"], 0),
            },
        }
        for row in rows
    ]


def find_rows(
    idx,
    q,
    limit=10,
    graph=False,
    root=None,
    root_id=None,
    semantic=False,
    excluded_paths=None,
    *,
    languages=None,
    path_globs=None,
    exclude_globs=None,
    offset=None,
):
    scope = _search_scope(
        languages=languages,
        path_globs=path_globs,
        exclude_globs=exclude_globs,
        offset=offset,
        limit=limit,
    )
    scoped_or_paginated = bool(
        scope["languages"]
        or scope["path_globs"]
        or scope["exclude_globs"]
        or scope["pagination_requested"]
    )
    if excluded_paths is None and root is not None:
        ptr = load_pointer(root)
        if ptr is not None and Path(ptr["indexPath"]) == Path(idx):
            from .freshness import index_freshness

            excluded_paths = index_freshness(root, ptr)["excluded_paths"]
    excluded_paths = frozenset(excluded_paths or ())
    fallback_rows = []
    files, _ = filter_index_records(load_jsonl(idx / "files.jsonl"))
    files = _without_excluded(files, excluded_paths)
    _unused, symbols = filter_index_records(files, load_jsonl(idx / "symbols.jsonl"))
    file_paths = {f["file_id"]: f["rel_path"] for f in files}
    languages_by_path: dict[str, set[str]] = {f["rel_path"]: _file_languages(f) for f in files}
    size_by_path = {f["rel_path"]: f.get("size", 0) for f in files}
    for symbol in symbols:
        language = str(symbol.get("language") or "").strip().casefold()
        path = file_paths.get(symbol.get("file_id"))
        if language and path:
            languages_by_path.setdefault(path, set()).add(language)
    known_languages = set(EXTENSION_LANGUAGE.values()).union(*languages_by_path.values())
    unknown = [language for language in scope["languages"] if language not in known_languages]
    if unknown:
        shown = sorted({LANGUAGE_LABELS.get(g, g) for g in known_languages}, key=str.casefold)
        raise ValueError(f"unknown language {', '.join(unknown)}; use one of: " + ", ".join(shown))
    fts_scores = _fts_scores(idx, q)
    piece_by_path = _piece_parts(idx, q, file_paths)
    id_by_path = {f["rel_path"]: f["file_id"] for f in files}
    cut_at = {f["rel_path"]: f["cut_at"] for f in files if f.get("cut_at")}
    minified_paths = frozenset(f["rel_path"] for f in files if f.get("minified"))
    known_paths = set(id_by_path)
    candidate_limit = max(len(files), limit + scope["offset"]) if scoped_or_paginated else limit

    clusters = {}
    if graph and (idx / "graph.json").exists():
        clusters = json.loads((idx / "graph.json").read_text()).get("clusters", {})
    terms = query_terms(q)
    fuse_keywords = len(set(terms)) >= KEYWORD_FUSE_MIN_TERMS
    intent = intent_terms(q)
    named = named_paths(q, known_paths)
    # A test file named only in the body of a source task (a traceback
    # from the test that failed) is a symptom, not the edit site.
    tests_asked = bool(set(intent) & TEST_TERMS)
    exact_paths = {
        path for path in exact_name_paths(q, known_paths) if tests_asked or not is_test_file(path)
    }

    def keyword_allowed(path):
        """A keyword file joins the pool only if it is a candidate here.

        It must be indexed and not excluded, and a test file only when
        tests are asked.
        """
        return (
            path in known_paths
            and path not in excluded_paths
            and (tests_asked or not is_test_file(path))
        )

    for f in files:
        path = f["rel_path"]
        named_reason = EXACT_NAME_REASON if path in exact_paths else named.get(path)
        if named_reason and not tests_asked and is_test_file(path):
            named_reason = None
        named_match = (named_boost(named_reason), named_reason) if named_reason else None
        parts: dict = {}
        s, rs = score(
            f,
            q,
            fts_scores.get(f["file_id"]),
            terms=terms,
            intent=intent,
            named=named_match,
            parts=parts,
        )

        if s:
            folder = f["rel_path"].rsplit("/", 1)[0] if "/" in f["rel_path"] else "."
            if graph and folder in clusters:
                s += 5
                rs.append("graph cluster relationship")
                parts["cluster_bonus"] = 5
            row = {"path": f["rel_path"], "score": s, "reason": ", ".join(rs), "parts": parts}
            if any(
                adapter in f.get("adapter", "")
                for adapter in (
                    "config-manifest",
                    "nextjs-app-router",
                    "fastapi",
                    "react-native-expo",
                    "sql-schema",
                    "markdown-docs",
                )
            ):
                row["details"] = f.get("metadata_text", "")[:800]
            fallback_rows.append(row)
    fallback_rows = sorted(fallback_rows, key=lambda r: (-r["score"], r["path"]))
    fallback_by_path = {r["path"]: r for r in fallback_rows if "config-manifest" in r["reason"]}
    if root is not None and graph_available(root):
        # Every graph match, not just the top `limit`: a file the graph
        # ranks lower can lead once its content match is added below,
        # and the cut to `limit` comes after that merge.
        rows = graph_rows(root, q, None, minified_paths=minified_paths)
        if rows:
            rows = _without_excluded(
                [
                    {
                        **r,
                        "reason": _with_reason(
                            r["reason"], "MIMRY config-manifest operating context"
                        )
                        if r["path"] in fallback_by_path
                        else r["reason"],
                        **(
                            {"details": fallback_by_path[r["path"]].get("details", "")}
                            if r["path"] in fallback_by_path
                            else {}
                        ),
                    }
                    for r in rows
                ],
                excluded_paths,
            )
            # A file found by BOTH the graph and the content index is
            # better evidence than one found by either alone. Previously
            # the graph row won and the content score was dropped on the
            # floor.
            merged = [dict(r) for r in rows]
            by_path = {r["path"]: r for r in merged}
            content_by_path = {r["path"]: r for r in fallback_rows}
            for path, graph_row in by_path.items():
                content_row = content_by_path.get(path)
                if content_row:
                    graph_row["score"] += content_row["score"]
                    # On a shared key the graph part wins.
                    graph_row["parts"] = {
                        **content_row.get("parts", {}),
                        **graph_row.get("parts", {}),
                        "content_on_graph": content_row["score"],
                    }
                    graph_row["reason"] = _with_reason(
                        graph_row["reason"], "corroborated by MIMRY content index"
                    )
            seen = set(by_path)
            for r in fallback_rows:
                if r["path"] in seen:
                    continue
                extra = {**r}
                if "config-manifest" in r["reason"]:
                    extra["reason"] = _with_reason(
                        r["reason"], "MIMRY config-manifest operating context"
                    )
                else:
                    extra["reason"] = _with_reason(r["reason"], "MIMRY fallback index signal")
                merged.append(extra)
                seen.add(r["path"])
            ranked = sorted(merged, key=lambda r: (-r["score"], r["path"]))
            ranked = _named_definition_lift(ranked, q, symbols, id_by_path)
            ranked = _with_piece_parts(ranked, piece_by_path)
            ranked = _learned_rerank(ranked, size_by_path)
            if fuse_keywords:
                ranked = _keyword_fuse(ranked, piece_by_path, keyword_allowed)
            ranked = apply_feedback_to_rows(ranked, idx, root_id, q, known_paths=known_paths)
            ranked = _with_semantic(
                ranked,
                idx,
                root_id,
                q,
                known_paths,
                candidate_limit,
                semantic,
                excluded_paths,
            )
            ranked = _exact_names_first(ranked, exact_paths)
            rows = _stamp_final(
                _filter_and_page(ranked, scope=scope, languages_by_path=languages_by_path),
                size_by_path,
            )
            return _with_ranges(idx, q, rows, id_by_path, cut_at, minified_paths)
    fallback_rows = _named_definition_lift(fallback_rows, q, symbols, id_by_path)
    fallback_rows = _with_piece_parts(fallback_rows, piece_by_path)
    fallback_rows = _learned_rerank(fallback_rows, size_by_path)
    if fuse_keywords:
        fallback_rows = _keyword_fuse(fallback_rows, piece_by_path, keyword_allowed)
    ranked = apply_feedback_to_rows(fallback_rows, idx, root_id, q, known_paths=known_paths)
    ranked = _with_semantic(
        ranked,
        idx,
        root_id,
        q,
        known_paths,
        candidate_limit,
        semantic,
        excluded_paths,
    )
    ranked = _exact_names_first(ranked, exact_paths)
    rows = _stamp_final(
        _filter_and_page(ranked, scope=scope, languages_by_path=languages_by_path), size_by_path
    )
    return _with_ranges(idx, q, rows, id_by_path, cut_at, minified_paths)
