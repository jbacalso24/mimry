from __future__ import annotations

import codecs
import fnmatch
import os
import re
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
    r"(?ims)(?P<boundary>^|(?<=[{(,;])|(?P<spaced>(?<=[ \t])(?![ \t])))"
    # A PHP or Perl variable keeps its sigil in front of the label.
    # A quoted shell variable (``"$password"``) has its quote in front
    # of the ``$``.
    r"(?P<prefix>[ \t]*(?:export\s+)?(?:(?:const|let|var)\s+)?(?P<label_quote>['\"]?)\$?"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)"
    # A comparison is an operator of its own, so the first ``=`` of
    # ``==`` never reads as an assignment (#473). ``!=`` and ``!==``
    # are comparisons too (#500).
    # ``:=`` (Go, Python's walrus) is one operator too, so its ``=``
    # is not read as the value. A hash arrow (``'key' => "..."``) reads
    # as ``=`` with a value that starts at ``>``; see HASH_ENTRY_RE.
    r"[ \t]*(?P<operator>===|==|!==|!=|(?(spaced)=|(?::=|[=:])))[ \t]*"
    # A value may start on the next line when that line is an indented
    # scalar (``password:\n  hunter2``); a nested ``key: value`` is its
    # own assignment.
    r"(?:\r?\n(?=[ \t]+(?:[^\s#:\"'][^:\r\n]*(?:\r?\n|$)|"
    r"\"(?:\\.|[^\"\\\r\n])*\"(?![ \t]*:)|'(?:\\.|[^'\\\r\n])*'(?![ \t]*:)))[ \t]+)?)"
    r"(?P<value>\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\\r\n])*\"|"
    r"'(?:\\.|[^'\\\r\n])*'|"
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
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"(?is)-----BEGIN (?P<label>(?:RSA |EC |OPENSSH |DSA |ENCRYPTED |)PRIVATE KEY)-----"
    r".*?-----END (?P=label)-----"
)
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
_VALUE_END_RE = re.compile(r"[ \t]*(?:[,;)\]}\r\n]|\\(?=[nrt\"'])|\Z|(?<=[ \t])(?:#|//))")
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
    if not text:
        return False
    if (
        PRIVATE_KEY_BLOCK_RE.search(text)
        or STANDALONE_SECRET_RE.search(text)
        or SENSITIVE_VALUE_RE.search(text)
    ):
        return True
    # Every remaining detector is an assignment form whose pattern
    # requires a literal "=" or ":". Most filenames and many short
    # strings have neither.
    if "=" not in text and ":" not in text:
        return False
    scanner = _Scanner(text)
    if any(
        _spaced_assignment_value_is_sensitive(match, code=code, scanner=scanner)
        for match in SPACED_SENSITIVE_ASSIGNMENT_RE.finditer(text)
    ):
        return True
    if any(
        _is_sensitive_label(match.group("label"), match.group("body"))
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
        _is_sensitive_label(match.group("label"))
        for match in MULTILINE_QUOTED_ASSIGNMENT_START_RE.finditer(text)
    ):
        return True
    if any(
        _literal_comparison_is_sensitive(match, code=code)
        for match in LITERAL_COMPARED_RE.finditer(text)
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

    redacted = PRIVATE_KEY_BLOCK_RE.sub(REDACTED, text)
    redacted = STANDALONE_SECRET_RE.sub(REDACTED, redacted)
    return SENSITIVE_VALUE_RE.sub(REDACTED, redacted)


def redact_sensitive_text(text: str) -> str:
    """Remove high-confidence credentials from user and adapter text."""

    redacted = redact_standalone_secrets(text)
    redacted = _redact_unclosed_quoted_values(redacted)
    redacted = _redact_values(redacted, SPACED_SENSITIVE_ASSIGNMENT_RE, _redact_spaced_assignment)
    redacted = YAML_BLOCK_ASSIGNMENT_RE.sub(_redact_yaml_block, redacted)
    redacted = _redact_yaml_quoted_multiline(redacted)
    redacted = _redact_hash_entries(redacted)
    redacted = _redact_values(redacted, ASSIGNMENT_RE, _redact_assignment)
    redacted = LITERAL_COMPARED_RE.sub(_redact_literal_comparison, redacted)
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
    # The marker, with the closing quote _redacted_secret writes back.
    if (
        not candidate
        or candidate in {REDACTED, REDACTED + '"', REDACTED + "'"}
        or candidate[0] == "|"
    ):
        return False  # nothing assigned, already redacted, or a YAML block (classified above)
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
        # Punctuation alone (``...``, ``***``, ``---``) has no letter or
        # digit to be a credential. A bracket or quote opens an
        # expression (``API_KEY = (``), so it is not exempt.
        or (
            not any(char.isalnum() for char in candidate)
            and not any(char in "([{\"'" for char in candidate)
        )
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


def _has_credential_query(url: str) -> bool:
    """Whether a URL's query or fragment has a credential parameter.

    The value must not be a placeholder.
    """
    for part in re.split(r"[?#&;]", url)[1:]:
        name, separator, value = part.partition("=")
        if (
            separator
            and _is_credential_param(name)
            and not (
                value == ""
                or value == REDACTED
                or _PLACEHOLDER_RE.fullmatch(value)
                or _REFERENCE_RE.fullmatch(value)
            )
        ):
            return True
    return False


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

    def __init__(self, text: str, *, redaction: bool = False) -> None:
        self.text = text
        self.redaction = redaction
        self.left = 4 * len(text) + 1024

    def scan(self, start: int, *, strict: bool = False) -> _Value | None:
        if self.left <= 0:
            return None
        value = _scan_value(
            self.text, start, every_literal=strict, by_line=strict and not self.redaction
        )
        self.left -= value.end - start + 1
        return value


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
    if code and (
        ("f" in literal["prefix"].lower() and "{" in text)
        or (literal["literal"][0] == "`" and "${" in text)
    ):
        return False  # a formatted string or template literal is an expression
    if code and literal["literal"][0] == "`" and not any(char.isalnum() for char in text):
        return False  # tail of a nested template like `)}` has no letters or digits
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


def _redact_values(text: str, pattern: re.Pattern[str], render) -> str:
    """Replace each match ``render`` redacts, through the end of its
    value.

    A pattern stops capturing a value at its first comma or closing
    bracket, and a quoted value at its closing quote. Where the value
    goes on, replacing just the capture left ``or "fallback"``, later
    arguments, later elements and the rest of ``KEY=a,b`` in place.
    """
    scanner = _Scanner(text, redaction=True)
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


def _assignment_match_is_sensitive(
    match: re.Match[str], *, code: bool = False, scanner: _Scanner
) -> bool:
    label = match.group("label")
    if not _is_sensitive_label(label, match.group("value")):
        return False
    if match.group("operator") in COMPARISON_OPERATORS:
        return _comparison_is_credential(match.string, match.start("value"), code=code)
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
    stripped = match.group("value").strip()
    arrow = ""
    if stripped.startswith(">"):  # ``'key' => 'value'``
        arrow, stripped = "> ", stripped[1:].strip()
    return f"{match.group('prefix')}{arrow}{_redacted_secret(stripped)}"


def _literal_comparison_is_sensitive(match: re.Match[str], *, code: bool = False) -> bool:
    if not _is_sensitive_label(match.group("label")):
        return False
    return _comparison_is_credential(match.string, match.start("literal"), code=code)


def _redact_literal_comparison(match: re.Match[str]) -> str:
    if not _literal_comparison_is_sensitive(match):
        return match.group(0)
    literal = match.group("literal")
    return _redacted_secret(literal) + match.group(0)[len(literal) :]


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
    if not _is_sensitive_label(match.group("label"), match.group("body")):
        return match.group(0)
    newline = "\r\n" if "\r\n" in match.group("header") else "\n"
    return (
        f"{match.group('indent')}{match.group('label')}{match.group('header')}{match.group('indent')}"
        f"  {REDACTED}{newline}"
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


def _redacted_lines(value: str) -> str:
    """The value with each run of text on a line replaced by the marker.

    Every line break stays, so the line count does not change.
    """
    return re.sub(r"[^\r\n]+", REDACTED, value)


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
    if not _is_sensitive_label(match.group("label"), match.group("body")) or match.group(
        "quote"
    ) in match.group("body"):
        return match.group(0)
    return f"{match.group('prefix')}{match.group('quote')}{REDACTED}"


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


def path_has_ignored_part(path: str | Path) -> bool:
    """Classify artifact/index paths without opening their source."""

    normalized = str(path).replace("\\", "/")
    return _has_heavy_ignore(part for part in normalized.split("/") if part not in {"", "."})


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
