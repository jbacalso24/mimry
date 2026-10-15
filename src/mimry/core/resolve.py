from __future__ import annotations

import posixpath

from .languages import language_for
from .tsconfig import resolve_ts_alias

SYMBOL_KIND_PRIORITY = {"class": 0, "interface": 1, "function": 2, "method": 3, "sql_table": 4}

_JS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_TS_ALIAS_LANGUAGES = frozenset(("typescript", "tsx", "javascript"))


# Languages whose quoted and angle includes follow the C rules.
_C_INCLUDE_LANGUAGES = ("c", "cpp", "objc")


def _is_c_family(path: str, overrides: dict[str, str] | None = None) -> bool:
    return language_for(path, overrides) in _C_INCLUDE_LANGUAGES


def _relative_suffixes(importer: str, overrides: dict[str, str] | None = None) -> tuple[str, ...]:
    """Extensions a relative import may omit, by importer language."""
    language = language_for(importer, overrides)
    if language == "ruby":
        return ("", ".rb")
    if language == "php" or language in _C_INCLUDE_LANGUAGES:
        return ("",)  # an include names its file's extension
    return ("", *_JS_EXTENSIONS)


def symbol_selection_key(symbol, rel_path=""):
    """Total, documented order for resolving a symbol reference to one
    definition.

    Kind priority first (a class beats a loose function of the same
    name), then normalized repository-relative path, line start, line
    end, canonical symbol id. Every field is canonical, so the winner
    never depends on input order.
    """
    return (
        SYMBOL_KIND_PRIORITY.get(symbol.get("kind"), 99),
        rel_path,
        symbol.get("line_start") or 0,
        symbol.get("line_end") or 0,
        symbol.get("symbol_id") or symbol.get("name") or "",
    )


def resolve_imports(
    imports: dict[str, list[str]],
    rel_paths: set[str],
    overrides: dict[str, str] | None = None,
    dart_packages: dict[str, list[str]] | None = None,
    go_modules: dict[str, list[str]] | None = None,
    ts_paths: dict[str, dict] | None = None,
) -> list[dict]:
    """Resolve import strings to repo-relative target files.

    Args:
        imports: Dict mapping importer rel_path to list of module
            strings
        rel_paths: Set of all repo-relative file paths that exist
        overrides: Extension-to-language map from mimry.toml, used to
            pick each importer's language
        dart_packages: Pub package name to the directories of the
            pubspec.yaml files that declare it, for `package:` URIs
        go_modules: Go module path to the directories of the go.mod
            files that declare it. A Go import names a package, so it
            can link to one result per .go file in that package.
        ts_paths: Effective tsconfig/jsconfig alias table per config
            folder, from tsconfig.build_ts_paths.

    Returns:
        Sorted list of {"importer": str, "target": str,
        "confidence": str}. Confidence is "EXTRACTED" (rule 1/2) or
        "INFERRED" (rule 3/4). Unresolvable imports are dropped; no
        result for ambiguous rule-4 matches.
    """
    results = []
    go_package_files = _build_go_package_files(rel_paths) if go_modules else {}

    # Precompute suffix lookup for rule 4 efficiency
    suffix_lookup = _build_suffix_lookup(rel_paths)

    # Importers matching whole path suffixes (C headers, Lua and Perl
    # modules, PowerShell module names) share one exact lookup.
    exact_lookup = {}
    for importer in imports.keys():
        if _is_c_family(importer, overrides) or (
            language_for(importer, overrides) in _SUFFIX_INDEX_LANGUAGES
        ):
            exact_lookup = _build_suffix_lookup(rel_paths, keep_extension=True)
            break

    # Python package roots (src/ layouts) share one lookup per pass.
    package_roots = _build_package_roots(rel_paths)

    for importer, module_list in imports.items():
        is_go = language_for(importer, overrides) == "go"
        for module in module_list:
            if is_go:
                targets = _resolve_go(module, go_modules or {}, go_package_files)
            else:
                target = _resolve_import(
                    importer,
                    module,
                    rel_paths,
                    suffix_lookup,
                    exact_lookup,
                    overrides,
                    package_roots,
                    dart_packages,
                    ts_paths=ts_paths,
                )
                targets = [target] if target else []
            for target in targets:
                results.append(
                    {
                        "importer": importer,
                        "target": target["path"],
                        "confidence": target["confidence"],
                    }
                )

    # Sort deterministically by (importer, target)
    results.sort(key=lambda r: (r["importer"], r["target"]))
    return results


