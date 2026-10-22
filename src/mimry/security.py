from __future__ import annotations

import bisect
import codecs
import fnmatch
import functools
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple

from .constants import HEAVY_IGNORES, SENSITIVE_PATTERNS, TEXT_EXTS
from .core.languages import EXTENSION_LANGUAGE

HEAVY_IGNORES_CASEFOLD = frozenset(part.casefold() for part in HEAVY_IGNORES)
_HEAVY_IGNORES_SOURCE = HEAVY_IGNORES


def stat_identity(info: os.stat_result) -> tuple[int, ...]:
    """Return a file identity tuple for detecting a swap during a
    verified read.

    On POSIX, st_ctime is the inode change time and is a genuine tamper
    signal. On Windows it is the *creation* time, and os.fstat() reports
    it at a different precision than Path.lstat() for the very same file
    -- observed differing in the sub-microsecond digits. Including it
    there makes every comparison fail, so a descriptor check meant to
    catch tampering instead reports every file as tampered and callers
    fall back to "stale forever".

    dev/inode/size/mtime stay reliable on both platforms and are what
    the comparison actually depends on.
    """
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return identity if os.name == "nt" else (*identity, info.st_ctime_ns)


REDACTED = "[REDACTED]"
SENSITIVE_HOME_DIRS = {
    ".aws",
    ".azure",
    ".cache",
    ".config",
    ".gnupg",
    ".kube",
    ".local/share/keyrings",
    ".password-store",
    ".ssh",
}

# High-confidence standalone credential formats. These intentionally use
# fake-safe structural matching: tests use inert canaries and never real
# credentials.
STANDALONE_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|gh[oprsu]_[A-Za-z0-9]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{20,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|"
    r"sk_live_[0-9A-Za-z]{16,}"
    r")(?![A-Za-z0-9])"
)


class UnsafeRootError(ValueError):
    """A root MIMRY refuses: a file, a drive root or home."""


def safe_root(root: Path):
    r = root.resolve()
    home = Path.home().resolve()
    if r.exists() and not r.is_dir():
        raise UnsafeRootError(f"--root must be a folder, not a file: {r}")
    if r == Path(r.anchor):
        raise UnsafeRootError(f"Refusing to index filesystem root: {r}")
    if r == home:
        raise UnsafeRootError(f"Refusing to index entire home without explicit target scope: {r}")
    for relative in SENSITIVE_HOME_DIRS:
        sensitive = (home / relative).resolve()
        if r == sensitive or sensitive in r.parents:
            raise UnsafeRootError(f"Refusing to index sensitive user-data root: {r}")
    return r


ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", "env.example"}
# Keep label classification separate from assignment syntax. Substring
# matching (for example, ``AUTH`` in ``AUTHOR``) overblocks ordinary
# metadata and prose. Token/camel-case matching catches credential-like
# configuration labels while requiring an explicit assignment boundary
# before any value is classified.
SENSITIVE_LABEL_TOKENS = {
    "api_key",
    "apikey",
    "aws_access_key_id",
    "aws_secret_access_key",
    "auth",
    "authentication",
    "authorization",
    "client_secret",
    "clientsecret",
    "confidential",
    "credential",
    "credentials",
    "passphrase",
    "passwd",
    "password",
    "private_key",
    "privatekey",
    "secret",
    "secrets",
    "token",
}
# A comparison is judged by its literal, not as an assignment. Both
# senses of equality and inequality count (#500).
COMPARISON_OPERATORS = frozenset({"==", "===", "!=", "!=="})
# Shortest literal a comparison may hold and still be a credential.
COMPARED_SECRET_MIN_CHARS = 8
# Assignment boundaries are deliberately structural rather than
# line-only. This catches object/JSON/code members after ``{``, `(`, `,`
# or `;` without treating a credential word embedded in ordinary prose
# as an assignment. After whitespace only an equals sign counts, so a
# label mid-sentence is caught but a colon in prose is not. The label
# still starts at a boundary, so a credential word inside another
# identifier (``xpassword=``) is not an assignment.
# Values stop at the matching config delimiter; quoted values may
# contain delimiters, and TOML / Python-style triple-quoted values may
# span lines. An unquoted value also stops at an escape sequence (a
# backslash before n, r, t, a quote) that comes before any quote, and at
# a closing bracket that does not open a group inside the value
# (``list[str]``). After a quote the value runs as before, so an escape
# inside a string literal stays in it. A bare backslash is part of the
# value, so ``ab\cdef`` is one secret.
ASSIGNMENT_RE = re.compile(
    # A spaced boundary is only the last blank before the label: any
    # earlier blank of the run would rescan the rest of it, which made
    # long runs of spaces quadratic.
    r"(?ims)(?P<boundary>^|(?<=[{(,;\[])|(?P<spaced>(?<=[ \t])(?![ \t])))"
    # A PHP or Perl variable keeps its sigil in front of the label.
    # A quoted shell variable (``"$password"``) has its quote in front
    # of the ``$``.
    r"(?P<prefix>[ \t]*(?:export\s+)?(?:(?:const|let|var)\s+)?(?P<label_quote>['\"]?)\$?"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)(?:\])?"
    # A comparison is an operator of its own, so the first ``=`` of
    # ``==`` never reads as an assignment (#473). ``!=`` and ``!==``
    # are comparisons too (#500).
    # ``:=`` (Go, Python's walrus) is one operator too, so its ``=``
    # is not read as the value. A hash arrow (``'key' => "..."``) reads
    # as ``=`` with a value that starts at ``>``; see HASH_ENTRY_RE.
    # ``+=`` is an assignment. A subscript such as ``cfg["password"]``
    # has its ``]`` after the label, and a ``[`` opens it (#494).
    r"[ \t]*(?P<operator>===|==|!==|!=|\+=|(?(spaced)=|(?::=|[=:])))[ \t]*"
    # A value may start on the next line when that line is an indented
    # scalar (``password:\n  hunter2``); a nested ``key: value`` is its
    # own assignment.
    r"(?:\r?\n(?=[ \t]+(?:[^\s#:\"'][^:\r\n]*(?:\r?\n|$)|"
    r"\"(?:\\.|[^\"\\\r\n])*\"(?![ \t]*:)|'(?:\\.|[^'\\\r\n])*'(?![ \t]*:)))[ \t]+)?)"
    # A string with a prefix (``f"..."``, ``rb'...'``) or a template
    # literal is one value, so its placeholders stay inside it (#494).
    r"(?P<value>(?:[rR]?[fF])?(?:\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\\r\n])*\"|"
    r"'(?:\\.|[^'\\\r\n])*')|`(?:\\.|[^`\\\r\n])*`|"
    r"(?:\[[^\[\]\r\n]*\]|\\(?![nrt\"'])|[^,{;})\]\\\r\n'\"])*"
    r"(?:(?=[\"'])[^,{;})\r\n]*)?)"
    r"(?P<newline>\r?\n|$)?"
)
# A literal on the left of a comparison, as in ``if "..." == password``
# or ``[ "..." != "$password" ]`` (#500).
LITERAL_COMPARED_RE = re.compile(
    r"(?P<literal>\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*')"
    r"[ \t]*(?P<operator>===|==|!==|!=)[ \t]*"
    r"(?P<label_quote>['\"]?)\$?(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)"
)
# A sensitive label on the left of a comparison with a string literal,
# as in ``ok = token == "..."`` or ``foo(token != "...")``.
# ASSIGNMENT_RE reads the rest of such a line as one value, so a
# comparison inside it is not seen there (#494).
LABEL_COMPARED_RE = re.compile(
    r"(?<![\w$.-])(?P<label_quote>['\"]?)\$?(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)"
    r"[ \t]*(?P<operator>===|==|!==|!=)[ \t]*"
    r"(?P<literal>\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*')"
)
# A tuple whose first element is a credential name and whose second is a
# string literal, as in ``pair = (token, "...")``. Only a group counts,
# not a call's arguments; see _tuple_is_sensitive (#494).
TUPLE_LABELED_RE = re.compile(
    r"\((?P<label>[A-Za-z_][A-Za-z0-9_.-]*)[ \t]*,[ \t]*"
    r"(?P<literal>\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*')"
)
# A tuple or list assignment at the start of a statement, as in
# ``user, password = "admin", "..."``, ``(user, password) = (...)``,
# ``[user, password] = [...]``, or a one-element tuple such as
# ``password, = ...``. A target is a name, an attribute or a
# string subscript (``creds["password"]``). Each target takes the value
# at its own position (see _tuple_slots), so ASSIGNMENT_RE never reads
# the label as the owner of the value after it (#519).
_TUPLE_TARGET = r"[A-Za-z_][A-Za-z0-9_.]*(?:\[[ \t]*['\"][A-Za-z_][A-Za-z0-9_.]*['\"][ \t]*\])?"
_TUPLE_ASSIGNMENT_RE = re.compile(
    # A statement starts at a line start, after ``;``, or after a run of
    # blanks that follows a word, as in ``a.py user, password = ...``.
    # A blank run after a comma or a bracket is not a start, so a long
    # list of names is not tried from every element.
    r"(?m)(?:^[ \t]*|(?<=;)[ \t]*|(?<=[\w)\]'\"`])[ \t]+)"
    r"(?:(?:const|let|var)[ \t]+)?(?:[(\[][ \t]*)?"
    rf"(?P<targets>{_TUPLE_TARGET}(?:[ \t]*,[ \t]*{_TUPLE_TARGET})+|{_TUPLE_TARGET}[ \t]*,)"
    r"(?:[ \t]*,)?[ \t]*(?:[)\]][ \t]*)?:?=(?![=>])"
)
_TUPLE_TARGET_ITEM_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.]*)"
    r"(?:\[[ \t]*['\"](?P<key>[A-Za-z_][A-Za-z0-9_.]*)['\"][ \t]*\])?"
)
# A target that a value chains onto, as ``password = `` in
# ``a = password = "..."``. Each target takes the value after the last
# ``=``. A comparison (``==``, ``!=``, ``=>``) is not a target (#540).
_CHAINED_TARGET_RE = re.compile(
    r"\$?(?P<name>[A-Za-z_][A-Za-z0-9_.]*)"
    r"(?:\[[ \t]*['\"](?P<key>[A-Za-z_][A-Za-z0-9_.]*)['\"][ \t]*\])?"
    r"[ \t]*=(?![=>])[ \t]*"
)
# A value that is a keyword argument, as in
# ``f(a, password="...", b="...")``, is not a tuple value.
_KEYWORD_VALUE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*[ \t]*=(?![=>])")
# What ends a value or opens a nested one: quotes, brackets, a comma, a
# statement end, a line break, or a comment.
_TUPLE_BREAK_RE = re.compile(r"[\"'`()\[\]{},;#\r\n]|(?<=\s)//")
_TUPLE_TAIL_RE = re.compile(r"[ \t]*(?:\r?\n|;|#|//|\Z)")
# How far the values of one tuple assignment may run. A value that does
# not close within it is not read as a tuple, and is judged as before.
_TUPLE_VALUES_LIMIT = 4096
# An Authorization header written into a string literal, as in
# ``"Authorization: Bearer <token>"``, or written out in Markdown or
# prose, as in ``- Authorization: Bearer <token>`` (#586). The scheme
# stays and the value is judged as a credential. A placeholder, a
# reference or a template (``<token>``, ``$TOKEN``, ``{token}``) is kept
# (#519). See _auth_value_is_credential for the scheme and the value.
_AUTH_HEADER_HINT_RE = re.compile(r"(?i)authorization")
# The joined pieces of a value are separated by a NUL, which a header's
# blanks and value may run across, but which is not a letter, so it
# starts a header as the start of a line does. A value stops before the
# next header, so a header that follows one is not read as its value.
# A header may also follow an escaped line break, as written in source
# (backslash r or n), so it is read as starting a line too (#573).
_LITERAL_SEPARATOR = "\0"
# The Authorization schemes read as a scheme (#586), matched without
# case. Any other first word after the colon is the value, or a custom
# scheme when a token-shaped word follows it (#540). Keep this to real
# schemes: a word listed here is never read as a value.
_KNOWN_AUTH_SCHEMES = frozenset(
    {
        "AWS4-HMAC-SHA256",
        "ApiKey",
        "Basic",
        "Bearer",
        "Digest",
        "HOBA",
        "Key",
        "Mutual",
        "NTLM",
        "Negotiate",
        "OAuth",
        "SSO-Key",
        "Token",
    }
)
_KNOWN_AUTH_SCHEME_RE = "|".join(
    re.escape(scheme) for scheme in sorted(_KNOWN_AUTH_SCHEMES, key=len, reverse=True)
)
_AUTH_HEADER_START = r"(?:Proxy-)?Authorization[ \t\x00]*:"
# A header's value is one word. It stops at a blank, a line break, a NUL
# (the join between string literals), a quote or bracket, or a vertical
# bar (#586). It does not start with a known scheme word, since a scheme
# with nothing after it is no header, nor with ``=``, as in
# ``authorization: Token = None``.
_AUTH_VALUE = (
    rf"(?!{_AUTH_HEADER_START})(?!(?:{_KNOWN_AUTH_SCHEME_RE})(?![A-Za-z0-9-]))"
    r"[^\s\x00\"'`{}<>,;()\[\]\\$|=]"
    rf"(?:(?!{_AUTH_HEADER_START})[^\s\"'`{{}}<>,;()\[\]\\$|])*"
)
_AUTH_HEADER_VALUE_RE = re.compile(
    r"(?i)(?:(?<![A-Za-z0-9-])|(?<=\\(?-i:[rn])))(?:Proxy-)?Authorization[ \t\x00]*:[ \t\x00]*"
    rf"(?:(?P<scheme>{_KNOWN_AUTH_SCHEME_RE})(?![A-Za-z0-9-])(?=[ \t\x00]*[ \t])[ \t\x00]+"
    # Any other word is a custom scheme only before a token-shaped value
    # (#540).
    r"|(?P<custom>[A-Za-z][A-Za-z0-9-]*)(?=[ \t\x00]*[ \t])[ \t\x00]+"
    r"(?=[^\s\x00\"'`{}<>,;()\[\]\\$|]*[0-9_.~+/=-]))?"
    rf"(?P<value>{_AUTH_VALUE})"
)
# Outside string literals (#586), a header is read only when a known
# scheme follows its colon, so ``authorization: str`` in code is not
# one. It may follow a line start, a blank, a list marker, a quote or
# a sentence word, and the emphasis may open before it or close right
# after its colon, as in ``**Authorization:** Bearer <token>``.
# Any other word after the colon is a scheme only before a value that is
# token-shaped (a digit or one of ``-_.~+/=:``) and at least 12
# characters long, as in ``Authorization: HMAC-SHA256 tomato0123456789``
# (#593).
# Prose such as ``Authorization: Custom header values`` is not one. The
# scheme starts with a capital, as schemes are written, and a path or a
# file name (``see docs/auth.md``) is not a value.
_TOKEN_SHAPED_VALUE_AHEAD = (
    r"(?=[^\s\"'`{}<>,;()\[\]\\$|]{12})"
    r"(?=[^\s\"'`{}<>,;()\[\]\\$|]*[0-9_.~+/=:-])"
    r"(?![^\s\"'`{}<>,;()\[\]\\$|]*\.[A-Za-z]{1,5}(?![^\s\"'`{}<>,;()\[\]\\$|]))"
)
# The characters a colon-joined value is made of: each side of a colon
# must be one of these (#593).
_TOKEN_PART_RE = re.compile(r"[A-Za-z0-9_.~+/=-]+")
_PLAIN_AUTH_HEADER_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9-])(?<![A-Za-z0-9]_)(?:Proxy-)?Authorization[ \t]*:"
    r"(?:\*\*|__|[*_])?[ \t]*"
    rf"(?:(?P<scheme>{_KNOWN_AUTH_SCHEME_RE})(?![A-Za-z0-9-])[ \t]+"
    rf"|(?P<custom>(?-i:[A-Z])[A-Za-z0-9-]*)[ \t]+{_TOKEN_SHAPED_VALUE_AHEAD})"
    rf"(?P<value>{_AUTH_VALUE})"
)
# A Markdown table cell that holds the header, as in
# ``| Authorization | Bearer <token> |`` (#592). The bars and blanks
# around the name are part of the cell. The value is read as in
# _PLAIN_AUTH_HEADER_RE, and the bar after it ends it.
_PLAIN_AUTH_TABLE_RE = re.compile(
    r"(?i)\|[ \t]*(?:Proxy-)?Authorization[ \t]*\|[ \t]*"
    rf"(?P<scheme>{_KNOWN_AUTH_SCHEME_RE})(?![A-Za-z0-9-])[ \t]+"
    rf"(?P<value>{_AUTH_VALUE})"
)
# An Authorization label before a colon, as a line start reads it
# (#592), and the value after it: a known scheme and a value, or a
# bare value.
_AUTH_LABEL_RE = re.compile(r"(?i)(?:Proxy-)?Authorization")
_AUTH_LABEL_VALUE_RE = re.compile(
    rf"(?i)(?:(?P<scheme>{_KNOWN_AUTH_SCHEME_RE})(?![A-Za-z0-9-])[ \t]+)?(?P<value>{_AUTH_VALUE})"
)
# A known scheme word anywhere in the text: the gate for the literal
# pass that reads a scheme and a value alone in a string (#593).
_AUTH_SCHEME_WORD_RE = re.compile(
    rf"(?i)(?<![A-Za-z0-9-])(?:{_KNOWN_AUTH_SCHEME_RE})(?![A-Za-z0-9-])"
)
# A string literal whose whole body is a known scheme and one value, as
# in ``"Bearer tomato-canary-1"`` in a call argument or an assignment.
# The value is judged as an Authorization value of that scheme (#593).
_SCHEME_LITERAL_MIN_LENGTH = 12
_SCHEME_LITERAL_RE = re.compile(
    rf"(?i)(?P<scheme>{_KNOWN_AUTH_SCHEME_RE})(?![A-Za-z0-9-])"
    rf"[ \t\x00]+(?P<value>{_AUTH_VALUE})"
)
# The schemes whose value is a credential unless it is a prose word
# (#540, #573). After one of them, a word of letters alone that more
# text follows is a credential only with at least 8 letters (#586).
_BEARER_LIKE_AUTH_SCHEMES = frozenset({"basic", "bearer", "token"})
# Prose after an Authorization header, never its value (#561, #573): a
# word here is not a credential, even as a Bearer value or the last word
# of its header.
_AUTH_PROSE_WORDS = frozenset(
    """absent admin allowed anonymous approved auth authentication auto basic
    bearer cleared client configured credentials default denied disabled
    empty enabled error expected expired fail failed false forbidden granted
    guest header here internal invalid later local missing needed no none
    not nothing null ok optional pass pending present provided public
    rejected removed required scheme see sent server set skipped stored
    success supplied system tbd todo token true unauthorized unavailable
    unknown unset user value via yes""".split()
)
# What may follow the last word of an Authorization value (#561, #573):
# blanks, then the end of the joined literal, a comma, a semicolon, a
# vertical bar (#586), or a line break, real or escaped as in source.
# Other text there means the value is not the last word of its header.
_AUTH_VALUE_TAIL_RE = re.compile(r"[ \t\x00]*(?:\Z|[,;|\r\n]|\\[rn])")
# A parameter name and its ``=``, as in ``OAuth realm="Photos"``.
# After a scheme other than Basic, Bearer or Token it is prose, not a
# value (#586).
_AUTH_PARAMETER_RE = re.compile(r"[A-Za-z][A-Za-z_-]*=")
# Sentence punctuation that ends a sentence, not a header value. In
# plain text it is trimmed from a value before the value is judged, and
# the span redacted excludes it (#592).
_SENTENCE_PUNCTUATION = ".:!?"
# A placeholder name in an Authorization value (#592), never a
# credential: an upper snake name such as ``YOUR_TOKEN`` or ``API_KEY``,
# or a single upper word such as ``TOKEN``.
_AUTH_PLACEHOLDER_NAME_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[A-Z]+")
# A name a doc writes for its own value, as in ``your-token-here`` or
# ``my_key`` (#592). A digit makes it a token, as in ``my-key-7f3a``.
_AUTH_PLACEHOLDER_PREFIX_RE = re.compile(r"(?i)(?:your|my)[-_]")
# Stand-in words a doc or a test writes in place of a value (#592).
# ``credentials`` is already in _AUTH_PROSE_WORDS.
_AUTH_PLACEHOLDER_WORDS = frozenset(
    """abc xyz foo bar baz example sample dummy placeholder redacted
    changeme secret password mytoken""".split()
)
# What joins two string literals of one value: blanks, line breaks or a
# backslash-newline (implicit concatenation, as ``"a" "b"``, or a value
# split over lines), or a ``+``. A line break joins pieces across lines
# as the parenthesised case does (#559).
_LITERAL_JOIN_RE = re.compile(r"(?:[ \t]|\\?\r?\n)*(?:\+(?:[ \t]|\\?\r?\n)*)?")
# A backslash-newline and the blanks that start the next line (#559).
_LINE_CONTINUATION_RE = re.compile(r"\\\r?\n[ \t]*")
# Text that is not a statement of its own: a comment, or a triple-quoted
# string such as a docstring or YAML inside a Python string.
# ASSIGNMENT_RE reads through a triple-quoted string as one value, and
# a comment's text is read as code, so each is judged as text of its
# own (#494).
SUB_TEXT_RE = re.compile(
    r"(?m)(?:^|(?<=[ \t]))(?:#|//)[^\r\n]*"
    r"|(?P<quote>\"\"\"|''')[\s\S]*?(?P=quote)"
)
# How deep comments and strings are judged within each other. A comment
# is judged, and a comment inside it, and no deeper: a line of many
# ``//`` markers would otherwise recurse once per marker (#494).
_SUB_TEXT_DEPTH = 2
# A password in the userinfo of a URL, as in ``https://user:...@host``.
# The head keeps the user name, so only the password is replaced.
# The lookbehind starts a match at a word's first letter, so a long run
# of letters is scanned once, not once per letter (#494).
_URL_PASSWORD_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])(?P<head>[A-Za-z][A-Za-z0-9+.-]*://[^\s/?#@:]*:)"
    r"(?P<password>[^\s/?#@]+)(?=@)"
)
# Any URL in a text, from its scheme to the next space or quote.
_URL_SPAN_RE = re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'`<>]*")
# A word that a sensitive label may contain. A comment or string without
# one has no label to judge, so it is not read again.
_SENSITIVE_HINT_RE = re.compile(r"(?i)pass|token|secret|credential|auth|api|key|private|confid")
# A query or fragment parameter: ``?password=...``, ``&token=...``.
_URL_PARAM_RE = re.compile(
    r"(?P<lead>[?#&;])(?P<name>[^=?#&;\s]*)=(?P<value>[^&#;\s\"'`<>)\],}]*)"
)
# A placeholder in a formatted string or template literal, as in ``{p}``
# or ``${p}``. re.split keeps it, so odd parts are placeholders.
_FORMAT_PLACEHOLDER_RE = re.compile(r"(\$?\{[^{}\r\n]*\})")
# The literal text between placeholders that may hold a value: no space
# and no code punctuation.
_FORMATTED_PIECE_RE = re.compile(r"[^\s()\[\]{}<>;,'\"`]+")
# A string with a prefix or a template literal, as one whole literal.
_FORMATTED_LITERAL_RE = re.compile(
    r"(?P<prefix>[rRbBuUfF]{0,2})(?P<quote>[\"'`])(?P<body>[\s\S]*)(?P=quote)"
)
# A PHP or Ruby hash entry with a fat arrow, ``'password' => "..."`` or
# ``:password => "..."``, with its value on the same line. ASSIGNMENT_RE
# reads a bracketed value whole, so the entry inside ``$a = ['password'
# => "..."]`` is never seen there; this pattern finds it (#500).
HASH_ENTRY_RE = re.compile(
    r"(?m)(?P<prefix>(?:^|(?<=[{(\[,;]))[ \t]*:?(?P<label_quote>['\"]?)"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)[ \t]*=>[ \t]*)"
    r"(?P<value>\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*')"
)
MULTILINE_QUOTED_ASSIGNMENT_START_RE = re.compile(
    r"(?im)(?:^|(?<=[{(,;]))[ \t]*(?:export\s+)?(?:(?:const|let|var)\s+)?(?P<label_quote>['\"]?)"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)\s*(?:=|:)\s*(?P<quote>\"\"\"|''')"
)
UNCLOSED_MULTILINE_QUOTED_ASSIGNMENT_RE = re.compile(
    r"(?ims)(?P<boundary>^|(?<=[{(,;]))"
    r"(?P<prefix>[ \t]*(?:export\s+)?(?:(?:const|let|var)\s+)?(?P<label_quote>['\"]?)"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)\s*(?:=|:)\s*)"
    r"(?P<quote>\"\"\"|''')(?P<body>.*)$"
)
# A quoted value whose quote does not close on its line, as a Ruby,
# PHP or Rust string may run on to a later line. Triple quotes and
# empty strings are left to the patterns above.
UNCLOSED_QUOTED_ASSIGNMENT_START_RE = re.compile(
    # Only the last blank before the label is a boundary, as in
    # ASSIGNMENT_RE.
    r"(?im)(?:^|(?<=[{(\[,;])|(?<=[ \t])(?![ \t]))"
    r"[ \t]*(?:export\s+)?(?:(?:const|let|var)\s+)?\$?"
    r"(?P<label_quote>['\"]?)(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)"
    # ``=>`` is the key arrow of PHP and Ruby: ``'password' => "...``.
    r"[ \t]*(?:===|==|=>|[=:])[ \t]*(?P<quote>[\"'])(?![\"'])"
)
# How far a value may run on to find its closing quote. It stays within
# the overlap of a streamed scan, so a value is judged whole.
UNCLOSED_QUOTED_SPAN_LIMIT = 4096
YAML_BLOCK_ASSIGNMENT_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)"
    r"(?P<header>\s*:\s*[|>][+-]?[^\r\n]*\r?\n)"
    r"(?P<body>(?:(?P=indent)[ \t]+[^\r\n]*(?:\r?\n|$)|[ \t]*\r?\n)*)"
)
YAML_QUOTED_MULTILINE_ASSIGNMENT_START_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)"
    r"(?P<header>[ \t]*:[ \t]*)(?P<quote>['\"])"
)
# A private key block runs from its BEGIN line to the END line with the
# same label; see _private_key_block_spans.
_PRIVATE_KEY_BEGIN_RE = re.compile(
    r"-----BEGIN (?P<label>(?:RSA |EC |OPENSSH |DSA |ENCRYPTED |)PRIVATE KEY)-----",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=16)
