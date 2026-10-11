"""Read-only diagnostics for a MIMRY installation and root."""

from __future__ import annotations

import json
import re
from importlib import metadata
from pathlib import Path
from typing import Any

from . import __version__
from .commands import _health, _repair_command
from .paths import pointer_file
from .state import validate_generation

_SCOPE = [
    "package metadata and direct runtime dependencies",
    "tree-sitter parser construction",
    "root pointer, active index generation, freshness, graph, and semantic cache",
]
_NOT_CHECKED = [
    "network access, credentials, or remote services",
    "agent-skill installation and MCP process startup",
    "filesystem write access",
    "automatic repair",
]


def _check(check_id: str, status: str, summary: str, suggestion: str | None = None, **data: Any):
    result = {"id": check_id, "status": status, "summary": summary}
    if suggestion:
        result["suggestion"] = suggestion
    result.update(data)
    return result


def _dependency_checks() -> list[dict[str, Any]]:
    fix = _repair_command()
    try:
        installed = metadata.version("mimry")
        requirements = metadata.requires("mimry") or []
    except metadata.PackageNotFoundError:
        return [
            _check(
                "installation.metadata",
                "error",
                "package metadata is missing",
                f"Run `{fix}`.",
                code_version=__version__,
                metadata_version=None,
                state="missing",
            )
        ]

    checks = [
        _check(
            "installation.metadata",
            "ok" if installed == __version__ else "error",
            "installed code and package metadata match"
            if installed == __version__
            else "installed code and package metadata differ",
            None if installed == __version__ else f"Run `{fix}`.",
            code_version=__version__,
            metadata_version=installed,
            state="current" if installed == __version__ else "mismatch",
        )
    ]
    dependencies = []
    for requirement in sorted(requirements, key=str.casefold):
        if ";" in requirement:
            continue
        match = re.match(r"[A-Za-z0-9._-]+", requirement)
        if not match:
            continue
        name = match.group(0)
        try:
            version = metadata.version(name)
            dependencies.append({"name": name, "status": "ok", "version": version})
        except metadata.PackageNotFoundError:
            dependencies.append({"name": name, "status": "error", "version": None})
    missing = [item["name"] for item in dependencies if item["status"] == "error"]
    checks.append(
        _check(
            "installation.dependencies",
            "error" if missing else "ok",
            f"missing direct dependencies: {', '.join(missing)}"
            if missing
            else "direct runtime dependencies are installed",
            f"Run `{fix}`." if missing else None,
            dependencies=dependencies,
            state="missing" if missing else "current",
        )
    )
    return checks


def _parser_check() -> dict[str, Any]:
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return _check(
            "parsers.tree_sitter",
            "error",
            "tree-sitter parser package is unavailable",
            f"Run `{_repair_command()}`.",
            state="unavailable",
        )

    for language in ("python", "javascript"):
        try:
            parser = get_parser(language)
        except Exception:
            return _check(
                "parsers.tree_sitter",
                "error",
                f"installed parser pack does not provide the {language} grammar",
                f"Run `{_repair_command()}` to restore the pinned parser pack.",
                state="grammar_missing",
                language=language,
            )
        try:
            sample = (
                "def ready():\n    return True\n"
                if language == "python"
                else "function ready() { return true; }"
            )
            tree = parser.parse(sample)
            if tree.root_node().has_error():
                raise RuntimeError(f"{language} smoke parse returned errors")
        except Exception as exc:
            return _check(
                "parsers.tree_sitter",
                "error",
                f"{language} parser smoke test failed ({type(exc).__name__})",
                "Run the parser compatibility tests before changing installed packages.",
                state="incompatible",
                language=language,
            )
    return _check("parsers.tree_sitter", "ok", "tree-sitter parsers are ready", state="ready")