def _resolve_import(
    importer: str,
    module: str,
    rel_paths: set[str],
    suffix_lookup: dict,
    exact_lookup: dict | None = None,
    overrides: dict[str, str] | None = None,
    package_roots: dict | None = None,
    dart_packages: dict[str, list[str]] | None = None,
    ts_paths: dict[str, dict] | None = None,
) -> dict | None:
    """Try to resolve a single import against available paths.

    Returns {"path": str, "confidence": str} or None if unresolvable.
    Apply rules in order; first unambiguous match wins.
    """
    if exact_lookup is None:
        exact_lookup = {}

    # Sanitize module string
    module = module.strip() if module else ""
    if not module or module in (".", ".."):
        return None

    # Shell, PowerShell, Lua, Perl and Zig imports follow their own
    # rules.
    language = language_for(importer, overrides)
    if language in _SCRIPT_RESOLVERS:
        return _SCRIPT_RESOLVERS[language](importer, module, rel_paths, exact_lookup)
    if language == "dart":
        return _resolve_dart(importer, module, rel_paths, dart_packages or {})
    if language == "rust":
        result = _resolve_rust(importer, module, rel_paths)
        if result:
            return result
    # A bare Objective-C module is `@import Foundation;`, a framework
    # and never a file. Includes always keep a "./" or "<" form (see
    # _c_include).
    if language == "objc" and not module.startswith((".", "<")):
        return None

    # System include (C/C++): <path/to/file.h>
    if module.startswith("<") and module.endswith(">"):
        return _exact_suffix_match(module[1:-1], exact_lookup)

    # Rule 1: Relative specifier (JS/TS/C/C++) - starts with ./ or ../
    if module.startswith("./") or module.startswith("../"):
        result = _rule1_relative(importer, module, rel_paths, overrides)
        if result:
            return result
        # For C-family, also try exact match after rule 1 fails
        if _is_c_family(importer, overrides):
            return _exact_suffix_match(module, exact_lookup)
        return None

    # Python relative import. The leading-dot count is ImportFrom.level,
    # so one dot stays in the current package and each additional dot
    # climbs a package.
    if module.startswith("."):
        return _rule1_python_relative(importer, module, rel_paths)

    # Rule 2: Dotted absolute module (Python) - contains . and no /
    if "." in module and "/" not in module and importer.endswith(".py"):
        return _rule2_dotted_absolute(module, rel_paths, package_roots)
    # A dotted module from a non-Python importer is a Java/Kotlin-style
    # package, not a module path. Rule 2 probes <module>.py and
    # <module>/__init__.py, so returning its verdict for
    # `com.app.model.User` discarded the import before rule 4 ever saw
    # it. Rule 3 declines anything dotted, so control reaches the suffix
    # match, which resolves it or emits nothing.

    # Rule 3: Dotted/bare module relative to importer's package (Python)
    result = _rule3_relative_to_package(importer, module, rel_paths)
    if result:
        return result

    # A bare Python module (`import pkg`) is absolute too. Rule 3 gets
    # the first look because a bare name is also the importer-relative
    # form.
    if "/" not in module and "." not in module and importer.endswith(".py"):
        result = _rule2_dotted_absolute(module, rel_paths, package_roots)
        if result:
            return result

    # tsconfig/jsconfig path aliases and baseUrl, through the nearest
    # config. A hit is exact, so it beats the suffix guess below.
    if ts_paths and language in _TS_ALIAS_LANGUAGES:
        result = resolve_ts_alias(importer, module, rel_paths, ts_paths)
        if result:
            return result

    # Rule 4: Suffix match
    return _rule4_suffix_match(module, rel_paths, suffix_lookup, importer, overrides)


_SUFFIX_INDEX_LANGUAGES = frozenset(("lua", "perl", "powershell"))


def _resolve_script_path(importer: str, path: str, rel_paths: set[str]) -> dict | None:
    """A Bash or PowerShell path, relative to the importing file.

    The file must exist exactly there: no extension probing and no
    search by basename. An unexpanded `$variable` means we cannot know
    where the path points, so it resolves to nothing.
    """
    path = path.replace("\\", "/")
    if not path or "$" in path:
        return None
    candidate = posixpath.normpath(posixpath.join(posixpath.dirname(importer), path))
    if candidate in rel_paths:
        return {"path": candidate, "confidence": "EXTRACTED"}
    return None


def _resolve_bash(
    importer: str, module: str, rel_paths: set[str], exact_lookup: dict
) -> dict | None:
    return _resolve_script_path(importer, module, rel_paths)


def _resolve_powershell(
    importer: str, module: str, rel_paths: set[str], exact_lookup: dict
) -> dict | None:
    """Dot-sourced files and Import-Module / `using module` arguments.

    A path (contains a separator or ends in a PowerShell extension) is
    relative to the importer. A bare name resolves only when exactly one
    repo file is `<Name>.psm1` or `<Name>/<Name>.psm1` as a path suffix.
    """
    if "/" in module or "\\" in module or module.lower().endswith((".psm1", ".psd1", ".ps1")):
        return _resolve_script_path(importer, module, rel_paths)
    if "$" in module:
        return None
    matches = exact_lookup.get(f"{module}.psm1", [])
    if len(matches) == 1:
        return {"path": matches[0], "confidence": "INFERRED"}
    return None


def _resolve_lua(
    importer: str, module: str, rel_paths: set[str], exact_lookup: dict
) -> dict | None:
    """Lua `a.b` is `a/b.lua` or `a/b/init.lua` as a unique path suffix.

    A unique match under the importer's top-level directory wins over a
    match elsewhere; otherwise an ambiguous name resolves to nothing.
    """
    base = module.replace(".", "/")
    candidates = sorted(
        {*exact_lookup.get(f"{base}.lua", []), *exact_lookup.get(f"{base}/init.lua", [])}
    )
    if "/" in importer:
        top = importer.split("/", 1)[0] + "/"
        local = [path for path in candidates if path.startswith(top)]
        if len(local) == 1:
            return {"path": local[0], "confidence": "INFERRED"}
    if len(candidates) == 1:
        return {"path": candidates[0], "confidence": "INFERRED"}
    return None


def _resolve_perl(
    importer: str, module: str, rel_paths: set[str], exact_lookup: dict
) -> dict | None:
    """Perl `Foo::Bar` is `Foo/Bar.pm` as a unique path suffix."""
    matches = exact_lookup.get(module.replace("::", "/") + ".pm", [])
    if len(matches) == 1:
        return {"path": matches[0], "confidence": "INFERRED"}
    return None


def _resolve_zig(
    importer: str, module: str, rel_paths: set[str], exact_lookup: dict
) -> dict | None:
    """`@import("x.zig")` relative to the importer. `@import("std")` and
    other package names are recorded but never resolve.
    """
    if not module.endswith(".zig"):
        return None
    return _resolve_script_path(importer, module, rel_paths)


def _resolve_dart(
    importer: str, module: str, rel_paths: set[str], packages: dict[str, list[str]]
) -> dict | None:
    """Dart `package:<name>/<path>` through the one pubspec named
    <name>, or a relative URI beside the importer. `dart:` URIs never
    resolve.
    """
    if module.startswith("package:"):
        name, slash, path = module[len("package:") :].partition("/")
        pubspec_dirs = packages.get(name, [])
        if len(pubspec_dirs) != 1 or not slash or not path:
            return None
        candidate = posixpath.normpath(posixpath.join(pubspec_dirs[0], "lib", path))
        if candidate in rel_paths:
            return {"path": candidate, "confidence": "INFERRED"}
        return None
    if ":" in module:
        return None
    return _resolve_script_path(importer, module, rel_paths)


