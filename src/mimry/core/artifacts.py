from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..intent import apply_intent_adjustment, query_terms
from ..paths import graph_output_dir
from ..security import path_has_ignored_part, stat_identity, text_mentions_ignored_path


GRAPH_ARTIFACT_FILES = ("graph.json", "GRAPH_REPORT.md", "manifest.json")


def artifact_dir(root: Path) -> Path:
    """Return the graph artifact directory."""
    return graph_output_dir(root)


def _iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_regular_sha256(path: Path) -> str | None:
    """Hash a stable regular-file descriptor and reject pathname races."""

    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return None
            digest = hashlib.sha256()
            while chunk := os.read(fd, 65536):
                digest.update(chunk)
            after = os.fstat(fd)
            current = path.lstat()
            opened_id = stat_identity(opened)
            after_id = stat_identity(after)
            current_id = stat_identity(current)
            if after_id != opened_id or current_id != opened_id:
                return None
            return digest.hexdigest()
        finally:
            os.close(fd)
    except OSError:
        return None


def graph_path(root: Path) -> Path:
    return artifact_dir(root) / "graph.json"


def report_path(root: Path) -> Path:
    return artifact_dir(root) / "GRAPH_REPORT.md"


def manifest_path(root: Path) -> Path:
    return artifact_dir(root) / "manifest.json"


def _filtered_graph(graph: dict) -> dict:
    nodes = graph.get("nodes") or []
    retained_nodes = [node for node in nodes if not path_has_ignored_part(node_source_file(node) or "")]
    retained_ids = {str(node.get("id")) for node in retained_nodes if node.get("id") is not None}
    edge_key = "links" if "links" in graph else "edges"
    retained_edges = []
    for edge in graph.get(edge_key) or []:
        source, target = _edge_endpoints(edge)
        if source in retained_ids and target in retained_ids:
            retained_edges.append(edge)
    return {**graph, "nodes": retained_nodes, edge_key: retained_edges}


def load_graph(root: Path) -> dict:
    path = graph_path(root)
    if not path.exists():
        return {}
    graph = _load_json(path)
    return _filtered_graph(graph) if isinstance(graph, dict) else {}


def graph_available(root: Path) -> bool:
    g = load_graph(root)
    return bool(g.get("nodes"))


def _report_freshness_lines(path: Path) -> dict[str, str | None]:
    if not path.exists():
        return {"report_title": None, "built_from_commit": None}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"report_title": None, "built_from_commit": None}
    title = lines[0].strip() if lines else None
    built_from_commit = None
    for line in lines:
        if line.startswith("- Built from commit:"):
            built_from_commit = line.split(":", 1)[1].strip().strip("`")
            break
    if title and text_mentions_ignored_path(title):
        title = None
    if built_from_commit and text_mentions_ignored_path(built_from_commit):
        built_from_commit = None
    return {"report_title": title, "built_from_commit": built_from_commit}


