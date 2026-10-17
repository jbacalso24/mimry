"""Path aliases from tsconfig.json and jsconfig.json.

``compilerOptions.paths`` and ``baseUrl`` let an import such as
``@/urls`` name a file without a relative path. The scanner keeps each
config's own settings, and this module turns them into one effective
alias table per config folder, following relative ``extends`` chains.
"""

from __future__ import annotations

import posixpath

from .data_files import loads_jsonc

_JS_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".svelte",
)
CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")


def parse_tsconfig(text: str) -> dict | None:
    """The alias settings one config declares.

    Returns {"extends", "baseUrl", "paths"}; each is None when the file
    does not declare it. ``paths`` maps each pattern to its targets.
    None when the file does not parse.
    """
    try:
        data = loads_jsonc(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    options = data.get("compilerOptions")
    if not isinstance(options, dict):
        options = {}
    extends = data.get("extends")
    base_url = options.get("baseUrl")
    paths = options.get("paths")
    if isinstance(paths, dict):
        paths = {
            pattern: [t for t in targets if isinstance(t, str)]
            for pattern, targets in paths.items()
            if isinstance(targets, list)
        }
    else:
        paths = None
    return {
        "extends": extends if isinstance(extends, str) else None,
        "baseUrl": base_url if isinstance(base_url, str) else None,
        "paths": paths,
    }


def _normalise(path: str) -> str | None:
    """A repo-relative path, "" for the root, None if it escapes."""
    result = posixpath.normpath(path)
    if result == ".":
        return ""
    if result == ".." or result.startswith("../") or result.startswith("/"):
        return None
    return result


def _load(
    path: str, configs: dict[str, dict], stack: frozenset[str]
) -> tuple[str | None, dict | None, str]:
    """(baseUrl folder, paths map, paths' folder), after extends.

    A child's setting replaces its parent's. A relative ``extends`` that
    names a config in the index is followed; a cycle stops the chain.
    """
    parsed = configs[path]
    folder = posixpath.dirname(path)
    base: str | None = None
    paths: dict | None = None
    paths_folder = folder
    extends = parsed["extends"]
    if extends and extends.startswith(("./", "../")):
        target = _normalise(posixpath.join(folder, extends))
        if target is not None:
            for candidate in (target, target + ".json"):
                if candidate in configs and candidate not in stack:
                    base, paths, paths_folder = _load(candidate, configs, stack | {path})
                    break
    if parsed["baseUrl"] is not None:
        base = _normalise(posixpath.join(folder, parsed["baseUrl"]))
    if parsed["paths"] is not None:
        paths = parsed["paths"]
        paths_folder = folder
    return base, paths, paths_folder


def build_ts_paths(configs: dict[str, dict], rel_paths: set[str]) -> dict[str, dict]:
    """Effective alias table for each folder that holds a config.

    ``configs`` maps config rel path to parse_tsconfig output. Keys are
    folders. Each value has ``base`` (a folder or None) and ``paths``, a
    sorted list of (pattern, targets) where every target is already a
    repo-relative path. A tsconfig.json beats a jsconfig.json in the
    same folder. Every config in the index gets an entry, even an empty
    one, because the nearest config is the only one that applies.
    """
    chosen: dict[str, str] = {}
    for path in sorted(configs):
        folder = posixpath.dirname(path)
        name = posixpath.basename(path)
        if name not in CONFIG_NAMES:
            continue
        if folder not in chosen or name == "tsconfig.json":
            chosen[folder] = path

    table: dict[str, dict] = {}
    for folder, path in sorted(chosen.items()):
        base, paths, paths_folder = _load(path, configs, frozenset())
        anchor = base if base is not None else paths_folder
        entries = []
        for pattern, targets in sorted((paths or {}).items()):
            resolved = []
            for target in targets:
                joined = _normalise(posixpath.join(anchor, target))
                if joined is not None:
                    resolved.append(joined)
            entries.append((pattern, resolved))
        table[folder] = {"base": base, "paths": entries}
    return table


def _match(pattern: str, module: str) -> str | None:
    """What the `*` matched; "" if exact, None if no match."""
    if "*" not in pattern:
        return "" if pattern == module else None
    prefix, _, suffix = pattern.partition("*")
    if len(module) < len(prefix) + len(suffix):
        return None
    if module.startswith(prefix) and module.endswith(suffix):
        return module[len(prefix) : len(module) - len(suffix)]
    return None


def _probe(candidate: str, rel_paths: set[str]) -> str | None:
    """The file a module path names, probed as relative imports are."""
    for suffix in ("", *_JS_EXTENSIONS):
        if candidate + suffix in rel_paths:
            return candidate + suffix
    for ext in _JS_EXTENSIONS:
        index = posixpath.join(candidate, "index" + ext)
        if index in rel_paths:
            return index
    return None


def _nearest(importer: str, table: dict[str, dict]) -> dict | None:
    folder = posixpath.dirname(importer)
    while True:
        if folder in table:
            return table[folder]
        if not folder:
            return None
        folder = posixpath.dirname(folder)


def resolve_ts_alias(
    importer: str, module: str, rel_paths: set[str], table: dict[str, dict]
) -> dict | None:
    """Resolve a non-relative module via the importer's nearest config.

    The longest matching ``paths`` pattern wins, and an exact pattern
    beats a wildcard. Its targets are tried in order. When no pattern
    gives a file, ``baseUrl`` is tried as a folder for the module.
    Returns {"path", "confidence"} or None.
    """
    config = _nearest(importer, table)
    if config is None or module.startswith((".", "/")):
        return None

    matches = []
    for pattern, targets in config["paths"]:
        captured = _match(pattern, module)
        if captured is not None:
            prefix_length = len(pattern.partition("*")[0])
            matches.append(((0 if "*" not in pattern else 1), -prefix_length, captured, targets))
    if matches:
        matches.sort(key=lambda m: (m[0], m[1]))
        _, _, captured, targets = matches[0]
        for target in targets:
            path = _probe(target.replace("*", captured), rel_paths)
            if path:
                return {"path": path, "confidence": "EXTRACTED"}

    if config["base"] is not None:
        path = _probe(posixpath.join(config["base"], module), rel_paths)
        if path:
            return {"path": path, "confidence": "EXTRACTED"}
    return None