def _rust_crate_root(importer: str, rel_paths: set[str]) -> str | None:
    """Folder of the nearest lib.rs or main.rs above the importer."""
    parts = posixpath.dirname(importer).split("/") if "/" in importer else []
    for depth in range(len(parts), -1, -1):
        folder = "/".join(parts[:depth])
        if any(posixpath.join(folder, root) in rel_paths for root in ("lib.rs", "main.rs")):
            return folder
    return None


def _rust_module_dir(importer: str) -> str:
    """Folder that holds the child modules of the importer's module.

    mod.rs, lib.rs and main.rs own their folder. Any other file `a.rs`
    owns the folder `a/` beside it (Rust 2018 layout).
    """
    folder, name = posixpath.split(importer)
    if name in ("mod.rs", "lib.rs", "main.rs"):
        return folder
    return posixpath.join(folder, name[: -len(".rs")])


def _rust_module_file(base: str, segments: list[str], rel_paths: set[str]) -> str | None:
    """File of the longest leading run of segments under base, as
    `a/b.rs` or `a/b/mod.rs`.

    The last segment is often an item (`use crate::a::b::Item`), so
    shorter runs are tried after the longest one.
    """
    for count in range(len(segments), 0, -1):
        stem = posixpath.join(base, *segments[:count])
        for path in (stem + ".rs", posixpath.join(stem, "mod.rs")):
            if path in rel_paths:
                return path
    return None


def _resolve_rust(importer: str, module: str, rel_paths: set[str]) -> dict | None:
    """Rust `use` and `mod` paths to the file that defines the module.

    `crate::` starts at the crate root, `self::` at the importer's
    module and `super::` at its parent. An unprefixed path is looked up
    in the importer's module, as a Rust 2018 `use` of a sibling module
    is. A bare name is a `mod x;` declaration. Anything not found falls
    through to the suffix match.
    """
    segments = module.split("::")
    prefix = segments[0]
    base = _rust_module_dir(importer)
    if prefix == "crate":
        root = _rust_crate_root(importer, rel_paths)
        if root is None:
            return None
        base, segments = root, segments[1:]
    elif prefix == "self":
        segments = segments[1:]
    elif prefix == "super":
        while segments and segments[0] == "super":
            base, segments = posixpath.dirname(base), segments[1:]
    if not segments or not all(segments):
        return None
    path = _rust_module_file(base, segments, rel_paths)
    if path is None:
        return None
    # Only a path the module tree names outright is certain.
    exact = prefix in ("crate", "self", "super") or len(segments) == 1
    return {"path": path, "confidence": "EXTRACTED" if exact else "INFERRED"}


def _build_go_package_files(rel_paths: set[str]) -> dict[str, list[str]]:
    """Map each folder to its sorted .go files, test files excluded."""
    packages: dict[str, list[str]] = {}
    for path in sorted(rel_paths):
        if path.endswith(".go") and not path.endswith("_test.go"):
            packages.setdefault(posixpath.dirname(path), []).append(path)
    return packages


def _resolve_go(
    module: str, go_modules: dict[str, list[str]], package_files: dict[str, list[str]]
) -> list[dict]:
    """A Go import path to the .go files of the package it names.

    The longest go.mod module path that the import equals or extends
    maps the rest of the path onto that go.mod's folder. The standard
    library and third-party paths match no module, so they name nothing
    and never fall through to the suffix match.
    """
    best = None
    for name in go_modules:
        if module == name or module.startswith(name + "/"):
            if best is None or len(name) > len(best):
                best = name
    if best is None or len(go_modules[best]) != 1:
        return []
    rest = module[len(best) :].lstrip("/")
    package_dir = posixpath.normpath(posixpath.join(go_modules[best][0], rest))
    if package_dir == ".":
        package_dir = ""
    return [
        {"path": path, "confidence": "EXTRACTED"} for path in package_files.get(package_dir, [])
    ]


_SCRIPT_RESOLVERS = {
    "bash": _resolve_bash,
    "powershell": _resolve_powershell,
    "lua": _resolve_lua,
    "perl": _resolve_perl,
    "zig": _resolve_zig,
}


def _exact_suffix_match(path: str, exact_lookup: dict) -> dict | None:
    """Match path in exact_lookup, dropping . and .. segments."""
    # Normalize the path by dropping . and .. segments
    parts = path.split("/")
    normalized_parts = []
    for part in parts:
        if part == ".." or part == ".":
            continue
        normalized_parts.append(part)
    normalized = "/".join(normalized_parts)

    match = exact_lookup.get(normalized)
    if match and len(match) == 1:
        return {"path": match[0], "confidence": "INFERRED"}
    return None


def _rule1_relative(
    importer: str, module: str, rel_paths: set[str], overrides: dict[str, str] | None = None
) -> dict | None:
    """Rule 1: Relative specifier (JS/TS/C/C++/Ruby).

    Base = importer's directory
    Join base + module, normalize away ./ and ../
    Probe: exact path, then +suffixes, then +/index+suffixes
    Confidence: EXTRACTED
    """
    importer_dir = posixpath.dirname(importer)

    # Join and normalize
    candidate = posixpath.normpath(posixpath.join(importer_dir, module))

    suffixes = _relative_suffixes(importer, overrides)

    # Probe: exact path and with suffixes
    for suffix in suffixes:
        with_suffix = candidate + suffix
        if with_suffix in rel_paths:
            return {"path": with_suffix, "confidence": "EXTRACTED"}

    # Probe: /index with JS extensions only when not Ruby/C-family
    language = language_for(importer, overrides)
    if language not in ("ruby", "php", *_C_INCLUDE_LANGUAGES):
        index_base = posixpath.join(candidate, "index")
        for ext in _JS_EXTENSIONS:
            with_index = index_base + ext
            if with_index in rel_paths:
                return {"path": with_index, "confidence": "EXTRACTED"}

    return None


