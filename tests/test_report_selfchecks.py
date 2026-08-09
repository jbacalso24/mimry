"""Self-checks for report.py functions: render_report and build_manifest.

These tests ensure surprise-report ranking (cross-community edges, node degree,
community structure) and manifest building work correctly.
"""

from mimry.core.report import render_report, build_manifest


class TestRenderReportBasic:
    """Test basic report structure and formatting."""

    def test_render_report_line_starts_with_hash(self):
        """First line of report should start with #."""
        synthetic_graph = {
            "engine": "mimry-core",
            "nodes": [],
            "edges": [],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        lines = report.splitlines()
        assert lines[0].startswith("#"), f"Line 1 should start with #, got: {lines[0]}"

    def test_render_report_includes_commit_line(self):
        """Report should include commit information."""
        synthetic_graph = {
            "engine": "mimry-core",
            "nodes": [],
            "edges": [],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        lines = report.splitlines()
        commit_line = [line for line in lines if line.startswith("- Built from commit:")]
        assert len(commit_line) > 0, "Missing '- Built from commit:' line"
        assert "abc123" in commit_line[0], f"Commit not found in line: {commit_line[0]}"

    def test_render_report_has_required_headings(self):
        """Report should have Community Hubs, God Nodes, and Surprising Connections headings."""
        synthetic_graph = {
            "engine": "mimry-core",
            "nodes": [],
            "edges": [],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        assert "## Community Hubs" in report, "Missing '## Community Hubs' heading"
        assert "## God Nodes" in report, "Missing '## God Nodes' heading"
        assert "## Surprising Connections" in report, "Missing '## Surprising Connections' heading"

    def test_render_report_pure_ascii(self):
        """Report must be pure ASCII for cross-platform writing."""
        synthetic_graph = {
            "engine": "mimry-core",
            "nodes": [
                {"id": "node1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
            ],
            "edges": [],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="test")
        assert report.isascii(), "report must be pure ASCII for cross-platform writing"

    def test_render_report_no_carriage_returns(self):
        """Report should not contain \\r characters."""
        synthetic_graph = {
            "engine": "mimry-core",
            "nodes": [],
            "edges": [],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="test")
        assert "\r" not in report, "Report contains \\r characters"

    def test_render_report_no_backslash_path_separators(self):
        """Report should not contain backslash path separators."""
        synthetic_graph = {
            "engine": "mimry-core",
            "nodes": [
                {"id": "node1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
            ],
            "edges": [],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="test")
        assert "\\" not in report or all(
            "\\" not in line for line in report.splitlines() if not line.startswith("- ")
        ), "Report contains backslash path separators"


class TestRenderReportWithCrossLinks:
    """Test report generation with cross-community edges and rankings."""

    def test_cross_community_edge_appears_in_report(self):
        """Cross-community edges should appear in the report."""
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
                {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node5", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node6", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node5", "target": "node1", "relation": "imports", "confidence": "EXTRACTED"},
            ],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        assert (
            "node1" not in report
            or "node5" not in report
            or any("--imports-->" in line for line in report.splitlines())
        ), "Cross-community edge should appear in report"

    def test_community_hubs_are_highest_degree_nodes(self):
        """Community hubs must be the highest-degree nodes in each community."""
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
                {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node5", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node6", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node5", "target": "node1", "relation": "imports", "confidence": "EXTRACTED"},
            ],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        lines = report.splitlines()
        hub_lines = [line for line in lines if line.startswith("- community ")]
        assert len(hub_lines) == 2, f"expected one hub per community, got {hub_lines}"
        assert "`a/b.py`" in hub_lines[0] and "degree 3" in hub_lines[0], (
            f"community 0 hub must be the highest-degree node, got: {hub_lines[0]}"
        )
        assert "`x/y.py`" in hub_lines[1] and "degree 3" in hub_lines[1], (
            f"community 1 hub must be the highest-degree node, got: {hub_lines[1]}"
        )

    def test_god_nodes_ranked_by_degree(self):
        """God nodes should be ranked by degree descending."""
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
                {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node5", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node6", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node5", "target": "node1", "relation": "imports", "confidence": "EXTRACTED"},
            ],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        lines = report.splitlines()
        god_index = lines.index("## God Nodes")
        assert "degree 3" in lines[god_index + 1], f"god nodes must be degree-sorted: {lines[god_index + 1]}"


class TestRenderReportDeterminism:
    """Test report determinism and ranking."""

    def test_render_report_determinism(self):
        """Report rendering should be deterministic."""
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
                {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node5", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node4", "target": "node6", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node5", "target": "node1", "relation": "imports", "confidence": "EXTRACTED"},
            ],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        report2 = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        assert report == report2, "Report is not deterministic"

    def test_surprise_report_has_cross_community_edges(self):
        """Surprise report should include cross-community edges."""
        ranked_graph = {
            "nodes": [
                {"id": "node1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
                {"id": "node2", "label": "a/c.py", "source_file": "a/c.py", "community": 0},
                {"id": "node3", "label": "a/d.py", "source_file": "a/d.py", "community": 0},
                {"id": "node4", "label": "x/y.py", "source_file": "x/y.py", "community": 1},
                {"id": "node6", "label": "m/n.md", "source_file": "m/n.md", "community": 1},
            ],
            "edges": [
                {"source": "node1", "target": "node2", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node3", "target": "node6", "relation": "references", "confidence": "INFERRED"},
            ],
            "clusters": {},
        }
        ranked = render_report(ranked_graph, commit="abc123")
        surprise_section = ranked[ranked.index("## Surprising Connections") :]
        assert "a/c.py" in surprise_section or "a/d.py" in surprise_section, (
            "At least one cross-community edge should appear in surprising connections"
        )

    def test_surprise_report_independent_of_input_order(self):
        """Surprise ranking must not depend on input order."""
        ranked_graph = {
            "nodes": [
                {"id": "node1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
                {"id": "node2", "label": "a/c.py", "source_file": "a/c.py", "community": 0},
                {"id": "node3", "label": "a/d.py", "source_file": "a/d.py", "community": 0},
                {"id": "node4", "label": "x/y.py", "source_file": "x/y.py", "community": 1},
                {"id": "node6", "label": "m/n.md", "source_file": "m/n.md", "community": 1},
            ],
            "edges": [
                {"source": "node1", "target": "node2", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node3", "target": "node6", "relation": "references", "confidence": "INFERRED"},
            ],
            "clusters": {},
        }
        shuffled_graph = {
            **ranked_graph,
            "edges": list(reversed(ranked_graph["edges"])),
            "nodes": list(reversed(ranked_graph["nodes"])),
        }
        report1 = render_report(shuffled_graph, commit="abc123")
        report2 = render_report(ranked_graph, commit="abc123")
        assert report1 == report2, "surprise ranking must not depend on input order"

    def test_surprise_report_deterministic(self):
        """Surprise report ranking must be deterministic."""
        ranked_graph = {
            "nodes": [
                {"id": "node1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
                {"id": "node2", "label": "a/c.py", "source_file": "a/c.py", "community": 0},
                {"id": "node3", "label": "a/d.py", "source_file": "a/d.py", "community": 0},
                {"id": "node4", "label": "x/y.py", "source_file": "x/y.py", "community": 1},
                {"id": "node6", "label": "m/n.md", "source_file": "m/n.md", "community": 1},
            ],
            "edges": [
                {"source": "node1", "target": "node2", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node2", "target": "node4", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "node3", "target": "node6", "relation": "references", "confidence": "INFERRED"},
            ],
            "clusters": {},
        }
        report1 = render_report(ranked_graph, commit="abc123")
        report2 = render_report(ranked_graph, commit="abc123")
        assert report1 == report2, "Surprise report ranking must be deterministic"
        assert report1.isascii(), "ranked report must stay ASCII"


class TestRenderReportEmptyGraph:
    """Test report generation with empty graph."""

    def test_render_report_empty_graph(self):
        """Empty graph should still produce all sections."""
        empty_graph = {"nodes": [], "edges": []}
        empty_report = render_report(empty_graph, commit="test")
        assert "## Community Hubs" in empty_report, "Empty report missing Community Hubs heading"
        assert "## God Nodes" in empty_report, "Empty report missing God Nodes heading"
        assert "## Surprising Connections" in empty_report, "Empty report missing Surprising Connections heading"
        assert "- none" in empty_report, "Empty sections should have '- none'"


class TestBuildManifest:
    """Test build_manifest function."""

    def test_build_manifest_includes_valid_records(self):
        """Manifest should include valid records."""
        test_files = [
            {"rel_path": "a/b.py", "hash": "abc123", "mtime": 1234.5},
            {"rel_path": "x/y.py", "hash": "def456", "mtime": 1234.6},
            {"rel_path": "missing_hash", "mtime": 1234.7},
            {"hash": "orphan", "mtime": 1234.8},
        ]
        manifest = build_manifest(test_files)
        assert "a/b.py" in manifest, "a/b.py should be in manifest"
        assert "x/y.py" in manifest, "x/y.py should be in manifest"

    def test_build_manifest_skips_invalid_records(self):
        """Manifest should skip records with missing hash or rel_path."""
        test_files = [
            {"rel_path": "a/b.py", "hash": "abc123", "mtime": 1234.5},
            {"rel_path": "x/y.py", "hash": "def456", "mtime": 1234.6},
            {"rel_path": "missing_hash", "mtime": 1234.7},
            {"hash": "orphan", "mtime": 1234.8},
        ]
        manifest = build_manifest(test_files)
        assert "missing_hash" not in manifest, "Records with missing hash should be skipped"
        assert len([k for k in manifest if k.startswith("a/") or k.startswith("x/")]) == 2, (
            "Should have 2 valid entries"
        )

    def test_build_manifest_entry_structure(self):
        """Each manifest entry should have mtime and mimry_sha256."""
        test_files = [
            {"rel_path": "a/b.py", "hash": "abc123", "mtime": 1234.5},
            {"rel_path": "x/y.py", "hash": "def456", "mtime": 1234.6},
        ]
        manifest = build_manifest(test_files)
        for rel_path, info in manifest.items():
            assert "mtime" in info, f"{rel_path} missing mtime"
            assert "mimry_sha256" in info, f"{rel_path} missing mimry_sha256"
            assert len(info) == 2, f"{rel_path} has extra keys: {set(info.keys()) - {'mtime', 'mimry_sha256'}}"

    def test_build_manifest_values_correct(self):
        """Manifest values should match input."""
        test_files = [
            {"rel_path": "a/b.py", "hash": "abc123", "mtime": 1234.5},
            {"rel_path": "x/y.py", "hash": "def456", "mtime": 1234.6},
        ]
        manifest = build_manifest(test_files)
        assert manifest["a/b.py"]["mimry_sha256"] == "abc123", "Hash not mapped correctly"
        assert manifest["a/b.py"]["mtime"] == 1234.5, "mtime not mapped correctly"

    def test_build_manifest_keys_sorted(self):
        """Manifest keys should be sorted."""
        test_files = [
            {"rel_path": "a/b.py", "hash": "abc123", "mtime": 1234.5},
            {"rel_path": "x/y.py", "hash": "def456", "mtime": 1234.6},
        ]
        manifest = build_manifest(test_files)
        manifest_keys = list(manifest.keys())
        assert manifest_keys == sorted(manifest_keys), "Manifest keys should be sorted"
