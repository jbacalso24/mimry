from __future__ import annotations

import fnmatch
import codecs
import re
from pathlib import Path
from typing import Any

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
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)^(?P<prefix>\s*(?:[{,]\s*)?(?:export\s+)?['\"]?"
    r"(?:[A-Za-z_][A-Za-z0-9_-]*)?"
    r"(?:TOKEN|SECRET|PASSWORD|PASS(?:WORD)?|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|API[_-]?KEY|AUTH|"
    r"AWS[_-](?:ACCESS[_-]KEY[_-]ID|SECRET[_-]ACCESS[_-]KEY))"
    r"[A-Za-z0-9_-]*['\"]?\s*(?:=|:)\s*)"
    r"(?P<quote>['\"]?)(?P<value>[^\r\n'\"]{6,}?)(?P=quote)(?=\s*(?:[#;,}]|$))"
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
    return any(
        not match.group("value").startswith(("process.env.", "os.getenv(", "Deno.env.get(", "import.meta.env."))
        for match in SECRET_ASSIGNMENT_RE.finditer(text)
    )


def redact_sensitive_text(text: str) -> str:
    """Remove high-confidence credential values from user and adapter text."""

    redacted = PRIVATE_KEY_BLOCK_RE.sub(REDACTED, text)
    redacted = STANDALONE_SECRET_RE.sub(REDACTED, redacted)
    redacted = SENSITIVE_VALUE_RE.sub(REDACTED, redacted)
    redacted = SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}{REDACTED}{match.group('quote')}", redacted
    )
    return redacted


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


def _stream_contains_sensitive_content(path: Path) -> bool:
    """Scan an entire file with bounded memory, preserving cross-chunk matches."""

    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    overlap = ""
    try:
        with path.open("rb") as handle:
            for raw in iter(lambda: handle.read(STREAM_CHUNK_BYTES), b""):
                # Sparse files and binary-ish generated artifacts can contain
                # long NUL runs before otherwise ordinary UTF-8 JSON/text. Drop
                # NUL bytes before decoding so line-anchored assignment checks
                # still see the payload. This also catches UTF-16-style ASCII
                # credential material without loading the whole file.
                text = overlap + decoder.decode(raw.replace(b"\x00", b""))
                if contains_sensitive_text(text):
                    return True
                overlap = text[-STREAM_OVERLAP_CHARS:]
            tail = overlap + decoder.decode(b"", final=True)
    except OSError:
        return True
    return contains_sensitive_text(tail)


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
