"""Symbols and imports for Groovy and Gradle files.

The tree-sitter Groovy grammar reads a Groovy file as commands and
blocks. It has no declaration node for a class, a method or a task, and
it fails on everyday syntax such as a division, a Spock string method
name or a typed closure. So a definition is read from its header text:
the words before the first '{' of the command that starts it. The
tree only says which commands are readable.

A file is first cut into top-level pieces, so a piece the grammar cannot
read costs only itself (languages._extract_groovy does the parsing).
Lines come from the source bytes, so a definition keeps its real line
range even when the tree around it is damaged.
"""

from __future__ import annotations

import bisect
import re

# A type declaration, such as `class Account implements Ledger {`.
_TYPE_HEAD = re.compile(
    r"^(?:(?:public|private|protected|static|final|abstract|sealed|strictfp"
    r"|@[\w.]+(?:\([^)]*\))?)\s+)*"
    r"(?P<tag>class|interface|trait|enum)\s+(?P<name>[A-Za-z_$][\w$]*)"
)
# A word before a method name: a modifier, a type, `def`, an annotation
# or a generic parameter list.
_PREFIX_WORD = r"(?:@[\w.]+(?:\([^)]*\))?|<[^<>]*>|[A-Za-z_$][\w$.]*(?:<[^{}()=]*>)?(?:\[\])*)"
# A method or function header. Spock names, which are strings, keep
# their quotes here and lose them in head_at.
_CALLABLE_HEAD = re.compile(
    r"^(?P<prefix>(?:" + _PREFIX_WORD + r"\s+)+)"
    r"(?P<name>\"[^\"\n]+\"|'[^'\n]+'|[A-Za-z_$][\w$]*)\s*\([^()]*\)\s*(?:throws\s+[\w.,\s]+)?$"
)
# Words that look like a method call but are control flow or a keyword.
_NOT_NAMES = frozenset(
    ("if", "for", "while", "switch", "catch", "synchronized", "return", "new", "assert", "throw")
)
_NOT_PREFIXES = frozenset(("return", "new", "throw", "else", "case", "assert", "yield", "in"))
# A Gradle task: `task name {` or `tasks.register("name") {`.
_TASK_HEAD = re.compile(r"^task\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?:\([^)]*\))?$")
_TASK_REGISTER = re.compile(r"^tasks\.register\(\s*[\"'](?P<name>[^\"']+)[\"']\s*(?:,[^)]*)?\)$")
# An import line. A wildcard names a package, not a file, so it is
# dropped by the caller.
_IMPORT = re.compile(
    rb"^[ \t]*import[ \t]+(?:static[ \t]+)?(?P<module>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)"
    rb"(?P<wild>\.\*)?[ \t]*(?:as[ \t]+[A-Za-z_$][\w$]*)?[ \t]*;?[ \t]*\r?$",
    re.MULTILINE,
)
_TYPE_KINDS = {"class": "class", "trait": "class", "interface": "interface", "enum": "enum"}
# Bytes that continue a statement across a line break.
_CONTINUES_AFTER = frozenset(b",([{.+-*/&|=?:<>")
_CONTINUES_BEFORE = frozenset(b".?:&|+-*/,=<>{")
_QUOTES = (b'"', b"'")


def line_starts(raw: bytes) -> list[int]:
    """Byte offset where each line starts."""
    starts = [0]
    pos = raw.find(b"\n")
    while pos >= 0:
        starts.append(pos + 1)
        pos = raw.find(b"\n", pos + 1)
    return starts


def _skip_string(raw: bytes, i: int) -> int:
    """Offset just past the string that opens at ``i``."""
    quote = raw[i : i + 1]
    close = quote * 3 if raw[i : i + 3] == quote * 3 else quote
    end = raw.find(close, i + len(close))
    return len(raw) if end < 0 else end + len(close)


def _skip_comment(raw: bytes, i: int) -> int | None:
    """Offset past the comment at ``i``, or None if there is none."""
    if raw[i : i + 2] == b"//":
        end = raw.find(b"\n", i)
        return len(raw) if end < 0 else end
    if raw[i : i + 2] == b"/*":
        end = raw.find(b"*/", i + 2)
        return len(raw) if end < 0 else end + 2
    return None


def _next_significant(raw: bytes, i: int) -> int:
    """The first byte at or after ``i`` that is not blank, or 0."""
    n = len(raw)
    while i < n and raw[i] in b" \t\r\n":
        i += 1
    return raw[i] if i < n else 0


def top_level_chunks(raw: bytes) -> list[tuple[int, int]]:
    """Byte spans of the top-level pieces of ``raw``.

    A piece ends at a newline outside any bracket, unless the line ends
    with an operator or the next line starts with one that continues the
    statement. Strings and comments are skipped, so a brace in them
    counts for nothing. A brace-balanced class is one piece.
    """
    chunks: list[tuple[int, int]] = []
    depth = 0
    start = 0
    has_text = False  # a significant byte since the piece began
    last = 0  # the last significant byte outside comments
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if raw[i : i + 1] in _QUOTES:
            i = _skip_string(raw, i)
            last, has_text = c, True
            continue
        after_comment = _skip_comment(raw, i)
        if after_comment is not None:
            i = after_comment
            continue
        if c == 0x0A:
            if depth == 0 and has_text:
                nxt = _next_significant(raw, i + 1)
                if last not in _CONTINUES_AFTER and nxt and nxt not in _CONTINUES_BEFORE:
                    chunks.append((start, i))
                    start, has_text, last = i + 1, False, 0
        elif c not in b" \t\r":
            has_text, last = True, c
            if c in b"([{":
                depth += 1
            elif c in b")]}":
                depth = max(0, depth - 1)
        i += 1
    if has_text:
        chunks.append((start, n))
    return chunks


