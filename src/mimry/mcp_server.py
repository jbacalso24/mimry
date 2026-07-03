from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from mimry.adapters import list_adapters
from mimry.commands import require
from mimry.graphify_artifacts import graphify_relationship_lines, graphify_report_excerpt
from mimry.indexer import write_index
from mimry.paths import context_file
from mimry.search import find_rows
from mimry.storage import load_jsonl, load_pointer

mcp = FastMCP("MIMRY")


def _root(root: str | None) -> Path:
    return Path(root or ".").resolve()


def _status_payload(root_path: Path) -> dict[str, Any]:
    ptr = load_pointer(root_path)
    if not ptr:
        return {"initialized": False, "root": str(root_path), "recommended": "Run `mimry init`."}
    idx = Path(ptr["indexPath"])
    files = load_jsonl(idx / "files.jsonl")
    symbols = load_jsonl(idx / "symbols.jsonl")
    changed: list[str] = []
    missing: list[str] = []
    for f in files:
        p = root_path / f["rel_path"]
        if not p.exists():
            missing.append(f["rel_path"])
        elif p.stat().st_size != f["size"] or p.stat().st_mtime != f["mtime"]:
            changed.append(f["rel_path"])
    state = "missing" if not (idx / "files.jsonl").exists() else ("stale" if changed or missing else "current")
    return {
        "initialized": True,
        "root": str(root_path),
        "index_state": state,
        "last_indexed_at": ptr.get("lastIndexedAt"),
        "files_indexed": len(files),
        "symbols_indexed": len(symbols),
        "changed_files": changed,
        "deleted_files": missing,
        "index_path": str(idx),
    }


@mcp.tool
def mimry_list_adapters(active_only: bool = False) -> dict[str, Any]:
    """List active and planned MIMRY adapter plugins for coding-agent routing."""
    return {"adapters": list_adapters(include_planned=not active_only)}


@mcp.tool
def mimry_status(root: str | None = None) -> dict[str, Any]:
    """Return MIMRY initialization and index freshness for a root."""
    return _status_payload(_root(root))


@mcp.tool
def mimry_reindex(root: str | None = None) -> dict[str, Any]:
    """Rebuild the local MIMRY index for a root."""
    root_path = _root(root)
    stats = write_index(root_path, require(root_path))
    return {"root": str(root_path), **stats}


@mcp.tool
def mimry_find(query: str, root: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Search indexed files with ranking reasons."""
    root_path = _root(root)
    ptr = require(root_path)
    rows = find_rows(Path(ptr["indexPath"]), query, limit, root=root_path)
    return {"query": query, "root": str(root_path), "results": rows}


@mcp.tool
def mimry_related(query: str, root: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Return files related to a query using graph-aware ranking signals."""
    root_path = _root(root)
    ptr = require(root_path)
    rows = find_rows(Path(ptr["indexPath"]), query, limit, True, root=root_path)
    return {"query": query, "root": str(root_path), "results": rows}


@mcp.tool
def mimry_symbol(name: str, root: str | None = None) -> dict[str, Any]:
    """Search indexed symbols by name."""
    root_path = _root(root)
    ptr = require(root_path)
    idx = Path(ptr["indexPath"])
    files = {f["file_id"]: f for f in load_jsonl(idx / "files.jsonl")}
    matches = []
    for s in load_jsonl(idx / "symbols.jsonl"):
        if name.lower() in s["name"].lower():
            f = files.get(s["file_id"], {})
            matches.append(
                {
                    "name": s["name"],
                    "kind": s["kind"],
                    "language": s["language"],
                    "path": f.get("rel_path", s["file_id"]),
                    "line_start": s.get("line_start"),
                }
            )
    return {"name": name, "root": str(root_path), "symbols": matches}


@mcp.tool
def mimry_context(query: str, root: str | None = None) -> dict[str, Any]:
    """Generate a MIMRY context pack and return its path plus selected files."""
    root_path = _root(root)
    ptr = require(root_path)
    rows = find_rows(Path(ptr["indexPath"]), query, 8, True, root=root_path)
    paths = [r["path"] for r in rows]
    relationship_lines = graphify_relationship_lines(root_path, paths)
    report_excerpt = graphify_report_excerpt(root_path)
    lines = [
        "# MIMRY Context Pack",
        "",
        "## Query",
        query,
        "",
        "## Index Status",
        "Generated from Graphify artifacts when available; MIMRY index is fallback.",
        "",
        "## Summary",
        f"MIMRY found {len(rows)} relevant file(s), preferring Graphify graph nodes/edges/report when available.",
        "",
        "## Relevant Files",
    ]
    for i, r in enumerate(rows, 1):
        lines += [f"### {i}. `{r['path']}`", f"Score: {r['score']}", f"Reason: {r['reason']}", ""]
    lines += (
        [
            "## Relevant Symbols / Entities",
            "Use `mimry_symbol` for concrete symbols.",
            "",
            "## Relationship Paths",
            *(relationship_lines or ["No Graphify relationship path matched the selected files yet."]),
            "",
            "## Graphify Report Signals",
            report_excerpt or "No Graphify report excerpt available.",
            "",
            "## Suggested Reading Order",
        ]
        + [f"{i}. `{r['path']}`" for i, r in enumerate(rows, 1)]
        + [
            "",
            "## Risk Notes",
            "- Open source files before editing.",
            "- Re-run `mimry_reindex` after changes.",
            "",
            "## Suggested Verification",
            "- Run project tests/typecheck/build for affected files.",
            "",
            "## Source of Truth Reminder",
            "Original files, tests, builds, and human verification remain final truth.",
            "",
        ]
    )
    context_file(root_path).parent.mkdir(parents=True, exist_ok=True)
    context_file(root_path).write_text("\n".join(lines), encoding="utf-8")
    return {"query": query, "root": str(root_path), "output": str(context_file(root_path)), "files": rows}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
