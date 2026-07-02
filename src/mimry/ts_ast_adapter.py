from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import EXPORT_RE, IMPORT_RE
from .paths import stable_id
from .security import is_text

TS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}


def _language_for(path: Path) -> str:
    if path.suffix.lower() == ".tsx":
        return "tsx"
    if path.suffix.lower() == ".jsx":
        return "javascript"
    if path.suffix.lower() == ".ts":
        return "typescript"
    return "javascript"


def _text(source: str, node: Any) -> str:
    return source[node.start_byte() : node.end_byte()]


def _line(node: Any) -> int | None:
    try:
        return int(node.start_position().row) + 1
    except Exception:
        return None


def _end_line(node: Any) -> int | None:
    try:
        return int(node.end_position().row) + 1
    except Exception:
        return None


def _children(node: Any):
    for i in range(node.child_count()):
        yield node.child(i)


def _walk(node: Any):
    yield node
    for child in _children(node):
        yield from _walk(child)


def _first_identifier(node: Any, source: str) -> str | None:
    if node.kind() in {"identifier", "property_identifier", "type_identifier"}:
        return _text(source, node)
    for child in _children(node):
        found = _first_identifier(child, source)
        if found:
            return found
    return None


def _parent_kinds(node: Any, limit: int = 3) -> set[str]:
    kinds: set[str] = set()
    cur = node.parent()
    for _ in range(limit):
        if cur is None:
            break
        kinds.add(cur.kind())
        cur = cur.parent()
    return kinds


def _fallback_imports_exports(source: str) -> tuple[list[str], list[str]]:
    imports = [a or b for a, b in IMPORT_RE.findall(source) if a or b]
    exports = [m for m in EXPORT_RE.findall(source) if m]
    return sorted(set(imports)), sorted(set(exports))


def parse_ts_like(path: Path, root: Path, file_record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str], str]:
    """Parse JS/TS/JSX/TSX with tree-sitter and return symbols/edges/imports/exports/status."""
    if not is_text(path):
        return [], [], [], [], "parse_error:non_text"
    source = path.read_text(encoding="utf-8", errors="ignore")
    imports, exports = _fallback_imports_exports(source)
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()

    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(_language_for(path))
        tree = parser.parse(source)
        root_node = tree.root_node()
    except Exception as exc:
        return symbols, edges, imports, exports, f"parse_error:{exc.__class__.__name__}"

    def add_symbol(name: str | None, kind: str, node: Any, exported: bool = False, confidence: float = 0.95) -> None:
        if not name:
            return
        key = (name, kind, _line(node))
        if key in seen:
            return
        seen.add(key)
        sid = stable_id(file_record["file_id"], name, kind, _line(node) or 0)
        symbols.append(
            {
                "symbol_id": sid,
                "file_id": file_record["file_id"],
                "name": name,
                "kind": kind,
                "language": path.suffix.lower().lstrip("."),
                "exported": exported,
                "line_start": _line(node),
                "line_end": _end_line(node),
            }
        )
        edges.append(
            {
                "edge_id": stable_id(file_record["file_id"], sid, "defines"),
                "source_type": "file",
                "source_id": file_record["file_id"],
                "target_type": "symbol",
                "target_id": sid,
                "edge_type": "defines",
                "confidence": confidence,
            }
        )

    for node in _walk(root_node):
        kind = node.kind()
        parents = _parent_kinds(node)
        exported = "export_statement" in parents
        if kind == "function_declaration":
            add_symbol(_first_identifier(node, source), "function", node, exported)
        elif kind == "class_declaration":
            add_symbol(_first_identifier(node, source), "class", node, exported)
        elif kind == "variable_declarator":
            name = _first_identifier(node, source)
            text = _text(source, node)
            symbol_kind = "component" if "=>" in text and "<" in text else "variable"
            add_symbol(name, symbol_kind, node, exported, 0.85)
        elif kind in {"jsx_opening_element", "jsx_self_closing_element"}:
            name = _first_identifier(node, source)
            add_symbol(name, "jsx_element", node, False, 0.75)

    if root_node.has_error():
        return symbols, edges, imports, exports, "parse_error:tree_sitter_has_error"
    return symbols, edges, imports, exports, "ok"
