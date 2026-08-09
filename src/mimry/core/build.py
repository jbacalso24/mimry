from __future__ import annotations

import json
import re
from collections import defaultdict


class DuplicateIdentityError(ValueError):
    """Raised when duplicate IDs have conflicting field values."""

    pass


def edge_sort_key(edge: dict) -> tuple:
    """Total order over canonical edge fields. No two distinct edges tie.

    Includes all fields on the edge dict to ensure metadata participates in ordering.
    Serializes extra fields as a sorted JSON string for byte-stable comparison.
    """
    canonical_fields = {"source", "target", "relation", "confidence"}
    source = edge.get("source", "")
    target = edge.get("target", "")
    relation = edge.get("relation", "")
    confidence = edge.get("confidence", "")

    # Extract metadata fields not in the canonical set
    metadata = {k: v for k, v in edge.items() if k not in canonical_fields}
    metadata_str = json.dumps(metadata, sort_keys=True, default=str)

    return (source, target, relation, confidence, metadata_str)


def raw_edge_sort_key(edge: dict) -> tuple:
    """Total order over a raw (pre-graph) edge record.

    Raw edges carry an ``edge_id`` and typed endpoints rather than the
    ``source``/``target`` strings a built graph uses. Every field participates,
    so the order -- and therefore any error message derived from it -- is
    identical under reversed or shuffled input.
    """
    return (
        str(edge.get("edge_id", "")),
        str(edge.get("source_type", "")),
        str(edge.get("source_id", "")),
        str(edge.get("target_type", "")),
        str(edge.get("target_id", "")),
        str(edge.get("edge_type", "")),
        str(edge.get("confidence", "")),
        json.dumps(edge, sort_keys=True, default=str),
    )


def validate_edge_identity(edges: list[dict]) -> list[dict]:
    """Enforce edge-ID integrity before canonicalization or persistence.

    Policy: two records sharing an ``edge_id`` claim to be the same edge.

    * Every canonical field equal -> an exact duplicate. Collapsed to one.
    * Any field different -- endpoints, relation, confidence, origin, or extra
      metadata -- -> the ID is ambiguous, and MIMRY fails closed with
      :class:`DuplicateIdentityError`.

    The alternative is what MIMRY used to do: let a dict overwrite or an
    ``insert or replace`` pick a winner by arrival order, which makes the graph
    depend on filesystem traversal order and hides a real extractor bug.

    Records without an ``edge_id`` pass through untouched -- those are graph
    edges synthesized by the resolvers, whose identity is
    ``(source, target, relation)`` and which :func:`canonicalize_edges` owns.

    Returns the surviving edges in total order.
    """
    if not edges:
        return []

    groups: dict[str, list[dict]] = defaultdict(list)
    passthrough: list[dict] = []
    for edge in edges:
        edge_id = edge.get("edge_id")
        if edge_id:
            groups[str(edge_id)].append(edge)
        else:
            passthrough.append(edge)

    survivors: list[dict] = []
    for edge_id in sorted(groups):
        group = sorted(groups[edge_id], key=raw_edge_sort_key)
        first = group[0]
        conflicting = next((candidate for candidate in group if candidate != first), None)
        if conflicting is not None:
            raise DuplicateIdentityError(
                f"Conflicting records share edge_id {edge_id!r}. An edge ID must identify exactly one edge.\n"
                f"  {json.dumps(first, sort_keys=True, default=str)}\n"
                f"  {json.dumps(conflicting, sort_keys=True, default=str)}\n"
                "Fix the extractor so each edge gets a distinct ID, or make the duplicate records identical. "
                "MIMRY will not guess which one is correct."
            )
        survivors.append(first)

    return sorted(survivors + passthrough, key=raw_edge_sort_key)


def canonicalize_edges(edges: list[dict]) -> list[dict]:
    """Deduplicate equivalent edges and return them in total order.

    Policy: edges are identified by (source, target, relation). When two edges
    share that identity, EXTRACTED evidence wins over INFERRED, because
    EXTRACTED means the relation was read directly from source rather than
    guessed. Edges identical in every field are collapsed. Edges that differ in
    a field other than confidence are NOT merged -- they are kept and separated
    by the total sort key, so no genuine conflict is hidden.
    """
    # Group edges by (source, target, relation)
    groups = {}
    for edge in edges:
        key = (edge.get("source"), edge.get("target"), edge.get("relation"))
        if key not in groups:
            groups[key] = []
        groups[key].append(edge)

    # Deduplicate within each group
    result = []
    for group in groups.values():
        # Sort by confidence: EXTRACTED first, then INFERRED
        group.sort(key=lambda e: (e.get("confidence") != "EXTRACTED", edge_sort_key(e)))

        # Keep track of seen edges (excluding confidence) to deduplicate identical ones
        seen = set()
        for edge in group:
            # Create a hashable version of the edge excluding confidence
            edge_copy = dict(edge)
            edge_copy.pop("confidence", None)
            edge_tuple = tuple(sorted(edge_copy.items()))

            if edge_tuple not in seen:
                seen.add(edge_tuple)
                result.append(edge)

    # Sort result by total key
    result.sort(key=edge_sort_key)
    return result


