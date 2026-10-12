"""Index settings from ``mimry.toml`` at the root of the indexed folder.

The file is optional and lives in the repository, so a team shares one
set of rules for which files MIMRY indexes.
"""

from __future__ import annotations

import hashlib
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .constants import SETTINGS_FILE
from .search import _path_glob_matches, _validated_globs

_INDEX_KEYS = ("include", "exclude", "max_file_bytes")


class IndexSettingsError(ValueError):
    """``mimry.toml`` cannot be read, or names an invalid setting."""


@dataclass(frozen=True)
class IndexSettings:
    """Which files the index covers, as ``mimry.toml`` states.

    ``include`` None means every path; ``max_file_bytes`` None means the
    scanner's default. ``digest`` is the sha256 of the file's bytes, or
    None when there is no file, so an index can tell that they changed.
    """

    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    max_file_bytes: int | None = None
    digest: str | None = None

    def admits(self, rel_path: str) -> bool:
        """Whether a canonical repository-relative path passes the
        include and exclude rules. Exclude wins over include.
        """
        if any(_path_glob_matches(rel_path, pattern) for pattern in self.exclude):
            return False
        return self.include is None or any(
            _path_glob_matches(rel_path, pattern) for pattern in self.include
        )


def index_settings(root) -> IndexSettings:
    """The settings under ``root``, or the defaults when there is no
    ``mimry.toml``.
    """
    path = Path(root) / SETTINGS_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return IndexSettings()
    except OSError as exc:
        raise IndexSettingsError(f"{SETTINGS_FILE} could not be read: {exc}") from exc
    try:
        table = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise IndexSettingsError(f"{SETTINGS_FILE} is not valid TOML: {exc}") from exc
    index = table.get("index", {})
    if not isinstance(index, dict):
        raise IndexSettingsError(f"{SETTINGS_FILE}: [index] must be a table")
    unknown = sorted(set(index) - set(_INDEX_KEYS))
    if unknown:
        raise IndexSettingsError(
            f"{SETTINGS_FILE}: unknown setting {unknown[0]!r} in [index]; "
            "use include, exclude or max_file_bytes"
        )
    include = _globs(index, "include")
    if include == ():
        raise IndexSettingsError(
            f"{SETTINGS_FILE} [index] include: list at least one pattern, "
            "or remove include to index every path"
        )
    limit = index.get("max_file_bytes")
    if "max_file_bytes" in index and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
    ):
        raise IndexSettingsError(
            f"{SETTINGS_FILE} [index] max_file_bytes: must be a positive integer, got {limit!r}"
        )
    return IndexSettings(
        include=include,
        exclude=_globs(index, "exclude") or (),
        max_file_bytes=limit,
        digest=hashlib.sha256(raw).hexdigest(),
    )


def _globs(index: dict, key: str) -> tuple[str, ...] | None:
    """The validated, NFC-normalised patterns under ``key``, or None
    when the key is absent.
    """
    if key not in index:
        return None
    try:
        globs = _validated_globs(index[key], "pattern")
    except ValueError as exc:
        raise IndexSettingsError(f"{SETTINGS_FILE} [index] {key}: {exc}") from exc
    return tuple(unicodedata.normalize("NFC", glob) for glob in globs)
