from __future__ import annotations

import posixpath
from typing import Optional


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

    # Rule 2: Dotted absolute module (Python) - contains . and no /
    if "." in module and "/" not in module:
        return _rule2_dotted_absolute(module, rel_paths)

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

                # Pick the innermost (largest line_start)
                if enclosing_symbols:
                    caller_symbol = max(enclosing_symbols, key=lambda s: s.get("line_start", 0)).get("name")

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
                                "target_file": target_file,
                                "target_symbol": target_symbol,
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
                    owner = next(iter(owners))
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
            "confidence": "INFERRED",
        }

    # If found in multiple imported files or none -> None
    return None


def _pick_best_symbol(symbol_list: list[dict]) -> dict:
    """Pick the best symbol when multiple symbols have the same name.

    Prefer: function/method > class > other
    On tie: lowest line_start (first defined)
    """

    def key_func(s):
        kind = s.get("kind", "")
        # Prefer function/method (score 0) over class (score 1) over others (score 2)
        kind_score = 0 if kind in ("function", "method") else (1 if kind == "class" else 2)
        line_start = s.get("line_start", float("inf"))
        return (kind_score, line_start)

    return min(symbol_list, key=key_func)


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

    # Sort deterministically
    results.sort(key=lambda r: (r["source"], r["target_file"], r["target_symbol_id"]))
    return results


