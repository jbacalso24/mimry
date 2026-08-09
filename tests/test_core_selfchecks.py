"""Self-checks from core module __main__ blocks - moved to pytest."""

from __future__ import annotations

import json
import random

import pytest

from mimry.core.build import GraphEngine
from mimry.core.cluster import assign_communities
from mimry.core.resolve import resolve_imports


class TestBuildGraphSelfChecks:
    """Self-checks from core/build.py __main__."""

    @pytest.fixture
    def test_data(self):
        """Setup test data."""
        test_files = [
            {
                "file_id": "file1",
                "rel_path": "backend/api/auth.py",
                "extension": ".py",
            },
            {
                "file_id": "file2",
                "rel_path": "backend/utils/helpers.py",
                "extension": ".py",
            },
            {
                "file_id": "file3",
                "rel_path": "main.py",
                "extension": ".py",
            },
        ]

        test_symbols = [
            {
                "symbol_id": "sym1",
                "file_id": "file1",
                "name": "login",
                "kind": "function",
                "language": "python",
                "line_start": 42,
            },
            {
                "symbol_id": "sym2",
                "file_id": "file1",
                "name": "AuthManager",
                "kind": "class",
                "language": "python",
                "line_start": 7,
            },
            {
                "symbol_id": "sym3",
                "file_id": "file2",
                "name": "format_string",
                "kind": "function",
                "language": "python",
            },
            {
                "symbol_id": "sym_orphan",
                "file_id": "nonexistent_file",
                "name": "orphan_function",
                "kind": "function",
                "language": "python",
            },
        ]

        test_edges = [
            {
                "source_type": "file",
                "source_id": "file1",
                "target_type": "symbol",
                "target_id": "sym1",
                "edge_type": "defines",
                "confidence": 1.0,
            },
            {
                "source_type": "file",
                "source_id": "file1",
                "target_type": "symbol",
                "target_id": "sym2",
                "edge_type": "defines",
                "confidence": 0.8,
            },
            {
                "source_type": "file",
                "source_id": "file2",
                "target_type": "symbol",
                "target_id": "sym3",
                "edge_type": "defines",
                "confidence": 1.0,
            },
            {
                "source_type": "file",
                "source_id": "file1",
                "target_type": "symbol",
                "target_id": "nonexistent_symbol",
                "edge_type": "defines",
                "confidence": 1.0,
            },
        ]

        return test_files, test_symbols, test_edges

    def test_build_graph_node_structure(self, test_data):
        """1. Every node has required keys and valid source_file."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        for node in result["nodes"]:
            required_keys = {
                "id",
                "label",
                "norm_label",
                "source_file",
                "type",
                "kind",
                "file_type",
                "line",
                "community",
            }
            assert set(node.keys()) == required_keys
            assert isinstance(node["source_file"], str) and len(node["source_file"]) > 0
            assert node["line"] is None or isinstance(node["line"], int)

    def test_build_graph_symbol_lines(self, test_data):
        """1b. Symbol lines survive; missing line_start degrades to None."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)
        by_id = {n["id"]: n for n in result["nodes"]}

        assert by_id["symbol:sym1"]["line"] == 42
        assert by_id["symbol:sym2"]["line"] == 7
        assert by_id["symbol:sym3"]["line"] is None
        assert by_id["file:file1"]["line"] is None

    def test_build_graph_node_id_prefixes(self, test_data):
        """2. File and symbol node ids have correct prefixes."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        node_ids = [n["id"] for n in result["nodes"]]
        for nid in node_ids:
            assert nid.startswith("file:") or nid.startswith("symbol:")

    def test_build_graph_orphan_symbol_dropped(self, test_data):
        """3. Orphan symbols produce no node."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        orphan_ids = [n["id"] for n in result["nodes"] if "sym_orphan" in n["id"]]
        assert len(orphan_ids) == 0

    def test_build_graph_dangling_edge_dropped(self, test_data):
        """4. Dangling edges are dropped."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        edge_targets = [e["target"] for e in result["edges"]]
        assert "symbol:nonexistent_symbol" not in edge_targets

    def test_build_graph_edge_structure(self, test_data):
        """5. Edges have exactly the required keys."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        for edge in result["edges"]:
            edge_keys = {"source", "target", "relation", "confidence"}
            assert set(edge.keys()) == edge_keys

    def test_build_graph_edge_references_valid_nodes(self, test_data):
        """6. No edge references a non-existent node."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        node_id_set = set(n["id"] for n in result["nodes"])
        for edge in result["edges"]:
            assert edge["source"] in node_id_set
            assert edge["target"] in node_id_set

    def test_build_graph_confidence_values(self, test_data):
        """7. Confidence values are only EXTRACTED or INFERRED."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        for edge in result["edges"]:
            assert edge["confidence"] in {"EXTRACTED", "INFERRED"}

    def test_build_graph_norm_label(self, test_data):
        """8. norm_label normalization works correctly."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        auth_node = [n for n in result["nodes"] if n["label"] == "backend/api/auth.py"][0]
        assert auth_node["norm_label"] == "backend api auth py"

    def test_build_graph_deterministic(self, test_data):
        """9. Calling build_graph twice yields identical output."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data

        result1 = engine.build_graph(test_files, test_symbols, test_edges)
        result2 = engine.build_graph(test_files, test_symbols, test_edges)

        output1 = json.dumps(result1, sort_keys=True)
        output2 = json.dumps(result2, sort_keys=True)
        assert output1 == output2

    def test_build_graph_clusters(self, test_data):
        """10. Clusters maps folder to list of rel_paths and includes '.'."""
        engine = GraphEngine()
        test_files, test_symbols, test_edges = test_data
        result = engine.build_graph(test_files, test_symbols, test_edges)

        clusters = result["clusters"]
        assert "." in clusters
        assert "main.py" in clusters["."]
        assert "backend/api/auth.py" in clusters.get("backend/api", [])


