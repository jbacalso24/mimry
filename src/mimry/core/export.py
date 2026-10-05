"""Export graph in multiple formats: HTML, GraphML, Cypher, Obsidian."""

from __future__ import annotations

import collections
import html
import importlib.resources
import json
import re
import shutil
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path

from ..security import markdown_inline


class ExportTargetError(Exception):
    """Directory exists and is not safe to overwrite."""


def _sanitize_cypher_string(text: str) -> str:
    """Escape a string for single-quoted Cypher literals."""
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "\\r")
    return text


def _sanitize_obsidian_path(segment: str) -> str:
    """Remove characters Obsidian forbids in filenames.

    Removes: * \" \\ < > : | ? # ^ [ ]
    """
    forbidden = r'[*"\\<>:|?#^\[\]]'
    sanitized = re.sub(forbidden, "_", segment)
    # Collapse repeated underscores from consecutive forbidden chars
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("_. ")
    return sanitized


def _deterministic_hash_key(text: str) -> str:
    """Create a stable, short hash key for disambiguation."""
    digest = sha256(text.encode("utf-8")).hexdigest()
    return digest[:8]


def _xml_text(text: str) -> str:
    """Drop characters XML 1.0 cannot hold.

    ElementTree escapes the rest.
    """
    text = str(text)
    # XML 1.0 forbids control chars except tab, newline, carriage return
    allowed_chars = []
    for ch in text:
        code = ord(ch)
        if code == 0x09 or code == 0x0A or code == 0x0D or code >= 0x20:
            if code != 0xFFFE and code != 0xFFFF:
                allowed_chars.append(ch)
    return "".join(allowed_chars)


def _read_commit_from_report(root: Path) -> str | None:
    """Read commit hash from GRAPH_REPORT.md if available."""
    report_path = root / ".mimry" / "mimry-out" / "graph" / "GRAPH_REPORT.md"
    if not report_path.exists():
        return None
    try:
        lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            if line.startswith("- Built from commit:"):
                commit = line.split(":", 1)[1].strip().strip("`")
                return commit if commit else None
    except (OSError, IndexError):
        pass
    return None


