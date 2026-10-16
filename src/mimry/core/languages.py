from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

EXTENSION_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".java": "java",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sc": "scala",
    ".swift": "swift",
    ".sh": "bash",
    ".bash": "bash",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".dart": "dart",
    ".m": "objc",
    ".zig": "zig",
    ".ex": "elixir",
    ".exs": "elixir",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml_interface",
    ".r": "r",
    ".jl": "julia",
}

# Display name for each EXTENSION_LANGUAGE value.
# tests/test_supported_languages.py checks the README table and counts
# against supported_languages().
LANGUAGE_LABELS: dict[str, str] = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "tsx": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "csharp": "C#",
    "java": "Java",
    "php": "PHP",
    "c": "C",
    "cpp": "C++",
    "ruby": "Ruby",
    "kotlin": "Kotlin",
    "scala": "Scala",
    "swift": "Swift",
    "bash": "Bash",
    "powershell": "PowerShell",
    "lua": "Lua",
    "perl": "Perl",
    "dart": "Dart",
    "objc": "Objective-C",
    "zig": "Zig",
    "elixir": "Elixir",
    "haskell": "Haskell",
    "ocaml": "OCaml",
    "ocaml_interface": "OCaml",
    "r": "R",
    "julia": "Julia",
    # Symbol-only languages, with no extension entry.
    "markdown": "Markdown",
    "sql": "SQL",
}