def _rule1_python_relative(importer: str, module: str, rel_paths: set[str]) -> dict | None:
    """Resolve a Python leading-dot import by its declared level."""
    level = len(module) - len(module.lstrip("."))
    module_name = module[level:]
    if not module_name:
        return None
    package_dir = posixpath.dirname(importer)
    for _ in range(level - 1):
        package_dir = posixpath.dirname(package_dir)
    candidate = posixpath.join(package_dir, module_name.replace(".", "/"))
    for path in (candidate + ".py", posixpath.join(candidate, "__init__.py")):
        if path in rel_paths:
            return {"path": path, "confidence": "EXTRACTED"}
    return None


def _module_file(prefix: str, rel_paths: set[str]) -> str | None:
    """The file that makes `prefix` a module, or None.

    Probes `<prefix>.py` then `<prefix>/__init__.py`.
    """
    for path in (prefix + ".py", prefix + "/__init__.py"):
        if path in rel_paths:
            return path
    return None


def _build_package_roots(rel_paths: set[str]) -> dict[str, list[str]]:
    """Map each top-level Python name to the folders that hold it.

    A folder R (never the repository root) is a root for name `a` when
    `R/a/__init__.py` or `R/a.py` exists and R has no `__init__.py`, so
    `a` is top-level at R rather than a subpackage of it. Built once per
    resolve pass; roots are sorted so the lookup is deterministic.
    """
    package_dirs = {p[: -len("/__init__.py")] for p in rel_paths if p.endswith("/__init__.py")}
    roots: dict[str, set[str]] = {}
    for path in rel_paths:
        if not path.endswith(".py"):
            continue
        folder, _, name = path.rpartition("/")
        if name == "__init__.py":
            root, _, top = folder.rpartition("/")
        else:
            root, top = folder, name[: -len(".py")]
        if not root or not top or root in package_dirs:
            continue
        roots.setdefault(top, set()).add(root)
    return {top: sorted(found) for top, found in roots.items()}


def _rule2_dotted_absolute(
    module: str, rel_paths: set[str], package_roots: dict | None = None
) -> dict | None:
    """Rule 2: Absolute Python module, dotted (`a.b.c`) or bare (`a`).

    Module contains no /
    Candidate = module with . replaced by /
    Probe the repository root: <candidate>.py then
    <candidate>/__init__.py. Otherwise probe each package root R for
    the top-level name (src/ layouts): R/<candidate>.py then
    R/<candidate>/__init__.py. One root that resolves wins. Several
    resolving roots prefer the one named src (or ending in /src) when
    exactly one is; otherwise no edge.
    Confidence: EXTRACTED
    """
    candidate = module.replace(".", "/")

    root_hit = _module_file(candidate, rel_paths)
    if root_hit:
        return {"path": root_hit, "confidence": "EXTRACTED"}

    hits = []
    for root in (package_roots or {}).get(module.split(".", 1)[0], []):
        path = _module_file(posixpath.join(root, candidate), rel_paths)
        if path:
            hits.append((root, path))
    if len(hits) == 1:
        return {"path": hits[0][1], "confidence": "EXTRACTED"}
    src_hits = [hit for hit in hits if hit[0] == "src" or hit[0].endswith("/src")]
    if len(src_hits) == 1:
        return {"path": src_hits[0][1], "confidence": "EXTRACTED"}
    return None


def _rule3_relative_to_package(importer: str, module: str, rel_paths: set[str]) -> dict | None:
    """Rule 3: Relative to importer's package (Python).

    Try same as rule 2 but rooted at importer's directory. This handles
    the case where scanner recorded `from .payments import x` as just
    `"payments"`. Only applies to bare module names (no / or .).
    Confidence: INFERRED
    """
    # Only apply to bare module names (no / or .)
    if "/" in module or "." in module:
        return None

    importer_dir = posixpath.dirname(importer)

    # Try as module in importer's directory
    candidate = posixpath.join(importer_dir, module)

    # Probe: <candidate>.py
    py_file = candidate + ".py"
    if py_file in rel_paths:
        return {"path": py_file, "confidence": "INFERRED"}

    # Probe: <candidate>/__init__.py
    init_file = candidate + "/__init__.py"
    if init_file in rel_paths:
        return {"path": init_file, "confidence": "INFERRED"}

    return None


def _rule4_suffix_match(
    module: str,
    rel_paths: set[str],
    suffix_lookup: dict,
    importer: str = "",
    overrides: dict[str, str] | None = None,
) -> dict | None:
    r"""Rule 4: Suffix match (Go, Rust, C#, bare JS specifiers, Ruby).

    Normalize module by replacing :: and . and \ with / Find paths whose
    extension-stripped value ends with normalized suffix on path-segment
    boundary If EXACTLY ONE match, emit with confidence INFERRED
    Otherwise emit nothing (never guess between candidates)
    """
    # Normalize the module
    normalized = module.replace("::", "/").replace(".", "/").replace("\\", "/")
    normalized = normalized.strip("/")  # Strip leading/trailing /

    if not normalized:
        return None

    # Look up in suffix_lookup
    matches = suffix_lookup.get(normalized, [])

    # For Ruby importers, keep only matches ending in .rb
    if importer and language_for(importer, overrides) == "ruby":
        matches = [m for m in matches if m.endswith(".rb")]

    if len(matches) == 1:
        return {"path": matches[0], "confidence": "INFERRED"}

    return None  # Zero or multiple matches -> don't guess


