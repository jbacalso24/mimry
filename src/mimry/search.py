from __future__ import annotations

import fnmatch
import json
from functools import cache
from pathlib import Path

from .core.artifacts import graph_available, graph_rows
from .core.languages import EXTENSION_LANGUAGE, language_for
from .feedback import apply_feedback_to_rows
from .intent import (
    apply_exclusion_adjustment,
    apply_intent_adjustment,
    excluded_query_terms,
    query_terms,
)
from .paths import canonical_cached_rel_path
from .security import filter_index_records
from .semantic import merge_semantic_rows, semantic_rows
from .storage import connect, load_jsonl, load_pointer

# `--language py` means python: extensions work as language names.
LANGUAGE_ALIASES = {ext.lstrip("."): language for ext, language in EXTENSION_LANGUAGE.items()}


def _file_languages(path: str) -> set[str]:
    """A file's languages by extension; TSX counts as TypeScript."""
    language = language_for(path)
    if language is None:
        return set()
    return {language, "typescript"} if language == "tsx" else {language}


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


def _search_scope(*, languages=None, path_globs=None, exclude_globs=None, offset=None, limit=10):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if offset is not None and (
        isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
    ):
        raise ValueError("offset must be a non-negative integer")
    if languages is not None and not isinstance(languages, (list, tuple)):
        raise ValueError("languages must be a list of strings")
    normalized_languages = []
    for raw in languages or ():
        if not isinstance(raw, str):
            raise ValueError("language must be a string")
        value = raw.strip().casefold()
        if not value:
            raise ValueError("language must not be empty")
        if value not in EXTENSION_LANGUAGE.values():
            value = LANGUAGE_ALIASES.get(value, value)
        normalized_languages.append(value)
    return {
        "languages": tuple(dict.fromkeys(normalized_languages)),
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
    return " OR ".join(f'"{term}"*' for term in terms if term)


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
    for file_id, rank in rows:
        if best < 0:
            boost = 8 + round(72 * rank / best)
        else:
            boost = 8
        scores[file_id] = (boost, "FTS/BM25 match")
    return scores


def score(f, q, fts_boost: tuple[int, str] | None = None):
    terms = query_terms(q)
    h = {
        "filename": f["filename"].lower(),
        "path": f["rel_path"].lower(),
        "content hint": f.get("content_hint", "").lower(),
        "metadata": f.get("metadata_text", "").lower(),
    }
    s = 0
    reasons = []
    for term in terms:
        for label, text in h.items():
            if term in text:
                s += {"filename": 30, "path": 20, "content hint": 10, "metadata": 6}[label]
                reason = f"{label} match"
                token_reason = f"token match: {term}"
                if reason not in reasons:
                    reasons.append(reason)
                if token_reason not in reasons:
                    reasons.append(token_reason)

    if fts_boost:
        boost, reason = fts_boost
        s += boost
        if reason not in reasons:
            reasons.append(reason)

    if s and f["adapter"] != "generic":
        s += 8
        reasons.append(f["adapter"] + " adapter")

    s, intent_reasons = apply_intent_adjustment(s, f["rel_path"], terms)
    reasons.extend(intent_reasons)

    return s, reasons


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
        adjusted.append({**row, "score": score, "reason": reason})
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
    languages_by_path: dict[str, set[str]] = {
        f["rel_path"]: _file_languages(f["rel_path"]) for f in files
    }
    for symbol in symbols:
        language = str(symbol.get("language") or "").strip().casefold()
        path = file_paths.get(symbol.get("file_id"))
        if language and path:
            languages_by_path.setdefault(path, set()).add(language)
    known_languages = set(EXTENSION_LANGUAGE.values()).union(*languages_by_path.values())
    unknown = [language for language in scope["languages"] if language not in known_languages]
    if unknown:
        raise ValueError(
            f"unknown language {', '.join(unknown)}; use one of: "
            + ", ".join(sorted(known_languages))
        )
    fts_scores = _fts_scores(idx, q)
    known_paths = {f["rel_path"] for f in files}
    candidate_limit = max(len(files), limit + scope["offset"]) if scoped_or_paginated else limit

    clusters = {}
    if graph and (idx / "graph.json").exists():
        clusters = json.loads((idx / "graph.json").read_text()).get("clusters", {})
    for f in files:
        s, rs = score(f, q, fts_scores.get(f["file_id"]))

        if s:
            folder = f["rel_path"].rsplit("/", 1)[0] if "/" in f["rel_path"] else "."
            if graph and folder in clusters:
                s += 5
                rs.append("graph cluster relationship")
            row = {"path": f["rel_path"], "score": s, "reason": ", ".join(rs)}
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
        rows = graph_rows(root, q, None)
        if rows:
            rows = _without_excluded(
                [
                    {
                        **r,
                        "reason": r["reason"]
                        + (
                            ", MIMRY config-manifest operating context"
                            if r["path"] in fallback_by_path
                            else ""
                        ),
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
                    graph_row["reason"] += ", corroborated by MIMRY content index"
            seen = set(by_path)
            for r in fallback_rows:
                if r["path"] in seen:
                    continue
                extra = {**r}
                if "config-manifest" in r["reason"]:
                    extra["reason"] = r["reason"] + ", MIMRY config-manifest operating context"
                else:
                    extra["reason"] = r["reason"] + ", MIMRY fallback index signal"
                merged.append(extra)
                seen.add(r["path"])
            ranked = sorted(merged, key=lambda r: (-r["score"], r["path"]))
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
            return _filter_and_page(ranked, scope=scope, languages_by_path=languages_by_path)
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
    return _filter_and_page(ranked, scope=scope, languages_by_path=languages_by_path)
