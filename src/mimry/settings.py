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
from .core.documents import DOCUMENT_EXTENSIONS
from .core.languages import EXTENSION_LANGUAGE
from .search import _path_glob_matches, _validated_globs

_INDEX_KEYS = ("include", "exclude", "max_file_bytes", "languages")

# The first extension listed for each language, so an override parses
# as that language's canonical file type (".h" = "cpp" parses as ".cc").
_CANONICAL_EXTENSION: dict[str, str] = {}
for _extension, _language in EXTENSION_LANGUAGE.items():
    _CANONICAL_EXTENSION.setdefault(_language, _extension)


class IndexSettingsError(ValueError):
    """``mimry.toml`` cannot be read, or names an invalid setting."""


@dataclass(frozen=True)
class IndexSettings:
    """Which files the index covers, as ``mimry.toml`` states.

    ``include`` None means every path; ``max_file_bytes`` None means
    the scanner's default. ``languages`` maps lowercased extensions to
    language names, sorted. ``digest`` is the sha256 of the file's
    bytes, or None when there is no file, so an index can tell that
    they changed.
    """

    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    max_file_bytes: int | None = None
    digest: str | None = None
    languages: tuple[tuple[str, str], ...] = ()

    def admits(self, rel_path: str) -> bool:
        """Whether a canonical repository-relative path passes the
        include and exclude rules. Exclude wins over include.
        """
        if any(_path_glob_matches(rel_path, pattern) for pattern in self.exclude):
            return False
        return self.include is None or any(
            _path_glob_matches(rel_path, pattern) for pattern in self.include
        )

    def extension(self, path) -> str:
        """The extension ``path`` is parsed as.

        That is its lowercased suffix, or the canonical extension of the
        language that ``languages`` maps the suffix to.
        """
        suffix = Path(path).suffix.lower()
        for key, language in self.languages:
            if key == suffix:
                return _CANONICAL_EXTENSION[language]
        return suffix

    def language_overrides(self) -> dict[str, str]:
        """The ``languages`` table as a dict, for the resolver."""
        return dict(self.languages)


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
            "use include, exclude, max_file_bytes or languages"
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
        languages=_languages(index),
    )


def _languages(index: dict) -> tuple[tuple[str, str], ...]:
    """The validated ``[index.languages]`` table, as sorted pairs of
    lowercased extension and language name.
    """
    if "languages" not in index:
        return ()
    table = index["languages"]
    if not isinstance(table, dict):
        raise IndexSettingsError(
            f"{SETTINGS_FILE} [index.languages]: must be a table of extension = language"
        )
    names = sorted(set(EXTENSION_LANGUAGE.values()))
    pairs: dict[str, str] = {}
    for key, value in table.items():
        extension = key.lower()
        if not _is_extension(extension):
            raise IndexSettingsError(
                f"{SETTINGS_FILE} [index.languages] {key!r}: the key must be an extension "
                'such as ".inc" (a dot, then characters other than a slash, backslash, '
                "whitespace or another dot)"
            )
        if extension in DOCUMENT_EXTENSIONS:
            raise IndexSettingsError(
                f"{SETTINGS_FILE} [index.languages] {key!r}: {extension} is a document "
                "format and cannot be mapped to a language"
            )
        if not isinstance(value, str) or value.lower() not in names:
            raise IndexSettingsError(
                f"{SETTINGS_FILE} [index.languages] {key!r}: the value must be one of "
                f"{', '.join(names)}, got {value!r}"
            )
        if extension in pairs:
            raise IndexSettingsError(
                f"{SETTINGS_FILE} [index.languages] {key!r}: {extension} is listed twice "
                "(keys compare case-insensitively)"
            )
        pairs[extension] = value.lower()
    return tuple(sorted(pairs.items()))


def _is_extension(key: str) -> bool:
    """A dot, then at least one character, none of them a separator,
    whitespace or another dot.
    """
    return (
        key.startswith(".")
        and len(key) > 1
        and not any(ch in "/\\." or ch.isspace() for ch in key[1:])
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