def _build_suffix_lookup(rel_paths: set[str], *, keep_extension: bool = False) -> dict:
    """Precompute suffix lookup for rule 4 efficiency.

    Map from normalized suffix to list of matching paths. A path matches
    if, when its extension is removed (unless keep_extension=True), the
    result ends with the suffix on a path-segment boundary.
    """
    suffix_lookup = {}

    for path in rel_paths:
        # Remove extension unless keep_extension is True
        if keep_extension:
            without_ext = path
        else:
            without_ext = _strip_extension(path)

        # Generate all path-segment suffixes
        parts = without_ext.split("/")
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            if suffix not in suffix_lookup:
                suffix_lookup[suffix] = []
            suffix_lookup[suffix].append(path)

    return suffix_lookup


def _strip_extension(path: str) -> str:
    """Remove file extension from path, keeping directory structure."""
    # Split on the last / to separate dir from filename
    if "/" in path:
        dir_part, file_part = path.rsplit("/", 1)
    else:
        dir_part, file_part = "", path

    # Remove extension from filename
    if "." in file_part:
        file_without_ext = file_part.rsplit(".", 1)[0]
    else:
        file_without_ext = file_part

    # Rejoin
    if dir_part:
        return dir_part + "/" + file_without_ext
    else:
        return file_without_ext


_CONTAINER_KINDS = ("class", "interface")
_FUNCTION_KINDS = ("function", "method")
_CALLABLE_KINDS = ("function", "method", "class")
# Languages where same-module files call each other without imports.
_IMPLICIT_SCOPE_LANGUAGES = frozenset(("swift", "kotlin", "csharp"))


def resolve_calls(
    calls: dict[str, list[dict]],
    symbols_by_file: dict[str, list[dict]],
    import_edges: list[dict],
) -> list[dict]:
    """Resolve call sites to the symbols they invoke.

    Returns a sorted list of {"caller_file", "caller_symbol",
    "target_file", "target_symbol", "confidence"}. caller_symbol may be
    None when the call sits at module level.

    A call with a "receiver" (self, this, cls) prefers a method of the
    class around it. A bare call prefers the module-level symbols of
    its own file, then those of an imported file, and only then a
    method; a method never beats a module-level symbol of the same name.

    Confidence is EXTRACTED for a call resolved within its own file, and
    for a module-level symbol of a file that the caller imports by an
    EXTRACTED import edge. Everything else resolved here is INFERRED:
    a re-export, a heuristic import, a method of an imported file, or a
    unique owner in an implicit-scope language.
    """
    results = []

    # Build lookup for imports: importer -> set of targets
    imports_by_importer = {}
    for edge in import_edges:
        importer = edge.get("importer")
        target = edge.get("target")
        if importer and target:
            if importer not in imports_by_importer:
                imports_by_importer[importer] = set()
            imports_by_importer[importer].add(target)

    # Targets each importer reaches by an EXTRACTED import edge. Only an
    # explicit EXTRACTED counts, so a missing confidence is not exact.
    # A target reached by two edges is exact if either one is.
    exact_by_importer: dict[str, set] = {}
    for edge in import_edges:
        importer = edge.get("importer")
        target = edge.get("target")
        if importer and target and edge.get("confidence") == "EXTRACTED":
            exact_by_importer.setdefault(importer, set()).add(target)

    # Build symbol lookup: file -> {name -> list of symbols}
    # This allows efficient O(1) lookup by name within a file
    symbols_by_file_and_name = {}
    for file_path, symbols in symbols_by_file.items():
        if file_path not in symbols_by_file_and_name:
            symbols_by_file_and_name[file_path] = {}
        for symbol in symbols:
            name = symbol.get("name")
            if name:
                if name not in symbols_by_file_and_name[file_path]:
                    symbols_by_file_and_name[file_path][name] = []
                symbols_by_file_and_name[file_path][name].append(symbol)

    methods = _method_ids(symbols_by_file)

    # Repo-wide owners of each callable name, for the implicit-scope
    # languages' last-resort lookup.
    owners_by_name: dict[str, list[tuple[str, dict]]] = {}
    for file_path, symbols in symbols_by_file.items():
        for symbol in symbols:
            name = symbol.get("name")
            if name and symbol.get("kind") in _CALLABLE_KINDS:
                owners_by_name.setdefault(name, []).append((file_path, symbol))

    # Process each file's calls
    for file_path, call_list in calls.items():
        # Get symbols in this file
        file_symbols = symbols_by_file.get(file_path, [])
        file_classes = [s for s in file_symbols if s.get("kind") in _CONTAINER_KINDS]

        # Get imports from this file
        imported_targets = imports_by_importer.get(file_path, set())
        exact_targets = exact_by_importer.get(file_path, set())

        # Many calls share a line; the enclosing symbol depends on the
        # line alone.
        caller_by_line: dict[int, dict | None] = {}
        class_by_line: dict[int, dict | None] = {}

        for call in call_list:
            # Step 1: Find the calling symbol and, for self/this/cls
            # calls, the class around the call
            call_line = call.get("line")
            caller = None
            enclosing_class = None
            if call_line is not None:
                if call_line not in caller_by_line:
                    caller_by_line[call_line] = _enclosing_symbol(file_symbols, call_line)
                caller = caller_by_line[call_line]
                if call.get("receiver"):
                    if call_line not in class_by_line:
                        class_by_line[call_line] = _enclosing_symbol(file_classes, call_line)
                    enclosing_class = class_by_line[call_line]
            caller_symbol = caller.get("name") if caller else None
            caller_symbol_id = caller.get("symbol_id") if caller else None

            # Step 2: Generate candidates from the callee name
            callee_name = call.get("name", "")
            candidates = _generate_candidates(callee_name)

            # Step 3: Resolve candidates (first hit wins)
            target_info = None
            for candidate in candidates:
                target_info = _resolve_call_candidate(
                    candidate,
                    file_path,
                    imported_targets,
                    symbols_by_file_and_name,
                    methods,
                    imports_by_importer,
                    enclosing_class,
                    qualifier=call.get("qualifier"),
                    exact_targets=exact_targets,
                )
                if target_info:
                    break
            if target_info is None and candidates:
                target_info = _unique_owner(candidates[0], file_path, owners_by_name)

            if target_info:
                target_file = target_info["file"]
                target_symbol = target_info["symbol"]
                confidence = target_info["confidence"]

                # Never emit self-recursion (same symbol in same
                # file)
                if not (file_path == target_file and caller_symbol == target_symbol):
                    results.append(
                        {
                            "caller_file": file_path,
                            "caller_symbol": caller_symbol,
                            "caller_symbol_id": caller_symbol_id,
                            "target_file": target_file,
                            "target_symbol": target_symbol,
                            "target_symbol_id": target_info.get("symbol_id"),
                            "confidence": confidence,
                        }
                    )

    # Sort deterministically by (caller_file, caller_symbol,
    # target_file, target_symbol)
    results.sort(
        key=lambda r: (
            r["caller_file"],
            r["caller_symbol"] or "",
            r["target_file"],
            r["target_symbol"],
        )
    )

    return results


