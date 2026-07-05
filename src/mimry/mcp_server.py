from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from mimry.adapters import list_adapters
from mimry.commands import _write_context_pack, require
from mimry.freshness import index_freshness
from mimry.graphify_artifacts import graphify_health
from mimry.graphify_wrapper import graphify_source, pinned_commit_for_status
from mimry.indexer import write_index
from mimry.paths import context_file
from mimry.search import find_rows
from mimry.semantic import semantic_health, semantic_rows
from mimry.storage import load_jsonl, load_pointer

mcp = FastMCP("MIMRY")


def _root(root: str | None) -> Path:
    return Path(root or ".").resolve()


def _status_payload(root_path: Path) -> dict[str, Any]:
    ptr = load_pointer(root_path)
    if not ptr:
        return {"initialized": False, "root": str(root_path), "recommended": "Run `mimry init`."}
    fresh = index_freshness(root_path, ptr)
    idx = fresh["index_path"]
    files = fresh["files"]
    symbols = fresh["symbols"]
    changed = fresh["changed"]
    missing = fresh["missing"]
    state = fresh["state"]
    graphify = graphify_health(root_path, index_state=state)
    graphify["source"] = graphify_source()
    graphify["pinned_commit"] = pinned_commit_for_status()
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
        "graphify": graphify,
        "semantic": semantic_health(Path(idx), ptr.get("rootId"), expected_files=len(files)),
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
def mimry_find(query: str, root: str | None = None, limit: int = 10, semantic: bool = False) -> dict[str, Any]:
    """Search indexed files with ranking reasons."""
    root_path = _root(root)
    ptr = require(root_path)
    rows = find_rows(Path(ptr["indexPath"]), query, limit, root=root_path, root_id=ptr.get("rootId"), semantic=semantic)
    payload = {"query": query, "root": str(root_path), "results": rows}
    if semantic:
        payload["semantic"] = semantic_health(Path(ptr["indexPath"]), ptr.get("rootId"))
    return payload


@mcp.tool
def mimry_semantic(query: str, root: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Local-only semantic search over bounded MIMRY chunks."""
    root_path = _root(root)
    ptr = require(root_path)
    idx = Path(ptr["indexPath"])
    rows, health = semantic_rows(idx, ptr.get("rootId"), query, limit)
    return {"query": query, "root": str(root_path), "semantic": health, "results": rows}


@mcp.tool
def mimry_related(query: str, root: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Return files related to a query using graph-aware ranking signals."""
    root_path = _root(root)
    ptr = require(root_path)
    rows = find_rows(Path(ptr["indexPath"]), query, limit, True, root=root_path, root_id=ptr.get("rootId"))
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
def mimry_context(query: str, root: str | None = None, semantic: bool = False) -> dict[str, Any]:
    """Generate a MIMRY context pack and return its path plus selected files."""
    root_path = _root(root)
    ptr = require(root_path)
    rows = _write_context_pack(root_path, ptr, query, semantic=semantic)
    return {"query": query, "root": str(root_path), "output": str(context_file(root_path)), "files": rows}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