def _private_key_end_re(label: str) -> re.Pattern[str]:
    return re.compile(rf"-----END {re.escape(label)}-----", re.IGNORECASE)


def _private_key_block_spans(text: str) -> list[tuple[int, int]]:
    """The spans of the private key blocks in text.

    A BEGIN line with no END line of its label after it is skipped, and
    so is every later BEGIN line of that label, since no END line
    follows those either. A search from each BEGIN line rescanned the
    rest of the text each time, which was quadratic on many BEGIN lines.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    without_end: set[str] = set()
    # BEGIN lines may overlap in their dashes, so each search starts one
    # character after the last BEGIN line found, not after its end.
    scan = 0
    while (begin := _PRIVATE_KEY_BEGIN_RE.search(text, scan)) is not None:
        scan = begin.start() + 1
        label = begin.group("label")
        if begin.start() < pos or label.casefold() in without_end:
            continue
        end = _private_key_end_re(label).search(text, begin.end())
        if end is None:
            without_end.add(label.casefold())
            continue
        spans.append((begin.start(), end.end()))
        pos = end.end()
    return spans


SENSITIVE_VALUE_RE = re.compile(
    r"(?is)(\"type\"\s*:\s*\"service_account\"|"
    r"//firebase\.google\.com/docs/|"
    r"client-key-data\s*:|client-certificate-data\s*:|"
    # Any PRIVATE KEY header, even without its END line.
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----)"
)
SPACED_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<label>\b(?:api[ \t_-]+key|client[ \t_-]+secret|private[ \t_-]+key|"
    r"access[ \t_-]+token|auth(?:entication|orization)?[ \t_-]+token|"
    r"service[ \t_-]+credentials?))(?P<separator>[ \t]*(?:===|==|[:=])[ \t]*)"
    # A bracketed value is captured whole, so ``<your key here>`` is
    # judged as one placeholder, not as its first word. A template
    # placeholder such as ``{value}`` is captured whole too, and is not
    # a secret (see _TEMPLATE_VALUE_RE). An unquoted value stops at an
    # escape sequence or a closing bracket (see ASSIGNMENT_RE).
    r"(?P<value>\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*'|"
    r"<[^<>\r\n]*>(?=[\s,;})]|\Z)|\$?\{[A-Za-z_][A-Za-z0-9_.]*\}|"
    r"(?=[^\s,;})])(?:\[[^\[\]\s]*\]|\\(?![nrt\"'])|[^\s,;}\)\]\\\"'])*"
    r"(?:(?=[\"'])[^\s,;})]*)?)"
)

# Value shapes that are never a literal credential. Without them, any
# value under a credential-like name was a secret, and that dropped
# whole source files from the index over lines like ``"SECRET_KEY":
# None`` or ``max_tokens: 4096``.
_NON_CREDENTIAL_WORDS = frozenset(
    {
        "none",
        "null",
        "nil",
        "undefined",
        "true",
        "false",
        "yes",
        "no",
        "on",
        "off",
        "~",
        "read",
        "write",
    }
)
# Authorization schemes, as in ``"Bearer " + token``: the credential is
# what follows.
_AUTH_SCHEMES = frozenset({"basic", "bearer", "digest", "token"})
# A location, not a credential: a URL whose authority has no
# ``user:pass@`` (a URL with userinfo is still judged) and whose query
# or fragment carries no credential-named parameter.
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://(?![^\s/?#]*@)\S*")
_USERINFO_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/?#]*@")
_URL_IN_TEXT_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'`<>]*")
# Parameter names that carry a credential, compared without separators
# (``api_key`` is ``apikey``) and by their last segment (``x_api_key``).
_CREDENTIAL_PARAM_NAMES = frozenset(
    {
        "key",
        "apikey",
        "token",
        "accesstoken",
        "auth",
        "secret",
        "clientsecret",
        "password",
        "pass",
        "pwd",
        "sig",
        "signature",
        "credential",
    }
)
# The last ``_``, ``-`` or ``.`` segment of a label that names metadata
# about a credential, not the credential: ``TOKEN_URL``,
# ``password_hint``.
_NON_CREDENTIAL_LABEL_SEGMENTS = frozenset(
    {
        "url",
        "uri",
        "endpoint",
        "path",
        "policy",
        "hint",
        "file",
        "dir",
        "name",
        "header",
        "field",
        "type",
        "length",
        "min",
        "max",
        "regex",
        "pattern",
        "label",
        "prompt",
        "placeholder",
        "env",
        "var",
    }
)
_NUMBER_RE = re.compile(r"[-+]?(?:\d[\d_]*(?:\.\d*)?|\.\d+)")
# A whole value resolved elsewhere at run time: ``$VAR``, ``${VAR}``,
# ``${{ secrets.TOKEN }}``. Environment variables are upper case by
# convention, so ``$ecretPass1`` is a literal. It ends the line or comes
# before a comment, which follows whitespace (``$PG#x`` is one literal);
# a braced one may also end an entry of a flow collection.
_REFERENCE_RE = re.compile(
    r"(?m)(?:\$[A-Z_][A-Z0-9_]*|"
    r"(?P<braced>\$\{[A-Za-z_][A-Za-z0-9_.]*\}|\$\{\{[^{}\r\n]*\}\}))"
    r"(?=[ \t]*(?:\r|$)|[ \t]+#|(?(braced)[ \t]*[,}\]](?=\s|[,}\]]|$)|(?!)))"
)
# A template placeholder, as in ``f"api_key = {value}"`` or
# ``${value}``. It is filled in at run time, so it is not a literal
# credential. As a whole value it must end where a value ends (see
# _VALUE_END_RE).
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\$?\{[A-Za-z_][A-Za-z0-9_.]*\}")
_TEMPLATE_VALUE_RE = re.compile(
    _TEMPLATE_PLACEHOLDER_RE.pattern + r"(?=[ \t]*(?:[,;)\]}\"'\\\r\n]|\Z))"
)
# The trailing escape of a quoted value, as in ``"secret\n"``: a line
# break in the source, not part of the secret.
_TRAILING_ESCAPE_RE = re.compile(r"(?:\\[nrt])+\Z")
# Masks and placeholders: ``"********"``, ``"..."``, ``"<api-key>"``.
_PLACEHOLDER_RE = re.compile(r"[*.xX]{3,}|<[^<>\r\n]+>")
# An unquoted angle-bracket placeholder with a space in it, as in
# ``<your key here>``.
_SPACED_PLACEHOLDER_RE = re.compile(r"<[^<>]*\s[^<>]*>")
# An environment lookup, up to the name it looks up.
_ENV_LOOKUP_RE = re.compile(
    r"(?:process\.env|import\.meta\.env)\.[A-Za-z_]\w*"
    r"|(?:os\.getenv\(|os\.environ\.get\(|Deno\.env\.get\(|os\.environ\[)\s*(?P<name>\"\w+\"|'\w+')"
)
# Where a value ends outside parsed source: a separator, the line end,
# or a comment, which follows whitespace.
_VALUE_END_RE = re.compile(r"[ \t]*(?:[,;)\]}\r\n`]|\\(?=[nrt\"'])|\Z|(?<=[ \t])(?:#|//))")
# Prose after a closing quote: a blank, then a word that is not a
# keyword which continues an expression, as in ``"x" in config.``.
# ``"x" or "y"`` and ``"x" if c else "y"`` still continue, and their
# values are scanned.
_PROSE_AFTER_QUOTE_RE = re.compile(r"[ \t]+(?!(?:or|and|if|else)\b)[A-Za-z_]")
_CLOSING_BACKTICK_RE = re.compile(r"[ \t]*`")
_LINE_REST_RE = re.compile(r"[^\r\n]*")
_LINE_BREAK_RE = re.compile(r"[ \t]*\r?\n")
# An operator that binds tighter than a comparison, so the literal
# before it is only part of a larger operand (``== "a" + "b"``).
_OPERAND_CONTINUES_RE = re.compile(r"[ \t]*[-+*/%.\[(&|^<>=!~@]")
_STRING_LITERAL_RE = re.compile(
    r"(?P<prefix>[rRbBuUfF]{0,2})(?P<literal>\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|"
    r"\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*'|`(?:\\.|[^`\\])*`)"
)
# ``token: str = "..."``: an annotation, then the value.
_ANNOTATION_RE = re.compile(r"[^=\"'`\r\n]*?(?<![=!<>])=(?![=>])")
# A type has no words with only a space between them: ``OAuth
# realm="..."`` is prose, as an HTTP header written out in a docstring.
_SPACED_WORDS_RE = re.compile(r"\w[ \t]+\w")
_VALUE_RE = re.compile(
    r"\s*(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*'|[^,{;})\r\n]*)"
)
# ``'username' | 'password'``: a string literal joined to another by a
# single ``|`` is a member of a TypeScript literal-union type.
_UNION_NEXT_RE = re.compile(r"\s*\|(?!\|)\s*[\"'`]")
_UNION_PREVIOUS_RE = re.compile(r"[\"'`]\s*(?<!\|)\|\s*\Z")
# ``typeof secret === 'string'``: the literal names a type, it is not a
# value. Only a typeof result, after a typeof comparison, is exempt.
_TYPEOF_RESULTS = frozenset(
    {"bigint", "boolean", "function", "number", "object", "string", "symbol", "undefined"}
)
_TYPEOF_COMPARISON_RE = re.compile(r"\btypeof\s+[\w$.]+\s*[!=]==?\s*\Z")
# A relative path, as in a package.json ``exports`` list. It is judged
# as a file only as an array member; under a label it stays a value.
_RELATIVE_PATH_RE = re.compile(r"(?:\.{1,2}|~)/\S*")
# What is left of an array's shape around its members, and the bare
# members that are never credentials.
_ARRAY_PUNCTUATION_RE = re.compile(r"[\[\]{}(),:\s]+")
_ARRAY_CONSTANTS = frozenset({"true", "false", "null", "none", "nil"})
_FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z_]*")
# The rest is only for parsed source. Quotes, brackets and value ends,
# plus comments, which start after whitespace (``x  # note``, ``x  //
# note``).
_EXPRESSION_TOKEN_RE = re.compile(r"[\"'`()\[\]{},;\r\n]|(?<=\s)(?:#|//|/\*)")
_STRING_PREFIX_CHARS = frozenset("rRbBuUfF")
# Where a line break does not end an expression; see _line_goes_on.
_TRAILING_OPERATOR_RE = re.compile(r"(?:\+|\|\||&&|\?\?|=>)\Z")
# ``cond ?`` and PHP's ``'a' .``, but not a docstring's ``token?`` or
# ``the token.``
_TRAILING_SOURCE_OPERATOR_RE = re.compile(
    r"(?:(?<=[\w)\]] )\?|(?<=[\"'`])[ \t]?\.|(?P<colon>:))\Z"
)
_LEADING_OPERATOR_RE = re.compile(r"\n?[ \t]*(?:[+.?]|\|\||&&|(?P<colon>:))")
# Shell joins adjacent words: ``"Bearer "abc`` is one value.
_GLUED_WORD_RE = re.compile(r"\w")
# A Rust lifetime, as in ``&'a str``, is not a character literal.
_LIFETIME_RE = re.compile(r"'[A-Za-z_]\w*(?![\w'])")
# ``x or ("a", "b")`` opens a tuple, where ``f("a")`` opens a call.
_KEYWORDS_BEFORE_GROUPS = frozenset(
    {"and", "await", "else", "in", "is", "not", "or", "return", "yield"}
)

STREAM_CHUNK_BYTES = 64 * 1024
STREAM_OVERLAP_CHARS = 8 * 1024


def _is_env_example(path: Path) -> bool:
    return path.name in ENV_EXAMPLE_NAMES


# One regex over every sensitive filename pattern, with
# fnmatch.fnmatch's exact platform case rule (os.path.normcase on both
# sides). Runs once per scanned path.
_SENSITIVE_NAME_RE = re.compile(
    "|".join(fnmatch.translate(os.path.normcase(pat)) for pat in SENSITIVE_PATTERNS)
)


def is_sensitive(path):
    if _is_env_example(path):
        return False
    name = path.name
    if _SENSITIVE_NAME_RE.match(os.path.normcase(name)):
        return True
    suffix = path.suffix.lower()
    if name in {"credentials", "config"} or suffix == ".json":
        parts = {part.lower() for part in path.parts}
        if (
            (".aws" in parts and name in {"credentials", "config"})
            or (".kube" in parts and name == "config")
            or ("firebase" in parts and suffix == ".json")
        ):
            return True
    return contains_sensitive_text(name)


def contains_sensitive_text(text: str, *, code: bool = False) -> bool:
    """Whether text holds a credential.

    ``code`` marks parsed source (see is_code_path).
    """
    return _contains_sensitive_text(text, code=code, depth=0)


def _contains_sensitive_text(text: str, *, code: bool, depth: int) -> bool:
    """See contains_sensitive_text. ``depth`` counts the comments and
    strings the text is judged within, bounded by _SUB_TEXT_DEPTH.
    """
    if not text:
        return False
    if (
        _private_key_block_spans(text)
        or STANDALONE_SECRET_RE.search(text)
        or SENSITIVE_VALUE_RE.search(text)
        or _has_url_credential(text)
        or _auth_header_value_spans(text)
    ):
        return True
    # Every remaining detector is an assignment form whose pattern
    # requires a literal "=" or ":", or a tuple, which needs a "(".
    # Most filenames and many short strings have none of them.
    if "=" not in text and ":" not in text and "(" not in text:
        return False
    scanner = _Scanner(text)
    if any(
        _spaced_assignment_value_is_sensitive(match, code=code, scanner=scanner)
        for match in SPACED_SENSITIVE_ASSIGNMENT_RE.finditer(text)
    ):
        return True
    if any(
        _holds_text(match.group("body"))
        and _is_sensitive_label(match.group("label"), match.group("body"))
        for match in YAML_BLOCK_ASSIGNMENT_RE.finditer(text)
    ):
        return True
    if any(
        _is_sensitive_label(label, text[start:end])
        for start, end, label, _replacement in _yaml_quoted_multiline_assignments(text)
    ):
        return True
    if ("\n" in text or "\r" in text) and next(
        _sensitive_unclosed_quoted_values(text), None
    ) is not None:
        return True
    # That pattern requires a triple quote; skipping the scan without
    # one is exact, and the scan is one of the costliest over large
    # sources.
    if ('"""' in text or "'''" in text) and any(
        _is_sensitive_label(match.group("label")) and _holds_text(_triple_quoted_body(text, match))
        for match in MULTILINE_QUOTED_ASSIGNMENT_START_RE.finditer(text)
    ):
        return True
    if depth < _SUB_TEXT_DEPTH and any(
        _sub_text_is_sensitive(match, code=code, depth=depth)
        for match in SUB_TEXT_RE.finditer(text)
    ):
        return True
    if any(
        _literal_comparison_is_sensitive(match, code=code)
        for match in _literal_finditer(
            LITERAL_COMPARED_RE, text, quotes=_DOUBLE_OR_SINGLE_QUOTE_RE
        )
    ):
        return True
    if any(
        _label_comparison_is_sensitive(match, code=code)
        for match in LABEL_COMPARED_RE.finditer(text)
    ):
        return True
    if any(_tuple_is_sensitive(match, code=code) for match in TUPLE_LABELED_RE.finditer(text)):
        return True
    if any(
        _tuple_slot_is_sensitive(slot, code=code, scanner=scanner)
        for slot in scanner.tuple_slots()
    ):
        return True
    if any(
        _hash_entry_is_sensitive(match, code=code, scanner=scanner)
        for match in HASH_ENTRY_RE.finditer(text)
    ):
        return True
    return any(
        _assignment_match_is_sensitive(match, code=code, scanner=scanner)
        for match in ASSIGNMENT_RE.finditer(text)
    )


