from __future__ import annotations

import re
from collections import defaultdict

from ..intent import is_test_file
from ..security import markdown_inline
from . import languages

_CODE_EXTENSIONS = frozenset(ext.lstrip(".") for ext in languages.EXTENSION_LANGUAGE) | {
    "py",
    "js",
    "jsx",
    "ts",
    "tsx",
}
_DOC_EXTENSIONS = frozenset(["md", "mdx", "txt", "rst"])
_DATA_EXTENSIONS = frozenset(["sql", "json", "yaml", "yml", "toml", "ini", "cfg"])


def _file_category(path: str) -> str:
    """Bucket a path so code->doc edges can outrank code->code ones."""
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


def _safe_for_shell(raw_label: str) -> bool:
    """Check if raw label is safe to paste in a shell command."""
    return bool(re.match(r"^[\w./:-]+$", raw_label))


def _hub(nodes_in_community: list, degree: dict) -> dict | None:
    """Return highest-degree node in a community."""
    if not nodes_in_community:
        return None
    return min(
        nodes_in_community,
        key=lambda n: (
            -degree.get(str(n.get("id", "")), 0),
            str(n.get("label", n.get("id", ""))),
            str(n.get("id", "")),
        ),
    )


def _build_suggested_questions(
    god_nodes: list,
    scored_edges: list,
    nodes: list,
    communities: set,
    edges: list,
    degree: dict,
    node_by_id: dict,
) -> list[str]:
    """Build suggested questions, up to 5 per template."""
    questions: list[tuple[int, str]] = []

    # Template 1: Top non-test god node
    non_test = [n for n in god_nodes if not is_test_file(str(n.get("source_file", "")))]
    if non_test:
        top = non_test[0]
        raw_label = str(top.get("label", top.get("id", "")))
        esc_label = markdown_inline(raw_label)
        if _safe_for_shell(raw_label):
            q = (
                f"- What depends on `{esc_label}`, and what would a change "
                f'to it break? -> `mimry related "{raw_label}"`'
            )
        else:
            q = f"- What depends on `{esc_label}`, and what would a change to it break?"
        questions.append((1, q))

    # Template 2: Top surprising connection (skip test files)
    for _score, _reasons, edge, source_n, target_n in scored_edges:
        if is_test_file(str(source_n.get("source_file", ""))) or is_test_file(
            str(target_n.get("source_file", ""))
        ):
            continue
        raw_source = str(source_n.get("label", edge.get("source", "")))
        raw_target = str(target_n.get("label", edge.get("target", "")))
        esc_source = markdown_inline(raw_source)
        esc_target = markdown_inline(raw_target)
        if _safe_for_shell(raw_source) and _safe_for_shell(raw_target):
            q = (
                f"- Why does `{esc_source}` reach `{esc_target}` across "
                f'communities? -> `mimry path "{raw_source}" "{raw_target}"`'
            )
        else:
            q = f"- Why does `{esc_source}` reach `{esc_target}` across communities?"
        questions.append((2, q))
        break

    # Template 3: Community pair with most cross-community edges
    # (skip test files)
    if nodes and communities and len(communities) > 1:
        cross_pairs = defaultdict(int)
        for edge in edges:
            source_id = str(edge.get("source", ""))
            target_id = str(edge.get("target", ""))
            source_n = node_by_id.get(source_id)
            target_n = node_by_id.get(target_id)
            if not source_n or not target_n:
                continue
            if is_test_file(str(source_n.get("source_file", ""))) or is_test_file(
                str(target_n.get("source_file", ""))
            ):
                continue
            source_comm = source_n.get("community")
            target_comm = target_n.get("community")
            if source_comm is not None and target_comm is not None and source_comm != target_comm:
                pair = tuple(sorted([source_comm, target_comm]))
                cross_pairs[pair] += 1

        if cross_pairs:
            top_pair = max(cross_pairs.items(), key=lambda x: (x[1], tuple(sorted(x[0]))))
            comm_a, comm_b = top_pair[0]
            nodes_by_community = defaultdict(list)
            for node in nodes:
                comm = node.get("community")
                if comm is not None:
                    nodes_by_community[comm].append(node)

            hub_a = _hub(nodes_by_community[comm_a], degree)
            hub_b = _hub(nodes_by_community[comm_b], degree)

            if hub_a and hub_b:
                raw_a = str(hub_a.get("label", hub_a.get("id", "")))
                raw_b = str(hub_b.get("label", hub_b.get("id", "")))
                esc_a = markdown_inline(raw_a)
                esc_b = markdown_inline(raw_b)
                if _safe_for_shell(raw_a) and _safe_for_shell(raw_b):
                    q = (
                        f"- How do the `{esc_a}` and `{esc_b}` areas depend "
                        f'on each other? -> `mimry path "{raw_a}" "{raw_b}"`'
                    )
                else:
                    q = f"- How do the `{esc_a}` and `{esc_b}` areas depend on each other?"
                questions.append((3, q))

    # Template 4: Inferred calls/inherits with highest combined degree
    valid_inferred = [
        (
            edge,
            node_by_id.get(str(edge.get("source", ""))),
            node_by_id.get(str(edge.get("target", ""))),
        )
        for edge in edges
        if edge.get("confidence") == "INFERRED"
        and edge.get("relation", "").lower() in {"calls", "inherits"}
    ]
    valid_inferred = [x for x in valid_inferred if x[1] is not None and x[2] is not None]
    if valid_inferred:

        def _score(x):
            n1 = degree.get(str(x[1].get("id", "")), 0)
            n2 = degree.get(str(x[2].get("id", "")), 0)
            return (n1 + n2, str(x[1].get("id", "")), str(x[2].get("id", "")))

        best = max(valid_inferred, key=_score)
        edge, src_n, tgt_n = best
        raw_src = str(src_n.get("label", edge.get("source", "")))
        raw_tgt = str(tgt_n.get("label", edge.get("target", "")))
        esc_src = markdown_inline(raw_src)
        esc_tgt = markdown_inline(raw_tgt)
        rel = markdown_inline(edge.get("relation", "relates"))
        if _safe_for_shell(raw_src) and _safe_for_shell(raw_tgt):
            q = (
                f"- Is the inferred link `{esc_src}` --{rel}--> "
                f"`{esc_tgt}` real? -> "
                f'`mimry why "{raw_tgt}" --query "{raw_src}"`'
            )
        else:
            q = f"- Is the inferred link `{esc_src}` --{rel}--> `{esc_tgt}` real?"
        questions.append((4, q))

    # Template 5: CODE file node with most defines, no incoming
    # imports/calls/references/inherits edges
    targets_with_incoming = set()
    for edge in edges:
        if edge.get("relation", "").lower() in {"imports", "calls", "references", "inherits"}:
            targets_with_incoming.add(str(edge.get("target", "")))

    # Precompute defines count in one pass
    defines_by_node = defaultdict(int)
    for edge in edges:
        if edge.get("relation") == "defines":
            defines_by_node[str(edge.get("source", ""))] += 1

    file_candidates = []
    for node in nodes:
        # Only consider file nodes of code category
        if node.get("type") != "file":
            continue
        source_file = node.get("source_file", "")
        if not source_file or is_test_file(source_file):
            continue
        if _file_category(source_file) != "code":
            continue
        node_id = str(node.get("id", ""))
        if node_id in targets_with_incoming:
            continue
        defines = defines_by_node.get(node_id, 0)
        # Require at least one defines edge
        if defines == 0:
            continue
        file_candidates.append((node, defines, node_id))

    if file_candidates:
        top_file = max(file_candidates, key=lambda x: (x[1], x[0].get("label", ""), x[2]))
        raw_file = str(top_file[0].get("label", top_file[2]))
        esc_file = markdown_inline(raw_file)
        if _safe_for_shell(raw_file):
            q = (
                f"- Is `{esc_file}` still used? Nothing in the graph "
                f'points at it. -> `mimry related "{raw_file}"`'
            )
        else:
            q = f"- Is `{esc_file}` still used? Nothing in the graph points at it."
        questions.append((5, q))

    questions.sort(key=lambda x: x[0])
    return [q for _, q in questions]


