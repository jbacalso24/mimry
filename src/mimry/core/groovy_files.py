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
# A method or function header up to the '(' of its parameter list. Spock
# names, which are strings, keep their quotes here and lose them in
# head_at. The list is matched by bracket counting in head_at, because
# its annotations and defaults can hold brackets of their own.
_CALLABLE_HEAD = re.compile(
    r"^(?P<prefix>(?:" + _PREFIX_WORD + r"\s+)+)"
    r"(?P<name>\"[^\"\n]+\"|'[^'\n]+'|[A-Za-z_$][\w$]*)\s*\("
)
# What may follow a parameter list: a throws clause and nothing else.
_CALLABLE_TAIL = re.compile(r"^\s*(?:throws\s+[\w.,\s]+)?$")
# Words that look like a method call but are control flow or a keyword.
_NOT_NAMES = frozenset(
    ("if", "for", "while", "switch", "catch", "synchronized", "return", "new", "assert", "throw")
)
_NOT_PREFIXES = frozenset(("return", "new", "throw", "else", "case", "assert", "yield", "in"))
# Words that are never the name of a call, as a call or as the first
# word of a command. `this(...)` and `super(...)` chain constructors.
CALL_KEYWORDS = _NOT_NAMES | frozenset(
    (
        "this",
        "super",
        "it",
        "def",
        "class",
        "interface",
        "trait",
        "enum",
        "static",
        "final",
        "private",
        "public",
        "protected",
        "abstract",
        "package",
        "import",
        "else",
        "case",
        "yield",
        "in",
        "throws",
    )
)
# A Gradle task: `task name {` or `tasks.register("name") {`.
_TASK_HEAD = re.compile(r"^task\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?:\([^)]*\))?$")
_TASK_REGISTER = re.compile(r"^tasks\.register\(\s*[\"'](?P<name>[^\"']+)[\"']\s*(?:,[^)]*)?\)$")
# A type argument list, removed (innermost first) before the bases of a
# header are read, and the keywords that introduce the bases.
_TYPE_ARGUMENTS = re.compile(r"<[^<>]*>")
_HERITAGE_KEYWORD = re.compile(r"\b(?:extends|implements)\b")
_QUALIFIED_NAME = re.compile(r"\s*(?:[A-Za-z_$][\w$]*\s*\.\s*)*(?P<name>[A-Za-z_$][\w$]*)\s*")
# The package line of a file, such as `package a.b`.
_PACKAGE = re.compile(
    r"^[ \t]*package[ \t]+(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)[ \t]*;?[ \t]*\r?$",
    re.MULTILINE,
)
# An import line. A wildcard is kept as `a.b.*`, and the resolver
# links it to the files of package `a.b`.
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
# Bytes after which an operand comes next, so a slash there opens a
# slashy string. After these words an operand comes too.
_OPERAND_AFTER = frozenset(b"=(,~[{;:?!&|+-*%<>^")
_OPERAND_WORD = re.compile(rb"(?<![\w$])(?:return|assert|throw|yield|in|case|else)$")
# The byte a closed string leaves as the last one: no continuation.
_STRING_END = 0x22


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


def _operand_expected(raw: bytes, i: int) -> bool:
    """Whether the code before ``i`` leaves an operand to come."""
    j = i
    while j > 0 and raw[j - 1] in b" \t\r\n":
        j -= 1
    if j == 0 or raw[j - 1] in _OPERAND_AFTER:
        return True
    return _OPERAND_WORD.search(raw[max(0, j - 8) : j]) is not None


