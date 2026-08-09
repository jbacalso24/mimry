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
    ".java": "java",
    ".php": "php",
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
    "java": {
        # Constructors are declared separately from methods; without them the
        # wiring done in `new Foo(...)` has no caller to attribute.
        "definitions": frozenset(
            [
                "class_declaration",
                "interface_declaration",
                "method_declaration",
                "constructor_declaration",
                "enum_declaration",
                "record_declaration",
            ]
        ),
        "imports": frozenset(["import_declaration"]),
        "calls": frozenset(["method_invocation"]),
        # Java splits heritage in two: `extends` is superclass, `implements` is
        # super_interfaces. Listing only one silently halves the inheritance edges.
        "bases": frozenset(["superclass", "super_interfaces"]),
    },
    "php": {
        "definitions": frozenset(
            [
                "class_declaration",
                "interface_declaration",
                "trait_declaration",
                "enum_declaration",
                "function_definition",
                "method_declaration",
            ]
        ),
        "imports": frozenset(["namespace_use_declaration"]),
        # Three call shapes: helper(), $this->method(), Static::method().
        "calls": frozenset(["function_call_expression", "member_call_expression", "scoped_call_expression"]),
        "bases": frozenset(["base_clause", "class_interface_clause"]),
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
    if node_kind in ("record_declaration", "trait_declaration"):
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


def _definition_name(node: Any, source: bytes) -> str | None:
    """The declared name, preferring the grammar's own `name` field.

    _first_identifier returns the first identifier-like descendant, which on a
    typed method declaration is the *return type*: Java `public String greet()`
    indexed as `String`, and C# `public MyType Foo()` as `MyType` -- the method
    disappeared and a phantom symbol took its place. Grammars label the declared
    name with a `name` field, so ask for it and keep the positional scan only for
    the nodes that have none (Rust `impl_item` names its subject `type`).
    """
    try:
        named = node.child_by_field_name("name")
    except Exception:
        named = None
    if named is not None:
        text = _text(source, named).strip()
        if text:
            return text
    return _first_identifier(node, source)


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
            # TS splits extends/implements into two clauses; Java wraps its
            # interface list in a type_list, so `implements G, R` would otherwise
            # collapse to whichever name the reversed scan reached first.
            if kind in ("extends_clause", "implements_clause", "extends_type_clause", "type_list"):
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
    # Most grammars make the whole callee expression the first child, but Java's
    # method_invocation and PHP's member/scoped calls put the object there and the
    # method in a `name` field -- taking child 0 recorded `$this` and `Registry`
    # as the callee. Ask for the field first; where it is absent (python, js, ts,
    # go, rust, csharp) `function` is child 0, so nothing changes for them.
    callee = None
    for field in ("name", "function"):
        try:
            callee = node.child_by_field_name(field)
        except Exception:
            callee = None
        if callee is not None:
            break
    if callee is None:
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
            name = _definition_name(node, source)
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

            # Fallback: try to extract from identifier/scoped_identifier nodes
            # (Python/Rust/C#/Java, and PHP's namespace_use_clause wrapper)
            if not module:
                for child in _children(node):
                    child_kind = child.kind()
                    if child_kind in (
                        "identifier",
                        "dotted_name",
                        "scoped_identifier",
                        "qualified_name",
                        "namespace_use_clause",
                    ):
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
