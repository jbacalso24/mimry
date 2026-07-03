from __future__ import annotations

import json

from .feedback import apply_feedback_to_rows
from .graphify_artifacts import graphify_available, graphify_rows
from .intent import apply_intent_adjustment, query_terms
from .storage import load_jsonl


def score(f, q):
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
                if reason not in reasons:
                    reasons.append(reason)

    if s and f["adapter"] != "generic":
        s += 8
        reasons.append(f["adapter"] + " adapter")

    s, intent_reasons = apply_intent_adjustment(s, f["rel_path"], terms)
    reasons.extend(intent_reasons)

    return s, reasons


def find_rows(idx, q, limit=10, graph=False, root=None, root_id=None):
    fallback_rows = []
    files = load_jsonl(idx / "files.jsonl")
    known_paths = {f["rel_path"] for f in files}
    clusters = {}
    if graph and (idx / "graph.json").exists():
        clusters = json.loads((idx / "graph.json").read_text()).get("clusters", {})
    for f in files:
        s, rs = score(f, q)
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
            operating_context = [
                {**r, "reason": r["reason"] + ", MIMRY config-manifest operating context"}
                for r in fallback_rows
                if r["path"] not in seen and "config-manifest" in r["reason"]
            ]
            if operating_context:
                operating_context = operating_context[: min(2, limit)]
                merged = rows[: max(limit - len(operating_context), 0)] + operating_context
                return apply_feedback_to_rows(merged, idx, root_id, q, known_paths=known_paths)[:limit]
            return apply_feedback_to_rows(rows, idx, root_id, q, known_paths=known_paths)[:limit]
    return apply_feedback_to_rows(fallback_rows, idx, root_id, q, known_paths=known_paths)[:limit]


def print_rows(title, rows):
    print(title)
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['path']}\n   Score: {r['score']}\n   Reason: {r['reason']}")
        if r.get("details"):
            print(f"   Details: {r['details']}")
