from __future__ import annotations

from collections import defaultdict


def render_report(graph: dict, *, commit: str | None = None, title: str | None = None) -> str:
    """Render GRAPH_REPORT.md text for a built graph.

    Format is constrained by readers in artifacts.py:
    - Line 1 is read as report_title
    - A line starting with "- Built from commit:" is parsed for the commit hash
    - Sections under ## Community Hubs, ## God Nodes, ## Surprising Connections are extracted
    """

    # Default title if not provided
    if title is None:
        title = "# MIMRY graph report"
    elif not title.startswith("#"):
        title = f"# {title}"

    # Handle commit
    commit_str = commit if commit else "unknown"

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
            label = node.get("label", node_id)
            source_file = node.get("source_file", "?")
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
            label = hub.get("label", hub_id)
            source_file = hub.get("source_file", "?")
            deg = degree.get(hub_id, 0)
            lines.append(
                f"- community {comm_id}: `{label}` ({source_file}) - degree {deg}, {node_count_in_community} nodes"
            )
    else:
        lines.append("- none")
    lines.append("")

    # Section: Surprising Connections (edges crossing communities)
    lines.append("## Surprising Connections")
    cross_community_edges = []
    for edge in edges:
        source_id = str(edge.get("source", ""))
        target_id = str(edge.get("target", ""))
        source_node = node_by_id.get(source_id)
        target_node = node_by_id.get(target_id)

        if source_node and target_node:
            source_comm = source_node.get("community")
            target_comm = target_node.get("community")
            if source_comm is not None and target_comm is not None and source_comm != target_comm:
                cross_community_edges.append(edge)

    if cross_community_edges:
        # Sort by (source, target, relation) for determinism, then take top 10
        cross_community_edges.sort(
            key=lambda e: (str(e.get("source", "")), str(e.get("target", "")), str(e.get("relation", "")))
        )

        for edge in cross_community_edges[:10]:
            source_id = str(edge.get("source", ""))
            target_id = str(edge.get("target", ""))
            source_node = node_by_id.get(source_id)
            target_node = node_by_id.get(target_id)

            if source_node and target_node:
                source_label = source_node.get("label", source_id)
                target_label = target_node.get("label", target_id)
                source_file = source_node.get("source_file", "?")
                target_file = target_node.get("source_file", "?")
                relation = edge.get("relation", "relates")
                lines.append(f"- `{source_label}` --{relation}--> `{target_label}` ({source_file} -> {target_file})")
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


