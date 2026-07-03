from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

from .intent import apply_intent_adjustment, query_terms
from .paths import graphify_output_dir


def graphify_graph_path(root: Path) -> Path:
    return graphify_output_dir(root) / "graph.json"


def load_graphify_graph(root: Path) -> dict:
    path = graphify_graph_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def graphify_available(root: Path) -> bool:
    g = load_graphify_graph(root)
    return bool(g.get("nodes"))


def node_source_file(node: dict) -> str | None:
    src = node.get("source_file") or node.get("path")
    if isinstance(src, str) and src:
        return src
    return None


def graphify_rows(root: Path, query: str, limit: int = 10) -> list[dict]:
    g = load_graphify_graph(root)
    nodes = g.get("nodes") or []
    links = g.get("links") or g.get("edges") or []
    terms = query_terms(query)
    if not terms:
        return []

    by_file: dict[str, dict] = {}
    degree: dict[str, int] = defaultdict(int)
    for e in links:
        if e.get("source"):
            degree[str(e["source"])] += 1
        if e.get("target"):
            degree[str(e["target"])] += 1

    for n in nodes:
        src = node_source_file(n)
        if not src:
            continue
        text = " ".join(str(n.get(k, "")) for k in ("label", "norm_label", "source_file", "file_type", "id")).lower()
        score = 0
        reasons = []
        for term in terms:
            if term in text:
                if term in str(n.get("label", "")).lower() or term in str(n.get("norm_label", "")).lower():
                    score += 45
                    reasons.append("Graphify node label match")
                if term in src.lower():
                    score += 35
                    reasons.append("Graphify source file match")
                if term in str(n.get("id", "")).lower():
                    score += 10
                    reasons.append("Graphify node id match")
        if not score:
            continue
        deg = degree.get(str(n.get("id")), 0)
        if deg:
            score += min(25, deg * 5)
            reasons.append(f"Graphify degree {deg}")
        if n.get("community") is not None:
            score += 5
            reasons.append(f"Graphify community {n['community']}")
        row = by_file.setdefault(src, {"path": src, "score": 0, "reasons": set(), "nodes": []})
        row["score"] += score
        row["reasons"].update(reasons)
        row["nodes"].append(str(n.get("label") or n.get("id")))

    rows = []
    for row in by_file.values():
        row["score"], intent_reasons = apply_intent_adjustment(row["score"], row["path"], terms, graphify=True)
        if row["score"] <= 0:
            continue
        row["reasons"].update(intent_reasons)
        node_preview = ", ".join(row["nodes"][:4])
        reason = ", ".join(sorted(row["reasons"]))
        if node_preview:
            reason += f"; nodes: {node_preview}"
        rows.append({"path": row["path"], "score": row["score"], "reason": reason, "source": "graphify"})
    return sorted(rows, key=lambda r: (-r["score"], r["path"]))[:limit]


def graphify_relationship_lines(root: Path, selected_paths: list[str], max_lines: int = 12) -> list[str]:
    g = load_graphify_graph(root)
    nodes = g.get("nodes") or []
    links = g.get("links") or g.get("edges") or []
    id_to_node = {str(n.get("id")): n for n in nodes if n.get("id")}
    selected = set(selected_paths)
    lines = []
    for e in links:
        s = id_to_node.get(str(e.get("source")))
        t = id_to_node.get(str(e.get("target")))
        if not s or not t:
            continue
        sf = node_source_file(s)
        tf = node_source_file(t)
        if sf not in selected and tf not in selected:
            continue
        rel = e.get("relation") or e.get("type") or "relates"
        conf = e.get("confidence") or e.get("confidence_score") or ""
        src_label = s.get("label") or s.get("id")
        tgt_label = t.get("label") or t.get("id")
        src_file = sf or "?"
        tgt_file = tf or "?"
        line = f"- `{src_label}` --{rel}--> `{tgt_label}` ({src_file} → {tgt_file})"
        if conf:
            line += f" [{conf}]"
        lines.append(line)
        if len(lines) >= max_lines:
            return lines
    return lines


def graphify_report_excerpt(root: Path, max_chars: int = 1200) -> str:
    path = graphify_output_dir(root) / "GRAPH_REPORT.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    keep = []
    capture = False
    for line in text.splitlines():
        if (
            line.startswith("## Community Hubs")
            or line.startswith("## God Nodes")
            or line.startswith("## Surprising Connections")
        ):
            capture = True
        elif line.startswith("## ") and capture:
            capture = False
        if capture:
            keep.append(line)
    excerpt = "\n".join(keep).strip() or text[:max_chars]
    return excerpt[:max_chars]