class TestClusterAssignCommunities:
    """Self-checks from core/cluster.py __main__."""

    @pytest.fixture
    def test_data(self):
        """Setup test data."""
        test_nodes = [
            {"id": "a1", "community": 0},
            {"id": "a2", "community": 0},
            {"id": "a3", "community": 0},
            {"id": "b1", "community": 0},
            {"id": "b2", "community": 0},
            {"id": "b3", "community": 0},
            {"id": "c1", "community": 0},
        ]

        test_edges = [
            # Cluster A: fully connected
            {"source": "a1", "target": "a2"},
            {"source": "a1", "target": "a3"},
            {"source": "a2", "target": "a3"},
            # Cluster B: fully connected
            {"source": "b1", "target": "b2"},
            {"source": "b1", "target": "b3"},
            {"source": "b2", "target": "b3"},
            # c1 is isolated
        ]

        return test_nodes, test_edges

    def test_cluster_separate_groups(self, test_data):
        """1-5. Communities separate and count correctly."""
        test_nodes, test_edges = test_data
        result = assign_communities(test_nodes, test_edges)
        communities = {node["id"]: node["community"] for node in result}

        # Within-group equality
        assert communities["a1"] == communities["a2"]
        assert communities["a2"] == communities["a3"]
        assert communities["b1"] == communities["b2"]
        assert communities["b2"] == communities["b3"]

        # Between-group inequality
        assert communities["a1"] != communities["b1"]
        assert communities["c1"] != communities["a1"]
        assert communities["c1"] != communities["b1"]

        # Types and counts
        for node in result:
            assert isinstance(node["community"], int)

    def test_cluster_input_not_mutated(self, test_data):
        """Input nodes were NOT mutated."""
        test_nodes, test_edges = test_data
        original_community = test_nodes[0]["community"]
        assign_communities(test_nodes, test_edges)
        assert test_nodes[0]["community"] == original_community

    def test_cluster_deterministic_under_shuffle(self, test_data):
        """7. Determinism under input reordering."""
        test_nodes, test_edges = test_data
        result1 = assign_communities(test_nodes, test_edges)
        communities1 = {node["id"]: node["community"] for node in result1}

        shuffled_nodes = list(test_nodes)
        random.Random(1234).shuffle(shuffled_nodes)
        result2 = assign_communities(shuffled_nodes, test_edges)
        communities2 = {node["id"]: node["community"] for node in result2}

        assert communities1 == communities2

    def test_cluster_empty_graph(self):
        """8. Empty graph."""
        empty_result = assign_communities([], [])
        assert empty_result == []

    def test_cluster_unknown_edges_ignored(self, test_data):
        """9. Edges referencing unknown node ids are ignored."""
        test_nodes, test_edges = test_data
        unknown_edges = [
            {"source": "a1", "target": "a2"},
            {"source": "unknown", "target": "a3"},
            {"source": "b1", "target": "nonexistent"},
        ]
        result = assign_communities(test_nodes, unknown_edges)
        assert len(result) == len(test_nodes)