def _skip_slashy(raw: bytes, i: int) -> int | None:
    """Offset past the slashy or dollar-slashy string at ``i``, or None.

    A slash starts a string only where an operand is expected. After a
    name, a number or a closing bracket it is a division. '//' and '/*'
    are comments, and an unclosed string is not one.
    """
    dollar = raw[i : i + 2] == b"$/"
    if not dollar and raw[i : i + 1] != b"/":
        return None
    if _skip_comment(raw, i) is not None or not _operand_expected(raw, i):
        return None
    j = i + 2 if dollar else i + 1
    n = len(raw)
    while j < n:
        if dollar:
            # $$ and $/ are escapes, /$ closes the string.
            pair = raw[j : j + 2]
            if pair in (b"$$", b"$/"):
                j += 2
                continue
            if pair == b"/$":
                return j + 2
        elif raw[j : j + 1] == b"\\":
            j += 2
            continue
        elif raw[j : j + 1] == b"/":
            return j + 1
        j += 1
    return None


def _skip_literal(raw: bytes, i: int) -> int | None:
    """Offset past the quoted or slashy string at ``i``, or None."""
    if raw[i : i + 1] in _QUOTES:
        return _skip_string(raw, i)
    return _skip_slashy(raw, i)


def _close_of(raw: bytes, i: int, opener: int, closer: int) -> int:
    """Offset just past the bracket that closes the one at ``i``.

    Strings and comments are skipped. Returns len(raw) when nothing
    closes the bracket.
    """
    depth = 0
    n = len(raw)
    while i < n:
        after = _skip_comment(raw, i)
        if after is None:
            after = _skip_literal(raw, i)
        if after is not None:
            i = after
            continue
        c = raw[i]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _byte_len(text: str) -> int:
    """How many source bytes ``text`` was decoded from."""
    return len(text.encode("utf-8", "surrogateescape"))


