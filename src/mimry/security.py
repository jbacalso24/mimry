from __future__ import annotations

import fnmatch
import codecs
import re
from pathlib import Path
from typing import Any, BinaryIO

from .constants import HEAVY_IGNORES, SENSITIVE_PATTERNS, TEXT_EXTS


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

# High-confidence standalone credential formats. These intentionally use fake-safe
# structural matching: tests use inert canaries and never real credentials.
STANDALONE_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|gh[oprsu]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|"
    r"sk_live_[0-9A-Za-z]{16,}"
    r")(?![A-Za-z0-9])"
)


def safe_root(root: Path):
    r = root.resolve()
    home = Path.home().resolve()
    if r == Path(r.anchor):
        raise ValueError(f"Refusing to index filesystem root: {r}")
    if r == home:
        raise ValueError(f"Refusing to index entire home without explicit target scope: {r}")
    for relative in SENSITIVE_HOME_DIRS:
        sensitive = (home / relative).resolve()
        if r == sensitive or sensitive in r.parents:
            raise ValueError(f"Refusing to index sensitive user-data root: {r}")
    return r


ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", "env.example"}
# Keep label classification separate from assignment syntax. Substring matching
# (for example, ``AUTH`` in ``AUTHOR``) overblocks ordinary metadata and prose.
# Token/camel-case matching catches credential-like configuration labels while
# requiring an explicit assignment boundary before any value is classified.
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
# Assignment boundaries are deliberately structural rather than line-only. This
# catches object/JSON/code members after ``{``, ``,`` or ``;`` without treating a
# credential word embedded in ordinary prose as an assignment. Values stop at
# the matching config delimiter; quoted values may contain delimiters, and TOML
# / Python-style triple-quoted values may span lines.
ASSIGNMENT_RE = re.compile(
    r"(?ims)(?P<boundary>^|(?<=[{,;]))"
    r"(?P<prefix>[ \t]*(?:export\s+)?(?P<label_quote>['\"]?)"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)\s*(?P<operator>=|:)\s*)"
    r"(?P<value>\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\\r\n])*\"|"
    r"'(?:\\.|[^'\\\r\n])*'|[^,{;}\r\n]*)(?P<newline>\r?\n|$)?"
)
MULTILINE_QUOTED_ASSIGNMENT_START_RE = re.compile(
    r"(?im)(?:^|(?<=[{,;]))[ \t]*(?:export\s+)?(?P<label_quote>['\"]?)"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)\s*(?:=|:)\s*(?P<quote>\"\"\"|''')"
)
UNCLOSED_MULTILINE_QUOTED_ASSIGNMENT_RE = re.compile(
    r"(?ims)(?P<boundary>^|(?<=[{,;]))"
    r"(?P<prefix>[ \t]*(?:export\s+)?(?P<label_quote>['\"]?)"
    r"(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)(?P=label_quote)\s*(?:=|:)\s*)"
    r"(?P<quote>\"\"\"|''')(?P<body>.*)$"
)
YAML_BLOCK_ASSIGNMENT_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<label>[A-Za-z_][A-Za-z0-9_.-]*)"
    r"(?P<header>\s*:\s*[|>][+-]?[^\r\n]*\r?\n)"
    r"(?P<body>(?:(?P=indent)[ \t]+[^\r\n]*(?:\r?\n|$)|[ \t]*\r?\n)*)"
)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"(?is)-----BEGIN (?P<label>(?:RSA |EC |OPENSSH |DSA |ENCRYPTED |)PRIVATE KEY)-----"
    r".*?-----END (?P=label)-----"
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?is)(\"type\"\s*:\s*\"service_account\"|"
    r"//firebase\.google\.com/docs/|"
    r"client-key-data\s*:|client-certificate-data\s*:|"
    r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----)"
)

STREAM_CHUNK_BYTES = 64 * 1024
STREAM_OVERLAP_CHARS = 8 * 1024


def _is_env_example(path: Path) -> bool:
    return path.name in ENV_EXAMPLE_NAMES


def is_sensitive(path):
    if _is_env_example(path):
        return False
    name_match = any(fnmatch.fnmatch(path.name, pat) for pat in SENSITIVE_PATTERNS)
    parts = {part.lower() for part in path.parts}
    path_match = (
        (".aws" in parts and path.name in {"credentials", "config"})
        or (".kube" in parts and path.name == "config")
        or ("firebase" in parts and path.suffix.lower() == ".json")
    )
    return name_match or path_match or contains_sensitive_text(path.name)