def _method_ids(symbols_by_file: dict[str, list[dict]]) -> set[int]:
    """ids of the function symbols defined inside a class or interface.

    Grammars give a method the same kind as a free function, so scope
    comes from line containment: a function whose innermost enclosing
    symbol is a class is a method. Spans are sorted outermost first and
    kept on a stack of open ancestors, so this is linear in the symbol
    count.
    ponytail: symbols on one line cannot be told apart by line alone, so
    a minified file may count a function beside a class as its method.
    """
    methods: set[int] = set()
    for file_symbols in symbols_by_file.values():
        spans = [
            s
            for s in file_symbols
            if s.get("line_start") is not None and s.get("line_end") is not None
        ]
        spans.sort(key=lambda s: (s["line_start"], -s["line_end"]))
        stack: list[dict] = []
        for symbol in spans:
            # Ancestors that end before this symbol do not enclose it;
            # the rest of the stack is nested, so one check suffices.
            while stack and stack[-1]["line_end"] < symbol["line_end"]:
                stack.pop()
            if (
                symbol.get("kind") in _FUNCTION_KINDS
                and stack
                and stack[-1].get("kind") in _CONTAINER_KINDS
            ):
                methods.add(id(symbol))
            stack.append(symbol)
    return methods


def _resolve_call_candidate(
    candidate: str,
    caller_file: str,
    imported_targets: set,
    symbols_by_file_and_name: dict,
    methods: set[int],
    imports_by_importer: dict[str, set],
    enclosing_class: dict | None,
    qualifier: str | None = None,
    exact_targets: set | frozenset = frozenset(),
) -> dict | None:
    """Resolve a name used at a call site, by scope.

    Returns {"file": str, "symbol": str, "symbol_id", "confidence": str}
    or None. Tiers in order, each one ending the search when it answers:

    1. A method of the enclosing class (self/this/cls calls only).
       EXTRACTED.
    2. A module-level symbol of the caller's file. EXTRACTED.
    3. A module-level symbol of exactly one imported file. When the
       imported file is a package __init__ that does not define the
       name, the one file it imports is followed (one level of
       re-export, e.g. `from .core import Engine`). When several
       imported files define it and the call is `qualifier.name(...)`,
       the one whose module name is the qualifier wins, so
       `service.create()` picks service.py. The result is EXTRACTED
       only when that file is imported directly, with no re-export and
       no qualifier tie-break, and the import edge is in exact_targets
       (an EXTRACTED import). Otherwise INFERRED.
    4. A method of the caller's file. EXTRACTED.
    5. A method of exactly one imported file. INFERRED.

    A tier that finds names in two files declines rather than pick one.
    """

    def named(path: str) -> list[dict]:
        return symbols_by_file_and_name.get(path, {}).get(candidate, [])

    def module_level(path: str) -> list[dict]:
        return [s for s in named(path) if id(s) not in methods]

    local = named(caller_file)
    if enclosing_class is not None:
        own = [s for s in local if id(s) in methods and _contains(enclosing_class, s)]
        if own:
            return _found(caller_file, own, "EXTRACTED")
    if module_level(caller_file):
        return _found(caller_file, module_level(caller_file), "EXTRACTED")

    definers = {path for path in imported_targets if module_level(path)}
    via_reexport = False
    if not definers:
        via_reexport = True
        for path in imported_targets:
            if posixpath.basename(path) == "__init__.py":
                definers.update(
                    target for target in imports_by_importer.get(path, ()) if module_level(target)
                )
    narrowed = False
    if len(definers) > 1 and qualifier:
        named_for_qualifier = {path for path in definers if _module_name(path) == qualifier}
        if len(named_for_qualifier) == 1:
            definers = named_for_qualifier
            narrowed = True
    if len(definers) == 1:
        path = next(iter(definers))
        exact = not via_reexport and not narrowed and path in exact_targets
        return _found(path, module_level(path), "EXTRACTED" if exact else "INFERRED")
    if len(definers) > 1:
        return None

    if local:
        return _found(caller_file, local, "EXTRACTED")
    method_files = {path for path in imported_targets if named(path)}
    if len(method_files) == 1:
        path = next(iter(method_files))
        return _found(path, named(path), "INFERRED")
    return None


def _module_name(path: str) -> str:
    """The name a file is imported by: its stem, or its folder for
    __init__.py."""
    folder, _, name = path.rpartition("/")
    if name == "__init__.py":
        return folder.rpartition("/")[2]
    return posixpath.splitext(name)[0]


def _found(path: str, symbols: list[dict], confidence: str) -> dict:
    symbol = _pick_best_symbol(symbols)
    return {
        "file": path,
        "symbol": symbol.get("name"),
        "symbol_id": symbol.get("symbol_id"),
        "confidence": confidence,
    }


