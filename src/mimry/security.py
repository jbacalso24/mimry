from __future__ import annotations

import fnmatch
from pathlib import Path

from .constants import HEAVY_IGNORES, SENSITIVE_PATTERNS, TEXT_EXTS


def safe_root(root: Path):
    r = root.resolve()
    home = Path.home().resolve()
    if r == Path(r.anchor):
        raise ValueError(f"Refusing to index filesystem root: {r}")
    if r == home:
        raise ValueError(f"Refusing to index entire home without explicit target scope: {r}")


def is_sensitive(path):
    if path.name in {".env.example", ".env.sample", ".env.template", "env.example"}:
        return False
    return any(fnmatch.fnmatch(path.name, pat) for pat in SENSITIVE_PATTERNS)


def should_ignore(path, root):
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(p in HEAVY_IGNORES for p in parts) or is_sensitive(path)


def is_text(path):
    return path.suffix.lower() in TEXT_EXTS