def redact_standalone_secrets(text: str) -> str:
    """Redact credentials recognizable by their shape alone.

    Token prefixes and private key headers are caught with or without a
    label in front of them. redact_sensitive_text and feedback text both
    run this pass, so the list lives in one place.
    """

    redacted = _splice(
        text, [(start, end, REDACTED) for start, end in _private_key_block_spans(text)]
    )
    redacted = STANDALONE_SECRET_RE.sub(REDACTED, redacted)
    return SENSITIVE_VALUE_RE.sub(REDACTED, redacted)


def redact_sensitive_text(text: str) -> str:
    """Remove high-confidence credentials from user and adapter text."""
    return _redact_text(text, depth=0)


def _redact_text(text: str, *, depth: int) -> str:
    """See redact_sensitive_text. ``depth`` is bounded by
    _SUB_TEXT_DEPTH."""

    # Comments and triple-quoted strings first, each judged as text of
    # its own, so the assignment pass below does not read through them.
    redacted = text
    if depth < _SUB_TEXT_DEPTH:
        redacted = SUB_TEXT_RE.sub(lambda match: _redact_sub_text(match, depth=depth), text)
    # Before the standalone pass, which would redact an AWS key id alone
    # and leave its signature (#593).
    redacted = _redact_auth_headers(redacted)
    redacted = redact_standalone_secrets(redacted)
    redacted = _redact_url_credentials(redacted)
    redacted = _redact_unclosed_quoted_values(redacted)
    redacted = _redact_values(
        redacted, SPACED_SENSITIVE_ASSIGNMENT_RE, _redact_spaced_assignment, prose=depth > 0
    )
    redacted = YAML_BLOCK_ASSIGNMENT_RE.sub(_redact_yaml_block, redacted)
    redacted = _redact_yaml_quoted_multiline(redacted)
    redacted = _redact_hash_entries(redacted)
    redacted = _redact_values(redacted, ASSIGNMENT_RE, _redact_assignment, prose=depth > 0)
    redacted = _splice(
        redacted,
        [
            (match.start(), match.end(), _redact_literal_comparison(match))
            for match in _literal_finditer(
                LITERAL_COMPARED_RE, redacted, quotes=_DOUBLE_OR_SINGLE_QUOTE_RE
            )
        ],
    )
    redacted = LABEL_COMPARED_RE.sub(_redact_label_comparison, redacted)
    redacted = TUPLE_LABELED_RE.sub(_redact_tuple, redacted)
    redacted = _redact_tuple_slots(redacted)
    redacted = UNCLOSED_MULTILINE_QUOTED_ASSIGNMENT_RE.sub(
        _redact_unclosed_multiline_assignment, redacted
    )
    return redacted