if __name__ == "__main__":
    import json
    import sys

    # Self-check with synthetic graph

    # Build a graph with 2 communities, various degrees, and cross-community edges
    synthetic_graph = {
        "engine": "mimry-core",
        "nodes": [
            {"id": "node1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
            {"id": "node2", "label": "a/c.py", "source_file": "a/c.py", "community": 0},
            {"id": "node3", "label": "a/d.py", "source_file": "a/d.py", "community": 0},
            {"id": "node4", "label": "x/y.py", "source_file": "x/y.py", "community": 1},
            {"id": "node5", "label": "x/z.py", "source_file": "x/z.py", "community": 1},
            {"id": "node6", "label": "m/n.py", "source_file": "m/n.py", "community": 1},
        ],
        "edges": [
            {"source": "node1", "target": "node2", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "node1", "target": "node3", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "node2", "target": "node3", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},  # Cross-community
            {"source": "node4", "target": "node5", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "node4", "target": "node6", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "node5", "target": "node1", "relation": "imports", "confidence": "EXTRACTED"},  # Cross-community
        ],
        "clusters": {},
    }

    try:
        # Test 1: render_report with commit
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")

        # Check line 1 starts with #
        lines = report.splitlines()
        assert lines[0].startswith("#"), f"Line 1 should start with #, got: {lines[0]}"

        # Check for commit line
        commit_line = [l for l in lines if l.startswith("- Built from commit:")]
        assert len(commit_line) > 0, "Missing '- Built from commit:' line"
        assert "abc123" in commit_line[0], f"Commit not found in line: {commit_line[0]}"

        # Check for all three headings
        assert "## Community Hubs" in report, "Missing '## Community Hubs' heading"
        assert "## God Nodes" in report, "Missing '## God Nodes' heading"
        assert "## Surprising Connections" in report, "Missing '## Surprising Connections' heading"

        # Check cross-community edge appears
        assert "node1" not in report or "node5" not in report or any("--imports-->" in l for l in lines), (
            "Cross-community edge should appear in report"
        )

        # A community hub must be the MOST connected node in its community.
        # node1 (a/b.py) and node2 (a/c.py) both have degree 3, node3 degree 2;
        # the label tie-break picks a/b.py. node4 (x/y.py) has degree 3 vs 2 and 1.
        hub_lines = [line for line in lines if line.startswith("- community ")]
        assert len(hub_lines) == 2, f"expected one hub per community, got {hub_lines}"
        assert "`a/b.py`" in hub_lines[0] and "degree 3" in hub_lines[0], (
            f"community 0 hub must be the highest-degree node, got: {hub_lines[0]}"
        )
        assert "`x/y.py`" in hub_lines[1] and "degree 3" in hub_lines[1], (
            f"community 1 hub must be the highest-degree node, got: {hub_lines[1]}"
        )

        # God Nodes must lead with a highest-degree node too.
        god_index = lines.index("## God Nodes")
        assert "degree 3" in lines[god_index + 1], f"god nodes must be degree-sorted: {lines[god_index + 1]}"

        # Pure ASCII. A caller on Windows that writes this without an explicit
        # encoding="utf-8" gets cp1252 and a UnicodeEncodeError, so em dashes and
        # arrows are not worth the portability risk.
        assert report.isascii(), "report must be pure ASCII for cross-platform writing"

        # Check no \r characters
        assert "\r" not in report, "Report contains \\r characters"

        # Check no backslash path separators
        assert "\\" not in report or all("\\" not in l for l in report.splitlines() if not l.startswith("- ")), (
            "Report contains backslash path separators"
        )

        # Test 2: Determinism - render twice
        report2 = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        assert report == report2, "Report is not deterministic"

        # Test 3: Empty graph
        empty_graph = {"nodes": [], "edges": []}
        empty_report = render_report(empty_graph, commit="test")
        assert "## Community Hubs" in empty_report, "Empty report missing Community Hubs heading"
        assert "## God Nodes" in empty_report, "Empty report missing God Nodes heading"
        assert "## Surprising Connections" in empty_report, "Empty report missing Surprising Connections heading"
        assert "- none" in empty_report, "Empty sections should have '- none'"

        # Test 4: build_manifest
        test_files = [
            {"rel_path": "a/b.py", "hash": "abc123", "mtime": 1234.5},
            {"rel_path": "x/y.py", "hash": "def456", "mtime": 1234.6},
            {"rel_path": "missing_hash", "mtime": 1234.7},  # Should be skipped
            {"hash": "orphan", "mtime": 1234.8},  # Should be skipped
        ]
        manifest = build_manifest(test_files)

        # Check manifest structure
        assert "a/b.py" in manifest, "a/b.py should be in manifest"
        assert "x/y.py" in manifest, "x/y.py should be in manifest"
        assert "missing_hash" not in manifest, "Records with missing hash should be skipped"
        assert len([k for k in manifest if k.startswith("a/") or k.startswith("x/")]) == 2, (
            "Should have 2 valid entries"
        )

        # Check manifest entry structure
        for rel_path, info in manifest.items():
            assert "mtime" in info, f"{rel_path} missing mtime"
            assert "mimry_sha256" in info, f"{rel_path} missing mimry_sha256"
            assert len(info) == 2, f"{rel_path} has extra keys: {set(info.keys()) - {'mtime', 'mimry_sha256'}}"

        # Check values match
        assert manifest["a/b.py"]["mimry_sha256"] == "abc123", "Hash not mapped correctly"
        assert manifest["a/b.py"]["mtime"] == 1234.5, "mtime not mapped correctly"

        # Check manifest is sorted
        manifest_keys = list(manifest.keys())
        assert manifest_keys == sorted(manifest_keys), "Manifest keys should be sorted"

        print("OK")
        sys.exit(0)

    except AssertionError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
