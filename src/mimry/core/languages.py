from __future__ import annotations

from pathlib import Path
from typing import Any

EXTENSION_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
}

NODE_TYPES = {
    "python": {
        "definitions": frozenset(["function_definition", "class_definition"]),
        "imports": frozenset(["import_statement", "import_from_statement"]),
        "calls": frozenset(["call"]),
    },
    "javascript": {
        "definitions": frozenset(["function_declaration", "class_declaration", "variable_declarator"]),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
    },
    "typescript": {
        "definitions": frozenset(["function_declaration", "class_declaration", "variable_declarator"]),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
    },
    "tsx": {
        "definitions": frozenset(["function_declaration", "class_declaration", "variable_declarator"]),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
    },
    "go": {
        "definitions": frozenset(["function_declaration", "method_declaration", "type_declaration"]),
        "imports": frozenset(["import_declaration", "import_spec"]),
        "calls": frozenset(["call_expression"]),
    },
    "rust": {
        "definitions": frozenset(["function_item", "struct_item", "impl_item", "mod_item"]),
        "imports": frozenset(["use_declaration"]),
        "calls": frozenset(["call_expression"]),
    },
    "csharp": {
        "definitions": frozenset(["class_declaration", "method_declaration", "namespace_declaration"]),
        "imports": frozenset(["using_directive"]),
        "calls": frozenset(["invocation_expression"]),
    },
}


def language_for(path: str | Path) -> str | None:
    """Return the grammar name for a path's extension, or None if unsupported."""
    if isinstance(path, str):
        path = Path(path)
    ext = path.suffix.lower()
    return EXTENSION_LANGUAGE.get(ext)


def symbol_kind(language: str, node_kind: str) -> str:
    """Map a tree-sitter node kind to MIMRY's symbol kind vocabulary."""
    if "class" in node_kind or "struct" in node_kind:
        return "class"
    if node_kind == "impl_item":
        return "class"
    if node_kind in ("namespace_declaration", "mod_item"):
        return "module"
    if node_kind == "type_declaration":
        return "type"
    if node_kind == "variable_declarator":
        return "variable"
    return "function"


def _text(source: str, node: Any) -> str:
    """Extract text from source using byte offsets."""
    return source[node.start_byte() : node.end_byte()]


def _line(node: Any) -> int | None:
    """Get 1-indexed line number from node."""
    try:
        return int(node.start_position().row) + 1
    except Exception:
        return None


def _end_line(node: Any) -> int | None:
    """Get 1-indexed end line number from node."""
    try:
        return int(node.end_position().row) + 1
    except Exception:
        return None


def _children(node: Any):
    """Iterate over child nodes."""
    for i in range(node.child_count()):
        yield node.child(i)


def _walk(node: Any):
    """Depth-first walk of tree, document order."""
    yield node
    for child in _children(node):
        yield from _walk(child)


def _first_identifier(node: Any, source: str) -> str | None:
    """Find the first identifier-like child recursively."""
    kind = node.kind()
    if kind in ("identifier", "type_identifier", "field_identifier", "property_identifier", "name") or kind.endswith("identifier"):
        return _text(source, node)
    for child in _children(node):
        found = _first_identifier(child, source)
        if found:
            return found
    return None


def _parent_kinds(node: Any, limit: int = 3) -> set[str]:
    """Collect ancestor node kinds up to limit."""
    kinds: set[str] = set()
    cur = node.parent()
    for _ in range(limit):
        if cur is None:
            break
        kinds.add(cur.kind())
        cur = cur.parent()
    return kinds


def _is_exported(node: Any, language: str, source: str) -> bool:
    """Check if a symbol is exported based on language rules."""
    parents = _parent_kinds(node)

    # JS/TS: check for export_statement ancestor
    if language in ("javascript", "typescript", "tsx"):
        return "export_statement" in parents

    # Go: uppercase first character
    if language == "go":
        name = _first_identifier(node, source)
        if name and name[0].isupper():
            return True
        return False

    # Rust: check for visibility_modifier child
    if language == "rust":
        for child in _children(node):
            if child.kind() == "visibility_modifier":
                return True
        return False

    # C#: check for "public" in text
    if language == "csharp":
        text = _text(source, node)
        return "public" in text

    # Python: no exported concept in this context
    return False