def graph_health(root: Path, *, index_state: str | None = None) -> dict[str, Any]:
    """Return cheap graph artifact health without invoking the graph engine or changing ranking."""
    out = artifact_dir(root)
    graph_p = graph_path(root)
    report_p = report_path(root)
    manifest_p = manifest_path(root)
    raw_graph = _load_json(graph_p) if graph_p.exists() else None
    raw_graph = raw_graph if isinstance(raw_graph, dict) else {}
    ignored_artifact_sources = sorted(
        {
            source
            for node in raw_graph.get("nodes") or []
            if (source := node_source_file(node)) and path_has_ignored_part(source)
        }
    )
    graph = _filtered_graph(raw_graph)
    manifest = _load_json(manifest_p) if manifest_p.exists() else None
    manifest_entries = manifest if isinstance(manifest, dict) else {}

    ignored_manifest_sources = sorted(
        rel_path for rel_path in manifest_entries if isinstance(rel_path, str) and path_has_ignored_part(rel_path)
    )
    missing_sources: list[str] = []
    stale_sources: list[str] = []
    for rel_path, info in manifest_entries.items():
        if not isinstance(rel_path, str) or rel_path.startswith(".mimry/"):
            continue
        if path_has_ignored_part(rel_path):
            continue
        source = root / rel_path
        if not source.exists():
            missing_sources.append(rel_path)
            continue
        if isinstance(info, dict):
            expected_hash = info.get("mimry_sha256")
            if isinstance(expected_hash, str) and expected_hash:
                if _safe_regular_sha256(source) != expected_hash:
                    stale_sources.append(rel_path)
                continue
            if isinstance(info.get("mtime"), int | float):
                try:
                    source_mtime = source.stat().st_mtime
                except OSError:
                    stale_sources.append(rel_path)
                    continue
                # Compatibility fallback for manifests generated before MIMRY
                # added content hashes. New manifests never depend on timestamp
                # precision for freshness.
                if abs(source_mtime - float(info["mtime"])) > 1e-6:
                    stale_sources.append(rel_path)

    graph_exists = graph_p.exists()
    report_exists = report_p.exists()
    manifest_exists = manifest_p.exists()
    artifact_missing = not graph_exists or not report_exists or not manifest_exists
    source_stale = bool(missing_sources or stale_sources)
    policy_stale = bool(ignored_artifact_sources or ignored_manifest_sources)
    possibly_stale = source_stale or policy_stale or index_state not in (None, "current")
    status = "missing" if artifact_missing else ("stale" if possibly_stale else "current")

    report_info = _report_freshness_lines(report_p)
    return {
        "status": status,
        "output_dir": str(out),
        "graph_path": str(graph_p),
        "graph_exists": graph_exists,
        "graph_generated_at": _iso_mtime(graph_p),
        "graph_nodes": len(graph.get("nodes") or []),
        "graph_edges": len(graph.get("links") or graph.get("edges") or []),
        "report_path": str(report_p),
        "report_exists": report_exists,
        "report_generated_at": _iso_mtime(report_p),
        "report_title": report_info["report_title"],
        "built_from_commit": report_info["built_from_commit"],
        "manifest_path": str(manifest_p),
        "manifest_exists": manifest_exists,
        "manifest_generated_at": _iso_mtime(manifest_p),
        "manifest_entries": len(manifest_entries),
        "source_stale": source_stale,
        "source_changed_files": stale_sources,
        "source_missing_files": missing_sources,
        # Report only a count. Status is an agent-facing output and must not
        # disclose paths that policy intentionally excludes from agent context.
        "policy_filtered_source_count": len(set(ignored_artifact_sources + ignored_manifest_sources)),
        "index_state": index_state,
        "possibly_stale": possibly_stale,
    }


def node_source_file(node: dict) -> str | None:
    src = node.get("source_file") or node.get("path")
    if isinstance(src, str) and src:
        return src
    return None


def _edge_endpoints(edge: dict) -> tuple[str | None, str | None]:
    source = edge.get("source") or edge.get("from")
    target = edge.get("target") or edge.get("to")
    return (str(source) if source else None, str(target) if target else None)


def _edge_relation(edge: dict) -> str:
    return str(edge.get("relation") or edge.get("type") or "relates")


def _node_text(node: dict) -> str:
    return " ".join(
        str(node.get(k, "")) for k in ("id", "label", "norm_label", "source_file", "path", "type", "kind", "file_type")
    ).lower()


def surface_matches(root: Path, query: str, limit: int = 5) -> list[dict]:
    """Resolve a file/symbol/query to graph nodes, using artifact text only."""
    g = load_graph(root)
    terms = query_terms(query)
    if not terms:
        return []
    matches = []
    for node in g.get("nodes") or []:
        text = _node_text(node)
        score = 0
        for term in terms:
            if term in text:
                score += 10
                if term in str(node.get("label", "")).lower():
                    score += 20
                if term in str(node.get("source_file") or node.get("path") or "").lower():
                    score += 15
                if term == str(node.get("id", "")).lower():
                    score += 25
        if score:
            matches.append(
                {
                    "id": str(node.get("id")),
                    "label": str(node.get("label") or node.get("id")),
                    "path": node_source_file(node) or "?",
                    "score": score,
                    "node": node,
                }
            )
    return sorted(matches, key=lambda m: (-m["score"], m["path"], m["label"]))[:limit]