class GraphEngine:
    engine_name = "mimry-core"

    def build_graph(self, files, symbols, edges, imports=None, exports=None) -> dict:
        """Build a graph from files, symbols, and edges.

        Deduplicates nodes and edges: exact duplicates (all fields equal) are
        silently collapsed; records with same ID but any differing field raise
        DuplicateIdentityError. Edges are ordered by a total key that includes
        all fields, so results are byte-stable under input reordering.

        Args:
            files: List of file dicts with file_id, rel_path, extension
            symbols: List of symbol dicts with symbol_id, file_id, name, kind, language
            edges: List of edge dicts with source_type, source_id, target_type, target_id, edge_type, confidence
            imports: Optional, unused for now
            exports: Optional, unused for now

        Returns:
            Dict with keys: engine, nodes, edges, clusters
        """
        # Fail closed on ambiguous edge IDs before anything downstream can
        # collapse them by arrival order.
        edges = validate_edge_identity(edges)

        # Build a lookup from file_id to rel_path
        file_id_to_rel_path = {}
        for f in files:
            file_id_to_rel_path[f["file_id"]] = f["rel_path"]

        # Build nodes with deduplication
        nodes = []
        node_ids = set()
        seen_file_nodes = {}

        # File nodes
        for f in files:
            node_id = f"file:{f['file_id']}"
            norm_label = self._normalize_label(f["rel_path"])
            file_type = f["extension"].lstrip(".") if f["extension"] else "file"
            node_dict = {
                "id": node_id,
                "label": f["rel_path"],
                "norm_label": norm_label,
                "source_file": f["rel_path"],
                "type": "file",
                "kind": file_type,
                "file_type": file_type,
                # A file has no single line. Kept on every node so consumers can
                # read one field instead of branching on node type.
                "line": None,
                "community": 0,
            }

            # Check for duplicates
            if node_id in seen_file_nodes:
                existing = seen_file_nodes[node_id]
                if existing != node_dict:
                    raise DuplicateIdentityError(
                        f"Duplicate file node id '{node_id}' with conflicting fields. "
                        f"Existing: {existing}, New: {node_dict}"
                    )
            else:
                seen_file_nodes[node_id] = node_dict
                nodes.append(node_dict)
                node_ids.add(node_id)

        # Symbol nodes
        seen_symbol_nodes = {}
        for s in symbols:
            # Skip symbols whose file_id does not match any file
            if s["file_id"] not in file_id_to_rel_path:
                continue

            node_id = f"symbol:{s['symbol_id']}"
            norm_label = self._normalize_label(s["name"])
            source_file = file_id_to_rel_path[s["file_id"]]
            node_dict = {
                "id": node_id,
                "label": s["name"],
                "norm_label": norm_label,
                "source_file": source_file,
                "type": "symbol",
                "kind": s["kind"],
                "file_type": s["language"],
                # Scanners already carry line_start (ts_ast_adapter, core.languages);
                # without it every graph answer points at a file, not a location.
                "line": s.get("line_start"),
                "community": 0,
            }

            # Check for duplicates
            if node_id in seen_symbol_nodes:
                existing = seen_symbol_nodes[node_id]
                if existing != node_dict:
                    raise DuplicateIdentityError(
                        f"Duplicate symbol node id '{node_id}' with conflicting fields. "
                        f"Existing: {existing}, New: {node_dict}"
                    )
            else:
                seen_symbol_nodes[node_id] = node_dict
                nodes.append(node_dict)
                node_ids.add(node_id)

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
        graph_edges = canonicalize_edges(graph_edges)

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
        normalized = re.sub(r"[/\\_\-.]+", " ", label)
        # Collapse runs of whitespace
        normalized = re.sub(r"\s+", " ", normalized)
        # Strip
        normalized = normalized.strip()
        # Lowercase
        normalized = normalized.lower()
        return normalized

    def _cluster_by_folder(self, files: list) -> dict:
        """Group files by folder in deterministic order."""
        clusters = defaultdict(list)
        for f in files:
            rel_path = f["rel_path"]
            folder = rel_path.rsplit("/", 1)[0] if "/" in rel_path else "."
            clusters[folder].append(rel_path)
        # Sort folder names and sort the file lists within each folder
        return {folder: sorted(paths) for folder, paths in sorted(clusters.items())}
