from __future__ import annotations

import json
import re
from collections import defaultdict


class GraphEngine:
    engine_name = "mimry-core"

    def build_graph(self, files, symbols, edges, imports=None, exports=None) -> dict:
        """
        Build a graph from files, symbols, and edges.

        Args:
            files: List of file dicts with file_id, rel_path, extension
            symbols: List of symbol dicts with symbol_id, file_id, name, kind, language
            edges: List of edge dicts with source_type, source_id, target_type, target_id, edge_type, confidence
            imports: Optional, unused for now
            exports: Optional, unused for now

        Returns:
            Dict with keys: engine, nodes, edges, clusters
        """
        # Build a lookup from file_id to rel_path
        file_id_to_rel_path = {}
        for f in files:
            file_id_to_rel_path[f["file_id"]] = f["rel_path"]

        # Build nodes
        nodes = []
        node_ids = set()

        # File nodes
        for f in files:
            node_id = f"file:{f['file_id']}"
            node_ids.add(node_id)
            norm_label = self._normalize_label(f["rel_path"])
            file_type = f["extension"].lstrip(".") if f["extension"] else "file"
            nodes.append(
                {
                    "id": node_id,
                    "label": f["rel_path"],
                    "norm_label": norm_label,
                    "source_file": f["rel_path"],
                    "type": "file",
                    "kind": file_type,
                    "file_type": file_type,
                    "community": 0,
                }
            )

        # Symbol nodes
        for s in symbols:
            # Skip symbols whose file_id does not match any file
            if s["file_id"] not in file_id_to_rel_path:
                continue

            node_id = f"symbol:{s['symbol_id']}"
            node_ids.add(node_id)
            norm_label = self._normalize_label(s["name"])
            source_file = file_id_to_rel_path[s["file_id"]]
            nodes.append(
                {
                    "id": node_id,
                    "label": s["name"],
                    "norm_label": norm_label,
                    "source_file": source_file,
                    "type": "symbol",
                    "kind": s["kind"],
                    "file_type": s["language"],
                    "community": 0,
                }
            )

        # Build edges
        graph_edges = []
        for e in edges:
            source_id = f"{e['source_type']}:{e['source_id']}"
            target_id = f"{e['target_type']}:{e['target_id']}"

            # Drop any edge whose source or target is not in the node set
            if source_id not in node_ids or target_id not in node_ids:
                continue

            # Map confidence float to string
            confidence_value = e.get("confidence", None)
            if isinstance(confidence_value, (int, float)) and confidence_value >= 1.0:
                confidence_str = "EXTRACTED"
            else:
                confidence_str = "INFERRED"

            graph_edges.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "relation": e["edge_type"],
                    "confidence": confidence_str,
                }
            )

        # Build clusters
        clusters = self._cluster_by_folder(files)

        # Sort for determinism
        nodes = sorted(nodes, key=lambda n: n["id"])
        graph_edges = sorted(graph_edges, key=lambda e: (e["source"], e["target"], e["relation"]))

        return {
            "engine": self.engine_name,
            "nodes": nodes,
            "edges": graph_edges,
            "clusters": clusters,
        }

    def _normalize_label(self, label: str) -> str:
        r"""
        Normalize a label for case-insensitive matching.
        Replace /, \, _, -, . with space, collapse whitespace, strip.
        """
        # Replace separators with space
        normalized = re.sub(r'[/\\_\-.]+', ' ', label)
        # Collapse runs of whitespace
        normalized = re.sub(r'\s+', ' ', normalized)
        # Strip
        normalized = normalized.strip()
        # Lowercase
        normalized = normalized.lower()
        return normalized

    def _cluster_by_folder(self, files: list) -> dict:
        """Group files by folder."""
        clusters = defaultdict(list)
        for f in files:
            rel_path = f["rel_path"]
            folder = rel_path.rsplit("/", 1)[0] if "/" in rel_path else "."
            clusters[folder].append(rel_path)
        return dict(sorted(clusters.items()))


