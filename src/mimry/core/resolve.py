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


def _resolve_import(
    importer: str, module: str, rel_paths: set[str], suffix_lookup: dict
) -> Optional[dict]:
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


def _rule4_suffix_match(
    module: str, rel_paths: set[str], suffix_lookup: dict
) -> Optional[dict]:
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
            assert r["confidence"] == "EXTRACTED", (
                f"Expected EXTRACTED, got {r['confidence']} for {r}"
            )

        # 3. web/src/lib/payments.ts is target of TWO different importers
        payments_targets = [
            r for r in results if r["target"] == "web/src/lib/payments.ts"
        ]
        assert len(payments_targets) == 2, (
            f"Expected 2 imports of payments, got {len(payments_targets)}: {payments_targets}"
        )
        importers_of_payments = {r["importer"] for r in payments_targets}
        expected_importers = {
            "web/tests/checkout.test.tsx",
            "web/src/features/checkout/useCheckout.ts",
        }
        assert (
            importers_of_payments == expected_importers
        ), f"Wrong importers: {importers_of_payments}"

        # 4. No result has importer == target
        for r in results:
            assert r["importer"] != r["target"], f"Self-loop: {r}"

        # 5. Every target is in rel_paths
        for r in results:
            assert r["target"] in rel_paths, (
                f"Target {r['target']} not in rel_paths"
            )

        # 6. No backslash in any path
        for r in results:
            assert "\\" not in r["importer"], (
                f"Backslash in importer: {r['importer']}"
            )
            assert "\\" not in r["target"], f"Backslash in target: {r['target']}"

        # 7. Unresolvable import produces NO result
        test_unresolvable = resolve_imports(
            {"test/file.py": ["react"]}, rel_paths
        )
        assert len(test_unresolvable) == 0, (
            f"Expected 0 results for unresolvable, got {test_unresolvable}"
        )

        # 8. Ambiguous rule-4 suffix produces NO result
        ambiguous_paths = {"a/util.go", "b/util.go"}
        test_ambiguous = resolve_imports(
            {"test/file.go": ["util"]}, ambiguous_paths
        )
        assert len(test_ambiguous) == 0, (
            f"Expected 0 results for ambiguous suffix, got {test_ambiguous}"
        )

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
        assert len(test_windows) == 1, (
            f"Expected 1 result for Windows test, got {len(test_windows)}"
        )
        assert test_windows[0]["target"] == "web/src/lib/payments.ts", (
            f"Got {test_windows[0]['target']}"
        )
        assert "/" in test_windows[0]["target"], (
            "Target should have forward slashes"
        )
        assert "\\" not in test_windows[0]["target"], (
            "Target should not have backslashes"
        )

        print("OK")
        sys.exit(0)

    except AssertionError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
