"""Files a query names outright: a path, module or bare file name.

Issues and task texts often say where the code is: ``in query.py``,
a traceback line, ``django.db.models.query.QuerySet``, or
``./components/Button``. Splitting those into words gives the named
file no more credit than any file sharing its words, so ``find_rows``
asks this module which indexed files the query names and boosts them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .core.documents import DOCUMENT_EXTENSIONS
from .core.languages import EXTENSION_LANGUAGE

NAMED_PATH_BOOST = 120
PATH_REASON = "named in query: path"
MODULE_REASON = "named in query: module"
FILE_NAME_REASON = "named in query: file name"
EXACT_NAME_REASON = "named in query: exact file name"
# Several files sharing a suffix are no evidence for any one of them.
MAX_TIED_MATCHES = 3
MIN_SUFFIX_COMPONENTS = 2

# Word characters, dots, dashes and slashes (either kind). Quotes,
# backticks, colons (line numbers), commas and brackets end a run.
_MENTION_RE = re.compile(r"\w[\w.\-]*(?:[/\\][\w.\-]+)*")
# A word with one of these extensions is a file name (session.ts).
FILE_EXTENSIONS = frozenset(EXTENSION_LANGUAGE) | frozenset(DOCUMENT_EXTENSIONS)


@dataclass
class _Index:
    # name -> [(components, rel_path, is_full_name)]. A full name keeps
    # its extension ("query.py"). A stem ("query") or a package
    # ("utils" for utils/__init__.py) is reached without an extension.
    by_name: dict[str, list[tuple[tuple[str, ...], str, bool]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    extensions: set[str] = field(default_factory=set)
    top_dirs: set[str] = field(default_factory=set)


def _build_index(known_paths: Iterable[str]) -> _Index:
    index = _Index()
    for path in known_paths:
        comps = tuple(path.split("/"))
        last = comps[-1]
        index.by_name[last].append((comps, path, True))
        if len(comps) >= 2:
            index.top_dirs.add(comps[0])
        stem, dot, extension = last.rpartition(".")
        if not dot:
            continue
        index.extensions.add(extension)
        if stem:
            index.by_name[stem].append((comps[:-1] + (stem,), path, False))
        if stem in ("__init__", "index") and len(comps) >= 2:
            index.by_name[comps[-2]].append((comps[:-1], path, False))
    return index


def _shared_suffix(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    count = 0
    for x, y in zip(reversed(a), reversed(b), strict=False):
        if x != y:
            break
        count += 1
    return count


def _credit(paths: list[str]) -> list[str]:
    unique = list(dict.fromkeys(paths))
    return unique if 0 < len(unique) <= MAX_TIED_MATCHES else []


def _path_matches(index: _Index, comps: tuple[str, ...]) -> list[str]:
    """Files that share the longest suffix (at least two components).

    Covers a repo-relative path, a traceback path whose prefix lies
    outside the repo, and an extensionless import such as
    ``components/Button``.
    """
    best = 0
    found: list[str] = []
    for entry, path, _is_full in index.by_name.get(comps[-1], ()):
        shared = _shared_suffix(comps, entry)
        if shared < MIN_SUFFIX_COMPONENTS:
            continue
        if shared > best:
            best, found = shared, [path]
        elif shared == best:
            found.append(path)
    return _credit(found)


def _module_matches(index: _Index, parts: tuple[str, ...]) -> list[str]:
    """Resolve a dotted name, dropping trailing class or function parts.

    The longest prefix that ends some file's path wins, so ``a.b.c``
    matches ``a/b/c.py`` and ``a/b/c/__init__.py``.
    """
    for length in range(len(parts), MIN_SUFFIX_COMPONENTS - 1, -1):
        prefix = parts[:length]
        found = [
            path
            for entry, path, _is_full in index.by_name.get(prefix[-1], ())
            if len(entry) >= length and entry[-length:] == prefix
        ]
        credited = _credit(found)
        if credited:
            return credited
    return []


def _file_name_matches(index: _Index, name: str) -> list[str]:
    """A bare file name counts only when one indexed file has it."""
    found = [path for _entry, path, is_full in index.by_name.get(name, ()) if is_full]
    unique = list(dict.fromkeys(found))
    return unique if len(unique) == 1 else []


def _name_forms(path: str) -> tuple[str, str]:
    """A lower-cased path, and the same path without its extension."""
    lowered = path.lower()
    head, slash, name = lowered.rpartition("/")
    stem = name.rpartition(".")[0] or name
    return lowered, head + slash + stem


def _whole_name_matches(path: str, wanted: str) -> bool:
    forms = _name_forms(path)
    if "/" in wanted:
        return any(form == wanted or form.endswith("/" + wanted) for form in forms)
    return wanted in {form.rsplit("/", 1)[-1] for form in forms}


def exact_name_paths(q: str, known_paths: Iterable[str]) -> list[str]:
    """Files the whole query names exactly, ignoring case.

    The query must be one name: a file name, a name without its
    extension, or a path suffix. A word of a longer task never counts,
    so ``order`` does not name ``OrderList.vue``. Several files with the
    same name are credited up to MAX_TIED_MATCHES, as paths are.
    """
    raw = (q or "").strip().replace("\\", "/").removeprefix("./")
    wanted = raw.rstrip(".-").lower()
    if not wanted or _MENTION_RE.fullmatch(wanted) is None:
        return []
    return _credit([path for path in known_paths if _whole_name_matches(path, wanted)])


def is_file_name(token: str) -> bool:
    """A word that ends in a known file extension and has a stem."""
    stem, dot, extension = token.rpartition(".")
    return bool(stem and dot and f".{extension.lower()}" in FILE_EXTENSIONS)


def unindexed_file_names(q: str, known_paths: Iterable[str]) -> list[str]:
    """File names the query writes out that no indexed file has.

    ``nosuchthing.ts`` names a file; when no indexed file has that name,
    the caller says so instead of listing the other ``.ts`` files.
    """
    known = list(known_paths)
    missing: list[str] = []
    for raw in _MENTION_RE.findall(q or ""):
        token = raw.replace("\\", "/").rstrip(".-")
        if not is_file_name(token) or token in missing:
            continue
        if not any(_whole_name_matches(path, token.lower()) for path in known):
            missing.append(token)
    return missing


def named_paths(q: str, known_paths: Iterable[str]) -> dict[str, str]:
    """Map each indexed rel_path the query names to its reason.

    No index is built unless the query has a token with a slash or dot.
    """
    tokens = [raw.replace("\\", "/").rstrip(".-") for raw in _MENTION_RE.findall(q or "")]
    tokens = [token for token in dict.fromkeys(tokens) if "/" in token or "." in token]
    if not tokens:
        return {}

    index = _build_index(known_paths)
    path_mentions: list[tuple[str, ...]] = []
    module_mentions: list[tuple[str, ...]] = []
    file_names: list[str] = []
    for token in tokens:
        if "/" in token:
            path_mentions.append(tuple(token.split("/")))
        elif token.rpartition(".")[2] in index.extensions:
            file_names.append(token)
        else:
            parts = tuple(token.split("."))
            if len(parts) >= 3 or parts[0] in index.top_dirs:
                module_mentions.append(parts)

    # Strongest evidence first: setdefault keeps a file's best reason.
    named: dict[str, str] = {}
    for comps in path_mentions:
        for path in _path_matches(index, comps):
            named.setdefault(path, PATH_REASON)
    for parts in module_mentions:
        for path in _module_matches(index, parts):
            named.setdefault(path, MODULE_REASON)
    for name in file_names:
        for path in _file_name_matches(index, name):
            named.setdefault(path, FILE_NAME_REASON)
    return named


def named_boost(reason: str) -> int:
    """A bare file name is weaker evidence: half the boost."""
    return NAMED_PATH_BOOST // 2 if reason == FILE_NAME_REASON else NAMED_PATH_BOOST