def contains_sensitive_text(text: str) -> bool:
    if not text:
        return False
    if PRIVATE_KEY_BLOCK_RE.search(text) or STANDALONE_SECRET_RE.search(text) or SENSITIVE_VALUE_RE.search(text):
        return True
    if any(_is_sensitive_label(match.group("label")) for match in YAML_BLOCK_ASSIGNMENT_RE.finditer(text)):
        return True
    if any(_is_sensitive_label(match.group("label")) for match in MULTILINE_QUOTED_ASSIGNMENT_START_RE.finditer(text)):
        return True
    return any(_assignment_match_is_sensitive(match) for match in ASSIGNMENT_RE.finditer(text))


def redact_sensitive_text(text: str) -> str:
    """Remove high-confidence credential values from user and adapter text."""

    redacted = PRIVATE_KEY_BLOCK_RE.sub(REDACTED, text)
    redacted = STANDALONE_SECRET_RE.sub(REDACTED, redacted)
    redacted = SENSITIVE_VALUE_RE.sub(REDACTED, redacted)
    redacted = YAML_BLOCK_ASSIGNMENT_RE.sub(_redact_yaml_block, redacted)
    redacted = ASSIGNMENT_RE.sub(_redact_assignment, redacted)
    redacted = UNCLOSED_MULTILINE_QUOTED_ASSIGNMENT_RE.sub(_redact_unclosed_multiline_assignment, redacted)
    return redacted


def _label_tokens(label: str) -> set[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", label).lower()
    ordered = [piece for piece in re.split(r"[^a-z0-9]+", snake) if piece]
    pieces = set(ordered)
    pieces.update("_".join(pair) for pair in zip(ordered, ordered[1:]))
    pieces.add("_".join(ordered))
    pieces.add(re.sub(r"[^a-z0-9]", "", snake))
    return pieces


def _is_sensitive_label(label: str) -> bool:
    return bool(_label_tokens(label) & SENSITIVE_LABEL_TOKENS)


def _assignment_value_is_sensitive(value: str, operator: str = "=") -> bool:
    candidate = value.strip().rstrip(",;}").strip()
    if candidate.startswith(("|", ">")):
        # YAML block bodies are classified and redacted as a unit above.
        return False
    quoted = len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {'"', "'"}
    if quoted:
        candidate = candidate[1:-1].strip()
    if not candidate:
        return False
    if operator == ":" and not quoted and any(char.isspace() for char in candidate):
        # Bare colon phrases are common in prose and Markdown. Retain explicit
        # authorization schemes and recognisable standalone token formats.
        lowered = candidate.lower()
        if not lowered.startswith(("bearer ", "basic ")) and not STANDALONE_SECRET_RE.search(candidate):
            return False
    return not candidate.startswith(("process.env.", "os.getenv(", "Deno.env.get(", "import.meta.env."))


def _assignment_match_is_sensitive(match: re.Match[str]) -> bool:
    return _is_sensitive_label(match.group("label")) and _assignment_value_is_sensitive(
        match.group("value"), match.group("operator")
    )


def _redact_assignment(match: re.Match[str]) -> str:
    if not _assignment_match_is_sensitive(match):
        return match.group(0)
    value = match.group("value")
    stripped = value.strip()
    quote = ""
    if len(stripped) >= 6 and stripped[:3] == stripped[-3:] and stripped[:3] in {'"""', "'''"}:
        quote = stripped[:3]
    elif len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        quote = stripped[0]
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}{match.group('newline') or ''}"


def _redact_yaml_block(match: re.Match[str]) -> str:
    if not _is_sensitive_label(match.group("label")):
        return match.group(0)
    newline = "\r\n" if "\r\n" in match.group("header") else "\n"
    return f"{match.group('indent')}{match.group('label')}{match.group('header')}{match.group('indent')}  {REDACTED}{newline}"


def _redact_unclosed_multiline_assignment(match: re.Match[str]) -> str:
    if not _is_sensitive_label(match.group("label")) or match.group("quote") in match.group("body"):
        return match.group(0)
    return f"{match.group('prefix')}{match.group('quote')}{REDACTED}"


