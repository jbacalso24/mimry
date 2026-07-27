from __future__ import annotations

import fnmatch
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
    r"(?im)^\s*(?:export\s+)?"
    r"[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASS|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|API[_-]?KEY|AUTH)[A-Za-z0-9_]*"
    r"\s*=\s*['\"]?(?P<value>[^\s'\"]{6,})"
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?is)(\"private_key\"\s*:\s*\"-----BEGIN PRIVATE KEY-----|"
    r"\"type\"\s*:\s*\"service_account\"|"
    r"\"client_secret\"\s*:\s*\"[^\"]{6,}|"
    r"//firebase\.google\.com/docs/|"
    r":_authToken\s*=\s*[^\s]+|"
    r"client-key-data\s*:|client-certificate-data\s*:|"
    r"aws_access_key_id\s*=|aws_secret_access_key\s*=|"
    r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----)"
)


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
    if STANDALONE_SECRET_RE.search(text) or SENSITIVE_VALUE_RE.search(text):
        return True
    return any(
        not match.group("value").startswith(("process.env.", "os.getenv(", "Deno.env.get(", "import.meta.env."))
        for match in SECRET_ASSIGNMENT_RE.finditer(text)
    )


def redact_sensitive_text(text: str) -> str:
    """Remove high-confidence credential values from user and adapter text."""

    redacted = STANDALONE_SECRET_RE.sub(REDACTED, text)
    redacted = SENSITIVE_VALUE_RE.sub(REDACTED, redacted)
    redacted = SECRET_ASSIGNMENT_RE.sub(lambda match: match.group(0).replace(match.group("value"), REDACTED), redacted)
    return redacted


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


def has_sensitive_content(path: Path, *, limit: int = 1_000_001) -> bool:
    if _is_env_example(path):
        return False
    if path.suffix.lower() not in TEXT_EXTS and path.name not in {"config", "credentials"}:
        return False
    try:
        text = path.read_bytes()[:limit].decode("utf-8", errors="ignore")
    except OSError:
        return True
    if not text:
        return False
    return contains_sensitive_text(text)


def _raw_file_has_sensitive_content(path: Path, *, limit: int = 1_000_001) -> bool:
    """Inspect generated/specially named text without env-example exemptions."""

    try:
        text = path.read_bytes()[:limit].decode("utf-8", errors="ignore")
    except OSError:
        return True
    return contains_sensitive_text(text)


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
                if path.stat().st_size <= 1_000_000 and _raw_file_has_sensitive_content(path):
                    return True
                continue
            if path.stat().st_size <= 1_000_000 and has_sensitive_content(path):
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
        if contains_sensitive_text(path.name) or _raw_file_has_sensitive_content(path, limit=5_000_001):
            return True
    return False