if __name__ == "__main__":
    import sys

    # Self-check with synthetic data
    engine = GraphEngine()

    # Construct synthetic test data
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
        },
        {
            "symbol_id": "sym2",
            "file_id": "file1",
            "name": "AuthManager",
            "kind": "class",
            "language": "python",
        },
        {
            "symbol_id": "sym3",
            "file_id": "file2",
            "name": "format_string",
            "kind": "function",
            "language": "python",
        },
        {
            # This symbol's file_id does not match any file - should be skipped
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
            "confidence": 1.0,  # Should become "EXTRACTED"
        },
        {
            "source_type": "file",
            "source_id": "file1",
            "target_type": "symbol",
            "target_id": "sym2",
            "edge_type": "defines",
            "confidence": 0.8,  # Should become "INFERRED"
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
            # Dangling edge: target does not exist in nodes - should be dropped
            "source_type": "file",
            "source_id": "file1",
            "target_type": "symbol",
            "target_id": "nonexistent_symbol",
            "edge_type": "defines",
            "confidence": 1.0,
        },
    ]

    result = engine.build_graph(test_files, test_symbols, test_edges)

    # Assertions
    try:
        # 1. Every node has all 8 required keys, and source_file is non-empty
        for node in result["nodes"]:
            required_keys = {"id", "label", "norm_label", "source_file", "type", "kind", "file_type", "community"}
            assert set(node.keys()) == required_keys, f"Node {node['id']} missing keys: {required_keys - set(node.keys())}"
            assert isinstance(node["source_file"], str) and len(node["source_file"]) > 0, f"Node {node['id']} has empty source_file"

        # 2. File node ids start with "file:", symbol node ids start with "symbol:"
        node_ids = [n["id"] for n in result["nodes"]]
        for nid in node_ids:
            if nid.startswith("file:"):
                assert nid.startswith("file:"), f"File node id {nid} does not start with 'file:'"
            elif nid.startswith("symbol:"):
                assert nid.startswith("symbol:"), f"Symbol node id {nid} does not start with 'symbol:'"
            else:
                raise AssertionError(f"Node id {nid} has invalid prefix")

        # 3. The orphan symbol produced NO node
        orphan_ids = [n["id"] for n in result["nodes"] if "sym_orphan" in n["id"]]
        assert len(orphan_ids) == 0, f"Orphan symbol should not create a node, but found {orphan_ids}"

        # 4. The dangling edge was dropped
        edge_targets = [e["target"] for e in result["edges"]]
        assert "symbol:nonexistent_symbol" not in edge_targets, "Dangling edge should be dropped"

        # 5. Every edge has exactly the keys source, target, relation, confidence
        for edge in result["edges"]:
            edge_keys = {"source", "target", "relation", "confidence"}
            assert set(edge.keys()) == edge_keys, f"Edge has wrong keys: {set(edge.keys())}, expected {edge_keys}"

        # 6. No edge references an id that is not in the node set
        node_id_set = set(n["id"] for n in result["nodes"])
        for edge in result["edges"]:
            assert edge["source"] in node_id_set, f"Edge source {edge['source']} not in nodes"
            assert edge["target"] in node_id_set, f"Edge target {edge['target']} not in nodes"

        # 7. Confidence values are only ever the strings "EXTRACTED" or "INFERRED"
        for edge in result["edges"]:
            assert edge["confidence"] in {"EXTRACTED", "INFERRED"}, f"Edge confidence {edge['confidence']} is not EXTRACTED or INFERRED"

        # 8. norm_label for "backend/api/auth.py" equals "backend api auth py"
        auth_node = [n for n in result["nodes"] if n["label"] == "backend/api/auth.py"][0]
        assert auth_node["norm_label"] == "backend api auth py", f"norm_label incorrect: {auth_node['norm_label']}"

        # 9. Calling build_graph twice yields identical output
        result2 = engine.build_graph(test_files, test_symbols, test_edges)
        output1 = json.dumps(result, sort_keys=True)
        output2 = json.dumps(result2, sort_keys=True)
        assert output1 == output2, "Output is not deterministic"

        # 10. Clusters maps folder to a list of rel_paths and includes "." for root-level file
        clusters = result["clusters"]
        assert "." in clusters, "Clusters should include '.' for root-level files"
        assert "main.py" in clusters["."], f"Root file 'main.py' should be in clusters['.'], got {clusters['.']}"
        assert "backend/api/auth.py" in clusters.get("backend/api", []), f"backend/api/auth.py should be in clusters"

        print("OK")
        sys.exit(0)

    except AssertionError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