def _label_tokens(label: str) -> set[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", label).lower()
    ordered = [piece for piece in re.split(r"[^a-z0-9]+", snake) if piece]
    pieces = set(ordered)
    pieces.update("_".join(pair) for pair in zip(ordered, ordered[1:], strict=False))
    pieces.add("_".join(ordered))
    pieces.add(re.sub(r"[^a-z0-9]", "", snake))
    return pieces


def _has_credential_words(label: str) -> bool:
    return bool(_label_tokens(label) & SENSITIVE_LABEL_TOKENS)


def _is_sensitive_label(label: str, value: str = "") -> bool:
    """Whether a label names a credential, given its value.

    A label whose last segment is metadata about a credential
    (``TOKEN_URL``, ``password_policy``) is not one. Its value is still
    a credential when it is a URL that carries one.
    """
    if not _has_credential_words(label):
        return False
    if re.split(r"[_.-]", label)[-1].lower() in _NON_CREDENTIAL_LABEL_SEGMENTS:
        return _carries_credential(value)
    return True


# Parsed languages whose unquoted values are literal data, not
# expressions. They are judged as text, so `auth: <secret>` in a YAML
# file is still a literal credential, and an INSERT row in a SQL dump
# is too.
_LITERAL_VALUE_LANGUAGES = frozenset(("bash", "yaml", "json", "toml", "xml", "sql"))


def is_code_path(path: str | Path) -> bool:
    """Whether a file is parsed source.

    In parsed source, an unquoted value is an expression. Shell is not
    one: `export TOKEN=abc` binds the literal word abc, so a shell
    script is judged as text, as it was before shell was parsed. Data
    formats such as YAML and TOML are judged as text for the same
    reason: their unquoted values are the literal values.
    """
    ext = Path(path).suffix.lower()
    return ext in EXTENSION_LANGUAGE and EXTENSION_LANGUAGE[ext] not in _LITERAL_VALUE_LANGUAGES


def _after_continuations(text: str, pos: int) -> int:
    """``pos`` after the backslash-newlines that start there (#559)."""
    while (found := _LINE_CONTINUATION_RE.match(text, pos)) is not None:
        pos = found.end()
    return pos


def _closes_on_a_later_line(text: str, start: int) -> bool:
    """Whether the quote at ``text[start]`` opens a string that its
    line does not close but a later line does.

    Such a value is a multi-line one, judged by
    _sensitive_unclosed_quoted_values.
    """
    if text[start : start + 1] not in ('"', "'") or _STRING_LITERAL_RE.match(text, start):
        return False
    return _quote_close(text, start + 1, text[start]) is not None


def _value_is_credential(
    text: str, pos: int, value: str, operator: str, label: str, *, code: bool, scanner: _Scanner
) -> bool:
    """Whether the value captured as ``value`` at ``text[pos:]`` may be
    a literal credential.

    Outside parsed source (``code`` false) an unquoted value is the
    literal itself, and is exempt only as a whole value that cannot be a
    credential. Redaction always judges this way, so text leaving MIMRY
    stays strictly redacted. In parsed source an unquoted value is an
    expression, so only string literals are judged (see
    _code_value_is_credential). Values are scanned with ``scanner``.
    """
    candidate = value.strip().rstrip(",;}").strip()
    if candidate == "\\":
        # A backslash-newline: the value is on the next line (#559).
        pos = _after_continuations(text, pos + len(value) - len(value.lstrip()))
        value = _VALUE_RE.match(text, pos).group()
        candidate = value.strip().rstrip(",;}").strip()
    # The marker, with the closing quote _redacted_secret writes back.
    if (
        not candidate
        or candidate in {REDACTED, REDACTED + '"', REDACTED + "'"}
        or candidate[0] == "|"
        # A quoted block whose lines are only the marker, as a triple-
        # quoted value redacted line by line, is already redacted.
        or (REDACTED in candidate and not _holds_text(candidate.strip("\"'`")))
    ):
        return False  # nothing assigned, already redacted, or a YAML block (classified above)
    # Punctuation alone (``...``, ``***``, ``---``) has no letter or
    # digit to be a credential. A bracket or quote opens an expression
    # (``API_KEY = (``), so it is not exempt.
    if not any(char.isalnum() for char in candidate) and not any(
        char in "([{\"'" for char in candidate
    ):
        return False
    if _is_location(candidate):
        return False
    start = pos + len(value) - len(value.lstrip())
    if _TEMPLATE_VALUE_RE.match(text, start):
        return False  # ``{value}`` in an f-string or template is filled in at run time
    if candidate[0] == ">":
        if operator == ":":
            return False  # a YAML folded block
        # PHP's and Ruby's ``'key' => 'value'``; anything else after
        # ``=>`` is an arrow function.
        start = _skip_blanks(text, start + 1)
        if _closes_on_a_later_line(text, start):
            return False
        if code:
            return _code_value_is_credential(text, start, "=", label, scanner)
        return _STRING_LITERAL_RE.match(text, start) is not None and _expression_is_credential(
            text, start, scanner
        )
    if _closes_on_a_later_line(text, start):
        return False  # judged as a whole by _sensitive_unclosed_quoted_values
    if candidate[0] == "=":
        # ``:=`` binds: judge the right-hand side. A comparison's value
        # never starts with ``=``; see _comparison_is_credential.
        rhs = start
        while text.startswith(("=", " ", "\t"), rhs):
            rhs += 1
        rhs_value = _VALUE_RE.match(text, rhs).group()
        return _value_is_credential(text, rhs, rhs_value, "=", label, code=code, scanner=scanner)
    if code:
        return candidate[0] != ":" and _code_value_is_credential(
            text, start, operator, label, scanner
        )
    literal = _STRING_LITERAL_RE.match(text, start)
    if literal:
        if literal["literal"][:3] in ('"""', "'''"):
            return True  # a block, judged by its label alone (see contains_sensitive_text)
        if literal["literal"] in ('""', "''") and text.startswith(
            literal["literal"][0] * 3, literal.start("literal")
        ):
            return False  # an unclosed triple quote, classified and redacted on its own
        if _CLOSING_BACKTICK_RE.match(text, literal.end()):
            # The literal ends at the backtick closing inline code.
            # Scanning on would read it as an unclosed template.
            return _string_literal_is_credential(literal, code=False)
        return _expression_is_credential(text, start, scanner)
    lookup = _ENV_LOOKUP_RE.match(text, start)
    if lookup:
        return _expression_is_credential(text, start, scanner, name=lookup.start("name"))
    if candidate[0] == "[":
        return _array_is_credential(text, start, scanner)
    lowered = candidate.lower()
    if (
        lowered in _NON_CREDENTIAL_WORDS
        or _NUMBER_RE.fullmatch(candidate)
        or _REFERENCE_RE.match(text, start)
        or _SPACED_PLACEHOLDER_RE.fullmatch(candidate)
    ):
        return False
    if operator == ":":
        annotation = _ANNOTATION_RE.match(candidate)
        if annotation and not _SPACED_WORDS_RE.search(annotation.group()):
            # ``token: str = "..."`` in a code sample: the assigned
            # literal counts too.
            rhs = _skip_blanks(text, start + annotation.end())
            if _STRING_LITERAL_RE.match(text, rhs) and _expression_is_credential(
                text, rhs, scanner
            ):
                return True
        if any(char.isspace() for char in candidate):
            return _prose_is_credential(
                candidate
            )  # bare colon phrases are common in prose and Markdown
    return True


def _is_credential_param(name: str) -> bool:
    lowered = name.lower()
    return (
        re.sub(r"[_.-]", "", lowered) in _CREDENTIAL_PARAM_NAMES
        or re.split(r"[_.-]", lowered)[-1] in _CREDENTIAL_PARAM_NAMES
    )


def _is_placeholder_value(value: str) -> bool:
    """Whether a value is empty, already redacted, or filled in at run
    time (``{token}``, ``${TOKEN}``, ``***``).
    """
    return (
        value == ""
        or value == REDACTED
        or _PLACEHOLDER_RE.fullmatch(value) is not None
        or _REFERENCE_RE.fullmatch(value) is not None
        or _TEMPLATE_PLACEHOLDER_RE.fullmatch(value) is not None
    )


def _has_credential_query(url: str) -> bool:
    """Whether a URL's query or fragment has a credential parameter.

    The value must not be a placeholder.
    """
    for part in re.split(r"[?#&;]", url)[1:]:
        name, separator, value = part.partition("=")
        if separator and _is_credential_param(name) and not _is_placeholder_value(value):
            return True
    return False


def _has_url_credential(text: str) -> bool:
    """Whether a text holds a URL with a password in its userinfo or a
    credential query parameter, whatever label is around the URL (#494).

    A user name alone (``git@host``) is not a credential.
    """
    if "://" not in text:
        return False
    return any(
        not _is_placeholder_value(match["password"]) for match in _URL_PASSWORD_RE.finditer(text)
    ) or any(_has_credential_query(url) for url in _URL_SPAN_RE.findall(text))


def _redact_url_password(match: re.Match[str]) -> str:
    if _is_placeholder_value(match["password"]):
        return match.group(0)
    return match["head"] + REDACTED


def _redact_url_param(match: re.Match[str]) -> str:
    if not _is_credential_param(match["name"]) or _is_placeholder_value(match["value"]):
        return match.group(0)
    return f"{match['lead']}{match['name']}={REDACTED}"


def _redact_url_credentials(text: str) -> str:
    """Replace only the password and the credential parameter values of
    each URL, so the rest of the URL and the line stay in place (#494).
    """

    def render(match: re.Match[str]) -> str:
        url = _URL_PASSWORD_RE.sub(_redact_url_password, match.group(0))
        return _URL_PARAM_RE.sub(_redact_url_param, url)

    return _URL_SPAN_RE.sub(render, text)


def _carries_credential(value: str) -> bool:
    """Whether a value holds a URL with userinfo or a credential query.

    Judged per URL, so prose around a URL does not matter.
    """
    return _USERINFO_URL_RE.search(value) is not None or any(
        _has_credential_query(url) for url in _URL_IN_TEXT_RE.findall(value)
    )


def _is_location(value: str) -> bool:
    """Whether a value is a URL that is not a credential by its userinfo
    or query.
    """
    return _URL_RE.fullmatch(value) is not None and not _has_credential_query(value)


def _prose_is_credential(value: str) -> bool:
    """Words after a colon are prose.

    Only an authorization scheme or a recognisable token format is a
    credential.
    """
    return value.lower().startswith(("bearer ", "basic ")) or bool(
        STANDALONE_SECRET_RE.search(value)
    )


def _expression_is_credential(
    text: str, start: int, scanner: _Scanner, *, name: int | None = None
) -> bool:
    """Outside parsed source: whether a value starting with a literal or
    an environment lookup holds a credential.

    Such a value is a literal, or an expression that goes on (``"Bearer
    " + "..."``, ``os.getenv("KEY") or ""``), and every literal in it is
    judged, call arguments too (see _Scanner for how far it reads). The
    looked-up name, at ``name``, is not one.
    """
    value = scanner.scan(start, strict=True)
    if value is None or not value.closed:
        return True  # nested past the budget, or an unterminated literal: fail closed
    return any(
        _string_literal_is_credential(literal, code=False)
        or _GLUED_WORD_RE.match(text, literal.end())
        for literal in value.literals
        if literal.start() != name
    )


def _array_is_credential(text: str, start: int, scanner: _Scanner) -> bool:
    """Outside parsed source: whether an array value holds a credential.

    An array is a list, so the label alone does not make it one. Its
    string members are judged as literals, and a relative path member is
    a file, not a credential.
    """
    value = scanner.scan(start)
    if value is None or not value.closed:
        return True  # nested past the budget, or unterminated: fail closed
    # An unquoted member, as in a YAML flow list ``[abc123]``, may be
    # the credential itself; only numbers and literals are judged.
    members = list(text[start : value.end])
    for literal in value.literals:
        members[literal.start() - start : literal.end() - start] = " " * len(literal.group())
    bare = _ARRAY_PUNCTUATION_RE.sub(" ", "".join(members)).split()
    if any(
        _NUMBER_RE.fullmatch(token) is None
        and token.lower() not in _ARRAY_CONSTANTS
        and not _RELATIVE_PATH_RE.fullmatch(token)
        for token in bare
    ):
        return True
    return any(
        _string_literal_is_credential(literal, code=False)
        and not _RELATIVE_PATH_RE.match(_literal_text(literal))
        for literal in value.literals
    )


def _literal_names_field(literal: re.Match[str], label: str) -> bool:
    """Whether a literal names a field, not a credential.

    A literal names a field when it matches [A-Za-z][A-Za-z_]* and
    (lowercased) is in the label's tokens or SENSITIVE_LABEL_TOKENS.
    """
    text = _literal_text(literal)
    if not _FIELD_NAME_RE.fullmatch(text):
        return False
    text_lower = text.lower()
    return text_lower in _label_tokens(label) or text_lower in SENSITIVE_LABEL_TOKENS


def _in_literal_union(text: str, literal: re.Match[str]) -> bool:
    """Whether a literal is part of a TypeScript literal-union type."""
    start = literal.start()
    return bool(
        _UNION_NEXT_RE.match(text, literal.end())
        or _UNION_PREVIOUS_RE.search(text, max(0, start - 80), start)
    )


def _in_typeof_comparison(text: str, literal: re.Match[str]) -> bool:
    """Whether a literal is the type a typeof comparison tests for."""
    start = literal.start()
    return _literal_text(literal) in _TYPEOF_RESULTS and (
        _TYPEOF_COMPARISON_RE.search(text, max(0, start - 80), start) is not None
    )


def _code_value_is_credential(
    text: str, start: int, operator: str, label: str, scanner: _Scanner
) -> bool:
    """Whether a value in parsed source may be a literal credential.

    String literals that form the value are judged as literals. An
    unquoted value is a credential only if it cannot be read as code:
    names, calls, types and other expressions are code,
    ``privacy-canary-value`` or ``cGFzc3dvcmQ=`` are not. A docstring or
    comment example that reads as code passes this gate; text taken from
    those into the index is redacted as configuration (see
    scanner.text_hint).
    """
    value = scanner.scan(start)
    if value is None or not value.closed:
        return True  # nested past the budget, or an unterminated literal: fail closed
    shape = value.shape.strip()
    if operator == ":":
        annotation = _ANNOTATION_RE.match(text, start, value.end)
        if annotation and _SPACED_WORDS_RE.search(annotation.group()):
            return _prose_is_credential(shape)  # ``Authorization: OAuth realm="..."``
        if annotation:  # ``token: str = "..."``: the annotation names a type; judge the value
            return _code_value_is_credential(
                text, _skip_blanks(text, annotation.end()), "=", label, scanner
            )

    # ``app.secret_key = "secret_key"``, ``"auth": ("auth", "oauth")``,
    # ``secret: 'passphrase'`` and ``AuthUserKey = "user"``: a value
    # naming its own label or a field is a keyword table, not a secret.
    if label.rsplit(".", 1)[-1].lower() in {
        _literal_text(literal).lower() for literal in value.literals
    }:
        return False
    # A relative path that is a member of an array is a file, as in
    # _array_is_credential.
    in_array = shape.startswith("[")
    if any(
        _string_literal_is_credential(literal, code=True)
        for literal in value.literals
        if not _literal_names_field(literal, label)
        and not _in_literal_union(text, literal)
        and not _in_typeof_comparison(text, literal)
        and not (in_array and _RELATIVE_PATH_RE.match(_literal_text(literal)))
    ):
        return True
    if not shape or shape[0] in "_([":
        return False  # nothing assigned, or a value made of the literals just judged
    if operator == ":" and any(char.isspace() for char in shape):
        return _prose_is_credential(
            shape
        )  # after a colon, words with spaces between them are prose or a type
    return not _reads_as_code(shape)


class _Value(NamedTuple):
    """A scanned value: see _scan_value."""

    literals: list[re.Match[str]]
    shape: str
    end: int
    closed: bool


class _Scanner:
    """Scans the values in one text (see _scan_value), within a budget
    that keeps judging it linear.

    A value is scanned to its own end, past any assignment nested in it
    (``default=`` in a call, ``"user":`` in a dict), and the nested one
    is scanned again as a value of its own. Ordinary source spends a
    fraction of the budget; text that nests deeply enough to spend it
    fails closed.

    A ``strict`` scan judges every literal. It reads text that is not
    parsed source line by line, as a bracket or an operator in prose
    often goes nowhere. For ``redaction`` it reads as source is read,
    across lines: what redaction leaves in place leaves MIMRY.
    """

    def __init__(self, text: str, *, redaction: bool = False, prose: bool = False) -> None:
        self.text = text
        self.redaction = redaction
        # Whether the text is prose, a comment or a string's text (see
        # _redact_text), where a quoted value may be followed by words.
        self.prose = prose
        self.left = 4 * len(text) + 1024
        self._tuple_slots: list[_TupleSlot] | None = None
        self._tuple_label_starts: set[int] | None = None

    def scan(self, start: int, *, strict: bool = False) -> _Value | None:
        if self.left <= 0:
            return None
        value = _scan_value(
            self.text, start, every_literal=strict, by_line=strict and not self.redaction
        )
        self.left -= value.end - start + 1
        return value

    def tuple_slots(self) -> list[_TupleSlot]:
        """The targets of the tuple assignments, read once."""
        if self._tuple_slots is None:
            self._tuple_slots = _tuple_slots(self.text)
        return self._tuple_slots

    def tuple_label_starts(self) -> set[int]:
        if self._tuple_label_starts is None:
            self._tuple_label_starts = {slot.label_start for slot in self.tuple_slots()}
        return self._tuple_label_starts


def _scan_value(
    text: str, start: int, *, every_literal: bool = False, by_line: bool = False
) -> _Value:
    """The value at ``text[start:]``: its string literals, its shape and
    where it ends.

    A literal is part of the value at the top level (``cfg.get("k") or
    "..."``) and in tuples and lists, which may span lines. A call's or
    subscript's arguments are not (``kwargs.pop("auth")``): as in Yelp's
    detect-secrets, they are lookup keys, prompts or patterns; nor are
    the contents of braces, whose entries are assignments of their own.
    The shape is the top-level text with each literal written as ``_``
    and each bracket's contents dropped. The value ends at a top-level
    comma or semicolon, at a line end unless the expression goes on (see
    _line_goes_on), or at the bracket closing its context; a comment
    before that line end is not part of it. ``closed`` is false when a
    literal is left unterminated; the value then runs to its line end.

    With ``every_literal``, every literal counts. With ``by_line``, for
    text that is not parsed source, the value ends at its line end even
    inside brackets, and fewer operators carry it on to the next line.
    """
    literals: list[re.Match[str]] = []
    shape: list[str] = []
    # Per open bracket: whether literals directly inside it are part of
    # the value.
    counted: list[bool] = []
    comment = None  # where a top-level comment on the current line starts
    ternary = False  # whether a top-level ``?`` has been seen
    pos, end = start, len(text)
    while (token := _EXPRESSION_TOKEN_RE.search(text, pos)) is not None:
        lead, at = token.group(), token.start()
        if not counted:
            shape.append(text[pos:at])
            ternary = ternary or "?" in shape[-1]
        pos = token.end()
        if lead in ("#", "//"):
            if not counted:
                comment = at
            newline = text.find("\n", at)
            pos = end if newline < 0 else newline
        elif lead == "/*":
            close = text.find("*/", at)
            pos = end if close < 0 else close + 2
        elif lead in "\"'`":
            if lead == "'" and text[at - 1 : at] in ("&", "<") and _LIFETIME_RE.match(text, at):
                if not counted:
                    shape.append(lead)
                continue
            prefix = at
            while prefix > start and at - prefix < 2 and text[prefix - 1] in _STRING_PREFIX_CHARS:
                prefix -= 1
            before = text[prefix - 1 : prefix]
            if before.isalnum() or before in ("_", "$"):
                # An apostrophe in a word (``user's``), or a tagged or
                # interpolated string.
                if not counted:
                    shape.append(lead)
                continue
            literal = _STRING_LITERAL_RE.match(text, prefix)
            if literal is None:
                newline = text.find("\n", at)
                return _Value(literals, "".join(shape), end if newline < 0 else newline, False)
            triple = lead * 3 if lead in "\"'" else lead
            if text.startswith(triple, at) and not literal["literal"].startswith(triple):
                # A triple quote the text never closes, as the closing
                # quotes of a docstring after a value, ends the value.
                # The short pattern would read its first two quotes as
                # an empty string.
                return _Value(literals, "".join(shape), prefix, True)
            if every_literal or not counted or counted[-1]:
                literals.append(literal)
            if not counted:
                shape[-1] = shape[-1][
                    : len(shape[-1]) - (at - prefix)
                ]  # the prefix is part of the literal
                shape.append("_")
            pos = literal.end()
        elif lead in "([{":
            if not counted:
                shape.append(lead)
            counted.append(
                lead != "{" and _opens_group(text, start, at) and (not counted or counted[-1])
            )
        elif lead in ")]}":
            if not counted:
                return _Value(
                    literals, "".join(shape), at, True
                )  # closes the bracket the value sits in
            counted.pop()
            if not counted:
                shape.append(lead)
        elif lead in "\r\n":
            if counted and not by_line:
                continue  # a tuple, list or call that spans lines
            line_end = at if comment is None else comment
            if counted or not _line_goes_on(
                text, start, line_end, pos, source=not by_line, ternary=ternary
            ):
                return _Value(literals, "".join(shape), line_end, True)
            comment = None
        elif not counted:
            return _Value(literals, "".join(shape), at, True)  # a top-level comma or semicolon
    if not counted:
        shape.append(text[pos:end])
    return _Value(literals, "".join(shape), max(pos, end), True)


def _line_goes_on(
    text: str, start: int, line_end: int, next_line: int, *, source: bool, ternary: bool = False
) -> bool:
    """Whether the expression at ``text[start:line_end]`` goes on to the
    next line.

    It does after a binary operator (``"Bearer " +``). Read as source,
    it also does after a backslash, a ternary's ``?`` or PHP's ``.``,
    and before a line that an operator leads (``? "..."``, ``.trim()``),
    as formatters wrap long expressions; a ``:`` only goes on with a
    ``?`` before it (``ternary``), as docstrings lead lines with
    ``:param``. Otherwise a backslash only joins an operator to the next
    line: in shell, ``TOKEN="" \\`` goes on to a command.
    """
    end = _end_of_line(text, start, line_end)
    if end > start and text[end - 1] == "\\":
        if source:
            return True
        end = _end_of_line(text, start, end - 1)
    window = max(start, end - 2)
    if _TRAILING_OPERATOR_RE.search(text, window, end):
        return True
    if not source:
        return False
    operators = (
        _TRAILING_SOURCE_OPERATOR_RE.search(text, window, end),
        _LEADING_OPERATOR_RE.match(text, next_line),
    )
    return any(
        operator is not None and (ternary or not operator["colon"]) for operator in operators
    )


def _end_of_line(text: str, start: int, end: int) -> int:
    """Where ``text[start:end]`` ends, without trailing blanks."""
    while end > start and text[end - 1] in " \t\r":
        end -= 1
    return end


# Characters of a collapsed source expression; anything else (``@``,
# ``#``, ``"``) makes an unquoted value data rather than code.
_CODE_SHAPE_RE = re.compile(r"[\w$.:&*!~?|+/%<>=,'()\[\]{}\s^-]*")
# ``privacy-canary-value`` is data; subtraction is written ``a - b`` or
# ``a-1``.
_HYPHENATED_WORD_RE = re.compile(r"[A-Za-z_]-[A-Za-z]")


def _reads_as_code(shape: str) -> bool:
    """Whether a collapsed unquoted value can be a source expression.

    See _scan_value.
    """
    # Accept JS private member access by normalizing .# to .
    normalized = shape.replace(".#", ".")
    return (
        bool(_CODE_SHAPE_RE.fullmatch(normalized))
        and not _HYPHENATED_WORD_RE.search(shape)
        # An expression does not end in an operator: ``cGFzc3dvcmQ=`` is
        # base64. (``>`` closes generics, as in ``Option<Auth>``.)
        and shape[-1] not in "=:+-*/%&|^~<,."
    )


def _opens_group(text: str, start: int, at: int) -> bool:
    """Whether the bracket at ``text[at]`` opens a tuple or list.

    Not a call or subscript.
    """
    end = at
    while end > start and text[end - 1] in " \t\r\n":
        end -= 1
    begin = end
    while begin > start and (text[begin - 1].isalnum() or text[begin - 1] in "_$"):
        begin -= 1
    if begin < end:
        return text[begin:end] in _KEYWORDS_BEFORE_GROUPS
    return end == start or text[end - 1] not in ")]}\"'`"


def _literal_text(literal: re.Match[str]) -> str:
    quoted = literal["literal"]
    width = 3 if quoted[:3] in {'"""', "'''"} else 1
    return quoted[width:-width].strip()


def _string_literal_is_credential(literal: re.Match[str], *, code: bool) -> bool:
    text = _literal_text(literal)
    if ("f" in literal["prefix"].lower() and "{" in text) or (
        literal["literal"][0] == "`" and "${" in text
    ):
        # A formatted string or template literal is judged by the text
        # around its placeholders: ``f"{p}<secret>"`` by its secret, and
        # ``f"Bearer {token}"`` not at all (#494).
        return any(_text_is_credential(piece) for piece in _formatted_pieces(text))
    if code and literal["literal"][0] == "`" and not any(char.isalnum() for char in text):
        return False  # tail of a nested template like `)}` has no letters or digits
    return _text_is_credential(text)


def _formatted_pieces(text: str) -> list[str]:
    """The text of a formatted string between its placeholders.

    Only a bare run of characters is judged as a value. A piece that
    ends a label (``token=``, ``url:``), or holds code or prose
    (``Bearer abc``, or ``stringToBase64(`` cut off by a nested
    template), names a value rather than holding one, so it is not
    judged (#494).
    """
    pieces = []
    for piece in _FORMAT_PLACEHOLDER_RE.split(text)[::2]:
        piece = piece.strip()
        if _FORMATTED_PIECE_RE.fullmatch(piece) and not piece.endswith((":", "=", "/", "?", "&")):
            pieces.append(piece)
    return pieces


def _text_is_credential(text: str) -> bool:
    lowered = text.lower()
    return bool(text) and not (
        text == REDACTED
        or _TEMPLATE_PLACEHOLDER_RE.fullmatch(text)
        or lowered in _NON_CREDENTIAL_WORDS
        or lowered in _AUTH_SCHEMES
        # A count or a size, as in ``Secret: "42"``. A PIN-length
        # number is still a credential.
        or (_NUMBER_RE.fullmatch(text) is not None and len(text) < 4)
        or _REFERENCE_RE.fullmatch(text)
        or _PLACEHOLDER_RE.fullmatch(text)
        or _ENV_LOOKUP_RE.fullmatch(text)
        or _is_location(text)
    )


def _skip_blanks(text: str, pos: int) -> int:
    while text.startswith((" ", "\t"), pos):
        pos += 1
    return pos


def _spaced_assignment_value_is_sensitive(
    match: re.Match[str], *, code: bool = False, scanner: _Scanner
) -> bool:
    label = match.group("label")
    # A label with a space in it (``api key: ...``) only occurs in
    # comments and prose.
    code = code and not any(char.isspace() for char in label)
    if match.group("separator").strip(" \t") in COMPARISON_OPERATORS:
        return _comparison_is_credential(match.string, match.start("value"), code=code)
    return _value_is_credential(
        match.string,
        match.start("value"),
        match.group("value"),
        "=",
        label,
        code=code,
        scanner=scanner,
    )


def _is_comparison(match: re.Match[str]) -> bool:
    if "operator" in match.re.groupindex:
        return match.group("operator") in COMPARISON_OPERATORS
    return match.group("separator").strip(" \t") in COMPARISON_OPERATORS


def _redact_values(text: str, pattern: re.Pattern[str], render, *, prose: bool = False) -> str:
    """Replace each match ``render`` redacts, through the end of its
    value.

    A pattern stops capturing a value at its first comma or closing
    bracket, and a quoted value at its closing quote. Where the value
    goes on, replacing just the capture left ``or "fallback"``, later
    arguments, later elements and the rest of ``KEY=a,b`` in place.
    """
    scanner = _Scanner(text, redaction=True, prose=prose)
    pieces: list[str] = []
    pos = 0
    for match in pattern.finditer(text):
        if match.start() < pos:
            continue  # inside a value already redacted
        replacement = render(match, scanner)
        if replacement is None:
            continue
        pieces.append(text[pos : match.start()])
        end = _redacted_value_end(match, scanner)
        if end is None:
            pieces.append(REDACTED)  # nested past the budget: fail closed
            return "".join(pieces)
        judged = _judged_start(match)
        value_span = text[judged:end]
        if "\n" in value_span or "\r" in value_span:
            # A value over several lines keeps its line breaks, so each
            # line is redacted in place instead of collapsing to one.
            # Chained targets before a judged literal are kept as they
            # are.
            replacement = text[match.start() : judged] + _redacted_multiline(value_span)
        pieces.append(replacement)
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)


