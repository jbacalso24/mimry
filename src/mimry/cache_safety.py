from __future__ import annotations

import os
from pathlib import Path

from .paths import cache_home, repo_root


class UnsafeCachePathError(ValueError):
    pass


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validated_cache_home(current_root: Path | None = None) -> Path:
    raw = os.environ.get("MIMRY_CACHE_HOME")
    if raw is not None and not raw.strip():
        raise UnsafeCachePathError("MIMRY_CACHE_HOME is empty")

    cache = cache_home().expanduser()
    if not cache.is_absolute():
        raise UnsafeCachePathError(f"MIMRY cache path must be absolute: {cache}")

    resolved = cache.resolve(strict=False)
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    project_root = repo_root().resolve()
    root = current_root.resolve() if current_root is not None else None

    if resolved.parent == resolved:
        raise UnsafeCachePathError("Refusing to wipe filesystem root as MIMRY cache")
    if resolved == home:
        raise UnsafeCachePathError("Refusing to wipe home directory as MIMRY cache")
    if resolved in {cwd, project_root}:
        raise UnsafeCachePathError("Refusing to wipe current working directory or MIMRY repo as cache")
    if root is not None and resolved == root:
        raise UnsafeCachePathError("Refusing to wipe current root as MIMRY cache")

    mimry_looking = resolved.name == "mimry" or (resolved / "roots.json").exists() or (resolved / "indexes").exists()
    if resolved.exists() and not mimry_looking:
        raise UnsafeCachePathError(f"Refusing to wipe non-MIMRY-looking cache path: {resolved}")
    return resolved


def validated_current_index_path(index_path: Path, current_root: Path | None = None) -> Path:
    cache = validated_cache_home(current_root)
    indexes_base = (cache / "indexes").resolve(strict=False)
    idx = index_path.expanduser()
    if not idx.is_absolute():
        raise UnsafeCachePathError(f"MIMRY index path must be absolute: {idx}")
    resolved_idx = idx.resolve(strict=False)
    if resolved_idx == indexes_base or not _is_relative_to(resolved_idx, indexes_base):
        raise UnsafeCachePathError(f"Refusing to wipe index outside MIMRY indexes cache: {resolved_idx}")
    return resolved_idx