def sanitize_query(value: str) -> str:
    """Sanitize a user-controlled lookup surface before it enters MIMRY internals."""

    return redact_sensitive_text(value)


def sanitize_data(value: Any) -> Any:
    """Recursively redact strings at persistence/API boundaries."""

    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {key: sanitize_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_data(item) for item in value)
    return value


def contains_sensitive_data(value) -> bool:
    if isinstance(value, str):
        return contains_sensitive_text(value)
    if isinstance(value, dict):
        return any(contains_sensitive_data(key) or contains_sensitive_data(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(contains_sensitive_data(item) for item in value)
    return False


def stream_contains_sensitive_content(handle: BinaryIO, *, strict_text: bool = False) -> bool:
    """Scan an opened file with bounded memory, preserving cross-chunk matches.

    ``strict_text`` is used at the Graphify trust boundary. Invalid UTF-8 and
    NUL-bearing/binary-ambiguous input are rejected there rather than silently
    decoded or trusted based on a filename extension.
    """

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict" if strict_text else "ignore")
    overlap = ""
    for raw in iter(lambda: handle.read(STREAM_CHUNK_BYTES), b""):
        if strict_text and any(byte < 32 and byte not in (9, 10, 13) for byte in raw):
            raise ValueError("Graphify source is binary or has an unknown text encoding")
        # Sparse files and binary-ish generated artifacts can contain long NUL
        # runs before ordinary UTF-8 text. Drop NULs so line-anchored checks also
        # catch UTF-16-style ASCII without loading the whole file.
        try:
            decoded = decoder.decode(raw.replace(b"\x00", b""))
        except UnicodeDecodeError as exc:
            raise ValueError("Graphify source is binary or has an unknown text encoding") from exc
        text = overlap + decoded
        if contains_sensitive_text(text):
            return True
        overlap = text[-STREAM_OVERLAP_CHARS:]
    try:
        tail = overlap + decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise ValueError("Graphify source is binary or has an unknown text encoding") from exc
    return contains_sensitive_text(tail)


def _stream_contains_sensitive_content(path: Path) -> bool:
    """Scan an entire file with bounded memory, preserving cross-chunk matches."""

    try:
        with path.open("rb") as handle:
            return stream_contains_sensitive_content(handle)
    except OSError:
        return True


def opened_file_has_sensitive_content(path: Path, handle: BinaryIO) -> bool:
    """Classify the exact already-opened file used by a no-follow handoff copy."""

    # Extension is not a security boundary: an unlisted text format such as
    # ``.properties`` must receive the same byte-level classification and secret
    # scan as Python/JSON/TOML. Env examples retain their indexing exemption, but
    # are still required to be unambiguous UTF-8 before Graphify sees them.
    sensitive = stream_contains_sensitive_content(handle, strict_text=True)
    return False if _is_env_example(path) else sensitive


def has_sensitive_content(path: Path) -> bool:
    if _is_env_example(path):
        return False
    if path.suffix.lower() not in TEXT_EXTS and path.name not in {"config", "credentials"}:
        return False
    return _stream_contains_sensitive_content(path)


def _raw_file_has_sensitive_content(path: Path) -> bool:
    """Inspect generated/specially named text without env-example exemptions."""

    return _stream_contains_sensitive_content(path)


def should_ignore(path, root):
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(p in HEAVY_IGNORES for p in parts) or is_sensitive(path) or has_sensitive_content(path)


def is_text(path):
    return path.suffix.lower() in TEXT_EXTS


def root_contains_sensitive_content(root: Path) -> bool:
    """Detect ordinary secret-bearing text before handing a root to Graphify."""

    safe_root(root)
    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            parts = path.relative_to(root).parts
            if contains_sensitive_text(path.name):
                return True
            if any(part in HEAVY_IGNORES for part in parts):
                continue
            if is_sensitive(path) or _is_env_example(path):
                if _raw_file_has_sensitive_content(path):
                    return True
                continue
            if has_sensitive_content(path):
                return True
        except OSError:
            # Unreadable source is not safe to pass to a separate indexer.
            return True
    return False


def tree_contains_sensitive_content(root: Path) -> bool:
    """Validate MIMRY-owned generated artifacts before they are exposed."""

    if not root.exists():
        return False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if contains_sensitive_text(path.name) or _raw_file_has_sensitive_content(path):
            return True
    return False