def _redacted_value_end(match: re.Match[str], scanner: _Scanner) -> int | None:
    """Where a redacted value ends, or None when it is nested past the
    budget.

    The pattern's capture stops at the first comma or closing bracket,
    at a closing quote or at the line end. Where the value may go on
    past it (``f(a, "...")``, ``"a" or "..."``, ``x ?\\n "..."``), it is
    scanned as source.
    """
    text, value, end = match.string, match.group("value"), match.end("value")
    comparison = _is_comparison(match)
    if comparison and not _OPERAND_CONTINUES_RE.match(text, end):
        # The value of a comparison is its literal. What follows it is
        # code of its own (``and ok:``), kept for later matches.
        return end
    quoted = value.strip().lstrip(">").lstrip()[:1] in ('"', "'")  # after ``=>`` too
    if quoted and scanner.prose and _PROSE_AFTER_QUOTE_RE.match(text, end):
        # Prose after the closing quote is text of its own, as in
        # ``"x" in config.`` (#520).
        return end
    if (
        value.count("(") + value.count("[") > value.count(")") + value.count("]")
        or (quoted and not _VALUE_END_RE.match(text, end))
        or _LINE_BREAK_RE.match(text, end)
    ):
        scanned = scanner.scan(match.start("value"))
        if scanned is None:
            return None
        if comparison:
            # A comparison's operand may go on (``== "a" + "b"``). It is
            # redacted through its last literal, so the code after it
            # (the colon of ``if``) stays in place.
            return max([end, *(literal.end() for literal in scanned.literals)])
        return max(end, scanned.end)
    if (
        not quoted
        and text.startswith(",", end)
        and (match.start() == 0 or text[match.start() - 1] in "\r\n")
    ):
        # On a line of its own, as in ``.env`` and YAML, a comma is part
        # of the value.
        return _LINE_REST_RE.match(text, end).end()
    return end


def _redacted_secret(value: str) -> str:
    """The redacted form of a captured value: the secret between its
    quotes, or the whole value when it is unquoted.

    A trailing ``\\n``, ``\\r`` or ``\\t`` escape stays after the
    secret, before the closing quote.
    """
    formatted = _FORMATTED_LITERAL_RE.fullmatch(value)
    if formatted and ("f" in formatted["prefix"].lower() or formatted["quote"] == "`"):
        return _redacted_formatted(formatted)
    quote = ""
    if len(value) >= 6 and value[:3] == value[-3:] and value[:3] in {'"""', "'''"}:
        quote = value[:3]
    elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        quote = value[0]
    if not quote:
        # An odd quote at the end closes an enclosing string, as in
        # ``f("token=x")``, so it stays after the marker. A balanced one
        # is part of the expression, as in ``f"..."``.
        closing = value[-1:] if value[-1:] in {'"', "'"} and value.count(value[-1]) % 2 else ""
        return REDACTED + closing
    escape = _TRAILING_ESCAPE_RE.search(value, len(quote), len(value) - len(quote))
    return f"{quote}{REDACTED}{escape.group() if escape else ''}{quote}"


def _redacted_formatted_chunk(chunk: str) -> str:
    """Redact the literal text of a formatted string between its
    placeholders, keeping a trailing escape such as ``\\n``.
    """
    escape = _TRAILING_ESCAPE_RE.search(chunk)
    core = chunk[: escape.start()] if escape else chunk
    if not core.strip():
        return chunk
    return REDACTED + (escape.group() if escape else "")


def _redacted_formatted(literal: re.Match[str]) -> str:
    """The redacted form of a formatted string: its placeholders stay,
    so ``f"{p}<secret>"`` becomes ``f"{p}[REDACTED]"`` and still parses.
    """
    parts = _FORMAT_PLACEHOLDER_RE.split(literal["body"])
    body = "".join(
        part if index % 2 else _redacted_formatted_chunk(part) for index, part in enumerate(parts)
    )
    return f"{literal['prefix']}{literal['quote']}{body}{literal['quote']}"


def _redact_spaced_assignment(match: re.Match[str], scanner: _Scanner) -> str | None:
    if not _spaced_assignment_value_is_sensitive(match, scanner=scanner):
        return None
    label, separator = match.group("label"), match.group("separator")
    return f"{label}{separator}{_redacted_secret(match.group('value'))}"


def _comparison_is_credential(text: str, start: int, *, code: bool) -> bool:
    """Whether a comparison's right-hand side, at ``text[start:]``, is a
    string literal that may be a credential.

    The literal must be long enough, hold a letter or digit, and pass
    the literal judge, so placeholders stay exempt. A typeof result is
    a type name, not a value.
    """
    literal = _STRING_LITERAL_RE.match(text, start)
    if literal is None:
        return False
    content = _literal_text(literal)
    return (
        len(content) >= COMPARED_SECRET_MIN_CHARS
        and any(char.isalnum() for char in content)
        and not _in_typeof_comparison(text, literal)
        and _string_literal_is_credential(literal, code=code)
    )


