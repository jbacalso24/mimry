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
        "imports": frozenset(["use_declaration"]),
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
        "imports": frozenset(["namespace_use_declaration"]),
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
}


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


def symbol_kind(language: str, node_kind: str) -> str:
    """Map a tree-sitter node kind to MIMRY's symbol kind vocabulary."""
    # A PowerShell method is named "class_..." by the grammar but is a
    # function, not a class.
    if node_kind == "class_method_definition":
        return "function"
    if node_kind == "interface_declaration":
        return "interface"
    if node_kind == "protocol_declaration":
        return "interface"
    if node_kind in ("enum_declaration", "enum_specifier"):
        return "enum"
    if node_kind in (
        "record_declaration",
        "trait_declaration",
        "trait_definition",
        "object_definition",
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
    ):
        return "module"
    if node_kind in ("type_declaration", "typedef_declaration"):
        return "type"
    if node_kind == "variable_declarator":
        return "variable"
    if node_kind == "union_specifier":
        return "class"
    return "function"


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


def _definition_name(node: Any, source: bytes) -> str | None:
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


_IMPORT_READERS = {
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
}


def _import_module(lang: str, node: Any, source: bytes) -> str | None:
    """The module an import node names, or None when it names none."""
    reader = _IMPORT_READERS.get(lang)
    if reader is not None:
        return reader(node, source)
    return _generic_import_module(lang, node, source)


def _expression_name(node: Any, source: bytes) -> str | None:
    """Reduce a callee expression to the identifier a caller would match
    on.

    ponytail: positional heuristic, not name resolution. Upgrade to
    per-language field lookups if a language needs more than "last
    segment wins".
    """
    kind = node.type
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


def _callee_name(node: Any, source: bytes) -> str | None:
    """The called function's name, not the expression that produced it.

    A call node's first child is the whole function expression. Using
    its text verbatim turns `schemes.Where(x => ...)` into a multi-line
    blob -- unmatchable as a call target, and large enough to drag file
    content into the record (one such blob tripped the secret scanner
    and silently dropped the file from the index). Take the final
    identifier, as the Python adapter does with `Attribute.attr`.
    """
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


def _pack_grammar(getter: str, lang: str) -> Any:
    """Call ``tree_sitter_language_pack.<getter>(lang)``.

    This is the one grammar loader. After a DownloadError, every later
    call in this process raises GrammarUnavailableError without trying
    again. Other errors pass through.
    """
    global _download_failed
    import tree_sitter_language_pack as pack

    if _download_failed:
        raise GrammarUnavailableError(lang)
    try:
        return getattr(pack, getter)(lang)
    except pack.DownloadError as exc:
        _download_failed = True
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
                    # Heritage hangs directly off the type declaration.
                    # Functions have no such child, so this never fires
                    # for them.
                    for child in _children(node):
                        if child.type in base_kinds:
                            for base in _base_type_names(child, source):
                                inherits.append({"type": name, "base": base, "line": line})
                    if lang == "powershell" and kind == "class_statement":
                        for base in _powershell_bases(node, source):
                            inherits.append({"type": name, "base": base, "line": line})

        # Imports
        if category in (None, "imports") and kind in node_types["imports"]:
            module = _import_module(lang, node, source)
            if module:
                imports.append(
                    {
                        "module": module,
                        "line": _line(node),
                    }
                )
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
                calls.append(
                    {
                        "name": call_name,
                        "line": _line(node),
                    }
                )

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
