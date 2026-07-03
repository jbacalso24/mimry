from __future__ import annotations

import hashlib
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


def context_file(root):
    return mdir(root) / "context" / "latest.md"


def stable_id(*parts):
    return hashlib.sha256("::".join(map(str, parts)).encode()).hexdigest()[:24]


def repo_root():
    return Path(__file__).resolve().parents[2]


def graphify_vendor_path():
    return repo_root() / "vendor" / "graphify"


def graphify_output_dir(root: Path):
    return mdir(root) / "graphify"
