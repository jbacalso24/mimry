"""Path and identity utilities for MIMRY.

CRITICAL: NFC normalization is the single canonical form for all paths and
filenames used in identity-bearing metadata (file_id, rel_path, filename,
extension, metadata_text, semantic chunks, and canonical digest).

This ensures that checkouts where the filesystem stores filenames in NFC
vs NFD (a precomposed accented character vs the same letter followed by a
combining accent) produce identical records, hashes, and search
indices. The raw native path is preserved only in the "path" field.

All identity-bearing path fields MUST derive from canonical_rel_path() or
Path(canonical_rel_path()).name / .suffix.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def cache_home():
    # Resolve the override first. Passing Path.home() as a get() default evaluates it
    # eagerly, so an explicit MIMRY_CACHE_HOME still crashed in environments without a
    # resolvable home directory (Windows sandboxes with no USERPROFILE).
    configured = os.environ.get("MIMRY_CACHE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "mimry"


def roots_file():
    return cache_home() / "roots.json"


def idx_path(root_id):
    return cache_home() / "indexes" / root_id


def mdir(root):
    return root / ".mimry"


def pointer_file(root):
    return mdir(root) / "pointer.json"


def output_dir(root):
    return mdir(root) / "mimry-out"


def legacy_output_dir(root):
    return root / "mimry-out"


def context_file(root):
    return output_dir(root) / "context" / "latest.md"


def legacy_context_file(root):
    return legacy_output_dir(root) / "context" / "latest.md"


def graph_output_dir(root):
    return output_dir(root) / "graph"


def stable_id(*parts):
    return hashlib.sha256("::".join(map(str, parts)).encode()).hexdigest()[:24]


def canonical_rel_path(path, root) -> str:
    """The canonical repository-relative identity of a file.

    POSIX separators, NFC-normalized Unicode, case preserved. This is the single
    normalization rule for sorting and identity on Linux, macOS, and Windows.
    Sorting on the resulting str uses codepoint order, which is locale-independent.
    """
    rel = Path(path).relative_to(root).as_posix()
    return unicodedata.normalize("NFC", rel)


def repo_root():
    return Path(__file__).resolve().parents[2]


def _root_id_from_pointer(root: Path) -> str | None:
    try:
        payload = json.loads(pointer_file(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    root_id = payload.get("rootId")
    return root_id if isinstance(root_id, str) and root_id else None