def _chained_target(match: re.Match[str]) -> tuple[str, int] | None:
    """The credential target that an assignment's value chains onto, and
    where the literal it takes starts (#540).

    In ``a = password = "..."`` both targets take the literal, so the
    literal is judged as ``password``'s value and ``a = password = `` is
    kept in front of it. Only a quoted literal, or a value that opens
    with ``(`` or a backslash-newline, is judged this way (#559): a
    value that is not one (``TOKEN=abc=def``) is one value, judged as
    before, and so is a value whose own label is a credential.
    """
    if match.group("operator") != "=" or _is_sensitive_label(
        match.group("label"), match.group("value")
    ):
        return None
    text, pos, end = match.string, match.start("value"), match.end("value")
    credential = None
    while (found := _CHAINED_TARGET_RE.match(text, pos, end)) is not None:
        name = found["key"] or found["name"]
        if _is_sensitive_label(name):
            credential = name
        pos = found.end()
    if credential is None or not _chained_value_starts(text, pos, end):
        return None
    return credential, pos


def _chained_value_starts(text: str, pos: int, end: int) -> bool:
    """Whether the value a chain takes starts at ``text[pos]``: a
    string literal, a parenthesised value, or one after a backslash-
    newline. The last two are judged as a plain assignment's are (#559).
    """
    return (
        _STRING_LITERAL_RE.match(text, pos, end) is not None
        or (pos < end and text[pos] == "(")
        or _LINE_CONTINUATION_RE.match(text, pos) is not None
    )


def _judged_start(match: re.Match[str]) -> int:
    """Where the value that an assignment match judges starts: after any
    chained targets before the literal (#540)."""
    if "operator" not in match.re.groupindex:
        return match.start("value")
    chained = _chained_target(match)
    return match.start("value") if chained is None else chained[1]


def _assignment_match_is_sensitive(
    match: re.Match[str], *, code: bool = False, scanner: _Scanner
) -> bool:
    chained = _chained_target(match)
    if chained is not None:
        credential, start = chained
        return _value_is_credential(
            match.string,
            start,
            match.string[start : match.end("value")],
            "=",
            credential,
            code=code,
            scanner=scanner,
        )
    label = match.group("label")
    if not _is_sensitive_label(label, match.group("value")):
        return False
    if match.start("label") in scanner.tuple_label_starts():
        return False  # a tuple target takes the value at its own position (#519)
    if match.group("operator") in COMPARISON_OPERATORS:
        return _comparison_is_credential(match.string, match.start("value"), code=code)
    if (
        match.group("operator") == ":"
        and _AUTH_LABEL_RE.fullmatch(label) is not None
        and _auth_assignment_is_placeholder(match.string, match.start("value"))
    ):
        return False  # a placeholder in an Authorization header (#592)
    if code and not match.group("label_quote") and match.group("value").startswith(">"):
        # ``token => token.value``: a bare name before ``=>`` is an
        # arrow function's parameter. Only a literal it returns, as in
        # C#'s ``Token => "..."``, is a value.
        if not _STRING_LITERAL_RE.match(
            match.string, _skip_blanks(match.string, match.start("value") + 1)
        ):
            return False
    return _value_is_credential(
        match.string,
        match.start("value"),
        match.group("value"),
        match.group("operator"),
        label,
        code=code,
        scanner=scanner,
    )


def _redact_assignment(match: re.Match[str], scanner: _Scanner) -> str | None:
    if not _assignment_match_is_sensitive(match, scanner=scanner):
        return None
    start = _judged_start(match)
    head = match.string[match.start("value") : start]
    stripped = match.string[start : match.end("value")].strip()
    arrow = ""
    if stripped.startswith(">"):  # ``'key' => 'value'``
        arrow, stripped = "> ", stripped[1:].strip()
    return f"{match.group('prefix')}{head}{arrow}{_redacted_secret(stripped)}"


def _literal_comparison_is_sensitive(match: re.Match[str], *, code: bool = False) -> bool:
    if not _is_sensitive_label(match.group("label")):
        return False
    return _comparison_is_credential(match.string, match.start("literal"), code=code)


def _redact_literal_comparison(match: re.Match[str]) -> str:
    if not _literal_comparison_is_sensitive(match):
        return match.group(0)
    literal = match.group("literal")
    return _redacted_secret(literal) + match.group(0)[len(literal) :]


def _label_comparison_is_sensitive(match: re.Match[str], *, code: bool = False) -> bool:
    if not _is_sensitive_label(match.group("label")):
        return False
    return _comparison_is_credential(match.string, match.start("literal"), code=code)


def _redact_label_comparison(match: re.Match[str]) -> str:
    if not _label_comparison_is_sensitive(match):
        return match.group(0)
    literal = match.group("literal")
    return match.group(0)[: -len(literal)] + _redacted_secret(literal)


def _tuple_is_sensitive(match: re.Match[str], *, code: bool = False) -> bool:
    """Whether a tuple's first element names a credential and its second
    is a credential-shaped literal, as in ``pair = (token, "...")``.

    The ``(`` must open a group: in a call, ``log(token, "...")`` is an
    argument list, whose literal is a message or a key.
    """
    if not _is_sensitive_label(match.group("label")):
        return False
    line_start = match.string.rfind("\n", 0, match.start()) + 1
    if not _opens_group(match.string, line_start, match.start()):
        return False
    return _comparison_is_credential(match.string, match.start("literal"), code=code)


def _redact_tuple(match: re.Match[str]) -> str:
    if not _tuple_is_sensitive(match):
        return match.group(0)
    literal = match.group("literal")
    return match.group(0)[: -len(literal)] + _redacted_secret(literal)


class _TupleSlot(NamedTuple):
    """A target of a tuple assignment and the value at its position.

    ``literal`` is the string literal that is exactly that value, or
    None for any other expression, judged as an assignment's value.
    """

    label: str
    label_start: int
    start: int
    end: int
    literal: re.Match[str] | None


def _tuple_slots(text: str) -> list[_TupleSlot]:
    """The targets of each statement-level tuple or list assignment in
    ``text``, paired with the value at the same position (#519).

    A statement whose values do not pair one to one with its targets is
    not a tuple here; it is judged as before.
    """
    slots: list[_TupleSlot] = []
    statements = list(_TUPLE_ASSIGNMENT_RE.finditer(text))
    for index, statement in enumerate(statements):
        # A statement's values end before the next statement starts, so
        # each scan covers its own span and no span is read twice.
        # Without this bound, each unclosed opener in a run of
        # ``a, p = (`` lines was read to the value limit (#519).
        stop = statements[index + 1].start() if index + 1 < len(statements) else len(text)
        items = list(
            _TUPLE_TARGET_ITEM_RE.finditer(
                text, statement.start("targets"), statement.end("targets")
            )
        )
        values = _statement_values(text, statement.end(), stop)
        if values is None or len(values) != len(items):
            values = _chained_tuple_values(text, statement.end(), stop, len(items))
            if values is None:
                continue
        for item, (start, end) in zip(items, values, strict=True):
            if item.group("key") is not None:
                label, label_start = item.group("key"), item.start("key")
            else:
                label, label_start = item.group("name"), item.start("name")
            literal = _STRING_LITERAL_RE.match(text, start, end)
            if literal is not None and literal.end() != end:
                literal = None  # part of a larger expression, as ``"a" + b``
            slots.append(_TupleSlot(label, label_start, start, end, literal))
    return slots


def _statement_values(text: str, pos: int, stop: int) -> list[tuple[int, int]] | None:
    """The stripped spans of the values right of a tuple's operator.

    They start at ``text[pos:]`` and end before ``stop``. None when they
    do not read as one statement: a bracket opens outside them (a call's
    arguments), one is a keyword argument, or they run past
    _TUPLE_VALUES_LIMIT. The whole right-hand side may be bracketed, as
    in ``= ("a", "b")``.
    """
    pos = _skip_blanks(text, pos)
    if text.startswith(("(", "["), pos):
        closer = ")" if text[pos] == "(" else "]"
        if all(text.find(char, pos + 1, stop) == -1 for char in ")]}"):
            return None  # no bracket can close this one before the next statement
        wrapped = _split_elements(text, pos + 1, closer, stop)
        if wrapped is not None and _TUPLE_TAIL_RE.match(text, wrapped[1]) is not None:
            return _stripped_values(text, wrapped[0])
    bare = _split_elements(text, pos, None, stop)
    return None if bare is None else _stripped_values(text, bare[0])


def _chained_tuple_values(
    text: str, pos: int, stop: int, count: int
) -> list[tuple[int, int]] | None:
    """The spans of the values of a tuple assignment whose targets are
    chained to them, as in ``password, a = b = "..."`` (#559).

    The chained targets are skipped. A single value is taken by every
    target. Several values take the targets one each, but only when all
    of them are string literals, so an expression is never split.
    """
    pos = _skip_blanks(text, pos)
    chained = False
    while (found := _CHAINED_TARGET_RE.match(text, pos, stop)) is not None:
        pos, chained = found.end(), True
    if not chained:
        return None
    values = _statement_values(text, pos, stop)
    if values is None:
        return None
    if len(values) == 1:
        return values * count
    if len(values) != count:
        return None
    for start, end in values:
        literal = _STRING_LITERAL_RE.match(text, start, end)
        if literal is None or literal.end() != end:
            return None
    return values


def _split_elements(
    text: str, pos: int, closer: str | None, stop: int
) -> tuple[list[tuple[int, int]], int] | None:
    """The top-level comma-separated spans of the values.

    They start at ``text[pos:]``. The values end at ``closer``, a
    bracket that opens before ``pos``, or at the end of the statement
    when ``closer`` is None. Returns their spans and the index after
    them, or None when a bracket closes outside them or they do not end
    before ``stop`` or within the limit.
    Quotes, nested brackets and comments are stepped over, so a comma in
    any of them does not split a value.
    """
    limit = min(len(text), pos + _TUPLE_VALUES_LIMIT, stop)
    spans: list[tuple[int, int]] = []
    start = pos
    depth = 0
    i = pos
    while True:
        found = _TUPLE_BREAK_RE.search(text, i, limit)
        if found is None:
            if closer is None and limit == len(text):
                spans.append((start, limit))
                return spans, limit
            return None
        i = found.start()
        char = found.group()
        if char in "\"'`":
            literal = _STRING_LITERAL_RE.match(text, i, limit)
            if literal is None:
                if text.startswith(char * 3, i):
                    return None  # a triple quote that does not close in range
                i += 1
            else:
                i = literal.end()
            continue
        if char in ("#", "//"):
            if closer is None:
                if depth:
                    return None
                spans.append((start, i))
                return spans, i
            newline = text.find("\n", i, limit)
            if newline == -1:
                return None
            i = newline
            continue
        if char in "\r\n":
            if closer is None:
                if depth:
                    return None  # a bare value that runs on past its line
                spans.append((start, i))
                return spans, i
            i += 1
            continue
        if char == ";":
            if closer is None:
                if depth:
                    return None
                spans.append((start, i))
                return spans, i
            if depth == 0:
                return None
            i += 1
            continue
        if char == ",":
            if depth == 0:
                spans.append((start, i))
                start = i + 1
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if depth:
                depth -= 1
            elif char == closer:
                spans.append((start, i))
                return spans, i + 1
            else:
                return None
        i += 1


def _stripped_values(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]] | None:
    values: list[tuple[int, int]] = []
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue  # a trailing comma leaves an empty value
        if _KEYWORD_VALUE_RE.match(text, start) is not None:
            return None
        values.append((start, end))
    return values


def _tuple_slot_is_sensitive(slot: _TupleSlot, *, code: bool, scanner: _Scanner) -> bool:
    if not _is_sensitive_label(slot.label):
        return False
    if slot.literal is not None:
        return _string_literal_is_credential(slot.literal, code=code)
    return _value_is_credential(
        scanner.text,
        slot.start,
        scanner.text[slot.start : slot.end],
        "=",
        slot.label,
        code=code,
        scanner=scanner,
    )


def _redact_tuple_slots(text: str) -> str:
    scanner = _Scanner(text, redaction=True)
    edits: list[tuple[int, int, str]] = []
    for slot in scanner.tuple_slots():
        if not _tuple_slot_is_sensitive(slot, code=False, scanner=scanner):
            continue
        if edits and edits[-1][0] == slot.start:
            continue  # a chained value several targets take is redacted once (#559)
        if slot.literal is not None:
            prefix = slot.literal["prefix"]
            replacement = prefix + _redacted_secret(slot.literal["literal"])
            edits.append((slot.start, slot.literal.end(), replacement))
        else:
            edits.append((slot.start, slot.end, _redacted_secret(text[slot.start : slot.end])))
    return _splice(text, edits)


def _literal_chains(text: str) -> Iterator[list[re.Match[str]]]:
    """The string literals of ``text``, grouped where each one is joined
    to the one before it (see _LITERAL_JOIN_RE)."""
    chain: list[re.Match[str]] = []
    for literal in _literal_finditer(
        _STRING_LITERAL_RE, text, quotes=_LITERAL_QUOTE_RE, prefixed=True
    ):
        if chain and _LITERAL_JOIN_RE.fullmatch(text, chain[-1].end(), literal.start()) is None:
            yield chain
            chain = []
        chain.append(literal)
    if chain:
        yield chain


def _auth_value_is_credential(
    scheme: str | None, value: str, *, last: bool, called: bool = False
) -> bool:
    """Whether an Authorization value is a credential (#540, #586).

    ``scheme`` is the scheme before the value, or None when the value
    is the first word after the colon. A placeholder, a reference, or a
    prose word is never one (_text_is_credential, _AUTH_PROSE_WORDS,
    _AUTH_PLACEHOLDER_WORDS, _AUTH_PLACEHOLDER_NAME_RE,
    _AUTH_PLACEHOLDER_PREFIX_RE), and neither is a value followed
    directly by ``(``, as in ``base64(user:pass)``, which is
    function-like (#592). ``called`` says whether it is.

    A value that reads as a token (a digit or one of ``-_.~+/=``) is
    one, after any scheme. A word of letters alone is one when it ends
    its header (``last``). The exceptions need at least 8 letters: with
    no scheme, or after Basic, Bearer or Token, the word may be followed
    by more text, so ``Authorization: tomatocanary extra`` and
    ``Authorization: Bearer tomatocanary extra`` redact the word, while
    ``Authorization: Bearer is required`` keeps it. After any other
    scheme the word must end its header, as in
    ``Authorization: Digest tomatocanary``, and a parameter such as
    ``realm="Photos"`` is kept. A value with a colon is one only when
    each side of the colon is token characters, as in
    ``acct:dG9tYXRvY2FuYXJ5`` (#593).
    """
    if (
        not _text_is_credential(value)
        or value.lower() in _AUTH_PROSE_WORDS
        or value.lower() in _AUTH_PLACEHOLDER_WORDS
        or _AUTH_PLACEHOLDER_NAME_RE.fullmatch(value) is not None
        or (
            _AUTH_PLACEHOLDER_PREFIX_RE.match(value) is not None
            and not any(char.isdigit() for char in value)
        )
        or called
    ):
        return False
    if ":" in value and any(_TOKEN_PART_RE.fullmatch(part) is None for part in value.split(":")):
        return False  # a colon joins two token-shaped parts only (#593)
    token = any(char.isdigit() or char in "-_.~+/=" for char in value)
    letters = value.isascii() and value.isalpha()
    if scheme is None:
        return token or (letters and (last or len(value) >= 8))
    if scheme.lower() in _BEARER_LIKE_AUTH_SCHEMES:
        return last or not letters or len(value) >= 8
    if _AUTH_PARAMETER_RE.fullmatch(value) is not None:
        return False
    return token or (letters and last)


def _auth_header_credential_span(
    text: str, scheme: str | None, low: int, raw_high: int
) -> tuple[int, int] | None:
    """The span of the value ``text[low:raw_high]`` of a header read
    outside string literals, when it is a credential (#586, #592).

    Sentence punctuation after the value ends the sentence, not the
    value: it is trimmed before the value is judged, and the span
    excludes it. The value is the last word of its header when a line
    break, a comma, a semicolon or a bar follows it, and a sentence end
    before that break counts as the break.
    """
    high = low + len(text[low:raw_high].rstrip(_SENTENCE_PUNCTUATION))
    last = _AUTH_VALUE_TAIL_RE.match(text, raw_high) is not None
    called = text[raw_high : raw_high + 1] == "("
    if _auth_value_is_credential(scheme, text[low:high], last=last, called=called):
        return low, high
    return None


