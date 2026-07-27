from __future__ import annotations

import json

from .feedback import apply_feedback_to_rows
from .graphify_artifacts import graphify_available, graphify_rows
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
            "from files_fts where files_fts match ? order by rank limit 80",
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
    clusters = {}
    if graph and (idx / "graph.json").exists():
        clusters = json.loads((idx / "graph.json").read_text()).get("clusters", {})
    for f in files:
        s, rs = score(f, q, fts_scores.get(f["file_id"]))
        if s:
            folder = f["rel_path"].rsplit("/", 1)[0] if "/" in f["rel_path"] else "."
            if graph and folder in clusters:
                s += 5
                rs.append("Graphify cluster relationship")
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
    if root is not None and graphify_available(root):
        rows = graphify_rows(root, q, limit)
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
            seen = {r["path"] for r in rows}
            merged = list(rows)
            for r in fallback_rows:
                if r["path"] in seen:
                    continue
                extra = {**r}
                if "config-manifest" in r["reason"]:
                    extra["reason"] = r["reason"] + ", MIMRY config-manifest operating context"
                else:
                    extra["reason"] = r["reason"] + ", MIMRY fallback index signal"
                    extra["score"] = max(1, int(r["score"] * 0.75))
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