if __name__ == "__main__":
    import sys
    import json

    # Test fixture from the task
    imports = {
        "backend/api/auth.py": ["backend.services.session_service"],
        "backend/services/session_service.py": ["backend.repositories.session_repository"],
        "backend/tests/test_auth.py": ["backend.services.session_service"],
        "web/tests/checkout.test.tsx": ["../src/lib/payments"],
        "web/src/app/checkout/page.tsx": ["../../features/checkout/useCheckout"],
        "web/src/features/checkout/useCheckout.ts": ["../../lib/payments"],
    }

    rel_paths = {
        "archive/checkout-old.ts",
        "backend/api/auth.py",
        "backend/middleware/auth.py",
        "backend/repositories/session_repository.py",
        "backend/services/session_service.py",
        "backend/tests/test_auth.py",
        "db/migrations/20260701_add_session_expiry.sql",
        "db/schema.sql",
        "docs/auth-design.md",
        "docs/checkout-plan.md",
        "examples/auth_demo.py",
        "pyproject.toml",
        "web/src/app/checkout/page.tsx",
        "web/src/features/checkout/useCheckout.ts",
        "web/src/lib/payments.ts",
        "web/tests/checkout.test.tsx",
    }

    results = resolve_imports(imports, rel_paths)

    # Self-checks
    try:
        # 1. All six imports resolve
        assert len(results) == 6, f"Expected 6 results, got {len(results)}: {results}"

        # 2. Every result has confidence EXTRACTED (rules 1 and 2 only in this fixture)
        for r in results:
            assert r["confidence"] == "EXTRACTED", f"Expected EXTRACTED, got {r['confidence']} for {r}"

        # 3. web/src/lib/payments.ts is target of TWO different importers
        payments_targets = [r for r in results if r["target"] == "web/src/lib/payments.ts"]
        assert len(payments_targets) == 2, (
            f"Expected 2 imports of payments, got {len(payments_targets)}: {payments_targets}"
        )
        importers_of_payments = {r["importer"] for r in payments_targets}
        expected_importers = {
            "web/tests/checkout.test.tsx",
            "web/src/features/checkout/useCheckout.ts",
        }
        assert importers_of_payments == expected_importers, f"Wrong importers: {importers_of_payments}"

        # 4. No result has importer == target
        for r in results:
            assert r["importer"] != r["target"], f"Self-loop: {r}"

        # 5. Every target is in rel_paths
        for r in results:
            assert r["target"] in rel_paths, f"Target {r['target']} not in rel_paths"

        # 6. No backslash in any path
        for r in results:
            assert "\\" not in r["importer"], f"Backslash in importer: {r['importer']}"
            assert "\\" not in r["target"], f"Backslash in target: {r['target']}"

        # 7. Unresolvable import produces NO result
        test_unresolvable = resolve_imports({"test/file.py": ["react"]}, rel_paths)
        assert len(test_unresolvable) == 0, f"Expected 0 results for unresolvable, got {test_unresolvable}"

        # 8. Ambiguous rule-4 suffix produces NO result
        ambiguous_paths = {"a/util.go", "b/util.go"}
        test_ambiguous = resolve_imports({"test/file.go": ["util"]}, ambiguous_paths)
        assert len(test_ambiguous) == 0, f"Expected 0 results for ambiguous suffix, got {test_ambiguous}"

        # 9. Calling twice yields identical output
        results2 = resolve_imports(imports, rel_paths)
        assert results == results2, "Output is not deterministic"

        # 10. Empty inputs return []
        assert resolve_imports({}, set()) == [], "Empty inputs should return []"

        # 11. Windows safety: resolve ../../lib/payments from web/src/features/checkout/useCheckout.ts
        # should yield exactly web/src/lib/payments.ts with forward slashes
        test_windows = resolve_imports(
            {"web/src/features/checkout/useCheckout.ts": ["../../lib/payments"]},
            rel_paths,
        )
        assert len(test_windows) == 1, f"Expected 1 result for Windows test, got {len(test_windows)}"
        assert test_windows[0]["target"] == "web/src/lib/payments.ts", f"Got {test_windows[0]['target']}"
        assert "/" in test_windows[0]["target"], "Target should have forward slashes"
        assert "\\" not in test_windows[0]["target"], "Target should not have backslashes"

        # ===== Tests for resolve_calls =====

        # Fixture from task
        calls = {
            "backend/api/auth.py": [{"name": "renew_login", "line": 5}],
            "backend/services/session_service.py": [
                {"name": "SessionRepository().extend_expiry", "line": 5},
                {"name": "SessionRepository", "line": 5},
            ],
            "web/src/lib/payments.ts": [{"name": "window.location.assign", "line": 1}],
        }
        symbols_by_file = {
            "backend/api/auth.py": [
                {
                    "name": "refresh_session",
                    "kind": "function",
                    "line_start": 4,
                    "line_end": 5,
                }
            ],
            "backend/services/session_service.py": [
                {"name": "renew_login", "kind": "function", "line_start": 4, "line_end": 5}
            ],
            "backend/repositories/session_repository.py": [
                {"name": "SessionRepository", "kind": "class", "line_start": 1, "line_end": 4},
                {
                    "name": "extend_expiry",
                    "kind": "function",
                    "line_start": 2,
                    "line_end": 4,
                },
            ],
            "web/src/lib/payments.ts": [
                {
                    "name": "redirectToPayment",
                    "kind": "function",
                    "line_start": 1,
                    "line_end": 1,
                }
            ],
        }
        import_edges_for_calls = [
            {
                "importer": "backend/api/auth.py",
                "target": "backend/services/session_service.py",
                "confidence": "EXTRACTED",
            },
            {
                "importer": "backend/services/session_service.py",
                "target": "backend/repositories/session_repository.py",
                "confidence": "EXTRACTED",
            },
        ]

        call_results = resolve_calls(calls, symbols_by_file, import_edges_for_calls)

        # Assertion 1: backend/api/auth.py produces a call edge with
        # caller_symbol == "refresh_session", target_symbol == "renew_login"
        auth_edges = [
            r for r in call_results if r["caller_file"] == "backend/api/auth.py" and r["target_symbol"] == "renew_login"
        ]
        assert len(auth_edges) == 1, f"Expected 1 auth edge to renew_login, got {len(auth_edges)}: {auth_edges}"
        assert auth_edges[0]["caller_symbol"] == "refresh_session", (
            f"Expected caller_symbol refresh_session, got {auth_edges[0]['caller_symbol']}"
        )
        assert auth_edges[0]["target_file"] == "backend/services/session_service.py", (
            f"Expected target backend/services/session_service.py, got {auth_edges[0]['target_file']}"
        )
        assert auth_edges[0]["confidence"] == "INFERRED", f"Expected INFERRED, got {auth_edges[0]['confidence']}"

        # Assertion 2: backend/services/session_service.py produces an edge to extend_expiry
        extend_expiry_edges = [
            r
            for r in call_results
            if r["caller_file"] == "backend/services/session_service.py" and r["target_symbol"] == "extend_expiry"
        ]
        assert len(extend_expiry_edges) == 1, (
            f"Expected 1 extend_expiry edge, got {len(extend_expiry_edges)}: {extend_expiry_edges}"
        )
        assert extend_expiry_edges[0]["target_file"] == "backend/repositories/session_repository.py", (
            f"Unexpected target file: {extend_expiry_edges[0]['target_file']}"
        )

        # Assertion 3: window.location.assign resolves to NOTHING
        assign_edges = [r for r in call_results if r.get("target_symbol") == "assign"]
        assert len(assign_edges) == 0, f"Expected no edges for 'assign', got {len(assign_edges)}: {assign_edges}"

        # Assertion 4: Every result has exactly five keys
        for r in call_results:
            keys = set(r.keys())
            expected = {"caller_file", "caller_symbol", "target_file", "target_symbol", "confidence"}
            assert keys == expected, f"Result {r} has wrong keys: {keys}, expected {expected}"

        # Assertion 5: No edge has caller and target being the same symbol in same file
        for r in call_results:
            if r["caller_file"] == r["target_file"]:
                assert r["caller_symbol"] != r["target_symbol"], f"Self-recursion detected: {r}"

        # Assertion 6: Confidence values are only EXTRACTED or INFERRED
        for r in call_results:
            assert r["confidence"] in (
                "EXTRACTED",
                "INFERRED",
            ), f"Invalid confidence: {r['confidence']}"

        # Assertion 7: Call at line inside no definition yields caller_symbol None
        test_module_level = resolve_calls(
            {
                "backend/api/auth.py": [{"name": "some_func", "line": 100}]  # line 100 not inside any definition
            },
            symbols_by_file,
            import_edges_for_calls,
        )
        # Should resolve or not depending on whether some_func exists, but shouldn't crash
        assert isinstance(test_module_level, list), "Should return a list without crashing"

        # Assertion 8: Calling twice yields identical output
        call_results2 = resolve_calls(calls, symbols_by_file, import_edges_for_calls)
        assert call_results == call_results2, f"Non-deterministic output:\n{call_results}\nvs\n{call_results2}"

        # Assertion 9: resolve_calls({}, {}, []) returns []
        empty_result = resolve_calls({}, {}, [])
        assert empty_result == [], f"Empty inputs should return [], got {empty_result}"

        # Assertion 10: A name defined in TWO different imported files yields NO edge
        # Create a scenario where extend_expiry is in two files
        ambiguous_calls = {"backend/api/auth.py": [{"name": "shared_func", "line": 5}]}
        ambiguous_symbols = {
            "backend/api/auth.py": [
                {
                    "name": "refresh_session",
                    "kind": "function",
                    "line_start": 4,
                    "line_end": 5,
                }
            ],
            "file_a.py": [{"name": "shared_func", "kind": "function", "line_start": 1, "line_end": 2}],
            "file_b.py": [{"name": "shared_func", "kind": "function", "line_start": 1, "line_end": 2}],
        }
        ambiguous_imports = [
            {"importer": "backend/api/auth.py", "target": "file_a.py", "confidence": "EXTRACTED"},
            {"importer": "backend/api/auth.py", "target": "file_b.py", "confidence": "EXTRACTED"},
        ]
        ambiguous_result = resolve_calls(ambiguous_calls, ambiguous_symbols, ambiguous_imports)
        # Should have no edge because shared_func is ambiguous
        shared_func_edges = [
            r
            for r in ambiguous_result
            if r["caller_file"] == "backend/api/auth.py" and r["target_symbol"] == "shared_func"
        ]
        assert len(shared_func_edges) == 0, (
            f"Expected no edge for ambiguous shared_func, got {len(shared_func_edges)}: {shared_func_edges}"
        )

        # Assertion 11: No path contains backslash
        for r in call_results:
            assert "\\" not in r["caller_file"], f"Backslash in caller_file: {r['caller_file']}"
            assert "\\" not in r["target_file"], f"Backslash in target_file: {r['target_file']}"

        # ===== Tests for resolve_doc_links =====

        # Test fixture: markdown docs with various link types
        doc_links = {
            "docs/auth-guide.md": ["../src/auth/session.py", "https://example.com", "#anchor-only", "wiki-link"],
            "docs/checkout.md": ["/backend/payments.py"],
        }

        rel_paths_for_docs = {
            "src/auth/session.py",
            "backend/payments.py",
            "wiki-link.md",
            "docs/auth-guide.md",
            "docs/checkout.md",
        }

        doc_link_results = resolve_doc_links(doc_links, rel_paths_for_docs)

        # Assertion 1: doc link to code file resolves EXTRACTED
        auth_to_session = [
            r for r in doc_link_results if r["source"] == "docs/auth-guide.md" and r["target"] == "src/auth/session.py"
        ]
        assert len(auth_to_session) == 1, (
            f"Expected 1 link from auth-guide to session.py, got {len(auth_to_session)}: {auth_to_session}"
        )
        assert auth_to_session[0]["confidence"] == "EXTRACTED", (
            f"Expected EXTRACTED, got {auth_to_session[0]['confidence']}"
        )

        # Assertion 2: absolute path resolves correctly
        checkout_to_payments = [
            r for r in doc_link_results if r["source"] == "docs/checkout.md" and r["target"] == "backend/payments.py"
        ]
        assert len(checkout_to_payments) == 1, (
            f"Expected 1 link from checkout to payments, got {len(checkout_to_payments)}: {checkout_to_payments}"
        )

        # Assertion 3: wiki-link resolves INFERRED by unique basename
        auth_to_wiki = [
            r for r in doc_link_results if r["source"] == "docs/auth-guide.md" and "wiki-link" in r["target"]
        ]
        assert len(auth_to_wiki) == 1, f"Expected wiki-link to resolve uniquely, got {len(auth_to_wiki)}"
        assert auth_to_wiki[0]["confidence"] == "INFERRED", (
            f"Expected INFERRED for wiki-link, got {auth_to_wiki[0]['confidence']}"
        )

        # Assertion 4: external URLs (https://) produce no edge
        https_links = [r for r in doc_link_results if "example.com" in r.get("target", "")]
        assert len(https_links) == 0, f"Expected no edge for external URL, got {https_links}"

        # Assertion 5: anchors-only produce no edge
        anchor_links = [r for r in doc_link_results if r.get("target") == "#anchor-only"]
        assert len(anchor_links) == 0, f"Expected no edge for anchor-only, got {anchor_links}"

        # Assertion 6: No self-loops
        for r in doc_link_results:
            assert r["source"] != r["target"], f"Self-loop detected: {r}"

        # Assertion 7: No backslashes
        for r in doc_link_results:
            assert "\\" not in r["source"], f"Backslash in source: {r['source']}"
            assert "\\" not in r["target"], f"Backslash in target: {r['target']}"

        # ===== Tests for resolve_table_refs =====

        # Test fixture: SQL table references
        table_refs = {
            "backend/repositories/session_repository.py": ["sessions", "user_sessions"],
            "backend/migrations/init.sql": ["sessions"],
            "backend/api/auth.py": ["USERS", "payments"],  # case-insensitive
        }

        # table_symbols: table_name.lower() -> [(rel_path, symbol_id), ...]
        table_symbols = {
            "sessions": [("db/schema.sql", "sql_table_sessions")],
            "user_sessions": [("db/schema.sql", "sql_table_user_sessions")],
            "users": [("db/schema.sql", "sql_table_users")],
            "payments": [
                ("db/schema.sql", "sql_table_payments_v1"),
                ("db/schema.sql", "sql_table_payments_v2"),
            ],  # ambiguous
        }

        table_ref_results = resolve_table_refs(table_refs, table_symbols)

        # Assertion 8: table referenced by exactly one schema resolves INFERRED
        session_refs = [r for r in table_ref_results if r["target_symbol_id"] == "sql_table_sessions"]
        assert len(session_refs) > 0, f"Expected reference to sessions table, got {table_ref_results}"
        assert session_refs[0]["confidence"] == "INFERRED", f"Expected INFERRED, got {session_refs[0]['confidence']}"

        # Assertion 9: table defined in two files produces NO edge
        payment_refs = [r for r in table_ref_results if "payments" in r.get("target_symbol_id", "")]
        assert len(payment_refs) == 0, f"Expected no edge for ambiguous payment table, got {payment_refs}"

        # Assertion 10: case-insensitive lookup
        user_refs = [r for r in table_ref_results if "users" in r.get("target_symbol_id", "")]
        assert len(user_refs) > 0, f"Expected case-insensitive lookup for USERS, got {table_ref_results}"

        # Assertion 11: No file-to-itself edges
        for r in table_ref_results:
            assert r["source"] != r["target_file"], f"File-to-itself edge: {r}"

        # Assertion 12: No backslashes
        for r in table_ref_results:
            assert "\\" not in r["source"], f"Backslash in source: {r['source']}"
            assert "\\" not in r["target_file"], f"Backslash in target_file: {r['target_file']}"

        print("OK")
        sys.exit(0)

    except AssertionError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