def evidence_for_path(root: Path, surface: str, max_lines: int = 6) -> list[str]:
    """Return concise graph node/edge evidence for a file or symbol surface."""
    g = load_graph(root)
    nodes = g.get("nodes") or []
    links = g.get("links") or g.get("edges") or []
    surface_lower = surface.lower()
    matched_ids = set()
    lines = []
    for node in nodes:
        src = node_source_file(node) or ""
        label = str(node.get("label") or node.get("id"))
        if (
            surface_lower in src.lower()
            or surface_lower in label.lower()
            or surface_lower == str(node.get("id", "")).lower()
        ):
            matched_ids.add(str(node.get("id")))
            lines.append(f"- node `{label}` in `{src or '?'}`")
            if len(lines) >= max_lines:
                return lines
    id_to_node = {str(n.get("id")): n for n in nodes if n.get("id")}
    for edge in links:
        source, target = _edge_endpoints(edge)
        if source not in matched_ids and target not in matched_ids:
            continue
        s = id_to_node.get(source or "", {})
        t = id_to_node.get(target or "", {})
        lines.append(f"- edge `{s.get('label') or source}` --{_edge_relation(edge)}--> `{t.get('label') or target}`")
        if len(lines) >= max_lines:
            return lines
    return lines


def shortest_path(root: Path, source_query: str, target_query: str, max_hops: int = 6) -> dict:
    """Find a shortest relationship path in graph artifacts; do not infer missing edges."""
    g = load_graph(root)
    nodes = g.get("nodes") or []
    links = g.get("links") or g.get("edges") or []
    id_to_node = {str(n.get("id")): n for n in nodes if n.get("id")}
    source_matches = surface_matches(root, source_query, limit=5)
    target_matches = surface_matches(root, target_query, limit=5)
    if not source_matches or not target_matches:
        return {"found": False, "source_matches": source_matches, "target_matches": target_matches, "steps": []}

    target_ids = {m["id"] for m in target_matches}
    adjacency: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for edge in links:
        source, target = _edge_endpoints(edge)
        if not source or not target:
            continue
        adjacency[source].append((target, edge))
        adjacency[target].append((source, {**edge, "relation": "reverse " + _edge_relation(edge)}))

    for source in source_matches:
        queue = deque([(source["id"], [])])
        seen = {source["id"]}
        while queue:
            node_id, path_edges = queue.popleft()
            if node_id in target_ids:
                steps = []
                current = source["id"]
                for edge in path_edges:
                    edge_source, edge_target = _edge_endpoints(edge)
                    next_id = edge_target if edge_source == current else edge_source
                    steps.append(
                        {
                            "from": id_to_node.get(current, {"id": current}),
                            "edge": edge,
                            "to": id_to_node.get(next_id or "", {"id": next_id}),
                        }
                    )
                    current = next_id or current
                return {
                    "found": True,
                    "source_matches": source_matches,
                    "target_matches": target_matches,
                    "steps": steps,
                }
            if len(path_edges) >= max_hops:
                continue
            for next_id, edge in adjacency.get(node_id, []):
                if next_id in seen:
                    continue
                seen.add(next_id)
                queue.append((next_id, [*path_edges, edge]))
    return {"found": False, "source_matches": source_matches, "target_matches": target_matches, "steps": []}


# How far a query's evidence travels along relationships, and how fast it decays.
# Two hops covers "the page calls a hook that calls the payments module" without
# letting a hub node drag in the whole repository.
_EXPANSION_DECAY = (0.35, 0.15)