def _plain(text: str) -> str:
    """``text`` with undecodable bytes replaced, for a symbol name."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def mask_slashy(raw: bytes) -> bytes:
    """``raw`` with each slashy string blanked to a quoted one.

    The tree-sitter grammar cannot read a slashy string and loses the
    commands after it, so the parser is given this copy. The copy has
    the same length, so byte offsets still point into ``raw``.
    """
    masked = bytearray(raw)
    i = 0
    n = len(raw)
    while i < n:
        after = _skip_comment(raw, i)
        if after is not None:
            i = after
        elif raw[i : i + 1] in _QUOTES:
            i = _skip_string(raw, i)
        else:
            after = _skip_slashy(raw, i)
            if after is None:
                i += 1
            else:
                masked[i:after] = b'"' + b" " * (after - i - 2) + b'"'
                i = after
    return bytes(masked)


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
        literal = _skip_literal(raw, i)
        if literal is not None:
            i, last, has_text = literal, _STRING_END, True
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
        if _skip_comment(raw, i) is not None:
            return i, False
        literal = _skip_literal(raw, i)
        if literal is not None:
            i = literal
            continue
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
    return _close_of(raw, brace, 0x7B, 0x7D)


def head_at(raw: bytes, start: int, starts: list[int]) -> dict | None:
    """The definition whose header begins at byte ``start``, or None.

    The result carries the header's line span and byte span. A method
    with no body is kept here and judged by its enclosing type later.
    """
    while start < len(raw) and raw[start] in b" \t":
        start += 1
    end, has_body = _header_end(raw, start)
    # Decoded so that every character maps back to its own bytes.
    text = raw[start:end].decode("utf-8", "surrogateescape")
    head = text.strip()
    if not head:
        return None
    span_end = _body_end(raw, end) if has_body else end
    line_start = bisect.bisect_right(starts, start)
    line_end = bisect.bisect_right(starts, max(span_end - 1, start))
    base = {"start": start, "span_end": span_end, "line_start": line_start, "line_end": line_end}
    found = _TYPE_HEAD.match(head)
    if found:
        bases = _heritage_names(head[found.end() :])
        return {
            **base,
            "kind": "type",
            "tag": found.group("tag"),
            "name": found.group("name"),
            "bases": bases,
        }
    if has_body:
        found = _TASK_HEAD.match(head) or _TASK_REGISTER.match(head)
        if found:
            name = _plain(found.group("name"))
            return {**base, "kind": "task", "tag": "task", "name": name}
    found = _CALLABLE_HEAD.match(head)
    if found is None:
        return None
    # The parameter list opens at the '(' that ends the match. Its close
    # must fall inside the header, and only a throws clause may follow.
    lead = _byte_len(text[: len(text) - len(text.lstrip())])
    open_at = start + lead + _byte_len(head[: found.end() - 1])
    close = _close_of(raw, open_at, 0x28, 0x29)
    tail = raw[close:end].decode("utf-8", "surrogateescape")
    if close > end or _CALLABLE_TAIL.match(tail) is None:
        return None
    prefix = found.group("prefix").split()
    name = found.group("name")
    if name in _NOT_NAMES or prefix[0] in _NOT_PREFIXES:
        return None
    name_start = start + lead + _byte_len(head[: found.start("name")])
    return {
        **base,
        "kind": "callable",
        "tag": "callable",
        "name": _plain(name.strip("\"'")),
        "name_start": name_start,
        "has_body": has_body,
        "abstract": "abstract" in prefix,
    }


def _heritage_names(rest: str) -> list[str]:
    """The base names an ``extends`` or ``implements`` clause names.

    ``rest`` is the header text after the type's name. Type arguments
    are dropped, and a qualified base keeps its last segment.
    """
    text = rest
    while True:
        reduced = _TYPE_ARGUMENTS.sub("", text)
        if reduced == text:
            break
        text = reduced
    names: list[str] = []
    # The text before the first keyword names no base, so it is skipped.
    for clause in _HERITAGE_KEYWORD.split(text)[1:]:
        for item in clause.split(","):
            found = _QUALIFIED_NAME.fullmatch(item)
            if found and found.group("name") not in names:
                names.append(found.group("name"))
    return names


def definitions(raw: bytes, offsets: list[int]) -> dict:
    """Symbols, inheritance records and callable bodies of a file.

    ``offsets`` are the byte offsets where a command starts. ``symbols``
    is what ``symbols`` returns. ``inherits`` holds the base names of
    each kept type, as {"type", "base", "line"} records. ``bodies``
    holds (name_start, start, span_end) for each kept function and
    method with a body. ``callable_names`` holds the names of the kept
    functions and methods.
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
    inherits: list[dict] = []
    bodies: list[tuple[int, int, int]] = []
    callable_names: set[str] = set()
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
        if head["kind"] == "type":
            inherits.extend(
                {"type": head["name"], "base": base, "line": head["line_start"]}
                for base in head["bases"]
            )
        elif kind in ("function", "method"):
            callable_names.add(head["name"])
            if head["has_body"]:
                bodies.append((head["name_start"], head["start"], head["span_end"]))
    return {
        "symbols": result,
        "inherits": inherits,
        "bodies": bodies,
        "callable_names": callable_names,
    }


def symbols(raw: bytes, offsets: list[int]) -> list[dict]:
    """Classes, interfaces, traits, enums, methods, functions and tasks.

    ``offsets`` are the byte offsets where a command starts. A callable
    inside a type is a method, one outside is a function. A method with
    no body is kept only in an interface, a trait or an abstract method.
    """
    return definitions(raw, offsets)["symbols"]


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
    """Module names of the import lines, with their lines.

    A wildcard import keeps its package with a ``.*`` suffix.
    """
    if starts is None:
        starts = line_starts(raw)
    found = []
    for match in _IMPORT.finditer(raw):
        module = match.group("module").decode("utf-8", "replace")
        if match.group("wild"):
            module += ".*"
        found.append({"module": module, "line": bisect.bisect_right(starts, match.start())})
    return found


def package_of(text: str) -> str | None:
    """The name of the package a Groovy file declares, if any."""
    found = _PACKAGE.search(text)
    return found.group("name") if found else None