def _extract_module_from_import(import_text: str, language: str) -> str:
    """Extract module name from import statement text."""
    # Strip quotes if present
    text = import_text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    elif text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    return text.strip()


def extract(path: str | Path, source: str) -> dict:
    """Parse and return definitions, imports, calls with status."""
    if isinstance(path, str):
        path = Path(path)

    lang = language_for(path)
    if lang is None:
        return {
            "definitions": [],
            "imports": [],
            "calls": [],
            "status": "parse_error:unsupported_language",
        }

    if lang not in NODE_TYPES:
        return {
            "definitions": [],
            "imports": [],
            "calls": [],
            "status": "parse_error:unsupported_language",
        }

    node_types = NODE_TYPES[lang]
    definitions: list[dict] = []
    imports: list[dict] = []
    calls: list[dict] = []
    seen_defs: set[tuple[str, int | None]] = set()

    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(lang)
        tree = parser.parse(source)
        root_node = tree.root_node()
    except Exception as exc:
        return {
            "definitions": [],
            "imports": [],
            "calls": [],
            "status": f"parse_error:{exc.__class__.__name__}",
        }

    # Walk tree and collect symbols
    for node in _walk(root_node):
        kind = node.kind()

        # Definitions
        if kind in node_types["definitions"]:
            name = _first_identifier(node, source)
            if name:  # Skip if no name found
                line = _line(node)
                key = (name, line)
                if key not in seen_defs:
                    seen_defs.add(key)
                    exported = _is_exported(node, lang, source)
                    definitions.append({
                        "name": name,
                        "kind": symbol_kind(lang, kind),
                        "line_start": line,
                        "line_end": _end_line(node),
                        "exported": exported,
                    })

        # Imports
        if kind in node_types["imports"]:
            import_text = _text(source, node)
            module = None

            # Try to find string literals first (JS/TS/Go)
            for child in _walk(node):
                child_kind = child.kind()
                if "string" in child_kind or "literal" in child_kind:
                    module_text = _text(source, child)
                    # Strip quotes from both ends
                    module = module_text.strip().strip('"').strip("'").strip("`")
                    if module and module not in ('"', "'", "`"):
                        break

            # Fallback: try to extract from identifier/scoped_identifier nodes (Python/Rust/C#)
            if not module:
                for child in _children(node):
                    child_kind = child.kind()
                    if child_kind in ("identifier", "dotted_name", "scoped_identifier", "qualified_name"):
                        module = _text(source, child)
                        break

            # Last resort: regex parsing for specific languages
            if not module:
                if lang == "python":
                    if "from" in import_text:
                        # from X import Y - module is X
                        parts = import_text.split("from", 1)[1].split("import", 1)
                        if len(parts) > 0:
                            module = parts[0].strip()
                    elif "import" in import_text:
                        # import X - module is X
                        parts = import_text.split("import", 1)
                        if len(parts) > 1:
                            module = parts[1].strip()

            if module:
                imports.append({
                    "module": module,
                    "line": _line(node),
                })

        # Calls
        if kind in node_types["calls"]:
            # For call expressions, get the function name (first child)
            first_child = None
            for child in _children(node):
                first_child = child
                break

            if first_child:
                call_name = _text(source, first_child)
                if call_name.strip():
                    calls.append({
                        "name": call_name,
                        "line": _line(node),
                    })

    # Check for parse errors
    if root_node.has_error():
        return {
            "definitions": definitions,
            "imports": imports,
            "calls": calls,
            "status": "parse_error:tree_sitter_has_error",
        }

    return {
        "definitions": definitions,
        "imports": imports,
        "calls": calls,
        "status": "ok",
    }


