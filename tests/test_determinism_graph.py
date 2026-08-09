"""Tests for graph determinism issues (FINDING 3, 4, 5)."""

from __future__ import annotations

import json
import random
from mimry.core.build import GraphEngine, DuplicateIdentityError, edge_sort_key, canonicalize_edges
from mimry.core.resolve import symbol_selection_key


class TestDuplicateNodeIdentity:
    """FINDING 3: Duplicate node/symbol/edge IDs."""

    def test_duplicate_node_id_with_conflicting_fields_raises(self):
        """Two files with the same file_id but different rel_path should raise."""
        engine = GraphEngine()
        files = [
            {"file_id": "f1", "rel_path": "a.py", "extension": ".py"},
            {"file_id": "f1", "rel_path": "b.py", "extension": ".py"},  # Same ID, different path
        ]
        symbols = []
        edges = []

        try:
            engine.build_graph(files, symbols, edges)
            raise AssertionError("Expected DuplicateIdentityError")
        except DuplicateIdentityError as e:
            assert "f1" in str(e)

    def test_exact_duplicate_node_is_deduplicated(self):
        """Identical file record twice should be silently deduplicated."""
        engine = GraphEngine()
        files = [
            {"file_id": "f1", "rel_path": "a.py", "extension": ".py"},
            {"file_id": "f1", "rel_path": "a.py", "extension": ".py"},  # Exact duplicate
        ]
        symbols = []
        edges = []

        result = engine.build_graph(files, symbols, edges)
        # Should have exactly one file node
        file_nodes = [n for n in result["nodes"] if n["id"] == "file:f1"]
        assert len(file_nodes) == 1

    def test_duplicate_symbol_and_edge_ids_rejected(self):
        """Same for symbols and edges."""
        engine = GraphEngine()
        files = [{"file_id": "f1", "rel_path": "a.py", "extension": ".py"}]
        symbols = [
            {"symbol_id": "s1", "file_id": "f1", "name": "foo", "kind": "function", "language": "python"},
            {"symbol_id": "s1", "file_id": "f1", "name": "bar", "kind": "function", "language": "python"},
        ]
        edges = []

        try:
            engine.build_graph(files, symbols, edges)
            raise AssertionError("Expected DuplicateIdentityError for conflicting symbols")
        except DuplicateIdentityError as e:
            assert "s1" in str(e)

    def test_duplicate_rejection_identical_under_input_reversal(self):
        """Reverse input list, assert SAME outcome (error or dedup)."""
        engine = GraphEngine()
        files = [
            {"file_id": "f1", "rel_path": "a.py", "extension": ".py"},
            {"file_id": "f1", "rel_path": "b.py", "extension": ".py"},
        ]
        symbols = []
        edges = []

        # Forward should raise
        try:
            engine.build_graph(files, symbols, edges)
            raised_forward = False
        except DuplicateIdentityError:
            raised_forward = True

        # Reversed should also raise
        try:
            engine.build_graph(list(reversed(files)), symbols, edges)
            raised_reversed = False
        except DuplicateIdentityError:
            raised_reversed = True

        assert raised_forward == raised_reversed, "Outcome differs when input is reversed"


