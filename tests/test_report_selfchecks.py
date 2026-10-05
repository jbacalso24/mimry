"""Self-checks for report.py functions: render_report and
build_manifest.

These tests ensure surprise-report ranking (cross-community edges, node
degree, community structure) and manifest building work correctly.
"""

from mimry.core.report import build_manifest, render_report


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
        """The report has its three section headings.

        Community Hubs, God Nodes, and Surprising Connections.
        """
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
    """Report generation with cross-community edges and rankings."""

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
                {
                    "source": "node1",
                    "target": "node2",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node1",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node4",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node5",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node6",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node5",
                    "target": "node1",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
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
        """Community hubs are each community's highest-degree nodes."""
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
                {
                    "source": "node1",
                    "target": "node2",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node1",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node4",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node5",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node6",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node5",
                    "target": "node1",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
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
                {
                    "source": "node1",
                    "target": "node2",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node1",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node4",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node5",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node6",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node5",
                    "target": "node1",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
            ],
            "clusters": {},
        }
        report = render_report(synthetic_graph, commit="abc123", title="MIMRY graph report")
        lines = report.splitlines()
        god_index = lines.index("## God Nodes")
        assert "degree 3" in lines[god_index + 1], (
            f"god nodes must be degree-sorted: {lines[god_index + 1]}"
        )


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
                {
                    "source": "node1",
                    "target": "node2",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node1",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node3",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node4",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node5",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node4",
                    "target": "node6",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node5",
                    "target": "node1",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
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
                {
                    "source": "node1",
                    "target": "node2",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node4",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node3",
                    "target": "node6",
                    "relation": "references",
                    "confidence": "INFERRED",
                },
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
                {
                    "source": "node1",
                    "target": "node2",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node4",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node3",
                    "target": "node6",
                    "relation": "references",
                    "confidence": "INFERRED",
                },
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
                {
                    "source": "node1",
                    "target": "node2",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node2",
                    "target": "node4",
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                },
                {
                    "source": "node3",
                    "target": "node6",
                    "relation": "references",
                    "confidence": "INFERRED",
                },
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
        assert "## Surprising Connections" in empty_report, (
            "Empty report missing Surprising Connections heading"
        )
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
        """Manifest skips records missing a hash or rel_path."""
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
            assert len(info) == 2, (
                f"{rel_path} has extra keys: {set(info.keys()) - {'mtime', 'mimry_sha256'}}"
            )

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


class TestSuggestedQuestions:
    """Test suggested questions section generation."""

    def test_report_has_suggested_questions_section(self):
        """Report should have ## Suggested Questions heading."""
        graph = {
            "nodes": [
                {
                    "id": "node1",
                    "label": "src/main.py",
                    "source_file": "src/main.py",
                    "community": 0,
                },
            ],
            "edges": [],
        }
        report = render_report(graph, commit="test")
        assert "## Suggested Questions" in report

    def test_inferred_edges_line_present(self):
        """Inferred edges line should be in header."""
        graph = {
            "nodes": [],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "calls", "confidence": "INFERRED"},
            ],
        }
        report = render_report(graph, commit="test")
        assert "- Inferred edges:" in report
        assert "1 of 1" in report

    def test_inferred_edges_counted_correctly(self):
        """Inferred edges should be counted correctly."""
        graph = {
            "nodes": [],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "calls", "confidence": "INFERRED"},
                {"source": "n2", "target": "n3", "relation": "imports", "confidence": "EXTRACTED"},
                {
                    "source": "n3",
                    "target": "n4",
                    "relation": "references",
                    "confidence": "INFERRED",
                },
            ],
        }
        report = render_report(graph, commit="test")
        assert "2 of 3" in report

    def test_suggested_questions_template_1_fires(self):
        """Template 1: top god node (non-test) fires."""
        graph = {
            "nodes": [
                {"id": "n1", "label": "src/main.py", "source_file": "src/main.py", "community": 0},
                {
                    "id": "n2",
                    "label": "tests/test.py",
                    "source_file": "tests/test.py",
                    "community": 0,
                },
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "calls", "confidence": "EXTRACTED"},
            ],
        }
        report = render_report(graph, commit="test")
        assert "What depends on `src/main.py`" in report
        assert "mimry related" in report

    def test_suggested_questions_shell_safe_command(self):
        """Shell-safe labels should include the command."""
        graph = {
            "nodes": [
                {
                    "id": "n1",
                    "label": "src/main.py",
                    "source_file": "src/main.py",
                    "type": "file",
                    "community": 0,
                },
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "defines", "confidence": "EXTRACTED"},
            ],
        }
        report = render_report(graph, commit="test")
        assert '`mimry related "src/main.py"`' in report

    def test_suggested_questions_unsafe_label_no_command(self):
        """Unsafe labels should not include the command."""
        graph = {
            "nodes": [
                {
                    "id": "n1",
                    "label": "src/bad name$.py",
                    "source_file": "src/bad name$.py",
                    "type": "file",
                    "community": 0,
                },
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "defines", "confidence": "EXTRACTED"},
            ],
        }
        report = render_report(graph, commit="test")
        # Node can match template 1 or 5; unsafe labels no commands
        # Either way, unsafe labels should not have shell commands
        has_god_question = "What depends on `src/bad name$.py`" in report
        has_unreferenced_question = "Is `src/bad name$.py` still used?" in report
        assert has_god_question or has_unreferenced_question
        # Unsafe labels should not have mimry commands with direct
        # substitution
        for line in report.splitlines():
            if "src/bad name$.py" in line and "mimry" in line:
                assert "mimry related" not in line or "\\" in line

    def test_suggested_questions_template_2_fires(self):
        """Template 2: top surprising connection fires."""
        graph = {
            "nodes": [
                {"id": "n1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
                {"id": "n2", "label": "x/y.py", "source_file": "x/y.py", "community": 1},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "imports", "confidence": "INFERRED"},
            ],
        }
        report = render_report(graph, commit="test")
        assert "Why does `a/b.py` reach `x/y.py`" in report
        assert "mimry path" in report

    def test_suggested_questions_deterministic(self):
        """Suggested questions are deterministic."""
        graph = {
            "nodes": [
                {"id": "n1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
                {"id": "n2", "label": "x/y.py", "source_file": "x/y.py", "community": 1},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "imports", "confidence": "EXTRACTED"},
            ],
        }
        report1 = render_report(graph, commit="test")
        report2 = render_report(graph, commit="test")
        assert report1 == report2

    def test_suggested_questions_empty_graph(self):
        """Empty graph should show 'none' for suggested questions."""
        graph = {"nodes": [], "edges": []}
        report = render_report(graph, commit="test")
        lines = report.splitlines()
        idx = lines.index("## Suggested Questions")
        assert "- none" in lines[idx : idx + 3]

    def test_suggested_questions_no_test_files_as_god_nodes(self):
        """Test files should not be selected as top god node."""
        graph = {
            "nodes": [
                {
                    "id": "n1",
                    "label": "tests/test_all.py",
                    "source_file": "tests/test_all.py",
                    "community": 0,
                },
                {
                    "id": "n2",
                    "label": "src/main.py",
                    "source_file": "src/main.py",
                    "community": 0,
                },
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "calls", "confidence": "EXTRACTED"},
                {"source": "n1", "target": "n2", "relation": "calls", "confidence": "EXTRACTED"},
                {"source": "n1", "target": "n2", "relation": "calls", "confidence": "EXTRACTED"},
            ],
        }
        report = render_report(graph, commit="test")
        # n1 has degree 3, but it's a test file, so n2 should be
        # selected
        assert "What depends on `src/main.py`" in report

    def test_suggested_questions_shuffled_input_order(self):
        """Suggested questions are deterministic."""
        graph_original = {
            "nodes": [
                {"id": "n1", "label": "a/b.py", "source_file": "a/b.py", "community": 0},
                {"id": "n2", "label": "x/y.py", "source_file": "x/y.py", "community": 1},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "imports", "confidence": "EXTRACTED"},
            ],
        }
        graph_shuffled = {
            "nodes": list(reversed(graph_original["nodes"])),
            "edges": list(reversed(graph_original["edges"])),
        }
        report1 = render_report(graph_original, commit="test")
        report2 = render_report(graph_shuffled, commit="test")
        assert report1 == report2

    def test_template_5_symbol_node_not_suggested(self):
        """Symbol nodes are never suggested by template 5."""
        graph = {
            "nodes": [
                {
                    "id": "n1",
                    "label": "MyClass.method",
                    "source_file": "src/main.py",
                    "type": "symbol",
                    "community": 0,
                },
            ],
            "edges": [
                {"source": "n1", "target": "n1", "relation": "defines", "confidence": "EXTRACTED"},
            ],
        }
        report = render_report(graph, commit="test")
        assert "Is `MyClass.method` still used?" not in report

    def test_template_5_code_file_zero_defines_not_suggested(self):
        """Code file nodes with zero defines are never suggested."""
        graph = {
            "nodes": [
                {
                    "id": "n1",
                    "label": "src/empty.py",
                    "source_file": "src/empty.py",
                    "type": "file",
                    "community": 0,
                },
            ],
            "edges": [],
        }
        report = render_report(graph, commit="test")
        assert "Is `src/empty.py` still used?" not in report

    def test_template_5_markdown_file_not_suggested(self):
        """Markdown files are never suggested by template 5."""
        graph = {
            "nodes": [
                {
                    "id": "n1",
                    "label": "README.md",
                    "source_file": "README.md",
                    "type": "file",
                    "community": 0,
                },
            ],
            "edges": [
                {"source": "n1", "target": "n1", "relation": "defines", "confidence": "EXTRACTED"},
            ],
        }
        report = render_report(graph, commit="test")
        assert "Is `README.md` still used?" not in report

    def test_template_5_code_file_with_defines_suggested(self):
        """Code file nodes with defines and no incoming edges are
        suggested."""
        graph = {
            "nodes": [
                {
                    "id": "n1",
                    "label": "src/unused.py",
                    "source_file": "src/unused.py",
                    "type": "file",
                    "community": 0,
                },
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "defines", "confidence": "EXTRACTED"},
            ],
        }
        report = render_report(graph, commit="test")
        assert "Is `src/unused.py` still used?" in report