def _auth_assignment_is_placeholder(text: str, value_start: int) -> bool:
    """Whether a line-start ``Authorization:`` value with a scheme is a
    placeholder (#592).

    Such a line is also judged as an assignment, whose prose reads any
    ``Bearer`` word as a credential. The header rules decide it instead,
    so the two readings agree. A bare value is left to the assignment
    rules, as a config line ``authorization: MIMRY_TEST_VALUE_123`` is
    (see the label-policy test).
    """
    value = _AUTH_LABEL_VALUE_RE.match(text, value_start)
    if value is None or value.group("scheme") is None:
        return False
    return _auth_header_credential_span(text, value.group("scheme"), *value.span("value")) is None


def _auth_header_value_spans(text: str) -> list[tuple[int, int]]:
    """The spans of credential values in Authorization headers (#586).

    Inside string literals the literals of one value are read together
    (#540): in ``"Authorization: Bearer " "tok"`` or
    ``"Authorization: Bearer tok-" + "en"`` a value runs across the
    pieces, and each piece it covers gives one span. A literal whose
    whole body is a known scheme and one value is judged with that
    scheme, as in ``"Bearer tomato-canary-1"`` (_SCHEME_LITERAL_RE,
    #593).

    Outside them a header is read with a known scheme after its colon,
    or with any other word before a token-shaped value
    (_PLAIN_AUTH_HEADER_RE, #586, #593), or in a Markdown table cell
    (_PLAIN_AUTH_TABLE_RE, #592). A header inside a literal is left to
    the literal pass, so each header is judged once.
    """
    hinted = _AUTH_HEADER_HINT_RE.search(text) is not None
    if not hinted and _AUTH_SCHEME_WORD_RE.search(text) is None:
        return []
    spans: list[tuple[int, int]] = []
    literal_starts: list[int] = []
    literal_ends: list[int] = []
    for chain in _literal_chains(text):
        literal_starts.append(chain[0].start())
        literal_ends.append(chain[-1].end())
        # Each piece as (start, end) in the joined text, and where its
        # body starts in the source.
        pieces: list[tuple[int, int, int]] = []
        bodies: list[str] = []
        total = 0
        for literal in chain:
            quoted = literal.group("literal")
            width = 3 if quoted[:3] in ('"""', "'''") else 1
            body_start = literal.start("literal") + width
            body_end = literal.end("literal") - width
            pieces.append((total, total + body_end - body_start, body_start))
            bodies.append(text[body_start:body_end])
            total += body_end - body_start + 1  # and the separator after it
        starts = [piece[0] for piece in pieces]
        joined = _LITERAL_SEPARATOR.join(bodies)
        # The values to redact, as (low, high) in the joined text.
        values: list[tuple[int, int]] = []
        if hinted:
            for header in _AUTH_HEADER_VALUE_RE.finditer(joined):
                value = header.group("value").replace(_LITERAL_SEPARATOR, "")
                low, high = header.span("value")
                last = _AUTH_VALUE_TAIL_RE.match(joined, high) is not None
                called = joined[high : high + 1] == "("
                scheme = header.group("scheme") or header.group("custom")
                if _auth_value_is_credential(scheme, value, last=last, called=called):
                    values.append((low, high))
        # One literal only. A scheme in one literal and a value in the
        # next, joined with ``+``, is ``"Bearer " + token``: kept. With
        # no header name the value must read as a token, so UI text such
        # as ``"Basic Plan"`` or ``"Key Vault"`` is kept.
        whole = _SCHEME_LITERAL_RE.fullmatch(joined) if len(chain) == 1 else None
        bare = whole.group("value").replace(_LITERAL_SEPARATOR, "") if whole else ""
        if (
            whole is not None
            and len(bare) >= _SCHEME_LITERAL_MIN_LENGTH
            and any(char.isdigit() or char in "-_.~+/=:" for char in bare)
            and _auth_value_is_credential(whole.group("scheme"), bare, last=True)
        ):
            values.append(whole.span("value"))
        for low, high in values:
            index = bisect.bisect_right(starts, low) - 1
            while index < len(pieces) and pieces[index][0] < high:
                piece_start, piece_end, source = pieces[index]
                lo, hi = max(low, piece_start), min(high, piece_end)
                if lo < hi:
                    spans.append((source + lo - piece_start, source + hi - piece_start))
                index += 1
    # The plain-text passes need the word Authorization (the hint).
    patterns = (_PLAIN_AUTH_HEADER_RE, _PLAIN_AUTH_TABLE_RE) if hinted else ()
    for pattern in patterns:
        for header in pattern.finditer(text):
            index = bisect.bisect_right(literal_starts, header.start()) - 1
            if index >= 0 and header.start() < literal_ends[index]:
                continue
            groups = header.groupdict()
            span = _auth_header_credential_span(
                text, groups.get("scheme") or groups.get("custom"), *header.span("value")
            )
            if span is not None:
                spans.append(span)
    spans.sort()
    return spans


def _redact_auth_headers(text: str) -> str:
    return _splice(text, [(start, end, REDACTED) for start, end in _auth_header_value_spans(text)])


def _splice(text: str, edits: list[tuple[int, int, str]]) -> str:
    """``text`` with each (start, end, replacement) edit made. The edits
    are in order and do not overlap."""
    if not edits:
        return text
    pieces: list[str] = []
    pos = 0
    for start, end, replacement in edits:
        pieces.append(text[pos:start])
        pieces.append(replacement)
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)


def _sub_text_parts(match: re.Match[str]) -> tuple[str, str, str]:
    """The opening, the text judged on its own, and the closing of a
    comment or a triple-quoted string.
    """
    whole = match.group(0)
    if match.group("quote") is not None:
        return whole[:3], whole[3:-3], whole[-3:]
    marker = "//" if whole.startswith("//") else "#"
    return marker, whole[len(marker) :], ""


def _sub_text_is_sensitive(match: re.Match[str], *, code: bool, depth: int) -> bool:
    _opening, body, _closing = _sub_text_parts(match)
    if _SENSITIVE_HINT_RE.search(body) is None:
        return False
    return _contains_sensitive_text(body, code=code, depth=depth + 1)


def _redact_sub_text(match: re.Match[str], *, depth: int) -> str:
    opening, body, closing = _sub_text_parts(match)
    if _SENSITIVE_HINT_RE.search(body) is None:
        return match.group(0)
    return opening + _redact_text(body, depth=depth + 1) + closing


def _hash_entry_is_sensitive(
    match: re.Match[str], *, code: bool = False, scanner: _Scanner
) -> bool:
    label = match.group("label")
    if not _is_sensitive_label(label, match.group("value")):
        return False
    return _value_is_credential(
        match.string,
        match.start("value"),
        match.group("value"),
        "=>",
        label,
        code=code,
        scanner=scanner,
    )


def _redact_hash_entries(text: str) -> str:
    scanner = _Scanner(text, redaction=True)

    def render(match: re.Match[str]) -> str:
        if not _hash_entry_is_sensitive(match, scanner=scanner):
            return match.group(0)
        return match.group("prefix") + _redacted_secret(match.group("value"))

    return HASH_ENTRY_RE.sub(render, text)


