"""Symbols and call edges for SQL files.

One generic SQL grammar reads PostgreSQL, MySQL and SQLite, but it
cannot read a trigger or procedure body written as BEGIN ... END, and
a parse error swallows the statements beside it. So a file is first cut
into statements, and each one is parsed alone: a statement the grammar
does not know costs only itself.

Schema objects (tables, views, indexes, sequences, schemas, types) and
programs (functions, procedures, triggers) get symbols, named as the
source writes them. A statement that reads or writes a table, or a
trigger that runs a function, gives a call from the object it sits in.
Plain queries and ALTER statements sit in no object, so they give no
caller.

extract_statement() takes a parsed tree, the bytes it was parsed from
and the statement's first row, and fills the lists it is given.
"""

from __future__ import annotations

import re

# Object statement type -> symbol kind.
OBJECT_KINDS = {
    "create_table": "table",
    "create_view": "view",
    "create_materialized_view": "view",
    "create_function": "function",
    "create_procedure": "function",
    "create_trigger": "trigger",
    "create_type": "type",
    "create_domain": "type",
    "create_index": "index",
    "create_sequence": "sequence",
    "create_schema": "module",
}
_QUOTES = '"`[]'
# A statement that starts with CREATE opens a block at its BEGIN, but
# BEGIN TRANSACTION and a bare BEGIN do not.
_CREATE_START = re.compile(rb"(?:\s|--[^\n]*)*CREATE\b", re.IGNORECASE)
# A trigger the grammar cannot read: its name, and the table after ON.
_TRIGGER_HEADER = re.compile(
    rb"(?:\s|--[^\n]*)*(?P<head>CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    rb"(?P<name>[^\s(]+)\s.*?\bON\s+(?P<table>[^\s(]+))",
    re.IGNORECASE | re.DOTALL,
)
# Tokens that matter for statement boundaries. Strings, quoted names and
# comments are read whole, so a ';' inside them ends nothing. Dollar
# quotes hold PostgreSQL function bodies. END IF, END LOOP and the like
# close blocks this scan does not count.
_TOKENS = (
    rb"'(?:[^'\\]|\\[\s\S]|'')*'"
    rb"|\"(?:[^\"]|\"\")*\""
    rb"|`[^`]*`"
    rb"|--[^\n]*"
    rb"|/\*[\s\S]*?\*/"
    rb"|\$\$[\s\S]*?\$\$"
    rb"|\$(?P<tag>[A-Za-z_]\w*)\$[\s\S]*?\$(?P=tag)\$"
    rb"|^[ \t]*DELIMITER[ \t]+(?P<newdelim>\S+)[ \t]*$"
    rb"|\bEND[ \t]+CASE\b(?P<end>)"
    rb"|\bEND[ \t]+(?:IF|LOOP|WHILE|REPEAT)\b"
    rb"|\bEND\b(?P<end2>)"
    rb"|\bBEGIN\b(?![ \t]*(?:;|TRANSACTION|WORK|DEFERRED|IMMEDIATE|EXCLUSIVE|TRAN\b))(?P<begin>)"
    rb"|\bCASE\b(?P<case>)"
    rb"|\w+"
    rb"|[^\s\w]"
)


def _pattern(delimiter: bytes) -> re.Pattern:
    """The token scan with ``delimiter`` as the statement terminator."""
    return re.compile(
        b"(?P<term>" + re.escape(delimiter) + b")|" + _TOKENS,
        re.IGNORECASE | re.MULTILINE,
    )


def statement_spans(raw: bytes) -> list[tuple[int, int]]:
    """Byte spans of the statements in ``raw``, without terminators.

    A statement ends at ';', or at the delimiter that a MySQL DELIMITER
    line sets. A ';' inside a BEGIN ... END block (a trigger or
    procedure body) does not end the statement. DELIMITER lines are not
    statements.
    """
    spans: list[tuple[int, int]] = []
    delimiter = b";"
    pattern = _pattern(delimiter)
    start = 0
    depth = 0
    pos = 0
    while (match := pattern.search(raw, pos)) is not None:
        pos = match.end()
        if match.group("newdelim") is not None:
            spans.append((start, match.start()))
            start = match.end()
            delimiter = match.group("newdelim")
            pattern = _pattern(delimiter)
            depth = 0
        elif match.group("term") is not None and depth == 0:
            spans.append((start, match.start()))
            start = match.end()
        elif match.group("begin") is not None:
            if _CREATE_START.match(raw, start) or depth:
                depth += 1
        elif match.group("case") is not None:
            depth += 1
        elif match.group("end") is not None or match.group("end2") is not None:
            depth = max(0, depth - 1)
    spans.append((start, len(raw)))
    return [(a, b) for a, b in spans if raw[a:b].strip()]


def _text(raw: bytes, node) -> str:
    return raw[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _unquoted(text: str) -> str:
    return text.strip().strip(_QUOTES)


def _name(raw: bytes, node) -> str:
    """A dotted name as written, such as public.users. Quotes go."""
    parts = [_unquoted(_text(raw, child)) for child in node.children if child.type == "identifier"]
    return ".".join(part for part in parts if part)


def _object_name(raw: bytes, node) -> str:
    """The name an object statement creates, or "" when it has none."""
    if node.type in ("create_index", "create_schema"):
        # An index may be unnamed (CREATE INDEX ON t (c)), and its table
        # is an object_reference, so only a bare identifier is the name.
        for child in node.children:
            if child.type == "identifier":
                return _unquoted(_text(raw, child))
        return ""
    for child in node.children:
        if child.type == "object_reference":
            return _name(raw, child)
    return ""


def _symbol(name: str, kind: str, start: int, end: int) -> dict:
    """A symbol over rows ``start`` to ``end``, both 1-based."""
    return {
        "name": name,
        "kind": kind,
        "line_start": start,
        "line_end": end,
        "exported": False,
    }


def _reference_kind(node) -> str | None:
    """What an object_reference names by its position, or None.

    FROM, JOIN, UPDATE, DELETE FROM and INSERT INTO name a table, as do
    CREATE INDEX ... ON and a trigger's ON. A foreign key's REFERENCES
    names one too. A trigger's EXECUTE FUNCTION names a function. Any
    other position (the object's own name, a column type) is not a
    reference.
    """
    previous = node.prev_sibling
    previous_type = previous.type if previous is not None else ""
    parent = node.parent
    parent_type = parent.type if parent is not None else ""
    if previous_type == "keyword_references":
        return "table"
    if parent_type in ("relation", "from", "insert", "create_index"):
        return "table"
    if parent_type == "create_trigger":
        if previous_type == "keyword_on":
            return "table"
        if previous_type in ("keyword_function", "keyword_procedure"):
            return "function"
    return None


def _references(raw: bytes, node, row: int) -> list[dict]:
    """Every table or function a statement names, as call records."""
    calls = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "object_reference" and _reference_kind(current) is not None:
            name = _name(raw, current)
            if name:
                calls.append({"name": name, "line": row + current.start_point[0] + 1})
        stack.extend(reversed(current.children))
    return calls


def extract_statement(root, raw: bytes, row: int, out: dict, seen: set) -> bool:
    """Add the objects of one parsed statement to ``out``.

    ``row`` is the number of rows before the statement, so its lines are
    file lines. Returns False when the statement creates no object, so
    the caller can try the trigger header fallback.
    """
    found = False
    stack = [root]
    while stack:
        node = stack.pop()
        kind = OBJECT_KINDS.get(node.type)
        if kind is None:
            stack.extend(reversed(node.children))
            continue
        found = True
        name = _object_name(raw, node)
        if not name or (name, kind) in seen:
            continue
        seen.add((name, kind))
        end = node.end_point[0]
        if node.end_point[1] == 0 and end > node.start_point[0]:
            end -= 1
        out["definitions"].append(
            _symbol(name, kind, row + node.start_point[0] + 1, row + end + 1)
        )
        out["calls"].extend(_references(raw, node, row))
    return found


def trigger_header(raw: bytes, row: int, out: dict, seen: set) -> None:
    """A trigger the grammar cannot read, from its header alone.

    The name gets a symbol, and the table after ON a call. The body is
    not read.
    """
    match = _TRIGGER_HEADER.match(raw)
    if match is None:
        return
    name = _unquoted(match.group("name").decode("utf-8", "replace"))
    table = _unquoted(match.group("table").decode("utf-8", "replace"))
    if not name or not table or (name, "trigger") in seen:
        return
    seen.add((name, "trigger"))
    first = row + raw.count(b"\n", 0, match.start("head")) + 1
    last = row + raw.count(b"\n") + 1
    out["definitions"].append(_symbol(name, "trigger", first, last))
    table_line = row + raw.count(b"\n", 0, match.start("table")) + 1
    out["calls"].append({"name": table, "line": table_line})


def new_result() -> dict:
    return {"definitions": [], "imports": [], "calls": [], "inherits": []}