class TestResolveImportsSelfChecks:
    """Self-checks from core/resolve.py resolve_imports __main__."""

    @pytest.fixture
    def test_data(self):
        """Setup test data."""
        imports = {
            "backend/api/auth.py": ["backend.services.session_service"],
            "backend/services/session_service.py": ["backend.repositories.session_repository"],
            "backend/tests/test_auth.py": ["backend.services.session_service"],
            "web/tests/checkout.test.tsx": ["../src/lib/payments"],
            "web/src/app/checkout/page.tsx": ["../../features/checkout/useCheckout"],
            "web/src/features/checkout/useCheckout.ts": ["../../lib/payments"],
        }

        rel_paths = {
            "archive/checkout-old.ts",
            "backend/api/auth.py",
            "backend/middleware/auth.py",
            "backend/repositories/session_repository.py",
            "backend/services/session_service.py",
            "backend/tests/test_auth.py",
            "db/migrations/20260701_add_session_expiry.sql",
            "db/schema.sql",
            "docs/auth-design.md",
            "docs/checkout-plan.md",
            "examples/auth_demo.py",
            "pyproject.toml",
            "web/src/app/checkout/page.tsx",
            "web/src/features/checkout/useCheckout.ts",
            "web/src/lib/payments.ts",
            "web/tests/checkout.test.tsx",
        }

        return imports, rel_paths

    def test_imports_all_resolve(self, test_data):
        """1. All six imports resolve."""
        imports, rel_paths = test_data
        results = resolve_imports(imports, rel_paths)
        assert len(results) == 6

    def test_imports_confidence_extracted(self, test_data):
        """2. Every result has confidence EXTRACTED."""
        imports, rel_paths = test_data
        results = resolve_imports(imports, rel_paths)
        for r in results:
            assert r["confidence"] == "EXTRACTED"

    def test_imports_payments_target_multiple(self, test_data):
        """3. web/src/lib/payments.ts is target of two importers."""
        imports, rel_paths = test_data
        results = resolve_imports(imports, rel_paths)

        payments_targets = [r for r in results if r["target"] == "web/src/lib/payments.ts"]
        assert len(payments_targets) == 2

        importers_of_payments = {r["importer"] for r in payments_targets}
        expected_importers = {
            "web/tests/checkout.test.tsx",
            "web/src/features/checkout/useCheckout.ts",
        }
        assert importers_of_payments == expected_importers

    def test_imports_no_self_loop(self, test_data):
        """4. No result has importer == target."""
        imports, rel_paths = test_data
        results = resolve_imports(imports, rel_paths)
        for r in results:
            assert r["importer"] != r["target"]

    def test_imports_target_in_paths(self, test_data):
        """5. Every target is in rel_paths."""
        imports, rel_paths = test_data
        results = resolve_imports(imports, rel_paths)
        for r in results:
            assert r["target"] in rel_paths

    def test_imports_no_backslash(self, test_data):
        """6. No backslash in any path."""
        imports, rel_paths = test_data
        results = resolve_imports(imports, rel_paths)
        for r in results:
            assert "\\" not in r["importer"]
            assert "\\" not in r["target"]

    def test_imports_unresolvable_empty(self, test_data):
        """7. Unresolvable import produces NO result."""
        _, rel_paths = test_data
        test_unresolvable = resolve_imports({"test/file.py": ["react"]}, rel_paths)
        assert len(test_unresolvable) == 0

    def test_imports_ambiguous_empty(self, test_data):
        """8. Ambiguous rule-4 suffix produces NO result."""
        ambiguous_paths = {"a/util.go", "b/util.go"}
        test_ambiguous = resolve_imports({"test/file.go": ["util"]}, ambiguous_paths)
        assert len(test_ambiguous) == 0

    def test_imports_java_package(self):
        """8b. Dotted module that rule 2 cannot place reaches rule 4."""
        nested_paths = {"src/main/java/com/app/model/User.java", "src/mimry/core/build.py"}
        test_java = resolve_imports({"src/main/java/com/app/svc/Svc.java": ["com.app.model.User"]}, nested_paths)
        assert len(test_java) == 1
        assert test_java[0]["target"] == "src/main/java/com/app/model/User.java"
        assert test_java[0]["confidence"] == "INFERRED"

    def test_imports_deterministic(self, test_data):
        """9. Calling twice yields identical output."""
        imports, rel_paths = test_data
        results1 = resolve_imports(imports, rel_paths)
        results2 = resolve_imports(imports, rel_paths)
        assert results1 == results2

    def test_imports_empty_inputs(self):
        """10. Empty inputs return []."""
        assert resolve_imports({}, set()) == []

    def test_imports_windows_safety(self, test_data):
        """11. Windows safety: relative paths work correctly."""
        _, rel_paths = test_data
        test_windows = resolve_imports(
            {"web/src/features/checkout/useCheckout.ts": ["../../lib/payments"]},
            rel_paths,
        )
        assert len(test_windows) == 1
        assert test_windows[0]["target"] == "web/src/lib/payments.ts"
        assert "/" in test_windows[0]["target"]
        assert "\\" not in test_windows[0]["target"]