def _surprise_score(
    edge: dict, source_node: dict, target_node: dict, degree: dict
) -> tuple[int, list[str]]:
    """Rank a cross-community edge by how non-obvious it is.

    Every candidate already crosses a community boundary, so that fact
    earns no points here -- it is the filter, not a discriminator.
    Scoring is pure integer arithmetic on graph facts, so it stays
    deterministic across runs and platforms.
    """
    score = 0
    reasons: list[str] = []

    # Resolved rather than stated: the reader cannot verify it by
    # reading one line.
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
    - A line starting with "- Built from commit:" is parsed for the
      commit hash
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

    # Count inferred edges
    inferred_count = sum(1 for edge in edges if edge.get("confidence") == "INFERRED")

    # Precompute god_nodes for use in suggested questions
    god_nodes = []
    if nodes and degree:
        god_nodes = sorted(
            nodes,
            key=lambda n: (
                -degree.get(str(n.get("id")), 0),
                str(n.get("label", n.get("id", ""))),
                str(n.get("id", "")),
            ),
        )[:10]

    # Start building report
    lines = [title, ""]
    lines.append(f"- Built from commit: {commit_str}")
    lines.append(f"- Nodes: {node_count}")
    lines.append(f"- Edges: {edge_count}")
    inferred_msg = f"- Inferred edges: {inferred_count} of {edge_count}"
    lines.append(inferred_msg)
    lines.append(f"- Communities: {community_count}")
    lines.append("")

    # Section: God Nodes (top 10 by degree)
    lines.append("## God Nodes")
    if god_nodes:
        for node in god_nodes:
            node_id = str(node.get("id", "?"))
            label = markdown_inline(node.get("label", node_id))
            source_file = markdown_inline(node.get("source_file", "?"))
            deg = degree.get(node_id, 0)
            lines.append(f"- `{label}` ({source_file}) - degree {deg}")
    else:
        lines.append("- none")
    lines.append("")

    # Section: Community Hubs (highest-degree node per community, top 10
    # communities by size)
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
            hub = _hub(comm_nodes, degree)
            if hub:
                community_hubs.append((comm_id, hub, len(comm_nodes)))

        # Limit to top 10 communities by size
        community_hubs.sort(key=lambda x: -x[2])  # Sort by node count descending
        community_hubs = sorted(
            community_hubs[:10], key=lambda x: x[0]
        )  # Re-sort by community id ascending

        for comm_id, hub, node_count_in_community in community_hubs:
            hub_id = str(hub.get("id", "?"))
            label = markdown_inline(hub.get("label", hub_id))
            source_file = markdown_inline(hub.get("source_file", "?"))
            deg = degree.get(hub_id, 0)
            lines.append(
                f"- community {markdown_inline(comm_id)}: `{label}` ({source_file}) - degree"
                f" {deg}, {node_count_in_community} nodes"
            )
    else:
        lines.append("- none")
    lines.append("")

    # Section: Surprising Connections (edges crossing communities,
    # ranked)
    #
    # Sorting these alphabetically returned an arbitrary 10 of however
    # many cross the boundary, not the 10 worth reading. Rank by
    # surprise instead, and say why.
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
        # Highest score first; (source, target, relation) breaks every
        # tie, so the ordering is total and the file stays
        # byte-identical across rebuilds.
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
            # reasons are built from fixed strings and category names,
            # never from node text, so the only untrusted values on this
            # line are the five above.
            why = "; ".join(reasons) if reasons else "crosses a community boundary"
            lines.append(
                f"- `{source_label}` --{relation}--> `{target_label}` "
                f"({source_file} -> {target_file}) - score {score}: {why}"
            )
    else:
        lines.append("- none")
    lines.append("")

    # Section: Suggested Questions
    lines.append("## Suggested Questions")
    questions = _build_suggested_questions(
        god_nodes, scored_edges, nodes, communities, edges, degree, node_by_id
    )
    if questions:
        lines.extend(questions)
    else:
        lines.append("- none")

    # Join with \n only (no \r\n on Windows)
    result = "\n".join(lines) + "\n"
    return result


def build_manifest(files: list[dict]) -> dict:
    """Return {rel_path: {"mtime": float, "mimry_sha256": str}} for
    indexed files.

    Reshapes file records from scanner output. Skips any record missing
    rel_path or hash. Keys are sorted for deterministic serialization.
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