def _contains(outer: dict, inner: dict) -> bool:
    bounds = (outer.get("line_start"), outer.get("line_end"))
    inner_bounds = (inner.get("line_start"), inner.get("line_end"))
    if None in bounds or None in inner_bounds:
        return False
    return bounds[0] <= inner_bounds[0] and inner_bounds[1] <= bounds[1]


def _unique_owner(name: str, caller_file: str, owners_by_name: dict) -> dict | None:
    """The one symbol in the repo with this name, for the implicit-scope
    languages only, so a Swift file can call a function defined in
    another file of its module. Two or more owners is a conflict and
    declines, as resolve_inheritance does.
    """
    language = language_for(caller_file)
    if language not in _IMPLICIT_SCOPE_LANGUAGES:
        return None
    owners = owners_by_name.get(name, [])
    if len(owners) != 1:
        return None
    path, symbol = owners[0]
    if language_for(path) != language:
        return None
    return {
        "file": path,
        "symbol": name,
        "symbol_id": symbol.get("symbol_id"),
        "confidence": "INFERRED",
    }


def _enclosing_symbol(file_symbols: list[dict], line: int) -> dict | None:
    """The innermost symbol spanning ``line`` (largest line_start), with
    total tie-break.

    Among the symbols sharing the largest line_start, the first in
    symbol_selection_key order wins.
    """
    innermost: dict | None = None
    innermost_start = None
    for symbol in file_symbols:
        line_start = symbol.get("line_start")
        line_end = symbol.get("line_end")
        # Missing bounds cannot enclose anything.
        if line_start is None or line_end is None or not line_start <= line <= line_end:
            continue
        if (
            innermost is None
            or line_start > innermost_start
            or (
                line_start == innermost_start
                and symbol_selection_key(symbol) < symbol_selection_key(innermost)
            )
        ):
            innermost, innermost_start = symbol, line_start
    return innermost


def _generate_candidates(callee_name: str) -> list[str]:
    """Generate candidate identifiers from a callee name.

    From "SessionRepository().extend_expiry", get ["extend_expiry",
    "SessionRepository"] From "fmt.Println", get ["Println", "fmt"] From
    "renew_login", get ["renew_login"]
    """
    # Split on dots first
    parts = callee_name.split(".")

    # Strip call syntax: remove everything from ( onwards in each part
    parts = [p[: p.index("(")] if "(" in p else p for p in parts]

    # Filter out empty parts and strip whitespace
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return []

    # Return: last part first (the actual invoked name), then first part
    # (if different)
    if len(parts) == 1:
        return parts
    else:
        # Last part first, then first part
        candidates = [parts[-1], parts[0]]
        # Remove duplicates while preserving order
        return [c for i, c in enumerate(candidates) if c not in candidates[:i]]


def resolve_inheritance(
    inherits: dict[str, list[dict]],
    symbols_by_file: dict[str, list[dict]],
    import_edges: list[dict],
) -> list[dict]:
    """Resolve `class Bar : BaseThing, IFoo` to the symbols those base
    types name.

    Returns a sorted list of {"child_file", "child_symbol", "base_file",
    "base_symbol", "confidence"}.

    Same resolution order and the same refusal to guess as
    resolve_calls: same file wins, then exactly one imported file,
    otherwise nothing is emitted. In C# the base type usually lives
    behind a namespace `using` rather than a path import, so a large
    share stays unresolved by design rather than being invented.
    """
    imports_by_importer: dict[str, set] = {}
    for edge in import_edges:
        importer = edge.get("importer")
        target = edge.get("target")
        if importer and target:
            imports_by_importer.setdefault(importer, set()).add(target)

    symbols_by_file_and_name: dict[str, dict] = {}
    for file_path, symbols in symbols_by_file.items():
        by_name = symbols_by_file_and_name.setdefault(file_path, {})
        for symbol in symbols:
            name = symbol.get("name")
            if name:
                by_name.setdefault(name, []).append(symbol)

    # Repo-wide index of type names, used only when the import graph
    # cannot answer. C# reaches a base type through `using <namespace>`,
    # not a path import, so path-based resolution finds almost nothing:
    # on a real solution this took inherits edges from 5 to ~507.
    # Restricted to type-like kinds and to names owned by exactly one
    # file, so it resolves rather than guesses -- method names collide
    # constantly, which is why calls deliberately do not get this
    # fallback.
    type_owners: dict[str, set] = {}
    for file_path, symbols in symbols_by_file.items():
        for symbol in symbols:
            # A Perl package is its language's class, but it has the
            # module kind. Elsewhere that kind is only a namespace, so
            # just Perl's module symbols count as type owners.
            is_perl_package = symbol.get("kind") == "module" and symbol.get("language") == "perl"
            if symbol.get("kind") in ("class", "interface", "enum") or is_perl_package:
                name = symbol.get("name")
                if name:
                    type_owners.setdefault(name, set()).add(file_path)

    results = []
    for file_path, entries in inherits.items():
        imported_targets = imports_by_importer.get(file_path, set())
        for entry in entries:
            child = entry.get("type")
            base = entry.get("base")
            if not child or not base:
                continue
            target_info = _resolve_candidate(
                base, file_path, imported_targets, symbols_by_file_and_name
            )
            if not target_info:
                owners = type_owners.get(base, ())
                # Exactly one owner is unambiguous. Two or more is a
                # genuine conflict: picking the lexicographically first
                # would invent an edge the source never justified, so
                # decline instead.
                if len(owners) == 1:
                    owner = next(iter(sorted(owners)))
                    target_info = {"file": owner, "symbol": base, "confidence": "INFERRED"}
            if not target_info:
                continue
            # A type is not its own base; guards a self-edge when a name
            # repeats.
            if file_path == target_info["file"] and child == target_info["symbol"]:
                continue
            results.append(
                {
                    "child_file": file_path,
                    "child_symbol": child,
                    "base_file": target_info["file"],
                    "base_symbol": target_info["symbol"],
                    "confidence": target_info["confidence"],
                }
            )

    results.sort(
        key=lambda r: (r["child_file"], r["child_symbol"], r["base_file"], r["base_symbol"])
    )
    return results


