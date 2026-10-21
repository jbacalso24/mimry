from __future__ import annotations

import os
from pathlib import Path

from .paths import cache_home, repo_root
from .state import path_exists, path_is_dir


class UnsafeCachePathError(ValueError):
    pass


CACHE_NOT_WRITABLE_HINT = (
    "The MCP tools still work. Or set MIMRY_CACHE_HOME to a folder inside the project, "
    "which a sandbox allows."
)
READ_ONLY_CACHE_STALE_WARNING = (
    "The index is out of date, and the cache is read-only here, so it was not updated"
)


def cache_write_denied(exc: BaseException) -> bool:
    """Whether ``exc`` is the OS refusing a write inside MIMRY's cache.

    A sandbox can allow writes only inside the project, so the cache can
    be read-only for a command that still has to answer.
    """
    if not isinstance(exc, PermissionError) or not exc.filename:
        return False
    return Path(exc.filename).is_relative_to(cache_home().expanduser())


class IndexOutsideCacheError(UnsafeCachePathError):
    """A repo's index lives outside the current MIMRY cache, usually
    because MIMRY_CACHE_HOME changed since it was built.
    """

    def __init__(self, index_path: Path, cache: Path):
        self.index_path = index_path
        self.cache = cache
        super().__init__(
            f"Its index is in {index_path}, outside the current MIMRY cache {cache}. "
            "Run `mimry index` to build it in the current cache, or set MIMRY_CACHE_HOME "
            "back to the cache that holds it."
        )


def _is_empty_dir(path: Path) -> bool:
    return path_is_dir(path) and next(path.iterdir(), None) is None


def _holds_model_folder(resolved: Path) -> bool:
    """Whether the folder holds MIMRY's own embedding model folder.

    `mimry embed download` makes it before any index exists (#250). A
    models/ folder of another tool does not count (#492).
    """
    # Imported here: embed imports this module.
    from .embed import MODEL_FOLDER_PREFIX

    models = resolved / "models"
    return path_is_dir(models) and any(
        path_is_dir(child) for child in models.glob(f"{MODEL_FOLDER_PREFIX}*")
    )


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
        raise UnsafeCachePathError("Refusing to use the filesystem root as MIMRY cache")
    if resolved == home:
        raise UnsafeCachePathError("Refusing to use the home directory as MIMRY cache")
    if resolved in {cwd, project_root}:
        raise UnsafeCachePathError(
            "Refusing to use the current working directory or MIMRY repo as cache"
        )
    if root is not None and resolved == root:
        raise UnsafeCachePathError("Refusing to use the current root as MIMRY cache")

    # An empty folder holds nothing to lose, like a fresh Docker volume.
    # roots.json.lock comes first when a first run registers its root,
    # so a concurrent run must not take it for someone else's files.
    mimry_looking = (
        resolved.name == "mimry"
        or path_exists(resolved / "roots.json")
        or path_exists(resolved / "roots.json.lock")
        or path_exists(resolved / "indexes")
        or _is_empty_dir(resolved)
        or _holds_model_folder(resolved)
    )
    if path_exists(resolved) and not mimry_looking:
        raise UnsafeCachePathError(
            f"MIMRY_CACHE_HOME is {resolved}, which holds files that are not a MIMRY cache. "
            "Point MIMRY_CACHE_HOME at an empty folder or one named mimry."
        )
    return resolved


def validated_current_index_path(index_path: Path, current_root: Path | None = None) -> Path:
    cache = validated_cache_home(current_root)
    indexes_base = (cache / "indexes").resolve(strict=False)
    idx = index_path.expanduser()
    if not idx.is_absolute():
        raise UnsafeCachePathError(f"MIMRY index path must be absolute: {idx}")
    resolved_idx = idx.resolve(strict=False)
    if resolved_idx == indexes_base:
        raise UnsafeCachePathError(f"Refusing to use the whole MIMRY indexes cache: {idx}")
    if not _is_relative_to(resolved_idx, indexes_base):
        raise IndexOutsideCacheError(resolved_idx, cache)
    return resolved_idx


def validated_current_root_cache_path(
    index_path: Path,
    root_id: str,
    generation_id: str | None,
    current_root: Path | None = None,
) -> Path:
    """Validate the pointer's exact root/generation scope.

    Then return its root cache.
    """
    base = validated_current_index_path(cache_home() / "indexes" / root_id, current_root)
    pointed = validated_current_index_path(index_path, current_root)
    expected = base if generation_id is None else base / "generations" / generation_id
    if pointed != expected.resolve(strict=False):
        raise UnsafeCachePathError(
            f"Refusing to use cache: pointer index path {pointed} does not match current root"
            f" scope {expected}"
        )
    return base
