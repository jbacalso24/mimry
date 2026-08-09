from __future__ import annotations

import posixpath
from typing import Optional

SYMBOL_KIND_PRIORITY = {"class": 0, "interface": 1, "function": 2, "method": 3, "sql_table": 4}


def symbol_selection_key(symbol, rel_path=""):
    """Total, documented order for resolving a symbol reference to one definition.

    Kind priority first (a class beats a loose function of the same name), then
    normalized repository-relative path, line start, line end, canonical symbol
    id. Every field is canonical, so the winner never depends on input order.
    """
    return (
        SYMBOL_KIND_PRIORITY.get(symbol.get("kind"), 99),
        rel_path,
        symbol.get("line_start") or 0,
        symbol.get("line_end") or 0,
        symbol.get("symbol_id") or symbol.get("name") or "",
    )


def resolve_imports(imports: dict[str, list[str]], rel_paths: set[str]) -> list[dict]:
    """Resolve import strings to repo-relative target files.

    Args:
        imports: Dict mapping importer rel_path to list of module strings
        rel_paths: Set of all repo-relative file paths that exist

    Returns:
        Sorted list of {"importer": str, "target": str, "confidence": str}.
        Confidence is "EXTRACTED" (rule 1/2) or "INFERRED" (rule 3/4).
        Unresolvable imports are dropped; no result for ambiguous rule-4 matches.
    """
    results = []

    # Precompute suffix lookup for rule 4 efficiency
    suffix_lookup = _build_suffix_lookup(rel_paths)

    for importer, module_list in imports.items():
        for module in module_list:
            target = _resolve_import(importer, module, rel_paths, suffix_lookup)
            if target:
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


def _resolve_import(importer: str, module: str, rel_paths: set[str], suffix_lookup: dict) -> Optional[dict]:
    """Try to resolve a single import against available paths.

    Returns {"path": str, "confidence": str} or None if unresolvable.
    Apply rules in order; first unambiguous match wins.
    """

    # Sanitize module string
    module = module.strip() if module else ""
    if not module or module in (".", ".."):
        return None

    # Rule 1: Relative specifier (JS/TS) - starts with ./ or ../
    if module.startswith("./") or module.startswith("../"):
        return _rule1_relative(importer, module, rel_paths)

    # Python relative import. The leading-dot count is ImportFrom.level, so one
    # dot stays in the current package and each additional dot climbs a package.
    if module.startswith("."):
        return _rule1_python_relative(importer, module, rel_paths)

    # Rule 2: Dotted absolute module (Python) - contains . and no /
    if "." in module and "/" not in module and importer.endswith(".py"):
        return _rule2_dotted_absolute(module, rel_paths)
    # A dotted module from a non-Python importer is a Java/Kotlin-style package,
    # not a module path. Rule 2 probes <module>.py and <module>/__init__.py, so
    # returning its verdict for `com.app.model.User` discarded the import before
    # rule 4 ever saw it. Rule 3 declines anything dotted, so control reaches the
    # suffix match, which resolves it or emits nothing.

    # Rule 3: Dotted/bare module relative to importer's package (Python)
    result = _rule3_relative_to_package(importer, module, rel_paths)
    if result:
        return result

    # Rule 4: Suffix match
    return _rule4_suffix_match(module, rel_paths, suffix_lookup)


def _rule1_relative(importer: str, module: str, rel_paths: set[str]) -> Optional[dict]:
    """Rule 1: Relative specifier (JS/TS).

    Base = importer's directory
    Join base + module, normalize away ./ and ../
    Probe: exact path, then +extensions, then +/index+extensions
    Confidence: EXTRACTED
    """
    importer_dir = posixpath.dirname(importer)

    # Join and normalize
    candidate = posixpath.normpath(posixpath.join(importer_dir, module))

    # Probe: exact path
    if candidate in rel_paths:
        return {"path": candidate, "confidence": "EXTRACTED"}

    # Probe: with extensions
    for ext in [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]:
        with_ext = candidate + ext
        if with_ext in rel_paths:
            return {"path": with_ext, "confidence": "EXTRACTED"}

    # Probe: /index with extensions
    index_base = posixpath.join(candidate, "index")
    for ext in [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]:
        with_index = index_base + ext
        if with_index in rel_paths:
            return {"path": with_index, "confidence": "EXTRACTED"}

    return None


def _rule1_python_relative(importer: str, module: str, rel_paths: set[str]) -> Optional[dict]:
    """Resolve a leading-dot Python import using its declared relative level."""
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