if __name__ == "__main__":
    import sys
    import tempfile

    # Self-check
    try:
        # Test language_for
        assert language_for("test.py") == "python"
        assert language_for("test.js") == "javascript"
        assert language_for("test.jsx") == "javascript"
        assert language_for("test.ts") == "typescript"
        assert language_for("test.tsx") == "tsx"
        assert language_for("test.go") == "go"
        assert language_for("test.rs") == "rust"
        assert language_for("test.cs") == "csharp"
        assert language_for("test.txt") is None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Python sample
            py_file = tmppath / "test.py"
            py_file.write_text("""
import os
from sys import path

def hello():
    print("world")

class MyClass:
    def method(self):
        os.getcwd()
""")
            result = extract(py_file, py_file.read_text())
            assert result["status"] == "ok", f"Python parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in python"
            assert len(result["imports"]) > 0, "No imports in python"
            assert len(result["calls"]) > 0, "No calls in python"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            py_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # JavaScript sample
            js_file = tmppath / "test.js"
            js_file.write_text("""
import React from 'react';
import { useState } from 'react';

function App() {
    const greeting = () => <h1>Hello</h1>;
    console.log("test");
}

class Counter {
    render() {
        return <div>Count</div>;
    }
}
""")
            result = extract(js_file, js_file.read_text())
            assert result["status"] == "ok", f"JavaScript parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in js"
            assert len(result["imports"]) > 0, "No imports in js"
            assert len(result["calls"]) > 0, "No calls in js"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            js_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # TypeScript sample
            ts_file = tmppath / "test.ts"
            ts_file.write_text("""
import { Component } from '@angular/core';

export function process(data: string): void {
    console.log(data);
}

export class Handler {
    execute() {
        process("test");
    }
}
""")
            result = extract(ts_file, ts_file.read_text())
            assert result["status"] == "ok", f"TypeScript parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in ts"
            assert len(result["imports"]) > 0, "No imports in ts"
            assert len(result["calls"]) > 0, "No calls in ts"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            ts_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # TSX sample (critical: must parse with tsx grammar)
            tsx_file = tmppath / "test.tsx"
            tsx_file.write_text("""
import React from 'react';

export function MyComponent() {
    return <div>Content</div>;
}

export class PageComponent extends React.Component {
    render() {
        const result = MyComponent();
        return <MyComponent />;
    }
}
""")
            result = extract(tsx_file, tsx_file.read_text())
            assert result["status"] == "ok", f"TSX parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in tsx"
            assert len(result["imports"]) > 0, "No imports in tsx"
            assert len(result["calls"]) > 0, "No calls in tsx"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            tsx_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # Go sample
            go_file = tmppath / "test.go"
            go_file.write_text("""package main

import (
    "fmt"
    "os"
)

func main() {
    fmt.Println("hello")
    os.Exit(0)
}

type Config struct {
    Name string
}
""")
            result = extract(go_file, go_file.read_text())
            assert result["status"] == "ok", f"Go parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in go"
            assert len(result["imports"]) > 0, "No imports in go"
            assert len(result["calls"]) > 0, "No calls in go"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            go_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # Rust sample
            rs_file = tmppath / "test.rs"
            rs_file.write_text("""use std::fmt;
use std::io;

fn main() {
    helper();
}

fn helper() {
    println!("test");
}

struct Point {
    x: i32,
}
""")
            result = extract(rs_file, rs_file.read_text())
            assert result["status"] == "ok", f"Rust parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in rust"
            assert len(result["imports"]) > 0, "No imports in rust"
            assert len(result["calls"]) > 0, "No calls in rust"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            rs_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # C# sample
            cs_file = tmppath / "test.cs"
            cs_file.write_text("""using System;
using System.Collections;

namespace MyApp {
    public class MyClass {
        public void MyMethod() {
            Console.WriteLine("hello");
        }
    }
}
""")
            result = extract(cs_file, cs_file.read_text())
            assert result["status"] == "ok", f"CSharp parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in csharp"
            assert len(result["imports"]) > 0, "No imports in csharp"
            assert len(result["calls"]) > 0, "No calls in csharp"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            cs_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

        print("OK")
        print(f"Python: {py_counts[0]} defs, {py_counts[1]} imports, {py_counts[2]} calls")
        print(f"JavaScript: {js_counts[0]} defs, {js_counts[1]} imports, {js_counts[2]} calls")
        print(f"TypeScript: {ts_counts[0]} defs, {ts_counts[1]} imports, {ts_counts[2]} calls")
        print(f"TSX: {tsx_counts[0]} defs, {tsx_counts[1]} imports, {tsx_counts[2]} calls")
        print(f"Go: {go_counts[0]} defs, {go_counts[1]} imports, {go_counts[2]} calls")
        print(f"Rust: {rs_counts[0]} defs, {rs_counts[1]} imports, {rs_counts[2]} calls")
        print(f"CSharp: {cs_counts[0]} defs, {cs_counts[1]} imports, {cs_counts[2]} calls")
        sys.exit(0)
    except AssertionError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
