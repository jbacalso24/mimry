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
        "bases": frozenset(["argument_list"]),
    },
    "javascript": {
        "definitions": frozenset(
            ["function_declaration", "class_declaration", "variable_declarator", "interface_declaration"]
        ),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["class_heritage", "extends_type_clause"]),
    },
    "typescript": {
        "definitions": frozenset(
            ["function_declaration", "class_declaration", "variable_declarator", "interface_declaration"]
        ),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["class_heritage", "extends_type_clause"]),
    },
    "tsx": {
        "definitions": frozenset(
            ["function_declaration", "class_declaration", "variable_declarator", "interface_declaration"]
        ),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["class_heritage", "extends_type_clause"]),
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
        # Interfaces, records and structs are first-class types in .NET; without them
        # "what implements IFoo" has no node to point at.
        "definitions": frozenset(
            [
                "class_declaration",
                "method_declaration",
                "namespace_declaration",
                "interface_declaration",
                "record_declaration",
                "struct_declaration",
                "enum_declaration",
            ]
        ),
        "imports": frozenset(["using_directive"]),
        "calls": frozenset(["invocation_expression"]),
        "bases": frozenset(["base_list"]),
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
    if node_kind == "interface_declaration":
        return "interface"
    if node_kind == "enum_declaration":
        return "enum"
    if node_kind == "record_declaration":
        return "class"
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


def _text(source: bytes, node: Any) -> str:
    """Extract a node's text.

    `source` must be the UTF-8 *bytes*, because tree-sitter reports byte offsets.
    Slicing the decoded str instead shifts every name by the number of extra bytes
    ahead of it -- a UTF-8 BOM alone costs 2 characters, and Visual Studio writes
    C#/TS files with a BOM by default, so `formattedNumber` came out `rmattedNumber`.
    """
    return source[node.start_byte() : node.end_byte()].decode("utf-8", "replace")


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
    if kind in ("identifier", "type_identifier", "field_identifier", "property_identifier", "name") or kind.endswith(
        "identifier"
    ):
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


_IDENTIFIER_KINDS = ("identifier", "type_identifier", "field_identifier", "property_identifier", "name")


def _is_identifier_kind(kind: str) -> bool:
    return kind in _IDENTIFIER_KINDS or kind.endswith("identifier")


def _expression_name(node: Any, source: bytes) -> str | None:
    """Reduce a callee expression to the identifier a caller would match on.

    ponytail: positional heuristic, not name resolution. Upgrade to per-language
    field lookups if a language needs more than "last segment wins".
    """
    kind = node.kind()
    if _is_identifier_kind(kind):
        return _text(source, node)
    if "generic" in kind:
        # GetService<IFoo>() -- the method is the first segment, not the type argument.
        for child in _children(node):
            name = _expression_name(child, source)
            if name:
                return name
        return None
    # Member access (a.b.Method): the method is the last segment.
    for child in reversed(list(_children(node))):
        if "argument" in child.kind():
            continue
        name = _expression_name(child, source)
        if name:
            return name
    return None


def _base_type_names(container: Any, source: bytes) -> list[str]:
    """Every base type named in a heritage clause, in source order.

    Deliberately not a _walk: descending the whole subtree would also collect the
    type arguments, so `IHandler<Command>` would yield `Command` as a second base.
    Recurse only through the clause wrappers (TS splits extends/implements into two).
    """
    names: list[str] = []

    def visit(node: Any) -> None:
        for child in _children(node):
            kind = child.kind()
            if kind in ("extends_clause", "implements_clause", "extends_type_clause"):
                visit(child)
                continue
            name = _expression_name(child, source)
            if name and name not in names:
                names.append(name)

    visit(container)
    return names