def _rule2_dotted_absolute(module: str, rel_paths: set[str]) -> Optional[dict]:
    """Rule 2: Dotted absolute module (Python).

    Module contains . and no /
    Candidate = module with . replaced by /
    Probe: <candidate>.py then <candidate>/__init__.py
    Confidence: EXTRACTED
    """
    candidate = module.replace(".", "/")

    # Probe: <candidate>.py
    py_file = candidate + ".py"
    if py_file in rel_paths:
        return {"path": py_file, "confidence": "EXTRACTED"}

    # Probe: <candidate>/__init__.py
    init_file = candidate + "/__init__.py"
    if init_file in rel_paths:
        return {"path": init_file, "confidence": "EXTRACTED"}

    return None


def _rule3_relative_to_package(importer: str, module: str, rel_paths: set[str]) -> Optional[dict]:
    """Rule 3: Relative to importer's package (Python).

    Try same as rule 2 but rooted at importer's directory.
    This handles the case where scanner recorded `from .payments import x` as just `"payments"`.
    Only applies to bare module names (no / or .).
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


def _rule4_suffix_match(module: str, rel_paths: set[str], suffix_lookup: dict) -> Optional[dict]:
    r"""Rule 4: Suffix match (Go, Rust, C#, bare JS specifiers).

    Normalize module by replacing :: and . and \ with /
    Find paths whose extension-stripped value ends with normalized suffix on path-segment boundary
    If EXACTLY ONE match, emit with confidence INFERRED
    Otherwise emit nothing (never guess between candidates)
    """
    # Normalize the module
    normalized = module.replace("::", "/").replace(".", "/").replace("\\", "/")
    normalized = normalized.strip("/")  # Strip leading/trailing /

    if not normalized:
        return None

    # Look up in suffix_lookup
    matches = suffix_lookup.get(normalized, [])

    if len(matches) == 1:
        return {"path": matches[0], "confidence": "INFERRED"}

    return None  # Zero or multiple matches -> don't guess


def _build_suffix_lookup(rel_paths: set[str]) -> dict:
    """Precompute suffix lookup for rule 4 efficiency.

    Map from normalized suffix to list of matching paths.
    A path matches if, when its extension is removed, the result ends with
    the suffix on a path-segment boundary.
    """
    suffix_lookup = {}

    for path in rel_paths:
        # Remove extension
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


def resolve_calls(
    calls: dict[str, list[dict]],
    symbols_by_file: dict[str, list[dict]],
    import_edges: list[dict],
) -> list[dict]:
    """Resolve call sites to the symbols they invoke.

    Returns a sorted list of
    {"caller_file", "caller_symbol", "target_file", "target_symbol", "confidence"}.
    caller_symbol may be None when the call sits at module level.
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

    # Process each file's calls
    for file_path, call_list in calls.items():
        # Get symbols in this file
        file_symbols = symbols_by_file.get(file_path, [])

        # Get imports from this file
        imported_targets = imports_by_importer.get(file_path, set())

        for call in call_list:
            # Step 1: Find the calling symbol
            call_line = call.get("line")
            caller_symbol = None

            if call_line is not None:
                # Find the innermost symbol that encloses this line
                enclosing_symbols = []
                for symbol in file_symbols:
                    line_start = symbol.get("line_start")
                    line_end = symbol.get("line_end")

                    # Handle missing values - cannot determine caller
                    if line_start is None or line_end is None:
                        continue

                    if line_start <= call_line <= line_end:
                        enclosing_symbols.append(symbol)

                # Pick the innermost (largest line_start), with total tie-break
                if enclosing_symbols:
                    caller = max(
                        sorted(enclosing_symbols, key=symbol_selection_key),
                        key=lambda s: s.get("line_start") or 0,
                    )
                    caller_symbol = caller.get("name")
                    caller_symbol_id = caller.get("symbol_id")
                else:
                    caller_symbol_id = None
            else:
                caller_symbol_id = None

            # Step 2: Generate candidates from the callee name
            callee_name = call.get("name", "")
            candidates = _generate_candidates(callee_name)

            # Step 3: Resolve candidates (first hit wins)
            for candidate in candidates:
                target_info = _resolve_candidate(
                    candidate,
                    file_path,
                    imported_targets,
                    symbols_by_file_and_name,
                )

                if target_info:
                    target_file = target_info["file"]
                    target_symbol = target_info["symbol"]
                    confidence = target_info["confidence"]

                    # Never emit self-recursion (same symbol in same file)
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
                    # First hit wins - don't try other candidates
                    break

    # Sort deterministically by (caller_file, caller_symbol, target_file, target_symbol)
    results.sort(key=lambda r: (r["caller_file"], r["caller_symbol"] or "", r["target_file"], r["target_symbol"]))

    return results


def _generate_candidates(callee_name: str) -> list[str]:
    """Generate candidate identifiers from a callee name.

    From "SessionRepository().extend_expiry", get ["extend_expiry", "SessionRepository"]
    From "fmt.Println", get ["Println", "fmt"]
    From "renew_login", get ["renew_login"]
    """
    # Split on dots first
    parts = callee_name.split(".")

    # Strip call syntax: remove everything from ( onwards in each part
    parts = [p[: p.index("(")] if "(" in p else p for p in parts]

    # Filter out empty parts and strip whitespace
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return []

    # Return: last part first (the actual invoked name), then first part (if different)
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
    """Resolve `class Bar : BaseThing, IFoo` to the symbols those base types name.

    Returns a sorted list of
    {"child_file", "child_symbol", "base_file", "base_symbol", "confidence"}.

    Same resolution order and the same refusal to guess as resolve_calls: same file
    wins, then exactly one imported file, otherwise nothing is emitted. In C# the
    base type usually lives behind a namespace `using` rather than a path import, so
    a large share stays unresolved by design rather than being invented.
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

    # Repo-wide index of type names, used only when the import graph cannot answer.
    # C# reaches a base type through `using <namespace>`, not a path import, so
    # path-based resolution finds almost nothing: on a real solution this took
    # inherits edges from 5 to ~507. Restricted to type-like kinds and to names owned
    # by exactly one file, so it resolves rather than guesses -- method names collide
    # constantly, which is why calls deliberately do not get this fallback.
    type_owners: dict[str, set] = {}
    for file_path, symbols in symbols_by_file.items():
        for symbol in symbols:
            if symbol.get("kind") in ("class", "interface", "enum"):
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
            target_info = _resolve_candidate(base, file_path, imported_targets, symbols_by_file_and_name)
            if not target_info:
                owners = type_owners.get(base, ())
                if len(owners) == 1:
                    owner = sorted(owners)[0]
                    target_info = {"file": owner, "symbol": base, "confidence": "INFERRED"}
                elif len(owners) > 1:
                    # Multiple files own this type; pick the one with earliest sorted path
                    owner = sorted(owners)[0]
                    target_info = {"file": owner, "symbol": base, "confidence": "INFERRED"}
            if not target_info:
                continue
            # A type is not its own base; guards a self-edge when a name repeats.
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

    results.sort(key=lambda r: (r["child_file"], r["child_symbol"], r["base_file"], r["base_symbol"]))
    return results


def _resolve_candidate(
    candidate: str,
    caller_file: str,
    imported_targets: set,
    symbols_by_file_and_name: dict,
) -> Optional[dict]:
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
        # Multiple files have this symbol; pick the one with earliest sorted path
        found_files.sort()
        imported_file = found_files[0]
        imported_symbols = symbols_by_file_and_name[imported_file]
        symbol = _pick_best_symbol(imported_symbols[candidate])
        return {
            "file": imported_file,
            "symbol": symbol.get("name"),
            "symbol_id": symbol.get("symbol_id"),
            "confidence": "INFERRED",
        }

    # If found in multiple imported files or none -> None
    return None


def _pick_best_symbol(symbol_list: list[dict]) -> dict:
    """Pick the best symbol when multiple symbols have the same name.

    Uses symbol_selection_key for a total order that never depends on input order.
    """
    return min(symbol_list, key=symbol_selection_key)


def resolve_doc_links(doc_links: dict[str, list[str]], rel_paths: set[str]) -> list[dict]:
    """Resolve markdown link targets to repo files.

    Returns sorted [{"source": rel_path, "target": rel_path, "confidence": str}].
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


def _resolve_doc_link(source: str, target: str, rel_paths: set[str]) -> Optional[tuple[str, str]]:
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

    # Rule 3: Bare basename - unique match in rel_paths (handles wiki-link matching wiki-link.md)
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


def resolve_table_refs(table_refs: dict[str, list[str]], table_symbols: dict[str, list[tuple[str, str]]]) -> list[dict]:
    """Link files that query a table to the file whose schema defines it.

    table_symbols maps a lowercased table name to [(rel_path, symbol_id), ...].
    Returns sorted [{"source": rel_path, "target_file": rel_path,
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
                # Multiple definitions; pick the one with earliest sorted path and symbol_id
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