class TestEdgeDeterminism:
    """FINDING 4: Edge ordering and canonicalization."""

    def test_edges_with_equal_endpoints_and_differing_confidence_are_total_ordered(self):
        """Edges same source/target/relation, different confidence should have stable total order."""
        engine = GraphEngine()
        files = [
            {"file_id": "f1", "rel_path": "a.py", "extension": ".py"},
            {"file_id": "f2", "rel_path": "b.py", "extension": ".py"},
        ]
        symbols = []
        edges = [
            {
                "source_type": "file",
                "source_id": "f1",
                "target_type": "file",
                "target_id": "f2",
                "edge_type": "calls",
                "confidence": 0.5,
            },  # INFERRED
            {
                "source_type": "file",
                "source_id": "f1",
                "target_type": "file",
                "target_id": "f2",
                "edge_type": "calls",
                "confidence": 1.0,
            },  # EXTRACTED
        ]

        result = engine.build_graph(files, symbols, edges)
        result_json = json.dumps(result, sort_keys=True)

        # Reversed input
        result2 = engine.build_graph(files, symbols, list(reversed(edges)))
        result2_json = json.dumps(result2, sort_keys=True)

        assert result_json == result2_json, "Edge output differs when input is reversed"

    def test_edge_metadata_participates_in_sort_key(self):
        """Edges differing only in an extra metadata field sort stably."""
        edges = [
            {"source": "f1", "target": "f2", "relation": "calls", "confidence": "EXTRACTED", "extra": "b"},
            {"source": "f1", "target": "f2", "relation": "calls", "confidence": "EXTRACTED", "extra": "a"},
        ]

        sorted_edges = canonicalize_edges(edges)
        # Should be deterministically sorted
        assert sorted_edges[0]["extra"] == "a"
        assert sorted_edges[1]["extra"] == "b"

    def test_graph_build_byte_stable_under_shuffle(self):
        """Full build_graph output is byte-identical across multiple shuffle seeds."""
        engine = GraphEngine()
        files = [
            {"file_id": "f1", "rel_path": "a.py", "extension": ".py"},
            {"file_id": "f2", "rel_path": "b.py", "extension": ".py"},
            {"file_id": "f3", "rel_path": "c.py", "extension": ".py"},
        ]
        symbols = [
            {"symbol_id": "s1", "file_id": "f1", "name": "foo", "kind": "function", "language": "python"},
            {"symbol_id": "s2", "file_id": "f2", "name": "bar", "kind": "class", "language": "python"},
        ]
        edges = [
            {
                "source_type": "file",
                "source_id": "f1",
                "target_type": "file",
                "target_id": "f2",
                "edge_type": "calls",
                "confidence": 1.0,
            },
            {
                "source_type": "file",
                "source_id": "f2",
                "target_type": "symbol",
                "target_id": "s1",
                "edge_type": "defines",
                "confidence": 0.8,
            },
        ]

        result_jsons = []
        for seed in [0, 1, 2]:
            shuffled_files = list(files)
            shuffled_symbols = list(symbols)
            shuffled_edges = list(edges)

            random.Random(seed).shuffle(shuffled_files)
            random.Random(seed).shuffle(shuffled_symbols)
            random.Random(seed).shuffle(shuffled_edges)

            result = engine.build_graph(shuffled_files, shuffled_symbols, shuffled_edges)
            result_jsons.append(json.dumps(result, sort_keys=True))

        # All should be identical
        assert result_jsons[0] == result_jsons[1] == result_jsons[2]


class TestSymbolResolutionTies:
    """FINDING 5: Symbol resolution tie-breaking."""

    def test_symbol_selection_key_is_total(self):
        """symbol_selection_key produces a total order."""
        sym1 = {"name": "foo", "kind": "function", "line_start": 10, "line_end": 20, "symbol_id": "s1"}
        sym2 = {"name": "foo", "kind": "function", "line_start": 10, "line_end": 20, "symbol_id": "s2"}

        key1 = symbol_selection_key(sym1)
        key2 = symbol_selection_key(sym2)

        # Different symbol_ids should produce different keys
        assert key1 != key2

    def test_symbol_resolution_tie_is_total(self):
        """Two symbols same name/line in different files resolve same under shuffle."""
        from mimry.core.resolve import resolve_calls

        calls = {
            "main.py": [{"name": "process", "line": 5}],
        }

        symbols_by_file = {
            "utils.py": [
                {"name": "process", "kind": "function", "line_start": 1, "line_end": 10, "symbol_id": "s1"},
            ],
            "helpers.py": [
                {"name": "process", "kind": "function", "line_start": 1, "line_end": 10, "symbol_id": "s2"},
            ],
            "main.py": [
                {"name": "main", "kind": "function", "line_start": 1, "line_end": 20, "symbol_id": "s3"},
            ],
        }

        import_edges = [
            {"importer": "main.py", "target": "utils.py"},
            {"importer": "main.py", "target": "helpers.py"},
        ]

        result1 = resolve_calls(calls, symbols_by_file, import_edges)

        # Shuffle symbols in each file
        symbols_by_file_shuffled = {}
        for file_path, symbols in symbols_by_file.items():
            symbols_by_file_shuffled[file_path] = list(symbols)
            random.Random(42).shuffle(symbols_by_file_shuffled[file_path])

        result2 = resolve_calls(calls, symbols_by_file_shuffled, import_edges)

        # Results should match
        assert result1 == result2


class TestEdgeSortKey:
    """Test the edge_sort_key function."""

    def test_edge_sort_key_total_order(self):
        """edge_sort_key produces different keys for different edges."""
        edges = [
            {"source": "f1", "target": "f2", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "f1", "target": "f2", "relation": "calls", "confidence": "INFERRED"},
            {"source": "f2", "target": "f1", "relation": "calls", "confidence": "EXTRACTED"},
        ]

        keys = [edge_sort_key(e) for e in edges]

        # All distinct edges should have different keys
        assert len(set(keys)) == 3

    def test_edge_sort_key_byte_stable(self):
        """edge_sort_key serializes consistently."""
        edge1 = {"source": "f1", "target": "f2", "relation": "calls", "confidence": "EXTRACTED", "x": 1, "y": 2}
        edge2 = {"source": "f1", "target": "f2", "relation": "calls", "confidence": "EXTRACTED", "y": 2, "x": 1}

        key1 = edge_sort_key(edge1)
        key2 = edge_sort_key(edge2)

        # Keys should be identical regardless of field order in dict
        assert key1 == key2
