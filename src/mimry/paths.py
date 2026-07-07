from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def cache_home():
    return Path(os.environ.get("MIMRY_CACHE_HOME", Path.home() / ".cache" / "mimry")).expanduser()


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


def repo_root():
    return Path(__file__).resolve().parents[2]


def graphify_vendor_path():
    return repo_root() / "vendor" / "graphify"


def _root_id_from_pointer(root: Path) -> str | None:
    try:
        payload = json.loads(pointer_file(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    root_id = payload.get("rootId")
    return root_id if isinstance(root_id, str) and root_id else None


def graphify_output_dir(root: Path):
    """Return MIMRY's private Graphify artifact cache for a root.

    Graphify remains an internal graph engine, but its artifacts should not be
    created as visible repo-local folders. Once a root is initialized, artifacts
    live beside the root's MIMRY index under the configured cache home.
    """
    root_id = _root_id_from_pointer(root)
    if root_id:
        return idx_path(root_id) / "graphify"
    return cache_home() / "indexes" / stable_id("root", root.resolve()) / "graphify"


def legacy_graphify_output_dir(root: Path):
    return mdir(root) / "graphify"