def _callee_name(node: Any, source: bytes) -> str | None:
    """The called function's name, not the expression that produced it.

    A call node's first child is the whole function expression. Using its text
    verbatim turns `schemes.Where(x => ...)` into a multi-line blob -- unmatchable
    as a call target, and large enough to drag file content into the record (one
    such blob tripped the secret scanner and silently dropped the file from the
    index). Take the final identifier, as the Python adapter does with `Attribute.attr`.
    """
    callee = next(_children(node), None)
    if callee is None:
        return None
    name = (_expression_name(callee, source) or "").strip()
    # Anything still carrying whitespace is an expression we failed to reduce.
    # Dropping it beats emitting an edge nothing can resolve.
    if not name or any(char.isspace() for char in name):
        return None
    return name


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
            "inherits": [],
            "status": "parse_error:unsupported_language",
        }

    if lang not in NODE_TYPES:
        return {
            "definitions": [],
            "imports": [],
            "calls": [],
            "inherits": [],
            "status": "parse_error:unsupported_language",
        }

    node_types = NODE_TYPES[lang]
    definitions: list[dict] = []
    imports: list[dict] = []
    calls: list[dict] = []
    inherits: list[dict] = []
    seen_defs: set[tuple[str, int | None]] = set()
    base_kinds = node_types.get("bases", frozenset())

    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(lang)
        tree = parser.parse(source)
        root_node = tree.root_node()
        # Past this point every read is by byte offset, so rebind to bytes once
        # rather than at each _text call site -- one missed site is silent corruption.
        source = source.encode("utf-8")
    except Exception as exc:
        return {
            "definitions": [],
            "imports": [],
            "calls": [],
            "inherits": [],
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
                    definitions.append(
                        {
                            "name": name,
                            "kind": symbol_kind(lang, kind),
                            "line_start": line,
                            "line_end": _end_line(node),
                            "exported": exported,
                        }
                    )
                    # Heritage hangs directly off the type declaration. Functions have
                    # no such child, so this never fires for them.
                    for child in _children(node):
                        if child.kind() in base_kinds:
                            for base in _base_type_names(child, source):
                                inherits.append({"type": name, "base": base, "line": line})

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
                imports.append(
                    {
                        "module": module,
                        "line": _line(node),
                    }
                )

        # Calls
        if kind in node_types["calls"]:
            call_name = _callee_name(node, source)
            if call_name:
                calls.append(
                    {
                        "name": call_name,
                        "line": _line(node),
                    }
                )

    # Check for parse errors
    if root_node.has_error():
        return {
            "definitions": definitions,
            "imports": imports,
            "calls": calls,
            "inherits": inherits,
            "status": "parse_error:tree_sitter_has_error",
        }

    return {
        "definitions": definitions,
        "imports": imports,
        "calls": calls,
        "inherits": inherits,
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
            py_file.write_text(
                """
import os
from sys import path

def hello():
    print("world")

class MyClass:
    def method(self):
        os.getcwd()
""",
                encoding="utf-8",
            )
            result = extract(py_file, py_file.read_text(encoding="utf-8"))
            assert result["status"] == "ok", f"Python parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in python"
            assert len(result["imports"]) > 0, "No imports in python"
            assert len(result["calls"]) > 0, "No calls in python"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            py_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # JavaScript sample
            js_file = tmppath / "test.js"
            js_file.write_text(
                """
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
""",
                encoding="utf-8",
            )
            result = extract(js_file, js_file.read_text(encoding="utf-8"))
            assert result["status"] == "ok", f"JavaScript parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in js"
            assert len(result["imports"]) > 0, "No imports in js"
            assert len(result["calls"]) > 0, "No calls in js"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            js_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # TypeScript sample
            ts_file = tmppath / "test.ts"
            ts_file.write_text(
                """
import { Component } from '@angular/core';

export function process(data: string): void {
    console.log(data);
}

export class Handler {
    execute() {
        process("test");
    }
}
""",
                encoding="utf-8",
            )
            result = extract(ts_file, ts_file.read_text(encoding="utf-8"))
            assert result["status"] == "ok", f"TypeScript parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in ts"
            assert len(result["imports"]) > 0, "No imports in ts"
            assert len(result["calls"]) > 0, "No calls in ts"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            ts_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # TSX sample (critical: must parse with tsx grammar)
            tsx_file = tmppath / "test.tsx"
            tsx_file.write_text(
                """
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
""",
                encoding="utf-8",
            )
            result = extract(tsx_file, tsx_file.read_text(encoding="utf-8"))
            assert result["status"] == "ok", f"TSX parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in tsx"
            assert len(result["imports"]) > 0, "No imports in tsx"
            assert len(result["calls"]) > 0, "No calls in tsx"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            tsx_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # Go sample
            go_file = tmppath / "test.go"
            go_file.write_text(
                """package main

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
""",
                encoding="utf-8",
            )
            result = extract(go_file, go_file.read_text(encoding="utf-8"))
            assert result["status"] == "ok", f"Go parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in go"
            assert len(result["imports"]) > 0, "No imports in go"
            assert len(result["calls"]) > 0, "No calls in go"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            go_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # Rust sample
            rs_file = tmppath / "test.rs"
            rs_file.write_text(
                """use std::fmt;
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
""",
                encoding="utf-8",
            )
            result = extract(rs_file, rs_file.read_text(encoding="utf-8"))
            assert result["status"] == "ok", f"Rust parse failed: {result['status']}"
            assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in rust"
            assert len(result["imports"]) > 0, "No imports in rust"
            assert len(result["calls"]) > 0, "No calls in rust"
            assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
            rs_counts = (len(result["definitions"]), len(result["imports"]), len(result["calls"]))

            # C# sample
            cs_file = tmppath / "test.cs"
            cs_file.write_text(
                """using System;
using System.Collections;

namespace MyApp {
    public class MyClass {
        public void MyMethod() {
            Console.WriteLine("hello");
        }
    }
}
""",
                encoding="utf-8",
            )
            result = extract(cs_file, cs_file.read_text(encoding="utf-8"))
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