def _resolve_candidate(
    candidate: str,
    caller_file: str,
    imported_targets: set,
    symbols_by_file_and_name: dict,
) -> dict | None:
    """Resolve a candidate identifier.

    Returns {"file": str, "symbol": str, "confidence": str} or None.

    Rules:
    1. Same file -> confidence EXTRACTED
    2. Exactly one imported file defines it -> confidence INFERRED
    3. Otherwise -> None (ambiguous or not found)
    """

    # Rule 1: Same file
    file_symbols = symbols_by_file_and_name.get(caller_file, {})
    if candidate in file_symbols:
        symbol = _pick_best_symbol(file_symbols[candidate])
        return {
            "file": caller_file,
            "symbol": symbol.get("name"),
            "symbol_id": symbol.get("symbol_id"),
            "confidence": "EXTRACTED",
        }

    # Rule 2: Imported files
    # Try to find the candidate in imported files (but only one file)
    found_files = []
    for imported_file in imported_targets:
        imported_symbols = symbols_by_file_and_name.get(imported_file, {})
        if candidate in imported_symbols:
            found_files.append(imported_file)

    # If found in exactly one imported file, resolve it
    if len(found_files) == 1:
        imported_file = found_files[0]
        imported_symbols = symbols_by_file_and_name[imported_file]
        symbol = _pick_best_symbol(imported_symbols[candidate])
        return {
            "file": imported_file,
            "symbol": symbol.get("name"),
            "symbol_id": symbol.get("symbol_id"),
            "confidence": "INFERRED",
        }
    elif len(found_files) > 1:
        # Ambiguous: more than one file exports this name and nothing in
        # the source picks between them. Declining is the contract;
        # selecting the first sorted path would hide the conflict behind
        # a stable-looking answer.
        return None

    # Found in no imported file.
    return None


def _pick_best_symbol(symbol_list: list[dict]) -> dict:
    """Pick the best symbol when multiple symbols have the same name.

    Uses symbol_selection_key for a total order that never depends on
    input order.
    """
    return min(symbol_list, key=symbol_selection_key)


def resolve_doc_links(doc_links: dict[str, list[str]], rel_paths: set[str]) -> list[dict]:
    """Resolve markdown link targets to repo files.

    Returns sorted [{"source": rel_path, "target": rel_path,
    "confidence": str}].
    """
    results = []

    for source, targets in doc_links.items():
        for target in targets:
            result = _resolve_doc_link(source, target, rel_paths)
            if result:
                resolved, confidence = result
                results.append(
                    {
                        "source": source,
                        "target": resolved,
                        "confidence": confidence,
                    }
                )

    # Remove self-loops
    results = [r for r in results if r["source"] != r["target"]]

    # Sort deterministically
    results.sort(key=lambda r: (r["source"], r["target"]))
    return results


def _resolve_doc_link(source: str, target: str, rel_paths: set[str]) -> tuple[str, str] | None:
    """Resolve a single markdown link target.

    Returns (resolved_path, confidence) or None if unresolvable.
    """
    # Rule 1: Absolute path (starts with /)
    if target.startswith("/"):
        candidate = target.lstrip("/")
        if candidate in rel_paths:
            return (candidate, "EXTRACTED")
        # Continue to other rules if exact match not found
        target = candidate

    # Rule 2: Relative path - join against source's directory
    source_dir = posixpath.dirname(source)
    candidate = posixpath.normpath(posixpath.join(source_dir, target))
    if candidate in rel_paths:
        return (candidate, "EXTRACTED")

    # Rule 3: Bare basename - unique match in rel_paths (handles
    # wiki-link matching wiki-link.md)
    if "/" not in target:
        basename_matches = []
        target_lower = target.lower()
        for path in rel_paths:
            path_basename = posixpath.basename(path)
            path_basename_lower = path_basename.lower()
            # Check exact match or match without extension
            if path_basename_lower == target_lower:
                basename_matches.append(path)
            elif path_basename_lower.rsplit(".", 1)[0] == target_lower:
                basename_matches.append(path)

        if len(basename_matches) == 1:
            return (basename_matches[0], "INFERRED")
        elif len(basename_matches) > 1:
            # Multiple matches; pick the one with earliest sorted path
            return (sorted(basename_matches)[0], "INFERRED")

    return None


def resolve_table_refs(
    table_refs: dict[str, list[str]], table_symbols: dict[str, list[tuple[str, str]]]
) -> list[dict]:
    """Link files that query a table to the file whose schema defines
    it.

    table_symbols maps a lowercased table name to [(rel_path,
    symbol_id), ...]. Returns sorted [{"source": rel_path,
    "target_file": rel_path,
                     "target_symbol_id": str, "confidence": str}].
    """
    results = []

    for source, tables in table_refs.items():
        for table in tables:
            # Look up the lowercased table name
            definitions = table_symbols.get(table.lower(), [])

            # Only emit if exactly one file defines the table
            if len(definitions) == 1:
                target_file, symbol_id = definitions[0]
                # Never emit edge from file to itself
                if source != target_file:
                    results.append(
                        {
                            "source": source,
                            "target_file": target_file,
                            "target_symbol_id": symbol_id,
                            "confidence": "INFERRED",
                        }
                    )
            elif len(definitions) > 1:
                # Multiple definitions; pick the one with earliest
                # sorted path and symbol_id
                sorted_defs = sorted(definitions, key=lambda d: (d[0], d[1]))
                target_file, symbol_id = sorted_defs[0]
                # Never emit edge from file to itself
                if source != target_file:
                    results.append(
                        {
                            "source": source,
                            "target_file": target_file,
                            "target_symbol_id": symbol_id,
                            "confidence": "INFERRED",
                        }
                    )

    # Sort deterministically
    results.sort(key=lambda r: (r["source"], r["target_file"], r["target_symbol_id"]))
    return results