def _expand_along_edges(
    nodes: list[dict],
    links: list[dict],
    matched_scores: dict[str, int],
    by_file: dict[str, dict],
) -> None:
    """Carry query relevance from matched nodes to their neighbours.

    Scoring a node only on its own text is what a plain search index already does;
    it wins a multi-hop question only when the answer file happens to contain the
    query words. Following edges is the thing a graph is actually for.
    """
    if not matched_scores:
        return
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in links:
        source, target = _edge_endpoints(edge)
        if source and target:
            adjacency[source].add(target)
            adjacency[target].add(source)
    if not adjacency:
        return

    node_by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    reached: dict[str, int] = {}
    frontier = dict(matched_scores)
    for decay in _EXPANSION_DECAY:
        nxt: dict[str, int] = {}
        for node_id in sorted(frontier):
            carried = int(frontier[node_id] * decay)
            if carried <= 0:
                continue
            for neighbour in sorted(adjacency.get(node_id, ())):
                # Directly matched nodes keep their own score. Letting expansion
                # top them up as well was measured and scored worse overall
                # (ndcg 0.6845 vs 0.6907): it rewards whichever file has the most
                # neighbours rather than the one the query is actually about.
                if neighbour in matched_scores:
                    continue
                if carried > reached.get(neighbour, 0):
                    reached[neighbour] = carried
                    nxt[neighbour] = carried
        frontier = nxt
        if not frontier:
            break

    for node_id in sorted(reached):
        node = node_by_id.get(node_id)
        src = node_source_file(node) if node else None
        if not src:
            continue
        row = by_file.setdefault(
            src,
            {
                "path": src,
                "score": 0,
                "reasons": set(),
                "nodes": [],
                "max_degree": 0,
                "communities": set(),
                "topology_boost": 0,
                "topology_nodes": 0,
            },
        )
        row["score"] += reached[node_id]
        row["reasons"].add("reached by MIMRY graph relationship")


def graph_rows(root: Path, query: str, limit: int = 10) -> list[dict]:
    g = load_graph(root)
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

    matched_scores: dict[str, int] = {}
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
                    reasons.append("graph node label match")
                if term in src.lower():
                    score += 35
                    reasons.append("graph source file match")
                if term in str(n.get("id", "")).lower():
                    score += 10
                    reasons.append("graph node id match")
        if not score:
            continue
        deg = degree.get(str(n.get("id")), 0)
        if deg:
            score += min(25, deg * 5)
        if n.get("community") is not None:
            score += 5
        row = by_file.setdefault(
            src,
            {
                "path": src,
                "score": 0,
                "reasons": set(),
                "nodes": [],
                "max_degree": 0,
                "communities": set(),
                "topology_boost": 0,
                "topology_nodes": 0,
            },
        )
        row["score"] += score
        row["reasons"].update(reasons)
        row["nodes"].append(str(n.get("label") or n.get("id")))
        row["max_degree"] = max(row["max_degree"], deg)
        topology_boost = min(25, deg * 5) + (5 if n.get("community") is not None else 0)
        if topology_boost:
            row["topology_boost"] += topology_boost
            row["topology_nodes"] += 1
        if n.get("community") is not None:
            row["communities"].add(str(n["community"]))
        matched_scores[str(n.get("id"))] = max(matched_scores.get(str(n.get("id")), 0), score)

    _expand_along_edges(nodes, links, matched_scores, by_file)

    rows = []
    for row in by_file.values():
        row["score"], intent_reasons = apply_intent_adjustment(row["score"], row["path"], terms, graph=True)
        if row["score"] <= 0:
            continue
        row["reasons"].update(intent_reasons)
        topology_details = []
        if row["max_degree"]:
            topology_details.append(f"max degree {row['max_degree']}")
        if row["communities"]:
            topology_details.append(f"{len(row['communities'])} communities")
        if row["topology_boost"]:
            detail = f" ({'; '.join(topology_details)})" if topology_details else ""
            row["reasons"].add(
                f"graph topology boost {row['topology_boost']} across {row['topology_nodes']} matched nodes{detail}"
            )
        node_preview = ", ".join(row["nodes"][:4])
        reason = ", ".join(sorted(row["reasons"]))
        if node_preview:
            reason += f"; nodes: {node_preview}"
        rows.append({"path": row["path"], "score": row["score"], "reason": reason, "source": "graph"})
    return sorted(rows, key=lambda r: (-r["score"], r["path"]))[:limit]


def relationship_lines(root: Path, selected_paths: list[str], max_lines: int = 12) -> list[str]:
    g = load_graph(root)
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
        line = f"- `{src_label}` --{rel}--> `{tgt_label}` ({src_file} -> {tgt_file})"
        if conf:
            line += f" [{conf}]"
        lines.append(line)
        if len(lines) >= max_lines:
            return lines
    return lines


def report_excerpt(root: Path, max_chars: int = 1200) -> str:
    path = report_path(root)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if text_mentions_ignored_path(text):
        # Reports can quote source snippets. If a legacy report references a
        # newly ignored tree, suppress it as a unit instead of guessing which
        # adjacent lines came from that source.
        return ""
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