def export_html(graph: dict, root: Path, output_path: Path, repo_name: str | None = None) -> None:
    """Export graph as self-contained interactive HTML viewer."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", graph.get("links", []))

    viewer_nodes = []
    for node in sorted(nodes, key=lambda n: str(n.get("id", ""))):
        viewer_nodes.append(
            {
                "id": str(node.get("id", "")),
                "label": str(node.get("label", "")),
                "type": str(node.get("type", "")),
                "kind": str(node.get("kind", "")),
                "source_file": str(node.get("source_file", "")),
                "line": node.get("line"),
                "community": node.get("community"),
            }
        )

    viewer_edges = []
    for edge in sorted(
        edges,
        key=lambda e: (
            str(e.get("source", "")),
            str(e.get("target", "")),
            str(e.get("relation", "")),
            str(e.get("confidence", "")),
        ),
    ):
        viewer_edges.append(
            {
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "relation": str(edge.get("relation", "")),
                "confidence": str(edge.get("confidence", "")),
            }
        )

    payload = {
        "nodes": viewer_nodes,
        "edges": viewer_edges,
        "meta": {
            "title": repo_name or root.name,
            "nodeCount": len(viewer_nodes),
            "edgeCount": len(viewer_edges),
            "commit": _read_commit_from_report(root),
        },
    }
    graph_json = json.dumps(payload, separators=(",", ":"))
    # json.dumps already writes U+2028/U+2029 as \u escapes.
    # These replace < > & to prevent closing <script> early.
    graph_json = graph_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    template_text = (
        importlib.resources.files("mimry.core").joinpath("viewer.html").read_text(encoding="utf-8")
    )

    html_content = template_text.replace("__MIMRY_TITLE__", html.escape(repo_name or root.name))
    html_content = html_content.replace("__MIMRY_GRAPH_JSON__", graph_json)

    output_path.write_text(html_content, encoding="utf-8", newline="\n")


def export_graphml(graph: dict, output_path: Path) -> None:
    """Export graph as GraphML for Gephi, yEd, networkx."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", graph.get("links", []))

    root = ET.Element("graphml")
    root.set("xmlns", "http://graphml.graphdrawing.org/xmlns")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("version", "1.0")

    # Key definitions
    for key_id, key_name, key_type in [
        ("label", "label", "string"),
        ("type", "type", "string"),
        ("kind", "kind", "string"),
        ("source_file", "source_file", "string"),
        ("line", "line", "int"),
        ("community", "community", "int"),
    ]:
        key = ET.SubElement(root, "key")
        key.set("id", key_id)
        key.set("for", "node")
        key.set("attr.name", key_name)
        key.set("attr.type", key_type)

    for key_id, key_name, key_type in [
        ("relation", "relation", "string"),
        ("confidence", "confidence", "string"),
    ]:
        key = ET.SubElement(root, "key")
        key.set("id", key_id)
        key.set("for", "edge")
        key.set("attr.name", key_name)
        key.set("attr.type", key_type)

    graph_elem = ET.SubElement(root, "graph")
    graph_elem.set("edgedefault", "directed")

    # Add nodes in sorted order
    for node in sorted(nodes, key=lambda n: str(n.get("id", ""))):
        node_elem = ET.SubElement(graph_elem, "node")
        node_elem.set("id", str(node.get("id", "")))

        for key_id, field_name in [
            ("label", "label"),
            ("type", "type"),
            ("kind", "kind"),
            ("source_file", "source_file"),
            ("line", "line"),
            ("community", "community"),
        ]:
            value = node.get(field_name)
            if value is not None:
                data = ET.SubElement(node_elem, "data")
                data.set("key", key_id)
                data.text = _xml_text(str(value))

    # Add edges in sorted order
    for i, edge in enumerate(
        sorted(
            edges,
            key=lambda e: (
                str(e.get("source", "")),
                str(e.get("target", "")),
                str(e.get("relation", "")),
                str(e.get("confidence", "")),
            ),
        )
    ):
        edge_elem = ET.SubElement(graph_elem, "edge")
        edge_elem.set("id", f"e{i}")
        edge_elem.set("source", str(edge.get("source", "")))
        edge_elem.set("target", str(edge.get("target", "")))

        for key_id, field_name in [("relation", "relation"), ("confidence", "confidence")]:
            value = edge.get(field_name)
            if value:
                data = ET.SubElement(edge_elem, "data")
                data.set("key", key_id)
                data.text = _xml_text(str(value))

    # Write with proper XML declaration
    tree = ET.ElementTree(root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def _cypher_map(fields: dict) -> str:
    """Render a Cypher row.

    Keys in fixed order, strings quoted, ints bare, None skipped.
    """
    key_order = [
        "id",
        "label",
        "type",
        "kind",
        "source_file",
        "line",
        "community",
        "source",
        "target",
        "relation",
        "confidence",
    ]
    parts = []
    for key in key_order:
        if key not in fields or fields[key] is None:
            continue
        val = fields[key]
        if isinstance(val, int):
            parts.append(f"{key}: {val}")
        else:
            parts.append(f"{key}: '{_sanitize_cypher_string(str(val))}'")
    return "{" + ", ".join(parts) + "}"


def export_cypher(graph: dict, output_path: Path) -> None:
    """Export graph as Neo4j Cypher import script."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", graph.get("links", []))

    lines = []
    lines.append("// Generated by mimry export. Run with: cypher-shell -f graph.cypher")
    lines.append("CREATE CONSTRAINT file_id IF NOT EXISTS FOR (f:File) REQUIRE f.id IS UNIQUE;")
    lines.append(
        "CREATE CONSTRAINT symbol_id IF NOT EXISTS FOR (s:Symbol) REQUIRE s.id IS UNIQUE;"
    )
    lines.append("")

    # Sort nodes once, split by label
    sorted_nodes = sorted(nodes, key=lambda n: str(n.get("id", "")))
    file_nodes = [n for n in sorted_nodes if str(n.get("type")) == "file"]
    symbol_nodes = [n for n in sorted_nodes if str(n.get("type")) != "file"]

    # Build node label map
    label_of = {}
    for node in sorted_nodes:
        node_id = str(node.get("id", ""))
        label_of[node_id] = "File" if str(node.get("type")) == "file" else "Symbol"

    # Write nodes by label in batches of 500
    for node_list, node_label in [(file_nodes, "File"), (symbol_nodes, "Symbol")]:
        for batch_idx in range(0, len(node_list), 500):
            batch = node_list[batch_idx : batch_idx + 500]
            lines.append("UNWIND [")
            for node in batch:
                row_dict = {
                    "id": str(node.get("id", "")),
                    "label": str(node.get("label", "")),
                    "kind": str(node.get("kind", "")),
                    "source_file": str(node.get("source_file", "")),
                }
                if node.get("line") is not None:
                    row_dict["line"] = node.get("line")
                if node.get("community") is not None:
                    row_dict["community"] = node.get("community")
                lines.append("  " + _cypher_map(row_dict) + ",")
            lines[-1] = lines[-1].rstrip(",")  # Remove trailing comma from last row
            lines.append("] AS row")
            lines.append(f"MERGE (n:{node_label} {{id: row.id}})")
            lines.append("SET n += row;")
            lines.append("")

    # Group edges by (source_label, target_label, rel_type), sorted
    edge_groups = {}
    for edge in edges:
        source_id = str(edge.get("source", ""))
        target_id = str(edge.get("target", ""))
        relation = str(edge.get("relation", ""))

        if source_id not in label_of or target_id not in label_of:
            continue

        rel_type = re.sub(r"[^A-Z0-9_]", "_", relation.upper()).strip("_") or "RELATES"
        if rel_type[0].isdigit():
            rel_type = "R_" + rel_type

        key = (label_of[source_id], label_of[target_id], rel_type)
        if key not in edge_groups:
            edge_groups[key] = []
        edge_groups[key].append(edge)

    # Sort groups and write edges in batches
    for src_label, tgt_label, rel_type in sorted(edge_groups.keys()):
        edge_list = sorted(
            edge_groups[(src_label, tgt_label, rel_type)],
            key=lambda e: (str(e.get("source", "")), str(e.get("target", ""))),
        )
        for batch_idx in range(0, len(edge_list), 500):
            batch = edge_list[batch_idx : batch_idx + 500]
            lines.append("UNWIND [")
            for edge in batch:
                row_dict = {
                    "source": str(edge.get("source", "")),
                    "target": str(edge.get("target", "")),
                    "relation": str(edge.get("relation", "")),
                    "confidence": str(edge.get("confidence", "")),
                }
                lines.append("  " + _cypher_map(row_dict) + ",")
            lines[-1] = lines[-1].rstrip(",")  # Remove trailing comma
            lines.append("] AS row")
            lines.append(f"MATCH (s:{src_label} {{id: row.source}})")
            lines.append(f"MATCH (t:{tgt_label} {{id: row.target}})")
            lines.append(f"MERGE (s)-[r:{rel_type}]->(t)")
            lines.append("SET r.relation = row.relation, r.confidence = row.confidence;")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _build_obsidian_note_content(
    node: dict, file_symbols: list, file_links: dict, note_rel_by_file: dict
) -> list[str]:
    """Build Markdown content for an Obsidian note from file node."""
    content_lines = ["---"]
    content_lines.append('mimry_type: "file"')
    source_file = node.get("source_file", "").strip()
    if node.get("community") is not None:
        content_lines.append(f"community: {node.get('community')}")
    content_lines.append(f'source: "{markdown_inline(source_file)}"')
    content_lines.append("---")
    content_lines.append("")

    # Symbols section
    if file_symbols:
        content_lines.append("## Symbols")
        content_lines.append("")

        def _symbol_sort_key(s: dict) -> tuple:
            return (s.get("line") or 0, str(s.get("label", "")))

        for sym in sorted(file_symbols, key=_symbol_sort_key):
            sym_label = markdown_inline(str(sym.get("label", "")))
            sym_kind = markdown_inline(str(sym.get("kind", "")))
            sym_line = sym.get("line")
            if sym_line:
                content_lines.append(f"- {sym_label} ({sym_kind}, line {sym_line})")
            else:
                content_lines.append(f"- {sym_label} ({sym_kind})")
        content_lines.append("")

    # Links section
    if file_links:
        content_lines.append("## Links")
        content_lines.append("")
        for relation in sorted(file_links.keys()):
            content_lines.append(f"### {markdown_inline(relation)}")
            content_lines.append("")
            for tgt_file, conf in sorted(set(file_links[relation])):
                if tgt_file not in note_rel_by_file:
                    continue
                tgt_rel_path = note_rel_by_file[tgt_file] + ".md"
                tgt_label = Path(tgt_file).name
                inferred = " (inferred)" if conf == "INFERRED" else ""
                safe_label = markdown_inline(tgt_label)
                content_lines.append(f"- [[{tgt_rel_path}|{safe_label}]]{inferred}")
            content_lines.append("")

    return content_lines


def export_obsidian(graph: dict, root: Path, output_path: Path, force: bool = False) -> None:
    """Export graph as Obsidian vault of linked notes."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", graph.get("links", []))

    # Check if directory exists and enforce safety
    marker_path = output_path / ".mimry-export"
    if output_path.exists():
        if marker_path.exists():
            # MIMRY created this vault; safe to delete
            shutil.rmtree(output_path)
        elif not force and any(output_path.iterdir()):
            # Not MIMRY's and not empty; raise unless force=True
            raise ExportTargetError(
                f"{output_path} is not empty and was not created by mimry export."
                " Use --force to write into it; nothing is deleted."
            )
        # With force=True and no marker: write into existing directory

    output_path.mkdir(parents=True, exist_ok=True)

    # Build file node map and edges at file level
    file_nodes = {}
    symbol_nodes = {}

    for node in nodes:
        node_id = str(node.get("id", ""))
        node_type = str(node.get("type", ""))
        if node_type == "file":
            file_nodes[node_id] = node
        else:
            symbol_nodes[node_id] = node

    # Precompute once: symbols by file, links by source, degree counter
    symbols_by_file = {}  # source_file -> [symbol nodes]
    links_by_source = {}  # source_file -> {relation -> [(target_file, confidence)]}
    degree = collections.Counter()  # node_id -> edge count

    for symbol_node in symbol_nodes.values():
        sf = symbol_node.get("source_file")
        if sf:
            if sf not in symbols_by_file:
                symbols_by_file[sf] = []
            symbols_by_file[sf].append(symbol_node)

    for edge in edges:
        source_id = str(edge.get("source", ""))
        target_id = str(edge.get("target", ""))
        relation = str(edge.get("relation", ""))
        confidence = str(edge.get("confidence", ""))

        degree[source_id] += 1
        degree[target_id] += 1

        source_file = None
        target_file = None

        if source_id in file_nodes:
            source_file = file_nodes[source_id].get("source_file")
        elif source_id in symbol_nodes:
            source_file = symbol_nodes[source_id].get("source_file")

        if target_id in file_nodes:
            target_file = file_nodes[target_id].get("source_file")
        elif target_id in symbol_nodes:
            target_file = symbol_nodes[target_id].get("source_file")

        if source_file and target_file and source_file != target_file:
            if source_file not in links_by_source:
                links_by_source[source_file] = {}
            if relation not in links_by_source[source_file]:
                links_by_source[source_file][relation] = []
            links_by_source[source_file][relation].append((target_file, confidence))

    # Pass 1: assign note paths and check validity
    notes = {}  # source_file -> (node, rel_path)
    taken = set()  # rel_path.casefold() to detect case-only collisions
    for node_id in sorted(file_nodes.keys()):
        node = file_nodes[node_id]
        source_file = node.get("source_file", "").strip()
        if not source_file:
            continue

        parts = Path(source_file).parts
        safe_parts = [_sanitize_obsidian_path(p) for p in parts]
        safe_parts = [p for p in safe_parts if p]  # Drop empty segments

        if not safe_parts:
            continue

        rel_path = "/".join(safe_parts)
        note_path = output_path / (rel_path + ".md")

        # Check it resolves inside vault BEFORE mkdir
        try:
            note_path.resolve().relative_to(output_path.resolve())
        except ValueError:
            continue

        # Check for collision and disambiguate (case-insensitive)
        if rel_path.casefold() in taken:
            rel_path += f"-{_deterministic_hash_key(source_file)}"

        taken.add(rel_path.casefold())
        notes[source_file] = (node, rel_path)

    # Derive note_rel_by_file for link building
    note_rel_by_file = {f: rel for f, (_, rel) in notes.items()}

    # Pass 2: write notes using precomputed data
    for source_file, (node, rel_path) in notes.items():
        note_path = output_path / (rel_path + ".md")
        note_path.parent.mkdir(parents=True, exist_ok=True)

        # Gather symbols and links from precomputed data
        file_symbols = symbols_by_file.get(source_file, [])
        file_links = links_by_source.get(source_file, {})

        # Build and write note content
        content_lines = _build_obsidian_note_content(
            node, file_symbols, file_links, note_rel_by_file
        )
        note_path.write_text("\n".join(content_lines), encoding="utf-8", newline="\n")

    # Write index note
    communities = {}
    for node in file_nodes.values():
        comm = node.get("community")
        if comm is not None:
            if comm not in communities:
                communities[comm] = []
            communities[comm].append(node)

    index_lines = ["# MIMRY Graph Index", ""]

    if communities:
        index_lines.append("## Communities")
        index_lines.append("")
        for comm in sorted(communities.keys()):
            hub_node = min(
                communities[comm],
                key=lambda n: (-degree[n.get("id", "")], n.get("source_file", "")),
            )
            hub_file = hub_node.get("source_file", "?")
            index_lines.append(f"### Community {comm}")
            index_lines.append(f"Hub: {markdown_inline(hub_file)}")
            index_lines.append("")

    index_lines.append("## Most Connected Files")
    index_lines.append("")
    top_files = sorted(
        [(nid, file_nodes[nid]) for nid in file_nodes.keys()],
        key=lambda x: (-degree[x[0]], x[1].get("source_file", "")),
    )[:10]

    for node_id, node in top_files:
        source_file = node.get("source_file", "?")
        edge_count = degree[node_id]
        index_lines.append(f"- {markdown_inline(source_file)} ({edge_count} edges)")

    index_lines.append("")
    index_file = output_path / "MIMRY Graph.md"
    index_file.write_text("\n".join(index_lines), encoding="utf-8", newline="\n")

    # Write marker
    marker_path.write_text("", encoding="utf-8")