def _header_end(raw: bytes, start: int) -> tuple[int, bool]:
    """Where the header at ``start`` ends, and whether a body follows.

    The header runs to the first '{' outside brackets (a body) or to the
    end of the line (no body).
    """
    depth = 0
    i = start
    n = len(raw)
    while i < n:
        c = raw[i]
        if raw[i : i + 1] in _QUOTES:
            i = _skip_string(raw, i)
            continue
        after_comment = _skip_comment(raw, i)
        if after_comment is not None:
            return i, False
        if c in b"([":
            depth += 1
        elif c in b")]":
            depth = max(0, depth - 1)
        elif c == 0x7B and depth == 0:  # {
            return i, True
        elif c in (0x0A, 0x3B) and depth == 0:  # newline, ;
            return i, False
        i += 1
    return n, False


def _body_end(raw: bytes, brace: int) -> int:
    """Offset just past the '}' that closes the '{' at ``brace``."""
    depth = 0
    i = brace
    n = len(raw)
    while i < n:
        c = raw[i]
        if raw[i : i + 1] in _QUOTES:
            i = _skip_string(raw, i)
            continue
        after_comment = _skip_comment(raw, i)
        if after_comment is not None:
            i = after_comment
            continue
        if c == 0x7B:  # {
            depth += 1
        elif c == 0x7D:  # }
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def head_at(raw: bytes, start: int, starts: list[int]) -> dict | None:
    """The definition whose header begins at byte ``start``, or None.

    The result carries the header's line span and byte span. A method
    with no body is kept here and judged by its enclosing type later.
    """
    while start < len(raw) and raw[start] in b" \t":
        start += 1
    end, has_body = _header_end(raw, start)
    head = raw[start:end].decode("utf-8", "replace").strip()
    if not head:
        return None
    span_end = _body_end(raw, end) if has_body else end
    line_start = bisect.bisect_right(starts, start)
    line_end = bisect.bisect_right(starts, max(span_end - 1, start))
    base = {"start": start, "span_end": span_end, "line_start": line_start, "line_end": line_end}
    found = _TYPE_HEAD.match(head)
    if found:
        return {**base, "kind": "type", "tag": found.group("tag"), "name": found.group("name")}
    if has_body:
        found = _TASK_HEAD.match(head) or _TASK_REGISTER.match(head)
        if found:
            return {**base, "kind": "task", "tag": "task", "name": found.group("name")}
    found = _CALLABLE_HEAD.match(head)
    if found is None:
        return None
    prefix = found.group("prefix").split()
    name = found.group("name")
    if name in _NOT_NAMES or prefix[0] in _NOT_PREFIXES:
        return None
    return {
        **base,
        "kind": "callable",
        "tag": "callable",
        "name": name.strip("\"'"),
        "has_body": has_body,
        "abstract": "abstract" in prefix,
    }


def symbols(raw: bytes, offsets: list[int]) -> list[dict]:
    """Classes, interfaces, traits, enums, methods, functions and tasks.

    ``offsets`` are the byte offsets where a command starts. A callable
    inside a type is a method, one outside is a function. A method with
    no body is kept only in an interface, a trait or an abstract method.
    """
    starts = line_starts(raw)
    heads = {}
    for offset in sorted(set(offsets)):
        head = head_at(raw, offset, starts)
        if head is not None:
            heads.setdefault((head["start"], head["name"]), head)
    ordered = sorted(heads.values(), key=lambda h: (h["start"], -h["span_end"]))
    types = [h for h in ordered if h["kind"] == "type"]
    type_starts = [h["start"] for h in types]
    result: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for head in ordered:
        if head["kind"] == "type":
            kind = _TYPE_KINDS[head["tag"]]
        elif head["kind"] == "task":
            kind = "task"
        else:
            enclosing = _innermost(head, types, type_starts)
            if enclosing is None:
                if not head["has_body"]:
                    continue
                kind = "function"
            else:
                interface_like = enclosing["tag"] in ("interface", "trait")
                if not head["has_body"] and not (interface_like or head["abstract"]):
                    continue
                kind = "method"
        key = (head["name"], kind, head["line_start"])
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "name": head["name"],
                "kind": kind,
                "line_start": head["line_start"],
                "line_end": head["line_end"],
                "exported": False,
            }
        )
    return result


def _innermost(head: dict, types: list[dict], type_starts: list[int]) -> dict | None:
    """The type declaration that contains ``head``, if any.

    ``types`` is sorted by start. Enclosing types nest, so the first one
    found going back from ``head`` that still runs past it is the
    innermost.
    """
    for i in range(bisect.bisect_left(type_starts, head["start"]) - 1, -1, -1):
        if types[i]["span_end"] > head["start"]:
            return types[i]
    return None


def imports(raw: bytes, starts: list[int] | None = None) -> list[dict]:
    """Module names of the import lines, with their lines."""
    if starts is None:
        starts = line_starts(raw)
    found = []
    for match in _IMPORT.finditer(raw):
        if match.group("wild"):
            continue
        found.append(
            {
                "module": match.group("module").decode("utf-8", "replace"),
                "line": bisect.bisect_right(starts, match.start()),
            }
        )
    return found
