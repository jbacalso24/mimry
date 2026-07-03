from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .constants import HEAVY_IGNORES, SENSITIVE_PATTERNS, TEXT_EXTS


def safe_root(root: Path):
    r = root.resolve()
    home = Path.home().resolve()
    if r == Path(r.anchor):
        raise ValueError(f"Refusing to index filesystem root: {r}")
    if r == home:
        raise ValueError(f"Refusing to index entire home without explicit target scope: {r}")


ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", "env.example"}
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASS|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|API[_-]?KEY|AUTH)[A-Za-z0-9_]*\s*=\s*['\"]?[^\s'\"]{6,}"
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?is)(\"private_key\"\s*:\s*\"-----BEGIN PRIVATE KEY-----|"
    r"\"type\"\s*:\s*\"service_account\"|"
    r"\"client_secret\"\s*:\s*\"[^\"]{6,}|"
    r"//firebase\.google\.com/docs/|"
    r":_authToken\s*=\s*[^\s]+|"
    r"client-key-data\s*:|client-certificate-data\s*:|token\s*:\s*[^\s]+|"
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
    return name_match or path_match


def has_sensitive_content(path: Path, *, limit: int = 64_000) -> bool:
    if _is_env_example(path):
        return False
    if path.suffix.lower() not in TEXT_EXTS and path.name not in {"config", "credentials"}:
        return False
    try:
        text = path.read_bytes()[:limit].decode("utf-8", errors="ignore")
    except OSError:
        return False
    if not text:
        return False
    return bool(SENSITIVE_VALUE_RE.search(text) or SECRET_ASSIGNMENT_RE.search(text))


def should_ignore(path, root):
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(p in HEAVY_IGNORES for p in parts) or is_sensitive(path) or has_sensitive_content(path)


def is_text(path):
    return path.suffix.lower() in TEXT_EXTS
