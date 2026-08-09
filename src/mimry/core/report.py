from __future__ import annotations

from collections import defaultdict

from ..security import markdown_inline

_CODE_EXTENSIONS = frozenset(["py", "js", "jsx", "ts", "tsx", "go", "rs", "cs", "java", "php"])
_DOC_EXTENSIONS = frozenset(["md", "mdx", "txt", "rst"])
_DATA_EXTENSIONS = frozenset(["sql", "json", "yaml", "yml", "toml", "ini", "cfg"])


def _file_category(path: str) -> str:
    """Bucket a path so a code->doc edge can outrank a code->code one."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in _CODE_EXTENSIONS:
        return "code"
    if ext in _DOC_EXTENSIONS:
        return "doc"
    if ext in _DATA_EXTENSIONS:
        return "data"
    return "other"


def _top_level_dir(path: str) -> str:
    return path.split("/")[0] if "/" in path else path


def _surprise_score(edge: dict, source_node: dict, target_node: dict, degree: dict) -> tuple[int, list[str]]:
    """Rank a cross-community edge by how non-obvious it is.

    Every candidate already crosses a community boundary, so that fact earns no
    points here -- it is the filter, not a discriminator. Scoring is pure integer
    arithmetic on graph facts, so it stays deterministic across runs and platforms.
    """
    score = 0
    reasons: list[str] = []

    # Resolved rather than stated: the reader cannot verify it by reading one line.
    if edge.get("confidence") == "INFERRED":
        score += 2
        reasons.append("inferred, not stated in source")
    else:
        score += 1

    source_file = source_node.get("source_file") or ""
    target_file = target_node.get("source_file") or ""

    source_category = _file_category(source_file)
    target_category = _file_category(target_file)
    if source_category != target_category:
        score += 2
        reasons.append(f"crosses file types ({source_category} <-> {target_category})")

    if _top_level_dir(source_file) != _top_level_dir(target_file):
        score += 2
        reasons.append("crosses top-level directories")

    source_degree = degree.get(str(source_node.get("id", "")), 0)
    target_degree = degree.get(str(target_node.get("id", "")), 0)
    if min(source_degree, target_degree) <= 2 and max(source_degree, target_degree) >= 5:
        score += 1
        reasons.append("peripheral node reaches a hub")

    return score, reasons


def render_report(graph: dict, *, commit: str | None = None, title: str | None = None) -> str:
    """Render GRAPH_REPORT.md text for a built graph.

    Format is constrained by readers in artifacts.py:
    - Line 1 is read as report_title
    - A line starting with "- Built from commit:" is parsed for the commit hash
    - Sections under ## Community Hubs, ## God Nodes, ## Surprising Connections are extracted
    """

    # Default title if not provided
    if title is None:
        title = "MIMRY graph report"
    else:
        title = title.lstrip("# ")
    title = f"# {markdown_inline(title)}"

    # Handle commit
    commit_str = markdown_inline(commit if commit else "unknown")

    # Extract nodes and edges
    nodes = graph.get("nodes") or []
    edge_key = "links" if "links" in graph else "edges"
    edges = graph.get(edge_key) or []

    # Count nodes and edges
    node_count = len(nodes)
    edge_count = len(edges)

    # Count communities
    communities = set()
    for node in nodes:
        if node.get("community") is not None:
            communities.add(node["community"])
    community_count = len(communities)

    # Build node lookup
    node_by_id = {str(node.get("id")): node for node in nodes if node.get("id")}

    # Calculate degree for each node
    degree = defaultdict(int)
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source:
            degree[str(source)] += 1
        if target:
            degree[str(target)] += 1

    # Start building report
    lines = [title, ""]
    lines.append(f"- Built from commit: {commit_str}")
    lines.append(f"- Nodes: {node_count}")
    lines.append(f"- Edges: {edge_count}")
    lines.append(f"- Communities: {community_count}")
    lines.append("")

    # Section: God Nodes (top 10 by degree)
    lines.append("## God Nodes")
    if nodes and degree:
        # Sort by degree (descending), then by label, then by id
        god_nodes = sorted(
            nodes,
            key=lambda n: (
                -degree.get(str(n.get("id")), 0),
                str(n.get("label", n.get("id", ""))),
                str(n.get("id", "")),
            ),
        )[:10]
        for node in god_nodes:
            node_id = str(node.get("id", "?"))
            label = markdown_inline(node.get("label", node_id))
            source_file = markdown_inline(node.get("source_file", "?"))
            deg = degree.get(node_id, 0)
            lines.append(f"- `{label}` ({source_file}) - degree {deg}")
    else:
        lines.append("- none")
    lines.append("")

    # Section: Community Hubs (highest-degree node per community, top 10 communities by size)
    lines.append("## Community Hubs")
    if nodes and communities:
        # Group nodes by community
        nodes_by_community = defaultdict(list)
        for node in nodes:
            comm = node.get("community")
            if comm is not None:
                nodes_by_community[comm].append(node)

        # For each community, find the highest-degree node
        community_hubs = []
        for comm_id in sorted(communities):
            comm_nodes = nodes_by_community[comm_id]
            if not comm_nodes:
                continue
            # min, not max: the key negates degree so ascending order puts the
            # highest-degree node first. max() here would select the least
            # connected node in the community, which is the opposite of a hub.
            hub = min(
                comm_nodes,
                key=lambda n: (
                    -degree.get(str(n.get("id", "")), 0),
                    str(n.get("label", n.get("id", ""))),
                    str(n.get("id", "")),
                ),
            )
            community_hubs.append((comm_id, hub, len(comm_nodes)))

        # Limit to top 10 communities by size
        community_hubs.sort(key=lambda x: -x[2])  # Sort by node count descending
        community_hubs = sorted(community_hubs[:10], key=lambda x: x[0])  # Re-sort by community id ascending

        for comm_id, hub, node_count_in_community in community_hubs:
            hub_id = str(hub.get("id", "?"))
            label = markdown_inline(hub.get("label", hub_id))
            source_file = markdown_inline(hub.get("source_file", "?"))
            deg = degree.get(hub_id, 0)
            lines.append(
                f"- community {markdown_inline(comm_id)}: `{label}` ({source_file}) - degree {deg}, {node_count_in_community} nodes"
            )
    else:
        lines.append("- none")
    lines.append("")

    # Section: Surprising Connections (edges crossing communities, ranked)
    #
    # Sorting these alphabetically returned an arbitrary 10 of however many cross
    # the boundary, not the 10 worth reading. Rank by surprise instead, and say why.
    lines.append("## Surprising Connections")
    scored_edges = []
    for edge in edges:
        source_id = str(edge.get("source", ""))
        target_id = str(edge.get("target", ""))
        source_node = node_by_id.get(source_id)
        target_node = node_by_id.get(target_id)
        if not source_node or not target_node:
            continue

        source_comm = source_node.get("community")
        target_comm = target_node.get("community")
        if source_comm is None or target_comm is None or source_comm == target_comm:
            continue

        score, reasons = _surprise_score(edge, source_node, target_node, degree)
        scored_edges.append((score, reasons, edge, source_node, target_node))

    if scored_edges:
        # Highest score first; (source, target, relation) breaks every tie, so the
        # ordering is total and the file stays byte-identical across rebuilds.
        scored_edges.sort(
            key=lambda item: (
                -item[0],
                str(item[2].get("source", "")),
                str(item[2].get("target", "")),
                str(item[2].get("relation", "")),
            )
        )

        for score, reasons, edge, source_node, target_node in scored_edges[:10]:
            source_label = markdown_inline(source_node.get("label", edge.get("source")))
            target_label = markdown_inline(target_node.get("label", edge.get("target")))
            source_file = markdown_inline(source_node.get("source_file", "?"))
            target_file = markdown_inline(target_node.get("source_file", "?"))
            relation = markdown_inline(edge.get("relation", "relates"))
            # reasons are built from fixed strings and category names, never from
            # node text, so the only untrusted values on this line are the five above.
            why = "; ".join(reasons) if reasons else "crosses a community boundary"
            lines.append(
                f"- `{source_label}` --{relation}--> `{target_label}` "
                f"({source_file} -> {target_file}) - score {score}: {why}"
            )
    else:
        lines.append("- none")

    # Join with \n only (no \r\n on Windows)
    result = "\n".join(lines) + "\n"
    return result


def build_manifest(files: list[dict]) -> dict:
    """Return {rel_path: {"mtime": float, "mimry_sha256": str}} for indexed files.

    Reshapes file records from scanner output. Skips any record missing rel_path or hash.
    Keys are sorted for deterministic serialization.
    """
    manifest = {}

    for record in files:
        # Skip records missing required fields
        rel_path = record.get("rel_path")
        file_hash = record.get("hash")

        if not rel_path or not file_hash:
            continue

        # Skip if mtime is missing
        mtime = record.get("mtime")
        if mtime is None:
            continue

        manifest[rel_path] = {"mtime": float(mtime), "mimry_sha256": file_hash}

    # Sort keys for deterministic output
    return dict(sorted(manifest.items()))