def _root_checks(root: Path, *, verify: bool) -> list[dict[str, Any]]:
    if not root.exists():
        return [
            _check(
                "root.path",
                "error",
                "root does not exist",
                "Choose an existing folder with `--root`.",
                state="missing",
            )
        ]
    if not root.is_dir():
        return [
            _check(
                "root.path",
                "error",
                "root is not a folder",
                "Choose a folder with `--root`.",
                state="invalid",
            )
        ]
    pointer = pointer_file(root)
    try:
        raw = pointer.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [
            _check(
                "root.index",
                "warning",
                "root is not initialized",
                "Run `mimry init` when you want to create local metadata.",
                state="uninitialized",
            )
        ]
    except (OSError, UnicodeError) as exc:
        return [
            _check(
                "root.index",
                "error",
                f"root pointer cannot be read ({type(exc).__name__})",
                "Check read permissions; doctor does not change them.",
                state="unreadable",
            )
        ]
    try:
        ptr = json.loads(raw)
        if not isinstance(ptr, dict) or not all(
            isinstance(ptr.get(key), str) and ptr[key]
            for key in ("rootId", "rootPath", "indexPath")
        ):
            raise ValueError("invalid pointer shape")
        if Path(ptr["rootPath"]).expanduser().resolve(strict=False) != root:
            raise ValueError("pointer belongs to another root")
        validate_generation(ptr)
        fresh, graph, semantic = _health(root, ptr, verify=verify)
    except Exception as exc:
        return [
            _check(
                "root.index",
                "error",
                f"index or cache state is invalid ({type(exc).__name__})",
                "Preserve damaged state, then run `mimry reindex` after review.",
                state="corrupt",
            )
        ]

    state = fresh["state"]
    index_status = "ok" if state == "current" else "warning"
    suggestion = None
    if state == "missing":
        suggestion = "Run `mimry index` to build the index."
    elif state == "stale":
        suggestion = "Run `mimry reindex` to update the index."
    checks = [
        _check(
            "root.index",
            index_status,
            f"root index is {state}",
            suggestion,
            state=state,
            changed_count=len(fresh.get("changed", [])),
            missing_count=len(fresh.get("missing", [])),
            unindexable_count=fresh.get("unindexable_count", 0),
        )
    ]
    for check_id, label, health, fix in (
        ("root.graph", "graph cache", graph, "mimry refresh"),
        ("root.semantic", "semantic cache", semantic, "mimry refresh"),
    ):
        current = health.get("status") == "current"
        checks.append(
            _check(
                check_id,
                "ok" if current else "warning",
                f"{label} is {health.get('status', 'unknown')}",
                None if current else f"Run `{fix}` to rebuild it.",
                state=health.get("status", "unknown"),
            )
        )
    return checks


def doctor_report(root: Path, *, verify: bool = False) -> dict[str, Any]:
    """Build a read-only report without paths or exception messages."""
    root = root.expanduser().resolve(strict=False)
    checks = [*_dependency_checks(), _parser_check(), *_root_checks(root, verify=verify)]
    statuses = {check["status"] for check in checks}
    status = "error" if "error" in statuses else "warning" if "warning" in statuses else "ok"
    by_id = {check["id"]: check for check in checks}
    root_check = by_id.get("root.path", by_id.get("root.index", {}))
    root_state = root_check.get("state")
    if root_check.get("id") == "root.path" and root_state == "missing":
        condition = "missing_root"
    elif root_state in {"corrupt", "unreadable", "invalid"}:
        condition = "corrupt"
    elif any(
        check["id"].startswith(("installation.", "parsers.")) and check["status"] == "error"
        for check in checks
    ) or root_state in {"uninitialized", "missing"}:
        condition = "incomplete"
    elif any(check["status"] == "warning" for check in checks):
        condition = "stale"
    else:
        condition = "healthy"
    return {
        "schema_version": 1,
        "status": status,
        "condition": condition,
        "read_only": True,
        "checks": checks,
        "scope": _SCOPE,
        "not_checked": _NOT_CHECKED,
    }


def doctor_exit_code(report: dict[str, Any]) -> int:
    return {"ok": 0, "warning": 1, "error": 2}[report["status"]]


def cmd_doctor(args) -> int:
    report = doctor_report(Path(args.root), verify=getattr(args, "verify", False))
    if getattr(args, "json", False):
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"MIMRY doctor: {report['condition']} ({report['status']})")
        for check in report["checks"]:
            print(f"[{check['status'].upper()}] {check['summary']}")
            if check.get("suggestion"):
                print(f"  {check['suggestion']}")
        print("\nChecked:")
        for item in report["scope"]:
            print(f"- {item}")
        print("Not checked:")
        for item in report["not_checked"]:
            print(f"- {item}")
    return doctor_exit_code(report)
