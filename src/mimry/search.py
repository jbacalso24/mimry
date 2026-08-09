from __future__ import annotations

import json

from .feedback import apply_feedback_to_rows
from .core.artifacts import graph_available, graph_rows
from .intent import apply_intent_adjustment, query_terms
from .semantic import merge_semantic_rows, semantic_rows
from .security import filter_index_records, redact_sensitive_text
from .storage import connect, load_jsonl


def _fts_match_query(terms: list[str]) -> str:
    # Prefix each normalized token for identifier/path fragments; quote to keep FTS syntax safe.
    return " OR ".join(f'"{term}"*' for term in terms if term)


def _fts_scores(idx, q: str) -> dict[str, tuple[int, str]]:
    terms = query_terms(q)
    match = _fts_match_query(terms)
    if not match:
        return {}
    try:
        con = connect(idx)
        rows = con.execute(
            "select file_id, bm25(files_fts, 1.0, 1.4, 0.4, 0.8, 0.8) as rank "
            "from files_fts where files_fts match ? order by rank, rel_path, file_id limit 80",
            (match,),
        ).fetchall()
        con.close()
    except Exception:
        return {}
    scores: dict[str, tuple[int, str]] = {}
    for file_id, rank in rows:
        # SQLite FTS5 bm25 returns lower-is-better negative-ish values. Convert to a bounded boost.
        boost = max(8, min(80, int(abs(float(rank)) * 1000) + 18))
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


def _with_semantic(rows, idx, root_id, q, known_paths, limit, semantic):
    if not semantic:
        return rows[:limit]
    sem_rows, _health = semantic_rows(idx, root_id, q, limit)
    merged = merge_semantic_rows(rows, sem_rows, limit=limit)
    return apply_feedback_to_rows(merged, idx, root_id, q, known_paths=known_paths)[:limit]


def find_rows(idx, q, limit=10, graph=False, root=None, root_id=None, semantic=False):
    fallback_rows = []
    files, _ = filter_index_records(load_jsonl(idx / "files.jsonl"))
    fts_scores = _fts_scores(idx, q)
    known_paths = {f["rel_path"] for f in files}

    # Load symbols and build file_id -> symbols mapping for definition boost
    symbols_by_file_id = {}
    try:
        symbols, _ = filter_index_records(load_jsonl(idx / "symbols.jsonl"))
        for sym in symbols:
            fid = sym.get("file_id")
            if fid:
                if fid not in symbols_by_file_id:
                    symbols_by_file_id[fid] = []
                symbols_by_file_id[fid].append(sym.get("name", "").lower())
    except (FileNotFoundError, Exception):
        symbols_by_file_id = {}

    terms = query_terms(q)
    term_set = set(t.lower() for t in terms if t)

    clusters = {}
    if graph and (idx / "graph.json").exists():
        clusters = json.loads((idx / "graph.json").read_text()).get("clusters", {})
    for f in files:
        s, rs = score(f, q, fts_scores.get(f["file_id"]))

        # Add definition boost: files that define matching symbols rank higher than files that mention them
        # Check if any term is a substring of any symbol name (handles plural variants, camelCase splits, etc.)
        file_id = f.get("file_id")
        if file_id and file_id in symbols_by_file_id and term_set:
            defined_symbols = symbols_by_file_id[file_id]
            # Match if term substring is in symbol or symbol substring is in term (handles plurals)
            matching_symbols = [
                sym for sym in defined_symbols if any((term in sym or sym in term) for term in term_set)
            ]
            if matching_symbols:
                # Strong boost: definition sites significantly outrank mere mentions.
                # Exceed path/filename match (+20) but stay at or below exact filename match (+30).
                s += 25
                rs.append(f"defines matching symbol: {', '.join(set(matching_symbols[:2]))}")

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
        rows = graph_rows(root, q, limit)
        if rows:
            rows = [
                {
                    **r,
                    "reason": r["reason"]
                    + (", MIMRY config-manifest operating context" if r["path"] in fallback_by_path else ""),
                    **(
                        {"details": fallback_by_path[r["path"]].get("details", "")}
                        if r["path"] in fallback_by_path
                        else {}
                    ),
                }
                for r in rows
            ]
            # A file found by BOTH the graph and the content index is better
            # evidence than one found by either alone. Previously the graph row
            # won and the content score was dropped on the floor.
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
            return _with_semantic(ranked, idx, root_id, q, known_paths, limit, semantic)
    ranked = apply_feedback_to_rows(fallback_rows, idx, root_id, q, known_paths=known_paths)
    return _with_semantic(ranked, idx, root_id, q, known_paths, limit, semantic)


def print_rows(title, rows):
    print(redact_sensitive_text(title))
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['path']}\n   Score: {r['score']}\n   Reason: {r['reason']}")
        if r.get("details"):
            print(f"   Details: {redact_sensitive_text(r['details'])}")