def _redact_yaml_block(match: re.Match[str]) -> str:
    body = match.group("body")
    if not _holds_text(body) or not _is_sensitive_label(match.group("label"), body):
        return match.group(0)
    # Each body line keeps its indent and its line break, so the block
    # keeps its line count and stays valid YAML.
    return (
        f"{match.group('indent')}{match.group('label')}{match.group('header')}"
        f"{_redacted_multiline(body, comments=False)}"
    )


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def _yaml_quote_on_line(
    content: str, start: int, quote: str, *, require_trailer: bool
) -> tuple[int | None, bool]:
    """Scan one physical line once.

    Looks for an unescaped quote and a YAML trailer.
    """

    index = start
    saw_unescaped_quote = False
    while index < len(content):
        char = content[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if char != quote:
            index += 1
            continue
        if quote == "'" and index + 1 < len(content) and content[index + 1] == quote:
            index += 2
            continue

        saw_unescaped_quote = True
        if not require_trailer:
            return index, True

        trailer = index + 1
        while trailer < len(content) and content[trailer] in " \t":
            trailer += 1
        if trailer == len(content) or content[trailer] == "#":
            return index, True
        index += 1
    return None, saw_unescaped_quote


def _yaml_quoted_multiline_assignments(text: str):
    """Yield sensitive quoted YAML scalar spans in linear time."""

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    index = 0
    while index < len(lines):
        first, _first_newline = _split_line_ending(lines[index])
        match = YAML_QUOTED_MULTILINE_ASSIGNMENT_START_RE.match(first)
        if match is None or not _has_credential_words(match.group("label")):
            index += 1
            continue

        quote = match.group("quote")
        opening_end = match.end("quote")
        closing, saw_quote = _yaml_quote_on_line(first, opening_end, quote, require_trailer=True)
        if closing is not None:
            index += 1
            continue
        if saw_quote:
            # A same-line quote with a non-YAML trailer (notably a
            # TSX/object comma) is not a YAML multiline scalar. Do not
            # consume following independent assignments while trying to
            # reinterpret it.
            index += 1
            continue

        cursor = index + 1
        while cursor < len(lines):
            body, newline = _split_line_ending(lines[cursor])
            closing, _saw_quote = _yaml_quote_on_line(body, 0, quote, require_trailer=True)
            if closing is not None:
                trailer = body[closing + 1 :]
                replacement = f"{first[:opening_end]}{REDACTED}{quote}{trailer}{newline}"
                yield (
                    offsets[index],
                    offsets[cursor] + len(lines[cursor]),
                    match.group("label"),
                    replacement,
                )
                cursor += 1
                break
            cursor += 1
        else:
            replacement = f"{first[:opening_end]}{REDACTED}{quote}"
            yield offsets[index], len(text), match.group("label"), replacement
        index = cursor


def _redact_yaml_quoted_multiline(text: str) -> str:
    matches = [
        match
        for match in _yaml_quoted_multiline_assignments(text)
        if _is_sensitive_label(match[2], text[match[0] : match[1]])
    ]
    if not matches:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end, _label, replacement in matches:
        parts.extend((text[cursor:start], replacement))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _sensitive_unclosed_quoted_values(text: str):
    """Yield (quote, close, end) for each sensitive quoted value whose
    quote does not close on its line.

    ``quote`` is where the value opens and ``end`` is past its closing
    quote on a later line. When no closing quote follows within the
    span limit, ``close`` is None and ``end`` is the end of the text,
    which fails closed. A value that closes on its own line is left to
    ASSIGNMENT_RE.
    """
    for match in UNCLOSED_QUOTED_ASSIGNMENT_START_RE.finditer(text):
        quote_at = match.end() - 1
        found = _quote_close(text, quote_at + 1, text[quote_at])
        if found is not None:
            close, crossed = found
            if not crossed:
                continue  # closes on its own line, which ASSIGNMENT_RE judges
        else:
            # Not closed within the span limit. A last line with no line
            # break after it is left to ASSIGNMENT_RE.
            rest = text[quote_at + 1 : quote_at + 1 + UNCLOSED_QUOTED_SPAN_LIMIT]
            if len(text) - quote_at - 1 <= UNCLOSED_QUOTED_SPAN_LIMIT and not (
                "\n" in rest or "\r" in rest
            ):
                continue
            close = None
        if close is None:
            # The value's end is unknown, so the label alone decides.
            end = len(text)
            sensitive = _is_sensitive_label(match.group("label"), text[quote_at:end])
        else:
            # Under a sensitive label the whole value is redacted line
            # by line, as a single-line value is. A value already
            # redacted that way is not judged again.
            end = close + 1
            value = text[quote_at + 1 : close]
            sensitive = _is_sensitive_label(match.group("label"), value) and _holds_text(value)
        if sensitive:
            yield quote_at, close, end


_MARKER_OR_BLANK_RE = re.compile(rf"{re.escape(REDACTED)}|\s")


def _holds_text(value: str) -> bool:
    """Whether a value holds anything besides the marker and blanks."""
    return _MARKER_OR_BLANK_RE.sub("", value) != ""


def _triple_quoted_body(text: str, match: re.Match[str]) -> str:
    """The text of a triple-quoted string, from the quote the match ends
    on to its closing quote, or to the end of the text when it never
    closes."""
    close = text.find(match.group("quote"), match.end())
    return text[match.end() : len(text) if close < 0 else close]


def _redacted_lines(value: str) -> str:
    """The value with each run of text on a line replaced by the marker.

    Every line break stays, so the line count does not change.
    """
    return re.sub(r"[^\r\n]+", REDACTED, value)


# A letter or digit, as str.isalnum counts them; a single search is much
# faster than a Python loop over each character of a long value.
_ALNUM_RE = re.compile(r"[^\W_]")
# A comment that starts a piece of a value's line.
_VALUE_COMMENT_RE = re.compile(r"[ \t]*(?:#|//)")
# A line break, kept between the lines of a value.
_LINE_BREAK_SPLIT_RE = re.compile(r"(\r\n|\r|\n)")
# A triple quote, which opens or closes a string that runs over lines.
_TRIPLE_QUOTE_RE = re.compile(r"\"\"\"|'''")
# The delimiters a value's line may start with, and may end with.
_VALUE_LEAD_DELIMITERS = "\"'`([{)]}"
_VALUE_TRAIL_DELIMITERS = "\"'`([{)]},;"


def _value_line_bounds(text: str) -> tuple[int, int]:
    """Where the body of a value's line starts and ends. Its leading
    spaces and delimiters, and its trailing delimiters and spaces, are
    not body. Each end is scanned once, so a long run of spaces stays
    linear."""
    start = 0
    while start < len(text) and text[start] in " \t":
        start += 1
    while start < len(text) and text[start] in _VALUE_LEAD_DELIMITERS:
        start += 1
    end = len(text.rstrip(" \t"))
    while end > start and text[end - 1] in _VALUE_TRAIL_DELIMITERS:
        end -= 1
    return start, end


def _redacted_multiline(value: str, *, comments: bool = True) -> str:
    """The value with the text of each of its lines redacted, as in a
    ``password = (`` block, a triple-quoted string or a YAML ``|``
    block.

    Line breaks, indents and delimiters stay, so the value keeps its
    line count and still parses. A line with no letter or digit has no
    text to redact and is kept.

    ``comments`` says whether a line starting with ``#`` or ``//`` is a
    comment, as in code. In a YAML block scalar, and inside a
    triple-quoted string, such a line is text to redact.
    """
    parts = _LINE_BREAK_SPLIT_RE.split(value)
    state: str | None = None  # the triple quote a string is open with
    # Line breaks sit at odd indexes, so the lines are the even ones.
    for index in range(0, len(parts), 2):
        line = parts[index]
        content = not comments or state is not None
        parts[index] = _redacted_value_line(line, comments=not content)
        if not comments or (state is None and _VALUE_COMMENT_RE.match(line)):
            continue
        state = _triple_state_after(line, state)
    return "".join(parts)


def _triple_state_after(line: str, state: str | None) -> str | None:
    """The triple quote a string is open with after this line, or
    None."""
    if '"""' not in line and "'''" not in line:
        return state
    for found in _TRIPLE_QUOTE_RE.finditer(line):
        if state is None:
            state = found.group()
        elif found.group() == state:
            state = None
    return state


# The quotes a string literal in a value's line may open with.
_LITERAL_QUOTE_RE = re.compile(r"[\"'`]")
# The prefix letters a string literal may have before its quote.
_LITERAL_PREFIXES = "rRbBuUfF"


def _value_literal_spans(line: str) -> list[tuple[int, int]]:
    """The non-empty string literals of a line of a value, as spans. A
    literal runs to the next quote of its kind that is not escaped, and
    a quote whose literal never closes on the line makes every later
    quote of its kind fail too. So one pass finds them all, in linear
    time, where a regex restarted at each quote was quadratic."""
    spans: list[tuple[int, int]] = []
    dead: set[str] = set()
    pos = 0
    index = 0
    while (found := _LITERAL_QUOTE_RE.search(line, index)) is not None:
        index = found.start()
        quote = line[index]
        if quote in dead:
            index += 1
            continue
        close = _literal_close(line, index)
        if close is None:
            dead.add(quote)
            index += 1
        elif close == index + 1:
            index += 1  # an empty pair is not a literal
        else:
            start = index
            while start > pos and index - start < 2 and line[start - 1] in _LITERAL_PREFIXES:
                start -= 1
            spans.append((start, close + 1))
            pos = index = close + 1
    return spans


def _literal_close(line: str, index: int) -> int | None:
    """The index of the quote that closes the string opened at
    ``index``, or None when the line ends first."""
    quote = line[index]
    pos = index + 1
    while pos < len(line):
        char = line[pos]
        if char == "\\":
            if pos + 1 == len(line):
                return None
            pos += 2
            continue
        if char == quote:
            return pos
        pos += 1
    return None


# The body of a quote-delimited literal, as the patterns above read it.
# A backslash escapes the next character. The body stops before a quote
# of its own kind, a line break, or a backslash that escapes nothing.
_LITERAL_BODY_RES = {
    '"': re.compile(r"(?:\\.|[^\"\\\r\n])*"),
    "'": re.compile(r"(?:\\.|[^'\\\r\n])*"),
    "`": re.compile(r"(?:\\.|[^`\\])*"),
}
_DOUBLE_OR_SINGLE_QUOTE_RE = re.compile(r"[\"']")


def _literal_finditer(
    pattern: re.Pattern[str],
    text: str,
    *,
    quotes: re.Pattern[str],
    prefixed: bool = False,
) -> Iterator[re.Match[str]]:
    """``pattern.finditer(text)``, found in one pass over the quotes.

    ``pattern`` matches a quote-delimited literal at a quote, after up
    to two prefix letters when ``prefixed``. Tried from every quote, a
    literal that does not close is scanned to where it stops, and so is
    each later quote of its kind inside it. Those quotes are escaped, so
    each fails where the first one fails. A quote before the end of an
    earlier literal of its kind is therefore skipped, and the pass is
    linear.
    """
    pos = 0
    # Per quote kind, the index before which a quote of that kind lies
    # inside an earlier literal that did not match.
    fails_before: dict[str, int] = {}
    for found in quotes.finditer(text):
        at = found.start()
        quote = found.group()
        if at < pos or at < fails_before.get(quote, 0):
            continue
        start = at
        if prefixed:
            while start > pos and at - start < 2 and text[start - 1] in _STRING_PREFIX_CHARS:
                start -= 1
        match = pattern.match(text, start)
        if match is None:
            fails_before[quote] = _LITERAL_BODY_RES[quote].match(text, at + 1).end()
            continue
        yield match
        pos = match.end()


def _redacted_value_line(line: str, *, comments: bool = True) -> str:
    """One line of a value. Its string literals are redacted whole, and
    the text between them is redacted as one piece, so each quote stays
    paired."""
    pieces: list[str] = []
    pos = 0
    # An empty pair is not a literal here: on a line of a triple-quoted
    # string, the first two quotes of ``"""`` are not one.
    for start, end in _value_literal_spans(line):
        pieces.append(_redacted_value_text(line[pos:start], comments=comments))
        pieces.append(_redacted_secret(line[start:end]))
        pos = end
    pieces.append(_redacted_value_text(line[pos:], comments=comments))
    return "".join(pieces)


def _redacted_value_text(text: str, *, comments: bool = True) -> str:
    # A comment after a value's literal is judged as text of its own
    # (see SUB_TEXT_RE), so it stays, as a line of code does.
    if comments and _VALUE_COMMENT_RE.match(text):
        return text
    # A line holding only the marker is already redacted. Wrapping it
    # again would grow the marker on every pass.
    if _ALNUM_RE.search(text.replace(REDACTED, "")) is None:
        return text
    start, end = _value_line_bounds(text)
    if _ALNUM_RE.search(text, start, end) is None:
        return text
    return text[:start] + REDACTED + text[end:]


def _quote_close(text: str, start: int, quote: str) -> tuple[int, bool] | None:
    """The quote that closes a string opened before ``start``, as its
    index and whether a line break comes before it. None when no such
    quote is within the span limit."""
    index = start
    limit = min(len(text), start + UNCLOSED_QUOTED_SPAN_LIMIT)
    crossed = False
    while index < limit:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char in "\r\n":
            crossed = True
        elif char == quote:
            return index, crossed
        index += 1
    return None


def _redact_unclosed_quoted_values(text: str) -> str:
    """Redact a sensitive quoted value through its closing quote on a
    later line, line by line.

    Each line of the value becomes the marker and every line break
    stays, so the redacted text has the source's line count and
    indexable_bytes keeps it. A value that never closes is cut at the
    end of the text, which fails closed.
    """
    pieces: list[str] = []
    cursor = 0
    for quote_at, close, end in _sensitive_unclosed_quoted_values(text):
        quote = text[quote_at]
        if close is None:
            body, closing = REDACTED, ""
        else:
            body, closing = _redacted_lines(text[quote_at + 1 : close]), quote
        pieces.extend((text[cursor:quote_at], quote + body + closing))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _redact_unclosed_multiline_assignment(match: re.Match[str]) -> str:
    body = match.group("body")
    if not _is_sensitive_label(match.group("label"), body) or match.group("quote") in body:
        return match.group(0)
    return f"{match.group('prefix')}{match.group('quote')}{_redacted_unclosed_body(body)}"


def _redacted_unclosed_body(body: str) -> str:
    """The body of a triple-quoted value that never closes, redacted as
    a closed block is: each run of text on a line becomes the marker and
    every line break stays. A body with no text keeps its breaks behind
    the marker, and is left as it is once it holds the marker, so a
    second pass does not grow it."""
    if _holds_text(body):
        return _redacted_lines(body)
    return body if REDACTED in body else REDACTED + body


def sanitize_query(value: str) -> str:
    """Sanitize a user-controlled lookup surface before internal use."""

    return redact_sensitive_text(value)


def markdown_inline(value: Any) -> str:
    """Render untrusted data as one Markdown-safe display line.

    Filenames, graph labels, and indexed excerpts can contain newlines,
    terminal controls, or backticks. They are valid data on some
    filesystems, but must never create headings, lists, code spans, or
    terminal escapes in generated agent-facing Markdown.
    """

    text = redact_sensitive_text(str(value))
    rendered: list[str] = []
    for char in text:
        codepoint = ord(char)
        if char == "\n":
            rendered.append("\\n")
        elif char == "\r":
            rendered.append("\\r")
        elif char == "\t":
            rendered.append("\\t")
        elif codepoint < 32 or codepoint == 127:
            rendered.append(f"\\x{codepoint:02x}")
        elif char == "`":
            rendered.append("&#96;")
        elif char == "&":
            rendered.append("&amp;")
        elif char == "<":
            rendered.append("&lt;")
        elif char == ">":
            rendered.append("&gt;")
        else:
            rendered.append(char)
    return "".join(rendered)


def sanitize_data(value: Any, _redacted: dict[str, str] | None = None) -> Any:
    """Recursively redact strings at persistence/API boundaries."""
    # Records repeat the same strings heavily (IDs reused as edge
    # endpoints, kinds, languages); redact each distinct one once per
    # call.
    redacted = {} if _redacted is None else _redacted
    if isinstance(value, str):
        if value not in redacted:
            redacted[value] = redact_sensitive_text(value)
        return redacted[value]
    if isinstance(value, dict):
        return {key: sanitize_data(item, redacted) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_data(item, redacted) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_data(item, redacted) for item in value)
    return value


def contains_sensitive_data(value, *, code: bool = False) -> bool:
    # Adapter output repeats the same strings heavily (IDs reused as
    # edge endpoints, kinds, languages): about 12x on a typical repo.
    # Scan each distinct one once.
    strings: set[str] = set()
    _collect_strings(value, strings)
    return any(contains_sensitive_text(text, code=code) for text in strings)


def _collect_strings(value, strings: set[str]) -> None:
    if isinstance(value, str):
        strings.add(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect_strings(key, strings)
            _collect_strings(item, strings)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_strings(item, strings)


def stream_contains_sensitive_content(
    handle: BinaryIO, *, strict_text: bool = False, code: bool = False
) -> bool:
    """Scan an opened file with bounded memory, preserving cross-chunk
    matches.

    ``strict_text`` is used at the graph-input trust boundary. Invalid
    UTF-8 and NUL-bearing/binary-ambiguous input are rejected there
    rather than silently decoded or trusted based on a filename
    extension.
    """

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict" if strict_text else "ignore")
    overlap = ""
    for raw in iter(lambda: handle.read(STREAM_CHUNK_BYTES), b""):
        if strict_text and any(byte < 32 and byte not in (9, 10, 13) for byte in raw):
            raise ValueError("source is binary or has an unknown text encoding")
        # Sparse files and binary-ish generated artifacts can contain
        # long NUL runs before ordinary UTF-8 text. Drop NULs so
        # line-anchored checks also catch UTF-16-style ASCII without
        # loading the whole file.
        try:
            decoded = decoder.decode(raw.replace(b"\x00", b""))
        except UnicodeDecodeError as exc:
            raise ValueError("source is binary or has an unknown text encoding") from exc
        text = overlap + decoded
        if contains_sensitive_text(text, code=code):
            return True
        overlap = text[-STREAM_OVERLAP_CHARS:]
    try:
        tail = overlap + decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise ValueError("source is binary or has an unknown text encoding") from exc
    return contains_sensitive_text(tail, code=code)


def _stream_contains_sensitive_content(path: Path) -> bool:
    """Scan a whole file in bounded memory; keep cross-chunk matches."""

    try:
        with path.open("rb") as handle:
            return stream_contains_sensitive_content(handle, code=is_code_path(path))
    except OSError:
        return True


def opened_file_has_sensitive_content(path: Path, handle: BinaryIO) -> bool:
    """Classify the exact open file behind a no-follow handoff copy."""

    # Extension is not a security boundary: an unlisted text format such
    # as ``.properties`` must receive the same byte-level classification
    # and secret scan as Python/JSON/TOML. Env examples retain their
    # indexing exemption, but are still required to be unambiguous UTF-8
    # before the graph sees them.
    sensitive = stream_contains_sensitive_content(
        handle, strict_text=True, code=is_code_path(path)
    )
    return False if _is_env_example(path) else sensitive


def has_sensitive_content(path: Path, data: bytes | None = None) -> bool:
    """Check if file has sensitive content.

    Pass `data` (bytes) to use already-captured content instead of
    reopening. If data is None, will read from path (for non-indexing
    use).
    """
    if _is_env_example(path):
        return False
    if path.suffix.lower() not in TEXT_EXTS and path.name not in {"config", "credentials"}:
        return _unlisted_text_is_sensitive(path, data)
    if data is not None:
        import io

        return stream_contains_sensitive_content(io.BytesIO(data), code=is_code_path(path))
    return _stream_contains_sensitive_content(path)


def _unlisted_text_is_sensitive(path: Path, data: bytes | None) -> bool:
    """Classify a text format outside TEXT_EXTS by its content.

    Such formats are ``.conf`` and ``Dockerfile``. A listed one is
    classified the same way.

    Binary input (the strict text sniff) is not a text format, so it is
    not scanned here and keeps the name rules.
    """
    try:
        if data is not None:
            import io

            return opened_file_has_sensitive_content(path, io.BytesIO(data))
        with path.open("rb") as handle:
            return opened_file_has_sensitive_content(path, handle)
    except ValueError:
        return False
    except OSError:
        return True


def _raw_file_has_sensitive_content(path: Path) -> bool:
    """Inspect generated/specially named text.

    Env-example exemptions do not apply.
    """

    return _stream_contains_sensitive_content(path)


def _has_heavy_ignore(parts) -> bool:
    """Match policy directory names regardless of filesystem case."""

    global HEAVY_IGNORES_CASEFOLD, _HEAVY_IGNORES_SOURCE
    if HEAVY_IGNORES is not _HEAVY_IGNORES_SOURCE:
        HEAVY_IGNORES_CASEFOLD = frozenset(part.casefold() for part in HEAVY_IGNORES)
        _HEAVY_IGNORES_SOURCE = HEAVY_IGNORES
    return any(str(part).casefold() in HEAVY_IGNORES_CASEFOLD for part in parts)


def should_ignore_path(path, root):
    """Apply filename/directory policy without reopening the file."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return _has_heavy_ignore(parts) or is_sensitive(path)


def should_ignore(path, root):
    return should_ignore_path(path, root) or has_sensitive_content(path)


@functools.cache
def _install_folder_marks() -> tuple[str, ...]:
    """The project skill folders as casefolded ``/folder/`` marks."""
    # Imported here: the installer is a large module and only the
    # path policy needs its table.
    from .installer import project_skill_folders

    return tuple(f"/{folder.casefold()}/" for folder in sorted(project_skill_folders()))


def in_mimry_install_folder(path: str) -> bool:
    """True for a folder a MIMRY install writes its skill into, or a
    path inside one, such as ``.claude/skills/mimry/SKILL.md``.

    The install keeps its ``.mimry_version`` and ``.mimry_state.json``
    beside the skill, so they go with the folder. A user's other skills
    and ``.claude/settings.json`` are outside these folders.
    """

    padded = f"/{path.strip('/').casefold()}/"
    return any(mark in padded for mark in _install_folder_marks())


def path_has_ignored_part(path: str | Path) -> bool:
    """Classify artifact/index paths without opening their source."""

    normalized = str(path).replace("\\", "/")
    return _has_heavy_ignore(
        part for part in normalized.split("/") if part not in {"", "."}
    ) or in_mimry_install_folder(normalized)


def text_mentions_ignored_path(text: str) -> bool:
    """Detect ignored path components in prose, not ordinary words."""

    normalized = text.replace("\\", "/")
    return any(
        re.search(rf"(?:^|[/\s`'\"(\[{{:=]){re.escape(part)}/", normalized, flags=re.IGNORECASE)
        for part in HEAVY_IGNORES
    )


def filter_index_records(
    files: list[dict], symbols: list[dict] | None = None
) -> tuple[list[dict], list[dict]]:
    """Hide records newly excluded by policy, even before a rebuild."""

    visible_files = [
        record for record in files if not path_has_ignored_part(record.get("rel_path", ""))
    ]
    visible_ids = {record.get("file_id") for record in visible_files}
    visible_symbols = [
        record for record in (symbols or []) if record.get("file_id") in visible_ids
    ]
    return visible_files, visible_symbols


def is_text(path):
    return path.suffix.lower() in TEXT_EXTS


def root_contains_sensitive_content(root: Path) -> bool:
    """Detect ordinary secret-bearing text before a root is indexed."""

    safe_root(root)
    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            parts = path.relative_to(root).parts
            if contains_sensitive_text(path.name):
                return True
            if _has_heavy_ignore(parts):
                continue
            if is_sensitive(path) or _is_env_example(path):
                if _raw_file_has_sensitive_content(path):
                    return True
                continue
            if has_sensitive_content(path):
                return True
        except OSError:
            # Unreadable source is not safe to pass to a separate
            # indexer.
            return True
    return False


def tree_contains_sensitive_content(root: Path) -> bool:
    """Validate MIMRY-owned artifacts before they are exposed."""

    if not root.exists():
        return False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if contains_sensitive_text(path.name) or _raw_file_has_sensitive_content(path):
            return True
    return False