# Shared by .ml and .mli: the interface grammar names the same forms.
# value_specification is a `val` declaration in a signature.
_OCAML_NODE_TYPES = {
    "definitions": frozenset(
        [
            "let_binding",
            "module_binding",
            "module_type_definition",
            "type_binding",
            "value_specification",
        ]
    ),
    "imports": frozenset(["open_module", "include_module"]),
    "calls": frozenset(["application_expression"]),
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
            [
                "function_declaration",
                "class_declaration",
                "variable_declarator",
                "interface_declaration",
            ]
        ),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["class_heritage", "extends_type_clause"]),
    },
    "typescript": {
        "definitions": frozenset(
            [
                "function_declaration",
                "class_declaration",
                "variable_declarator",
                "interface_declaration",
            ]
        ),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["class_heritage", "extends_type_clause"]),
    },
    "tsx": {
        "definitions": frozenset(
            [
                "function_declaration",
                "class_declaration",
                "variable_declarator",
                "interface_declaration",
            ]
        ),
        "imports": frozenset(["import_statement"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["class_heritage", "extends_type_clause"]),
    },
    "go": {
        "definitions": frozenset(
            ["function_declaration", "method_declaration", "type_declaration"]
        ),
        "imports": frozenset(["import_declaration", "import_spec"]),
        "calls": frozenset(["call_expression"]),
    },
    "rust": {
        "definitions": frozenset(["function_item", "struct_item", "impl_item", "mod_item"]),
        "imports": frozenset(["use_declaration", "mod_item"]),
        "calls": frozenset(["call_expression"]),
    },
    "csharp": {
        # Interfaces, records and structs are first-class types in .NET;
        # without them "what implements IFoo" has no node to point at.
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
        # Constructors are declared separately from methods; without
        # them the wiring done in `new Foo(...)` has no caller to
        # attribute.
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
        # Java splits heritage in two: `extends` is superclass,
        # `implements` is super_interfaces. Listing only one silently
        # halves the inheritance edges.
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
        "imports": frozenset(
            [
                "namespace_use_declaration",
                "require_expression",
                "require_once_expression",
                "include_expression",
                "include_once_expression",
            ]
        ),
        # Three call shapes: helper(), $this->method(),
        # Static::method().
        "calls": frozenset(
            ["function_call_expression", "member_call_expression", "scoped_call_expression"]
        ),
        "bases": frozenset(["base_clause", "class_interface_clause"]),
    },
    "c": {
        # C functions hide the name inside the declarator chain.
        "definitions": frozenset(
            [
                "function_definition",
                "struct_specifier",
                "enum_specifier",
                "union_specifier",
                "typedef_declaration",
            ]
        ),
        "imports": frozenset(["preproc_include"]),
        "calls": frozenset(["call_expression"]),
    },
    "cpp": {
        # C++ adds classes and namespaces; still uses declarators
        # for function names.
        "definitions": frozenset(
            [
                "function_definition",
                "class_specifier",
                "struct_specifier",
                "enum_specifier",
                "union_specifier",
                "namespace_definition",
                "typedef_declaration",
            ]
        ),
        "imports": frozenset(["preproc_include"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["base_class_clause"]),
    },
    "ruby": {
        # Ruby has classes, methods, singleton methods and modules.
        "definitions": frozenset(["class", "method", "singleton_method", "module"]),
        "imports": frozenset(["call"]),  # require/require_relative are calls
        "calls": frozenset(["call"]),
        "bases": frozenset(["superclass"]),
    },
    "kotlin": {
        "definitions": frozenset(
            [
                "class_declaration",
                "interface_declaration",
                "object_declaration",
                "function_declaration",
            ]
        ),
        "imports": frozenset(["import_header"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["delegation_specifier"]),
    },
    "scala": {
        "definitions": frozenset(
            [
                "class_definition",
                "object_definition",
                "trait_definition",
                "function_definition",
            ]
        ),
        "imports": frozenset(["import_declaration"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["extends_clause", "with"]),
    },
    "swift": {
        "definitions": frozenset(
            [
                "class_declaration",
                "struct_declaration",
                "enum_declaration",
                "protocol_declaration",
                "function_declaration",
                "extension_declaration",
            ]
        ),
        "imports": frozenset(["import_declaration"]),
        "calls": frozenset(["call_expression"]),
        "bases": frozenset(["inheritance_specifier"]),
    },
    "bash": {
        # `source x.sh` and `. x.sh` are commands, so imports
        # ride on the command node and readers keep the file loads.
        "definitions": frozenset(["function_definition"]),
        "imports": frozenset(["command"]),
        "calls": frozenset(["command"]),
    },
    "powershell": {
        # Dot-sourcing, Import-Module and `using module` are commands.
        # Classes inherit through bare simple_name children instead,
        # read by _powershell_bases. Methods are class_method_definition
        # nodes.
        "definitions": frozenset(
            ["function_statement", "class_statement", "class_method_definition"]
        ),
        "imports": frozenset(["command"]),
        "calls": frozenset(["command"]),
    },
    "lua": {
        "definitions": frozenset(["function_declaration"]),
        "imports": frozenset(["function_call"]),  # require(...) is a call
        "calls": frozenset(["function_call"]),
    },
    "perl": {
        "definitions": frozenset(["subroutine_declaration_statement", "package_statement"]),
        "imports": frozenset(["use_statement", "require_expression"]),
        # `use parent`/`use base` are use_statements too; _perl_import
        # declines them and they become inheritance edges instead.
        "calls": frozenset(["function_call_expression", "method_call_expression"]),
    },
    "dart": {
        # Methods are function_signature nodes inside method_signature,
        # so the signature is the one place the name is read from.
        "definitions": frozenset(
            [
                "class_definition",
                "mixin_declaration",
                "extension_declaration",
                "enum_declaration",
                "function_signature",
            ]
        ),
        "imports": frozenset(["uri"]),
        # Calls are flat: `f(x)` is an identifier then a selector, and
        # `a.b(x)` is a chain of selectors. `new` has its own node.
        "calls": frozenset(["selector", "new_expression"]),
        "bases": frozenset(["superclass", "interfaces"]),
    },
    "objc": {
        # Methods are method_declaration (in @interface and @protocol)
        # and method_definition (in @implementation). C functions keep
        # their declarator-based names.
        "definitions": frozenset(
            [
                "class_interface",
                "class_implementation",
                "protocol_declaration",
                "method_declaration",
                "method_definition",
                "function_definition",
            ]
        ),
        "imports": frozenset(["preproc_include", "module_import"]),
        "calls": frozenset(["call_expression", "message_expression"]),
    },
    "zig": {
        # `const Name = struct {...}` is a container: the definition is
        # the ContainerDecl, named by the VarDecl around it.
        "definitions": frozenset(["FnProto", "ContainerDecl"]),
        "imports": frozenset(["BUILTINIDENTIFIER"]),  # @import is a builtin call
        "calls": frozenset(["FnCallArguments"]),
    },
    "elixir": {
        # Every Elixir form is a `call`: def, defmodule, alias and plain
        # function calls alike. The readers tell them apart by the
        # keyword the call starts with.
        "definitions": frozenset(["call"]),
        "imports": frozenset(["call"]),
        "calls": frozenset(["call"]),
    },
    "haskell": {
        # One symbol per top-level name: a function with several
        # equations is merged after the walk. `instance` is not a
        # symbol, but its methods are. The grammar spells it
        # type_synomym.
        "definitions": frozenset(
            ["header", "data_type", "newtype", "type_synomym", "class", "function", "bind"]
        ),
        "imports": frozenset(["import"]),
        "calls": frozenset(["apply"]),
    },
    "ocaml": _OCAML_NODE_TYPES,
    "ocaml_interface": _OCAML_NODE_TYPES,
    "r": {
        # `name <- function(...)` is a binary_operator. Class
        # constructors and every call are calls.
        "definitions": frozenset(["binary_operator", "call"]),
        "imports": frozenset(["call"]),
        "calls": frozenset(["call"]),
    },
    "julia": {
        # `f(x) = ...` is an assignment whose target is a call. Julia
        # calls are call_expression nodes, which include `include`.
        "definitions": frozenset(
            [
                "module_definition",
                "struct_definition",
                "abstract_definition",
                "function_definition",
                "macro_definition",
                "assignment",
            ]
        ),
        "imports": frozenset(["using_statement", "import_statement", "call_expression"]),
        # `sin.(xs)` is a broadcast_call_expression, same callee shape.
        "calls": frozenset(["call_expression", "broadcast_call_expression"]),
        "bases": frozenset(["type_head"]),
    },
}


# Objective-C declarations and directives that start a line. C
# headers have none of them, so a `.h` with one of these is parsed as
# Objective-C.
_OBJC_HEADER_MARKER = re.compile(
    rb"^[ \t]*(?:@interface|@protocol|@implementation|@end|#import)\b", re.M
)


def is_objc_header(data: bytes) -> bool:
    """Whether a `.h` file's bytes hold Objective-C rather than C."""
    return _OBJC_HEADER_MARKER.search(data) is not None


def language_for(path: str | Path, overrides: dict[str, str] | None = None) -> str | None:
    """Grammar name for a path's extension, or None if unsupported.

    ``overrides`` maps lowercased extensions to language names and wins
    over the built-in table.
    """
    if isinstance(path, str):
        path = Path(path)
    ext = path.suffix.lower()
    if overrides and ext in overrides:
        return overrides[ext]
    return EXTENSION_LANGUAGE.get(ext)


def supported_languages() -> list[tuple[str, list[str]]]:
    """(label, sorted extensions) per language, in first-seen order."""
    groups: dict[str, list[str]] = {}
    for ext, lang in EXTENSION_LANGUAGE.items():
        groups.setdefault(LANGUAGE_LABELS[lang], []).append(ext)
    return [(label, sorted(exts)) for label, exts in groups.items()]


def language_counts(files: list[dict], symbols: list[dict]) -> dict[str, int]:
    """Indexed files per display language, as `find --lang` sees them.

    A file takes its extension's language. A file with none (Markdown,
    say) takes the language of its symbols, and a file with neither is
    "other".
    """
    symbol_language: dict[str, str] = {}
    for symbol in symbols:
        if symbol.get("language"):
            symbol_language.setdefault(symbol.get("file_id"), symbol["language"])
    counts: dict[str, int] = {}
    for record in files:
        language = language_for(record.get("rel_path") or "") or symbol_language.get(
            record.get("file_id")
        )
        name = LANGUAGE_LABELS.get(language, language.title()) if language else "other"
        counts[name] = counts.get(name, 0) + 1
    return counts


def symbol_kind(language: str, node_kind: str) -> str:
    """Map a tree-sitter node kind to MIMRY's symbol kind vocabulary."""
    # A PowerShell method is named "class_..." by the grammar but is a
    # function, not a class.
    if node_kind == "class_method_definition":
        return "function"
    if node_kind in ("interface_declaration", "protocol_declaration", "module_type_definition"):
        return "interface"
    # Haskell's class and Julia's abstract type are interface-like.
    if (language, node_kind) in (("haskell", "class"), ("julia", "abstract_definition")):
        return "interface"
    if node_kind in ("enum_declaration", "enum_specifier"):
        return "enum"
    if node_kind in (
        "record_declaration",
        "trait_declaration",
        "trait_definition",
        "object_definition",
        "mixin_declaration",
        "extension_declaration",
    ):
        return "class"
    if "class" in node_kind or "struct" in node_kind:
        return "class"
    if node_kind == "impl_item":
        return "class"
    if node_kind in (
        "namespace_declaration",
        "namespace_definition",
        "module",
        "mod_item",
        "package_statement",
        "module_definition",
        "module_binding",
        "header",
    ):
        return "module"
    if node_kind in (
        "type_declaration",
        "typedef_declaration",
        "data_type",
        "newtype",
        "type_synomym",
        "type_binding",
    ):
        return "type"
    if node_kind == "variable_declarator":
        return "variable"
    if node_kind == "union_specifier":
        return "class"
    return "function"


def _definition_span(language: str, node: Any) -> Any:
    """The node whose lines a definition covers. A Zig FnProto is only
    the signature; its enclosing Decl also holds the body, and calls in
    the body belong to the function.
    """
    if language == "zig" and node.type == "FnProto" and node.parent is not None:
        if node.parent.type == "Decl":
            return node.parent
    return node


def _definition_kind(language: str, node: Any, source: bytes) -> str:
    """Kind of a definition node. A Zig container's kind depends on
    its enum keyword, and an Elixir form on its keyword: the node kind
    alone says neither.
    """
    if language == "zig" and node.type == "ContainerDecl":
        return _zig_container_kind(node, source)
    if language == "elixir":
        return _ELIXIR_KINDS.get(_elixir_head(node, source), "function")
    if language == "r" and node.type == "call":
        return "class"
    if language in ("ocaml", "ocaml_interface") and node.type == "let_binding":
        # A binding is a function with parameters, or with a fun or
        # function body. Any other value is a variable.
        body = node.child_by_field_name("body")
        has_parameters = any(child.type == "parameter" for child in _children(node))
        is_lambda = body is not None and body.type in ("fun_expression", "function_expression")
        if not (has_parameters or is_lambda):
            return "variable"
    return symbol_kind(language, node.type)


def _text(source: bytes, node: Any) -> str:
    """Extract a node's text.

    `source` must be the UTF-8 *bytes*, because tree-sitter reports byte
    offsets. Slicing the decoded str instead shifts every name by the
    number of extra bytes ahead of it -- a UTF-8 BOM alone costs 2
    characters, and Visual Studio writes C#/TS files with a BOM by
    default, so `formattedNumber` came out `rmattedNumber`.
    """
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _line(node: Any) -> int | None:
    """Get 1-indexed line number from node."""
    try:
        return int(node.start_point.row) + 1
    except Exception:
        return None


def _end_line(node: Any) -> int | None:
    """Get 1-indexed end line number from node."""
    try:
        return int(node.end_point.row) + 1
    except Exception:
        return None


def _children(node: Any):
    """Iterate over child nodes."""
    for i in range(node.child_count):
        yield node.child(i)


def _walk(node: Any):
    """Depth-first walk of tree, document order.

    Iterative: a recursive generator hands every node up through one
    frame per tree level, which made walking deep parse trees quadratic
    in their depth.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        count = current.child_count
        if count:
            stack.extend([current.child(i) for i in range(count - 1, -1, -1)])


def _first_identifier(node: Any, source: str) -> str | None:
    """Find the first identifier-like child recursively."""
    kind = node.type
    if kind in (
        "identifier",
        "type_identifier",
        "field_identifier",
        "property_identifier",
        "name",
    ) or kind.endswith("identifier"):
        return _text(source, node)
    for child in _children(node):
        found = _first_identifier(child, source)
        if found:
            return found
    return None


def _definition_name(language: str, node: Any, source: bytes) -> str | None:
    """The declared name, preferring the grammar's own `name` field.

    _first_identifier returns the first identifier-like descendant,
    which on a typed method declaration is the *return type*: Java
    `public String greet()` indexed as `String`, and C# `public MyType
    Foo()` as `MyType` -- the method disappeared and a phantom symbol
    took its place. Grammars label the declared name with a `name`
    field, so ask for it and keep the positional scan only for the nodes
    that have none (Rust `impl_item` names its subject `type`).

    C/C++ functions hide names in declarator chains: reach through
    function_declarator to find it, then check for qualified_identifier
    (e.g. Widget::draw) and take only the final part.
    """
    # PowerShell functions, classes and methods name themselves with a
    # bare function_name or simple_name child, not a `name` field. A
    # method's type literal holds type_identifier, not simple_name, so
    # the first simple_name is always the method name.
    if node.type in ("function_statement", "class_statement", "class_method_definition"):
        for child in _children(node):
            if child.type in ("function_name", "simple_name"):
                return _text(source, child).strip() or None
        return None

    # The branches below are gated by language. PHP, Java, C#, Swift and
    # others reuse method_declaration, method_definition and
    # extension_declaration, and must keep their `name` field.
    if language == "dart" and node.type == "extension_declaration":
        # Dart `extension on String {}` is unnamed, and its first
        # identifier would be the extended type.
        return _direct_child_text(node, source, "identifier")
    if language == "objc" and node.type in ("method_declaration", "method_definition"):
        # Objective-C: the selector's first keyword is the direct
        # identifier child. The return type sits in method_type, where a
        # positional scan would find it first.
        return _direct_child_text(node, source, "identifier")
    if language == "zig" and node.type == "FnProto":
        return _direct_child_text(node, source, "IDENTIFIER")
    if language == "zig" and node.type == "ContainerDecl":
        return _zig_container_name(node, source)
    if language == "elixir":
        return _elixir_definition_name(node, source)
    if language == "haskell":
        return _haskell_definition_name(node, source)
    if language in ("ocaml", "ocaml_interface"):
        return _ocaml_definition_name(node, source)
    if language == "r":
        return _r_definition_name(node, source)
    if language == "julia":
        return _julia_definition_name(node, source)

    try:
        named = node.child_by_field_name("name")
    except Exception:
        named = None
    if named is not None and named.type in ("dot_index_expression", "method_index_expression"):
        # Lua `function M.run` and `function M:step`: the name is the
        # last segment, the field or method after the table.
        parts = named.named_children
        named = parts[-1] if parts else None
    if named is not None:
        text = _text(source, named).strip()
        if text:
            return text

    # C/C++ function_definition: unwrap through declarator chain
    if node.type == "function_definition":
        c_name = _c_declarator_name(node, source)
        if c_name is not None:
            return c_name

    return _first_identifier(node, source)


_C_NAME_KINDS = frozenset(
    ["identifier", "field_identifier", "qualified_identifier", "destructor_name", "operator_name"]
)


def _c_declarator_name(node: Any, source: bytes) -> str | None:
    """A C/C++ function's name: innermost declarator, unqualified."""
    declarator = node
    while True:
        inner = declarator.child_by_field_name("declarator")
        if inner is None and declarator.type == "reference_declarator":
            # `int& get()` gives its declarator no field name.
            named = declarator.named_children
            inner = named[-1] if named else None
        if inner is None:
            break
        declarator = inner
    if declarator.type not in _C_NAME_KINDS:
        return None
    return _text(source, declarator).rsplit("::", 1)[-1].strip() or None


def _parent_kinds(node: Any, limit: int = 3) -> set[str]:
    """Collect ancestor node kinds up to limit."""
    kinds: set[str] = set()
    cur = node.parent
    for _ in range(limit):
        if cur is None:
            break
        kinds.add(cur.type)
        cur = cur.parent
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
            if child.type == "visibility_modifier":
                return True
        return False

    # C#: check for "public" in text. Checked on the bytes: ASCII
    # survives UTF-8 decoding unchanged, and decoding a whole class per
    # member was quadratic.
    if language == "csharp":
        return b"public" in source[node.start_byte : node.end_byte]

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


_IDENTIFIER_KINDS = (
    "identifier",
    "type_identifier",
    "field_identifier",
    "property_identifier",
    "name",
    "constant",
    "simple_identifier",
)


def _is_identifier_kind(kind: str) -> bool:
    return kind in _IDENTIFIER_KINDS or kind.endswith("identifier")


def _ruby_require(node: Any, source: bytes) -> str | None:
    """Module string from Ruby require/require_relative, or None."""
    callee = _callee_name(node, source)
    if callee not in ("require", "require_relative"):
        return None
    # Extract string argument
    for child in _walk(node):
        if "string" in child.type:
            module = _text(source, child)
            module = module.strip().strip('"').strip("'")
            # Normalize require_relative to ./path
            if callee == "require_relative" and module and not module.startswith("."):
                module = "./" + module
            return module
    return None


def _c_include(node: Any, source: bytes) -> str | None:
    """Module string from C/C++ #include, or None."""
    for child in _children(node):
        if child.type == "system_lib_string":
            # <stdio.h> -> keep brackets for system includes
            text = _text(source, child)
            return text  # Keep <path>
        elif child.type == "string_literal":
            # "utils.h" -> ./utils.h unless starts with .
            for subchild in _children(child):
                if subchild.type == "string_content":
                    module = _text(source, subchild)
                    if module:
                        # Prepend "./" unless already starts with .
                        if not module.startswith("."):
                            module = "./" + module
                        return module
            text = _text(source, child)
            module = text.strip('"').strip("'")
            if module and not module.startswith("."):
                module = "./" + module
            return module
    return None


def _kotlin_import(node: Any, source: bytes) -> str | None:
    """Module string from Kotlin import_header, or None."""
    # Build the dotted identifier from the identifier node
    module = None
    for child in _children(node):
        if child.type == "identifier":
            module = _text(source, child)
            break
    # Check for wildcard
    for child in _children(node):
        if child.type == "wildcard_import":
            if module:
                module += ".*"
    return module


def _scala_import(node: Any, source: bytes) -> str | None:
    """Module string from Scala import_declaration, or None."""
    parts = []
    has_wildcard = False
    for child in _children(node):
        if child.type == "identifier":
            parts.append(_text(source, child))
        elif child.type == "namespace_selectors":
            # Braced selectors: import a.b.{C, D}
            for subchild in _children(child):
                if subchild.type == "identifier":
                    parts.append(_text(source, subchild))
        elif child.type == "namespace_wildcard":
            has_wildcard = True
    module = None
    if parts:
        module = ".".join(parts)
    if has_wildcard:
        module = (module or "") + "._"
    return module


def _swift_import(node: Any, source: bytes) -> str | None:
    """Module string from Swift import_declaration, or None."""
    for child in _children(node):
        if child.type == "identifier":
            return _text(source, child)
    return None


def _unquote(text: str) -> str:
    """Text without one pair of matching surrounding quotes."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _bash_import(node: Any, source: bytes) -> str | None:
    """Path a Bash `source` or `.` command loads, as written."""
    name = node.child_by_field_name("name")
    if name is None or _text(source, name) not in ("source", "."):
        return None
    argument = node.child_by_field_name("argument")
    if argument is None:
        return None
    return _unquote(_text(source, argument)) or None


def _bash_call(node: Any, source: bytes) -> str | None:
    """The command name of a Bash simple command."""
    name = node.child_by_field_name("name")
    if name is None:
        return None
    return _text(source, name).strip() or None


_POWERSHELL_ROOT = re.compile(r"^\$PSScriptRoot[/\\]")
_POWERSHELL_COMMAND = re.compile(r"[A-Za-z_][\w-]*")


def _powershell_path(text: str) -> str:
    """A PowerShell path, unquoted, without its `$PSScriptRoot/`.

    $PSScriptRoot is the importing script's own directory, so what is
    left is relative to the importer, as the resolver expects.
    """
    return _POWERSHELL_ROOT.sub("", _unquote(text))


def _powershell_dot_sourced(node: Any, source: bytes) -> bool:
    """True for `. file.ps1`, the dot-sourcing form of a command."""
    return any(
        child.type == "command_invokation_operator" and _text(source, child) == "."
        for child in _children(node)
    )


def _powershell_arguments(node: Any, source: bytes) -> list[str]:
    """Texts of a command's positional arguments, parameters skipped."""
    values = []
    for child in _children(node):
        if child.type != "command_elements":
            continue
        for element in _children(child):
            if not element.is_named or element.type in (
                "command_argument_sep",
                "command_parameter",
            ):
                continue
            values.append(_text(source, element).strip())
    return values


def _powershell_import(node: Any, source: bytes) -> str | None:
    """Path or module name a PowerShell command loads.

    Covers dot-sourcing, `Import-Module <name>` (or `-Name <name>`) and
    `using module <name>`. A dot-sourced bare word is a command, not a
    file, so it is not an import.
    """
    head = node.child_by_field_name("command_name")
    if head is None:
        return None
    if _powershell_dot_sourced(node, source):
        path = _powershell_path(_text(source, head))
        if "/" in path or "\\" in path or path.lower().endswith(".ps1"):
            return path or None
        return None
    word = _text(source, head).strip().lower()
    args = _powershell_arguments(node, source)
    if word == "import-module" and args:
        return _powershell_path(args[0]) or None
    if word == "using" and len(args) >= 2 and args[0].lower() == "module":
        return _powershell_path(args[1]) or None
    return None


def _powershell_call(node: Any, source: bytes) -> str | None:
    """A PowerShell command's name, when it is a bare command word."""
    if _powershell_dot_sourced(node, source):
        return None
    head = node.child_by_field_name("command_name")
    if head is None:
        return None
    name = _text(source, head).strip()
    return name if _POWERSHELL_COMMAND.fullmatch(name) else None


def _powershell_bases(node: Any, source: bytes) -> list[str]:
    """Base types of a PowerShell class: the simple_name children after
    the class's own name, which the grammar does not wrap.
    """
    names = [
        _text(source, child).strip() for child in _children(node) if child.type == "simple_name"
    ]
    return names[1:]


def _lua_require(node: Any, source: bytes) -> str | None:
    """Module string from a Lua require call with a string literal."""
    name = node.child_by_field_name("name")
    if name is None or name.type != "identifier" or _text(source, name) != "require":
        return None
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return None
    for child in _children(arguments):
        if child.type == "string":
            for part in _children(child):
                if part.type == "string_content":
                    return _text(source, part) or None
            return None
    return None


_PERL_PRAGMAS = frozenset(
    ["strict", "warnings", "utf8", "lib", "parent", "base", "constant", "vars", "feature"]
)
_PERL_NAME = re.compile(r"[A-Za-z_]\w*")


def _perl_import(node: Any, source: bytes) -> str | None:
    """Package a Perl `use Foo::Bar` or `require Foo::Bar` loads.

    Pragmas, version lines and `use parent`/`use base` are not imports.
    """
    if node.type == "require_expression":
        for child in _children(node):
            if child.type == "bareword":
                return _text(source, child).strip() or None
        return None
    if node.type != "use_statement":
        return None
    module = node.child_by_field_name("module")
    if module is None:
        return None
    name = _text(source, module).strip()
    if name in _PERL_PRAGMAS or re.match(r"v?\d", name):
        return None
    return name or None


def _perl_base_names(node: Any, source: bytes) -> list[str]:
    """Base packages that `use parent` or `use base` names, in order.

    Quoted and qw() words are string_content nodes. A flag such as
    `-norequire` is an autoquoted_bareword and is never collected.
    """
    module = node.child_by_field_name("module")
    if module is None or _text(source, module).strip() not in ("parent", "base"):
        return []
    names: list[str] = []
    for child in _walk(node):
        if child.type in ("string_content", "bareword"):
            for word in _text(source, child).split():
                if word not in names:
                    names.append(word)
    return names


def _perl_packages(root_node: Any, source: bytes) -> list[tuple[int, str]]:
    """(start byte, name) of each package statement, in order."""
    packages = []
    for node in _walk(root_node):
        if node.type != "package_statement":
            continue
        name = node.child_by_field_name("name")
        if name is not None and _text(source, name).strip():
            packages.append((node.start_byte, _text(source, name).strip()))
    return packages


def _perl_package_at(packages: list[tuple[int, str]], offset: int) -> str | None:
    """The package in force at a byte offset: the last one above it.

    Approximates Perl's lexical package scope: a `package` inside a
    block still counts from its declaration to the end of the file.
    """
    owner = None
    for start, name in packages:
        if start > offset:
            break
        owner = name
    return owner


def _direct_child_text(node: Any, source: bytes, kind: str) -> str | None:
    """Text of the first direct child of ``kind``, or None."""
    for child in _children(node):
        if child.type == kind:
            return _text(source, child).strip() or None
    return None


def _dart_import(node: Any, source: bytes) -> str | None:
    """The URI of a Dart import, export or part, as written."""
    return _unquote(_text(source, node)) or None


def _dart_base_names(container: Any, source: bytes) -> list[str]:
    """Types named by a Dart `extends ... with ...` or `implements`
    clause.

    Type arguments are sibling nodes of the type, so a generic base such
    as `Base<T>` names only `Base`.
    """
    names: list[str] = []
    for child in _children(container):
        if child.type in ("mixins", "interfaces"):
            found = [sub for sub in _children(child) if sub.type == "type_identifier"]
        elif child.type == "type_identifier":
            found = [child]
        else:
            continue
        for sub in found:
            name = _text(source, sub).strip()
            if name and name not in names:
                names.append(name)
    return names


def _dart_call(node: Any, source: bytes) -> str | None:
    """Callee of a Dart call: `f(x)` or `a.b(x)` (last segment), or `new
    T(x)`.
    """
    if node.type == "new_expression":
        children = list(_children(node))
        names = [c for c in children if c.type == "identifier"] or [
            c for c in children if c.type == "type_identifier"
        ]
        return _text(source, names[-1]).strip() if names else None
    # A call is a selector holding an argument_part. Its callee is the
    # node before it: a bare identifier, or a `.name` selector.
    if not any(c.type == "argument_part" for c in _children(node)):
        return None
    before = node.prev_named_sibling
    if before is None:
        return None
    if before.type == "identifier":
        return _text(source, before).strip() or None
    if before.type == "selector":
        for part in _children(before):
            if part.type in (
                "unconditional_assignable_selector",
                "conditional_assignable_selector",
            ):
                name = _direct_child_text(part, source, "identifier")
                if name:
                    return name
    return None


def _objc_import(node: Any, source: bytes) -> str | None:
    """`#import` and `#include` as C reads them, or the module of
    `@import Foo;`.
    """
    if node.type == "module_import":
        return _direct_child_text(node, source, "identifier")
    return _c_include(node, source)


def _objc_call(node: Any, source: bytes) -> str | None:
    """A C function's name, or the first keyword of a message `[recv
    sel:arg]`.
    """
    if node.type != "message_expression":
        return _callee_name(node, source)
    named = node.named_children
    if len(named) < 2 or named[1].type != "identifier":
        return None
    return _text(source, named[1]).strip() or None


def _objc_bases(node: Any, source: bytes) -> list[str]:
    """Superclass after ':' and the protocols in <...> of an
    @interface.
    """
    bases: list[str] = []
    children = list(_children(node))
    for index, child in enumerate(children):
        if child.type == ":" and index + 1 < len(children):
            if children[index + 1].type == "identifier":
                bases.append(_text(source, children[index + 1]).strip())
        elif child.type == "parameterized_arguments":
            for part in _children(child):
                if part.type == "type_name":
                    name = _direct_child_text(part, source, "type_identifier")
                elif part.type == "identifier":
                    name = _text(source, part).strip()
                else:
                    continue
                if name:
                    bases.append(name)
    return bases


def _zig_import(node: Any, source: bytes) -> str | None:
    """Path in `@import("x.zig")`. Other builtins are not imports."""
    if _text(source, node) != "@import":
        return None
    arguments = node.next_named_sibling
    if arguments is None or arguments.type != "FnCallArguments":
        return None
    for child in _walk(arguments):
        if child.type == "STRINGLITERALSINGLE":
            return _unquote(_text(source, child)) or None
    return None


def _zig_call(node: Any, source: bytes) -> str | None:
    """Callee of `f(x)` and `a.f(x)`: the identifier before the
    arguments.
    """
    before = node.prev_named_sibling
    if before is None or before.type != "IDENTIFIER":
        return None
    return _text(source, before).strip() or None


def _zig_container_name(node: Any, source: bytes) -> str | None:
    """Name of `const Name = struct {...}`: the VarDecl around the
    container.

    A container that is not assigned to a constant (a function's return
    value, for one) has no name and is not a definition.
    """
    holder = node.parent
    while holder is not None and holder.type in ("SuffixExpr", "ErrorUnionExpr"):
        holder = holder.parent
    if holder is None or holder.type != "VarDecl":
        return None
    return _direct_child_text(holder, source, "IDENTIFIER")


def _zig_container_kind(node: Any, source: bytes) -> str:
    """`enum` for an enum container; struct, union and opaque are
    classes.
    """
    for child in _children(node):
        if child.type == "ContainerDeclType":
            first = next(_children(child), None)
            if first is not None and _text(source, first).strip() == "enum":
                return "enum"
    return "class"


def _direct_child(node: Any, kind: str) -> Any:
    """The first direct child of ``kind``, or None."""
    for child in _children(node):
        if child.type == kind:
            return child
    return None


def _field_text(node: Any, field: str, source: bytes) -> str | None:
    """Stripped text of a named field, or None when absent or empty."""
    child = node.child_by_field_name(field)
    if child is None:
        return None
    return _text(source, child).strip() or None


def _merge_equations(definitions: list[dict], owners: list[int | None]) -> list[dict]:
    """One symbol per run of clauses with the same name, kind and
    declaring block. A Haskell function's equations, or an Elixir
    function's clauses, are one symbol from the first line to the last.
    Two instances that both define `show` stay apart, and so do
    same-named functions with other code between them.
    """
    merged: list[dict] = []
    last_key = None
    for definition, owner in zip(definitions, owners, strict=True):
        key = (definition["name"], definition["kind"], owner)
        if key == last_key:
            kept = merged[-1]
            kept["line_end"] = max(kept["line_end"] or 0, definition["line_end"] or 0)
        else:
            merged.append(definition)
            last_key = key
    return merged


# Elixir forms by the keyword that starts them, with their symbol kind.
_ELIXIR_KINDS = {
    "def": "function",
    "defp": "function",
    "defmacro": "function",
    "defmacrop": "function",
    "defmodule": "module",
    "defprotocol": "interface",
    "defimpl": "class",
    "defstruct": "class",
}
_ELIXIR_FUNCTION_HEADS = frozenset(("def", "defp", "defmacro", "defmacrop"))
_ELIXIR_IMPORT_HEADS = frozenset(("alias", "import", "require", "use"))
# Forms that open a block or special form, which are never a call.
_ELIXIR_NOT_CALLS = frozenset(
    (
        *_ELIXIR_KINDS,
        *_ELIXIR_IMPORT_HEADS,
        "if",
        "unless",
        "case",
        "cond",
        "with",
        "for",
        "try",
        "receive",
        "quote",
        "unquote",
        "super",
    )
)


def _elixir_head(node: Any, source: bytes) -> str | None:
    """The keyword a call starts with: `def` in `def f(x) do ... end`.
    A call on a module (`Mod.fun(x)`) has no such keyword.
    """
    target = node.child_by_field_name("target")
    if target is None or target.type != "identifier":
        return None
    return _text(source, target).strip() or None


def _elixir_arguments(node: Any) -> list[Any]:
    """The argument nodes of a call, in order."""
    arguments = _direct_child(node, "arguments")
    return list(arguments.named_children) if arguments is not None else []


def _elixir_function_name(head: Any, source: bytes) -> str | None:
    """Name of a def head: `f` in `f(x)`, `f`, or `f(x) when guard`."""
    while head is not None and head.type == "binary_operator":
        head = head.child_by_field_name("left")
    if head is not None and head.type == "call":
        head = head.child_by_field_name("target")
    if head is None or head.type != "identifier":
        return None
    return _text(source, head).strip() or None


def _elixir_impl_name(arguments: list[Any], source: bytes) -> str | None:
    """`defimpl Protocol, for: Type` is named `Protocol.Type`."""
    protocol = _text(source, arguments[0]).strip()
    for argument in arguments[1:]:
        if argument.type != "keywords":
            continue
        for pair in argument.named_children:
            key = pair.child_by_field_name("key")
            value = pair.child_by_field_name("value")
            if key is None or value is None:
                continue
            if _text(source, key).strip() == "for:" and value.type == "alias":
                return f"{protocol}.{_text(source, value).strip()}"
    return protocol or None


def _elixir_enclosing_module(node: Any, source: bytes) -> str | None:
    """Name of the innermost defmodule around a node."""
    owner = node.parent
    while owner is not None:
        if owner.type == "call" and _elixir_head(owner, source) == "defmodule":
            arguments = _elixir_arguments(owner)
            return _text(source, arguments[0]).strip() if arguments else None
        owner = owner.parent
    return None


def _elixir_definition_name(node: Any, source: bytes) -> str | None:
    """Name of a def, defmodule, defprotocol, defimpl or defstruct.
    A defstruct has no name of its own, so it takes its module's.
    """
    head = _elixir_head(node, source)
    if head == "defstruct":
        return _elixir_enclosing_module(node, source)
    arguments = _elixir_arguments(node)
    if not arguments:
        return None
    if head in ("defmodule", "defprotocol") and arguments[0].type == "alias":
        return _text(source, arguments[0]).strip() or None
    if head in _ELIXIR_FUNCTION_HEADS:
        return _elixir_function_name(arguments[0], source)
    if head == "defimpl":
        return _elixir_impl_name(arguments, source)
    return None


def _elixir_in_head(node: Any, source: bytes) -> bool:
    """Whether a call is in a def's head (`f(x)` or its guard), not its
    body.
    """
    owner = node.parent
    while owner is not None and owner.type not in ("do_block", "keywords", "pair"):
        if owner.type == "arguments":
            parent = owner.parent
            return (
                parent is not None
                and parent.type == "call"
                and _elixir_head(parent, source) in _ELIXIR_FUNCTION_HEADS
            )
        owner = owner.parent
    return False


def _elixir_import(node: Any, source: bytes) -> str | list[str] | None:
    """The module an alias, import, require or use names."""
    if _elixir_head(node, source) not in _ELIXIR_IMPORT_HEADS:
        return None
    arguments = _elixir_arguments(node)
    if not arguments:
        return None
    first = arguments[0]
    if first.type == "alias":
        return _text(source, first).strip() or None
    # `alias Foo.{Bar, Baz}` names Foo.Bar and Foo.Baz.
    parts = first.named_children if first.type == "dot" else []
    if len(parts) != 2 or parts[0].type != "alias" or parts[1].type != "tuple":
        return None
    prefix = _text(source, parts[0]).strip()
    return [
        f"{prefix}.{_text(source, member).strip()}"
        for member in parts[1].named_children
        if member.type == "alias"
    ] or None


def _elixir_call(node: Any, source: bytes) -> str | None:
    """The function a call invokes: `f` in `f(x)`, `fun` in
    `Mod.fun(x)`.
    """
    # `@doc "..."` is a module attribute, not a call.
    parent = node.parent
    if _elixir_in_head(node, source) or (parent is not None and parent.type == "unary_operator"):
        return None
    target = node.child_by_field_name("target")
    if target is None:
        return None
    if target.type == "dot":
        right = target.child_by_field_name("right")
        if right is None or right.type != "identifier":
            return None
        return _text(source, right).strip() or None
    if target.type != "identifier":
        return None
    name = _text(source, target).strip()
    return None if name in _ELIXIR_NOT_CALLS else name


def _haskell_module_name(module: Any, source: bytes) -> str | None:
    """Dotted name of a Haskell module node, such as `Data.Map`."""
    if module is None:
        return None
    parts = [_text(source, part).strip() for part in _children(module) if part.type == "module_id"]
    return ".".join(parts) or None


def _haskell_definition_name(node: Any, source: bytes) -> str | None:
    """A module header names its module. A function is a symbol only at
    top level or in an instance, never in a where or let.
    """
    if node.type == "header":
        return _haskell_module_name(node.child_by_field_name("module"), source)
    if node.type in ("function", "bind"):
        parent = node.parent
        if parent is None or parent.type not in ("declarations", "instance_declarations"):
            return None
    return _field_text(node, "name", source)


def _haskell_import(node: Any, source: bytes) -> str | None:
    """The module an import names, qualified or not."""
    return _haskell_module_name(node.child_by_field_name("module"), source)


def _haskell_call(node: Any, source: bytes) -> str | None:
    """The function an application calls. Only the outermost application
    of a chain names the call, and a type signature applies type
    constructors, which are not calls.
    """
    parent = node.parent
    if parent is not None and parent.type == "apply":
        function = parent.child_by_field_name("function")
        if (
            function is not None
            and function.start_byte == node.start_byte
            and function.end_byte == node.end_byte
        ):
            return None
    owner = parent
    while owner is not None:
        if owner.type == "signature":
            return None
        owner = owner.parent
    callee = node.child_by_field_name("function")
    while callee is not None and callee.type == "apply":
        callee = callee.child_by_field_name("function")
    if callee is not None and callee.type == "qualified":
        callee = callee.child_by_field_name("id")
    if callee is None or callee.type != "variable":
        return None
    return _text(source, callee).strip() or None


# OCaml declarations whose name is in a child node, by child kind.
_OCAML_NAME_CHILDREN = {
    "module_binding": "module_name",
    "module_type_definition": "module_type_name",
    "value_specification": "value_name",
}


def _ocaml_definition_name(node: Any, source: bytes) -> str | None:
    """A let binds a name only at module level: a let inside an
    expression is local.
    """
    if node.type == "let_binding":
        owner = node.parent.parent if node.parent is not None else None
        if owner is None or owner.type not in ("compilation_unit", "structure"):
            return None
        return _direct_child_text(node, source, "value_name")
    child_kind = _OCAML_NAME_CHILDREN.get(node.type)
    if child_kind is not None:
        return _direct_child_text(node, source, child_kind)
    return _field_text(node, "name", source)


def _ocaml_import(node: Any, source: bytes) -> str | None:
    """The module an open or include names."""
    return _field_text(node, "module", source)


def _ocaml_call(node: Any, source: bytes) -> str | None:
    """The value an application calls: `f` in `f x`, `fold_left` in
    `List.fold_left f`.
    """
    function = node.child_by_field_name("function")
    if function is None or function.type != "value_path":
        return None
    return _direct_child_text(function, source, "value_name")


# R calls that load a package or a file, and calls that define a class.
_R_IMPORT_CALLS = frozenset(("library", "require", "requireNamespace", "source"))
_R_CLASS_CALLS = frozenset(("setClass", "setRefClass", "R6Class"))


def _r_callee(node: Any, source: bytes) -> str | None:
    """The function a call names: `f` in `f(x)` and `pkg::f(x)`."""
    callee = node.child_by_field_name("function")
    if callee is not None and callee.type == "namespace_operator":
        callee = callee.child_by_field_name("rhs")
    if callee is None or callee.type != "identifier":
        return None
    return _text(source, callee).strip() or None


def _r_argument_values(node: Any) -> list[Any]:
    """The value nodes of a call's arguments, in order."""
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    values = [
        child.child_by_field_name("value")
        for child in _children(arguments)
        if child.type == "argument"
    ]
    return [value for value in values if value is not None]


def _r_definition_name(node: Any, source: bytes) -> str | None:
    """`name <- function(...)`, or the class a setClass, setRefClass or
    R6Class call names by its first string argument.
    """
    if node.type == "call":
        if _r_callee(node, source) not in _R_CLASS_CALLS:
            return None
        values = _r_argument_values(node)
        if values and values[0].type == "string":
            return _unquote(_text(source, values[0])) or None
        return None
    lhs = node.child_by_field_name("lhs")
    rhs = node.child_by_field_name("rhs")
    if lhs is None or rhs is None or rhs.type != "function_definition":
        return None
    if lhs.type not in ("identifier", "string"):
        return None
    return _unquote(_text(source, lhs)) or None


def _r_import(node: Any, source: bytes) -> str | None:
    """The package a library or require call names, or the file a
    source call loads.
    """
    name = _r_callee(node, source)
    if name not in _R_IMPORT_CALLS:
        return None
    values = _r_argument_values(node)
    if not values:
        return None
    value = values[0]
    if value.type == "string":
        return _unquote(_text(source, value)) or None
    if value.type == "identifier" and name != "source":
        return _text(source, value).strip() or None
    return None


def _r_call(node: Any, source: bytes) -> str | None:
    """The function a call invokes, without the import calls."""
    name = _r_callee(node, source)
    return None if name in _R_IMPORT_CALLS else name


def _julia_callee(node: Any, source: bytes) -> str | None:
    """The name a Julia call_expression calls: `f` in `f(x)`, `show` in
    `Base.show(io, x)`.
    """
    callee = node.named_children[0] if node.named_child_count else None
    if callee is not None and callee.type == "field_expression":
        callee = callee.named_children[-1] if callee.named_child_count else None
    if callee is None or callee.type != "identifier":
        return None
    return _text(source, callee).strip() or None


def _julia_definition_name(node: Any, source: bytes) -> str | None:
    """Name of a module, function, macro, struct, abstract type or a
    short-form function `f(x) = ...`. A plain `x = 1` is not one.
    """
    if node.type == "module_definition":
        return _field_text(node, "name", source)
    if node.type == "assignment":
        target = node.named_children[0] if node.named_child_count else None
        if target is None or target.type != "call_expression":
            return None
        return _julia_callee(target, source)
    if node.type in ("function_definition", "macro_definition"):
        signature = _direct_child(node, "signature")
        head = signature.named_children[0] if signature and signature.named_child_count else None
        if head is None:
            return None
        if head.type == "call_expression":
            return _julia_callee(head, source)
        if head.type == "identifier":
            return _text(source, head).strip() or None
        return None
    # struct and abstract type: the head is `Name`, `Name{T}`, or
    # `Name <: Super` with either of those on the left.
    head = _direct_child(node, "type_head")
    target = head.named_children[0] if head is not None and head.named_child_count else None
    if target is not None and target.type == "binary_expression":
        target = target.named_children[0] if target.named_child_count else None
    if target is not None and target.type == "parametrized_type_expression":
        target = target.named_children[0] if target.named_child_count else None
    if target is None or target.type != "identifier":
        return None
    return _text(source, target).strip() or None


def _julia_import(node: Any, source: bytes) -> str | list[str] | None:
    """The package a using or import names, or the file an include
    loads.
    """
    if node.type == "call_expression":
        if _julia_callee(node, source) != "include":
            return None
        arguments = _direct_child(node, "argument_list")
        first = arguments.named_children[0] if arguments and arguments.named_child_count else None
        if first is None or first.type != "string_literal":
            return None
        return _unquote(_text(source, first)) or None
    # `using A, B` names both; `import X: a` names only X.
    packages = []
    for target in node.named_children:
        if target.type == "selected_import":
            target = target.named_children[0] if target.named_child_count else None
        if target is not None and target.type in ("identifier", "import_path"):
            packages.append(_text(source, target).strip())
    return [name for name in packages if name] or None


def _julia_call(node: Any, source: bytes) -> str | None:
    """The function a call invokes. `include` is an import, and the call
    that heads a definition (`f(x) = ...`, `function f(x)`) names what
    it defines, not a call from it.
    """
    parent = node.parent
    if parent is not None and parent.type == "signature":
        return None
    if parent is not None and parent.type == "assignment" and parent.named_child_count:
        if parent.named_children[0].start_byte == node.start_byte:
            return None
    name = _julia_callee(node, source)
    return None if name == "include" else name


def _julia_base_names(head: Any, source: bytes) -> list[str]:
    """The supertype after `<:` in a Julia type head, if any."""
    for child in _children(head):
        if child.type != "binary_expression":
            continue
        if not any(_text(source, part).strip() == "<:" for part in _children(child)):
            continue
        supertype = child.named_children[-1] if child.named_child_count else None
        if supertype is not None and supertype.type == "identifier":
            return [_text(source, supertype).strip()]
    return []


def _generic_import_module(lang: str, node: Any, source: bytes) -> str | None:
    """Generic extraction: strings, identifiers, or regex parsing."""
    import_text = _text(source, node)
    module = None

    # Try to find string literals first (JS/TS/Go)
    for child in _walk(node):
        child_kind = child.type
        if "string" in child_kind or "literal" in child_kind:
            module_text = _text(source, child)
            # Strip quotes from both ends
            module = module_text.strip().strip('"').strip("'").strip("`")
            if module and module not in ('"', "'", "`"):
                break

    # Fallback: try to extract from identifier/scoped_identifier
    # nodes (Python/Rust/C#/Java, and PHP's namespace_use_clause
    # wrapper)
    if not module:
        for child in _children(node):
            child_kind = child.type
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

    return module


_PHP_INCLUDE_NODES = frozenset(
    (
        "require_expression",
        "require_once_expression",
        "include_expression",
        "include_once_expression",
    )
)


def _rust_import(node: Any, source: bytes) -> str | None:
    """A `use` path as written, or the name in a `mod x;` declaration.

    `mod x { ... }` declares its module inline, so it imports nothing.
    """
    if node.type != "mod_item":
        return _generic_import_module("rust", node, source)
    if any(child.type == "declaration_list" for child in _children(node)):
        return None
    return _direct_child_text(node, source, "identifier")


def _php_literal(node: Any, source: bytes) -> str | None:
    """Value of a PHP string with no interpolation, or None."""
    if node.type not in ("string", "encapsed_string"):
        return None
    if any(child.is_named and child.type != "string_content" for child in _children(node)):
        return None
    return _unquote(_text(source, node))


def _php_include(node: Any, source: bytes) -> str | None:
    """The file a PHP require/include loads when its path is a literal.

    The argument may be parenthesized, or `__DIR__ . '/x.php'`, which is
    the importer's own folder. A plain path is taken relative to the
    importing file, as Ruby's require_relative is. A variable or any
    other expression names nothing.
    """
    expr = next((child for child in _children(node) if child.is_named), None)
    while expr is not None and expr.type == "parenthesized_expression":
        expr = next((child for child in _children(expr) if child.is_named), None)
    if expr is None:
        return None
    if expr.type == "binary_expression":
        left = expr.child_by_field_name("left")
        operator = expr.child_by_field_name("operator")
        right = expr.child_by_field_name("right")
        if left is None or operator is None or right is None:
            return None
        if _text(source, left) != "__DIR__" or _text(source, operator) != ".":
            return None
        path = _php_literal(right, source)
        return "./" + path.lstrip("/") if path is not None else None
    path = _php_literal(expr, source)
    if not path or path.startswith("/"):
        return None
    return path if path.startswith(("./", "../")) else "./" + path


def _php_import(node: Any, source: bytes) -> str | None:
    """A PHP `use` clause, or the path of a require/include."""
    if node.type in _PHP_INCLUDE_NODES:
        return _php_include(node, source)
    return _generic_import_module("php", node, source)


_IMPORT_READERS = {
    "rust": _rust_import,
    "php": _php_import,
    "ruby": _ruby_require,
    "c": _c_include,
    "cpp": _c_include,
    "kotlin": _kotlin_import,
    "scala": _scala_import,
    "swift": _swift_import,
    "bash": _bash_import,
    "powershell": _powershell_import,
    "lua": _lua_require,
    "perl": _perl_import,
    "dart": _dart_import,
    "objc": _objc_import,
    "zig": _zig_import,
    "elixir": _elixir_import,
    "haskell": _haskell_import,
    "ocaml": _ocaml_import,
    "ocaml_interface": _ocaml_import,
    "r": _r_import,
    "julia": _julia_import,
}


def _import_module(lang: str, node: Any, source: bytes) -> str | list[str] | None:
    """The module an import node names, the modules when it names
    several, or None when it names none.
    """
    reader = _IMPORT_READERS.get(lang)
    if reader is not None:
        return reader(node, source)
    return _generic_import_module(lang, node, source)


_PATH_KINDS = frozenset(
    ["scoped_identifier", "scoped_type_identifier", "qualified_identifier", "scope_resolution"]
)


def _expression_name(node: Any, source: bytes) -> str | None:
    """Reduce a callee expression to the identifier a caller would match
    on.

    ponytail: positional heuristic, not name resolution. Upgrade to
    per-language field lookups if a language needs more than "last
    segment wins".
    """
    kind = node.type
    if kind in _PATH_KINDS:
        # `Foo::new`, `crate::x::f`, `ns::Foo::make`: the name is the
        # last `::` segment, as `a.b` keeps `b`. Rust and C++ both name
        # that segment with a `name` field.
        tail = node.child_by_field_name("name")
        if tail is None and node.named_child_count:
            tail = node.named_children[-1]
        return _expression_name(tail, source) if tail is not None else None
    if _is_identifier_kind(kind):
        return _text(source, node)
    if "generic" in kind:
        # GetService<IFoo>() -- the method is the first segment, not the
        # type argument.
        for child in _children(node):
            name = _expression_name(child, source)
            if name:
                return name
        return None
    # Member access (a.b.Method): the method is the last segment.
    for child in reversed(list(_children(node))):
        if "argument" in child.type:
            continue
        name = _expression_name(child, source)
        if name:
            return name
    return None


def _base_type_names(container: Any, source: bytes) -> list[str]:
    """Every base type named in a heritage clause, in source order.

    Deliberately not a _walk: descending the whole subtree would also
    collect the type arguments, so `IHandler<Command>` would yield
    `Command` as a second base. Recurse only through the clause wrappers
    (TS splits extends/implements into two).
    """
    names: list[str] = []

    def visit(node: Any) -> None:
        for child in _children(node):
            kind = child.type
            # TS splits extends/implements into two clauses; Java wraps
            # its interface list in a type_list, so `implements G, R`
            # would otherwise collapse to whichever name the reversed
            # scan reached first. Scala uses extends_clause and with.
            # Swift/Kotlin use delegation/inheritance_specifier.
            if kind in (
                "extends_clause",
                "implements_clause",
                "extends_type_clause",
                "type_list",
                "with",
                "delegation_specifier",
                "inheritance_specifier",
                "superclass",
            ):
                visit(child)
                continue
            name = _expression_name(child, source)
            if name and name not in names:
                names.append(name)

    visit(container)
    return names


def _callee_node(node: Any) -> Any:
    """The node holding the called expression of a call, or None."""
    # Most grammars make the whole callee expression the first child,
    # but Java's method_invocation and PHP's member/scoped calls put the
    # object there and the method in a `name` field -- taking child 0
    # recorded `$this` and `Registry` as the callee. Ask for the field
    # first; where it is absent (python, js, ts, go, rust, csharp)
    # `function` is child 0, so nothing changes for them.
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
    return callee


_SELF_CALLEE = re.compile(r"(self|this|cls)\.[A-Za-z_]\w*")


def _self_receiver(node: Any, source: bytes) -> str | None:
    """`self`, `this` or `cls` when the call is `<receiver>.name(...)`.

    A longer chain (`this.x.f()`) has no receiver.
    """
    callee = _callee_node(node)
    if callee is None:
        return None
    match = _SELF_CALLEE.fullmatch(_text(source, callee).strip())
    return match.group(1) if match else None


def _callee_name(node: Any, source: bytes) -> str | None:
    """The called function's name, not the expression that produced it.

    A call node's first child is the whole function expression. Using
    its text verbatim turns `schemes.Where(x => ...)` into a multi-line
    blob -- unmatchable as a call target, and large enough to drag file
    content into the record (one such blob tripped the secret scanner
    and silently dropped the file from the index). Take the final
    identifier, as the Python adapter does with `Attribute.attr`.
    """
    callee = _callee_node(node)
    if callee is None:
        return None
    name = (_expression_name(callee, source) or "").strip()
    # Anything still carrying whitespace is an expression we failed to
    # reduce. Dropping it beats emitting an edge nothing can resolve.
    if not name or name.split() != [name]:
        return None
    return name


def _perl_call(node: Any, source: bytes) -> str | None:
    """Perl function or method name: the last segment of `Pkg::name`."""
    callee = node.child_by_field_name("function")
    if callee is None:
        callee = node.child_by_field_name("method")
    if callee is None:
        return None
    name = _text(source, callee).strip().rsplit("::", 1)[-1]
    return name if _PERL_NAME.fullmatch(name) else None


_CALL_READERS = {
    "bash": _bash_call,
    "powershell": _powershell_call,
    "perl": _perl_call,
    "dart": _dart_call,
    "objc": _objc_call,
    "zig": _zig_call,
    "elixir": _elixir_call,
    "haskell": _haskell_call,
    "ocaml": _ocaml_call,
    "ocaml_interface": _ocaml_call,
    "r": _r_call,
    "julia": _julia_call,
}


def _call_name(lang: str, node: Any, source: bytes) -> str | None:
    """The name a call node invokes, by language."""
    reader = _CALL_READERS.get(lang)
    if reader is not None:
        return reader(node, source)
    return _callee_name(node, source)


GRAMMAR_UNAVAILABLE = "parse_error:GrammarUnavailable"


class GrammarUnavailableError(RuntimeError):
    """A tree-sitter grammar could not be downloaded in this process."""


# Set by the first failed download. The pack would otherwise retry the
# download for every file, each attempt waiting out the network first.
_download_failed = False
# What that first failure said, one line; see grammar_error_reason().
_download_reason: str | None = None

_REASON_MAX = 200
_DOWNLOAD_PREFIX = re.compile(r"^\s*download error:\s*", re.IGNORECASE)
# Words a network failure reports. A cache-directory or other local
# failure has none of them, so it is not blamed on the network.
_NETWORK_FAILURE = re.compile(
    r"\b(?:connect\w*|time[d]?\s?out|dns|resolv\w*|network|proxy|ssl|tls|certificate"
    r"|https?|unreachable|refused|socket)\b",
    re.IGNORECASE,
)


def grammar_error_reason(error: object) -> str:
    """What a grammar download reported, as one short, redacted line.

    The pack's ``Download error:`` prefix is dropped, since the warning
    already says the grammars failed.
    """
    from ..security import redact_sensitive_text

    lines = str(error).strip().splitlines()
    text = _DOWNLOAD_PREFIX.sub("", lines[0] if lines else "").strip()
    if len(text) > _REASON_MAX:
        text = text[: _REASON_MAX - 3].rstrip() + "..."
    return redact_sensitive_text(text)


def is_network_failure(reason: str | None) -> bool:
    """Whether a grammar failure reason names a network problem."""
    return bool(reason) and _NETWORK_FAILURE.search(reason) is not None


def is_grammar_download_failure(error: BaseException) -> bool:
    """Whether the pack raised a grammar download failure.

    Matched by the DownloadError name, since a stub pack may not define
    it. Some failures, such as a missing cache folder, come out as a
    plain RuntimeError with the same ``Download error:`` prefix.
    """
    if error.__class__.__name__ == "DownloadError":
        return True
    return _DOWNLOAD_PREFIX.match(str(error)) is not None


def grammar_failure_reason() -> str | None:
    """Why this process's grammar download failed, or None."""
    return _download_reason


def _pack_grammar(getter: str, lang: str) -> Any:
    """Call ``tree_sitter_language_pack.<getter>(lang)``.

    This is the one grammar loader. After a download failure, every
    later call in this process raises GrammarUnavailableError without
    trying again. Other errors pass through.
    """
    global _download_failed, _download_reason
    import tree_sitter_language_pack as pack

    if _download_failed:
        raise GrammarUnavailableError(lang)
    try:
        return getattr(pack, getter)(lang)
    except Exception as exc:
        if not is_grammar_download_failure(exc):
            raise
        _download_failed = True
        _download_reason = grammar_error_reason(exc)
        raise GrammarUnavailableError(lang) from exc


def load_language(lang: str) -> Any:
    """The tree-sitter Language for ``lang``."""
    return _pack_grammar("get_language", lang)


def load_parser(lang: str) -> Any:
    """The pack's ready-made parser for ``lang``."""
    return _pack_grammar("get_parser", lang)


@functools.cache
def _grammar(lang: str):
    """Parser and fact query for ``lang``, built once per process."""
    from tree_sitter import Parser, Query, QueryError

    language = load_language(lang)
    node_types = NODE_TYPES[lang]
    patterns = []
    for category in ("definitions", "imports", "calls"):
        for kind in sorted(node_types[category]):
            pattern = f"({kind}) @{category}"
            try:
                Query(language, pattern)
            except QueryError:
                # Not a node type of this grammar: the full walk never
                # saw one either.
                continue
            patterns.append(pattern)
    return Parser(language), (Query(language, "\n".join(patterns)) if patterns else None)


def _parse_view(lang: str, source: bytes) -> bytes:
    """The bytes the grammar parses, which may differ from ``source``.

    The PowerShell grammar reads `$PSScriptRoot/x.ps1` as a division
    and reports an error for the whole file, dropping every symbol
    in it. Masking the `$` keeps the length, so node offsets still
    point into the original source, where names and paths are read.
    """
    if lang == "powershell":
        return source.replace(b"$PSScriptRoot/", b"_PSScriptRoot/")
    return source


def _depth(node: Any) -> int:
    depth = 0
    while (node := node.parent) is not None:
        depth += 1
    return depth


def _captures(query: Any, root_node: Any) -> list[tuple[Any, str]]:
    """``(node, category)`` for every fact node, in the order the full
    walk visited them.

    Query captures are not returned in document order, so each category
    is sorted into the walk's pre-order: earlier start first, then the
    enclosing (longer) node, then -- only for identical spans -- the
    shallower one. The walk handled a node's categories in definitions,
    imports, calls order, but only the order within each category is
    observable.
    """
    from tree_sitter import QueryCursor

    by_category = QueryCursor(query).captures(root_node)
    ordered = []
    for category in ("definitions", "imports", "calls"):
        nodes = sorted(
            by_category.get(category, ()), key=lambda node: (node.start_byte, -node.end_byte)
        )
        spans = [(node.start_byte, node.end_byte) for node in nodes]
        if len(set(spans)) != len(spans):
            nodes.sort(key=lambda node: (node.start_byte, -node.end_byte, _depth(node)))
        ordered.extend((node, category) for node in nodes)
    return ordered


def extract(path: str | Path, source: str, language: str | None = None) -> dict:
    """Parse and return definitions, imports, calls with status.

    ``language`` replaces the one ``language_for(path)`` would pick.
    """
    if isinstance(path, str):
        path = Path(path)

    lang = language if language is not None else language_for(path)
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
    # The start byte of each definition's parent block, parallel to
    # definitions. Haskell and Elixir merge clauses by it.
    owners: list[int | None] = []
    imports: list[dict] = []
    calls: list[dict] = []
    inherits: list[dict] = []
    seen_defs: set[tuple[str, int | None]] = set()
    base_kinds = node_types.get("bases", frozenset())

    try:
        parser, query = _grammar(lang)
        # Every read is by byte offset, so bind to bytes once rather
        # than at each _text call site -- one missed site is silent
        # corruption.
        source = source.encode("utf-8")
        tree = parser.parse(_parse_view(lang, source))
        root_node = tree.root_node
    except GrammarUnavailableError:
        return {
            "definitions": [],
            "imports": [],
            "calls": [],
            "inherits": [],
            "status": GRAMMAR_UNAVAILABLE,
        }
    except Exception as exc:
        return {
            "definitions": [],
            "imports": [],
            "calls": [],
            "inherits": [],
            "status": f"parse_error:{exc.__class__.__name__}",
        }

    # Only definition, import and call nodes carry facts. The query
    # finds them natively, in document order per category; visiting
    # every node from Python instead cost more than parsing. A node
    # matching several categories is handled once per category, exactly
    # as the full walk did.
    facts = (
        _captures(query, root_node)
        if query is not None
        else ((node, None) for node in _walk(root_node))
    )
    perl_packages = _perl_packages(root_node, source) if lang == "perl" else []
    for node, category in facts:
        kind = node.type

        # Definitions
        if category in (None, "definitions") and kind in node_types["definitions"]:
            name = _definition_name(lang, node, source)
            if name:  # Skip if no name found
                span = _definition_span(lang, node)
                line = _line(span)
                key = (name, line)
                if key not in seen_defs:
                    seen_defs.add(key)
                    exported = _is_exported(node, lang, source)
                    definitions.append(
                        {
                            "name": name,
                            "kind": _definition_kind(lang, node, source),
                            "line_start": line,
                            "line_end": _end_line(span),
                            "exported": exported,
                        }
                    )
                    owners.append(node.parent.start_byte if node.parent is not None else None)
                    # Heritage hangs directly off the type declaration.
                    # Functions have no such child, so this never fires
                    # for them.
                    for child in _children(node):
                        if child.type in base_kinds:
                            if lang == "dart":
                                bases = _dart_base_names(child, source)
                            elif lang == "julia":
                                bases = _julia_base_names(child, source)
                            else:
                                bases = _base_type_names(child, source)
                            for base in bases:
                                inherits.append({"type": name, "base": base, "line": line})
                    if lang == "powershell" and kind == "class_statement":
                        for base in _powershell_bases(node, source):
                            inherits.append({"type": name, "base": base, "line": line})
                    if lang == "objc" and kind == "class_interface":
                        for base in _objc_bases(node, source):
                            inherits.append({"type": name, "base": base, "line": line})

        # Imports
        if category in (None, "imports") and kind in node_types["imports"]:
            module = _import_module(lang, node, source)
            if module:
                for name in [module] if isinstance(module, str) else module:
                    imports.append({"module": name, "line": _line(node)})
            elif lang == "perl":
                # `use parent X` makes X a base of the package above it.
                owner = _perl_package_at(perl_packages, node.start_byte)
                if owner:
                    for base in _perl_base_names(node, source):
                        inherits.append({"type": owner, "base": base, "line": _line(node)})

        # Calls
        if category in (None, "calls") and kind in node_types["calls"]:
            call_name = _call_name(lang, node, source)
            if call_name:
                call = {"name": call_name, "line": _line(node)}
                receiver = _self_receiver(node, source)
                if receiver:
                    call["receiver"] = receiver
                calls.append(call)

    if lang in ("haskell", "elixir"):
        definitions = _merge_equations(definitions, owners)

    # Check for parse errors
    if root_node.has_error:
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
