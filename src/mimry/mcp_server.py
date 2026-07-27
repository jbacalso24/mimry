from __future__ import annotations

import argparse
import contextlib
import io
import sys
from types import SimpleNamespace
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from mimry.adapters import list_adapters
from mimry.commands import (
    _write_context_pack,
    cmd_explain,
    cmd_init,
    cmd_path,
    cmd_preflight,
    cmd_refresh,
    cmd_why,
    require,
)
from mimry.freshness import index_freshness
from mimry.graphify_artifacts import graphify_health
from mimry.graphify_wrapper import graphify_runtime_provenance, pinned_commit_for_status
from mimry.indexer import write_index
from mimry.paths import context_file
from mimry.routing import route_payload, write_brief
from mimry.search import find_rows
from mimry.semantic import semantic_health, semantic_rows
from mimry.feedback import feedback_payload_from_args, record_feedback
from mimry.storage import load_jsonl, load_pointer

mcp = FastMCP("MIMRY")


def _capture_command(func, args: SimpleNamespace) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = func(args)
    return {"returncode": int(code or 0), "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


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
    provenance = graphify_runtime_provenance()
    graphify["source"] = provenance["source"]
    graphify["pinned_commit"] = pinned_commit_for_status()
    graphify["runtime_provenance"] = provenance
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
def mimry_init(root: str | None = None, root_type: str = "repo", skip_graphify: bool = False) -> dict[str, Any]:
    """Initialize MIMRY metadata for a root."""
    root_path = _root(root)
    payload = _capture_command(
        cmd_init, SimpleNamespace(root=str(root_path), root_type=root_type, skip_graphify=skip_graphify)
    )
    payload["status"] = _status_payload(root_path)
    return payload


@mcp.tool
def mimry_refresh(root: str | None = None) -> dict[str, Any]:
    """Run internal graph build, MIMRY index, semantic index, then status."""
    root_path = _root(root)
    payload = _capture_command(cmd_refresh, SimpleNamespace(root=str(root_path)))
    payload["status"] = _status_payload(root_path)
    return payload


@mcp.tool
def mimry_preflight(query: str, root: str | None = None, force_refresh: bool = False) -> dict[str, Any]:
    """Fast readiness check and task context generation, matching CLI preflight."""
    root_path = _root(root)
    payload = _capture_command(
        cmd_preflight, SimpleNamespace(root=str(root_path), task=query, force_refresh=force_refresh)
    )
    status = _status_payload(root_path)
    payload.update(
        {
            "query": query,
            "root": str(root_path),
            "context_path": str(context_file(root_path)),
            "initialized": status.get("initialized"),
            "index_state": status.get("index_state"),
            "status": status,
        }
    )
    return payload


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
def mimry_route(query: str, root: str | None = None, limit: int = 8) -> dict[str, Any]:
    """Recommend an agent/role, context packs, files, risk gates, and verification for a task."""
    root_path = _root(root)
    ptr = require(root_path)
    return route_payload(root_path, ptr, query, limit=limit)


@mcp.tool
def mimry_brief(query: str, agent: str, root: str | None = None, limit: int = 8) -> dict[str, Any]:
    """Write a role-aware MIMRY agent brief and return its path plus route payload."""
    root_path = _root(root)
    ptr = require(root_path)
    path, payload = write_brief(root_path, ptr, query, agent, limit=limit)
    return {"query": query, "root": str(root_path), "agent": payload["agent"], "output": str(path), "payload": payload}


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


@mcp.tool
def mimry_explain(query: str, root: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Explain top files, symbols, graph evidence, and verification hints for a task."""
    root_path = _root(root)
    return _capture_command(cmd_explain, SimpleNamespace(root=str(root_path), query=query, limit=limit))


@mcp.tool
def mimry_path(source: str, target: str, root: str | None = None) -> dict[str, Any]:
    """Find a Graphify relationship path between two files/symbols/queries."""
    root_path = _root(root)
    return _capture_command(cmd_path, SimpleNamespace(root=str(root_path), source=source, target=target))


@mcp.tool
def mimry_why(surface: str, query: str, root: str | None = None, limit: int = 25) -> dict[str, Any]:
    """Explain why a file or symbol ranked for a task query."""
    root_path = _root(root)
    return _capture_command(cmd_why, SimpleNamespace(root=str(root_path), surface=surface, query=query, limit=limit))


@mcp.tool
def mimry_feedback(
    query: str,
    root: str | None = None,
    context: str | None = None,
    suggested: list[str] | None = None,
    opened: list[str] | None = None,
    changed: list[str] | None = None,
    missed: list[str] | None = None,
    ignored: list[str] | None = None,
    verification: str | list[dict[str, str]] | None = None,
    outcome: str = "unknown",
    notes: str | None = None,
) -> dict[str, Any]:
    """Record local agent usage feedback for future ranking."""
    root_path = _root(root)
    ptr = require(root_path)
    idx = Path(ptr["indexPath"])
    args = SimpleNamespace(
        query=query,
        context=context,
        suggested=suggested,
        opened=opened,
        changed=changed,
        missed=missed,
        ignored=ignored,
        verification=verification,
        outcome=outcome,
        notes=notes,
        json=None,
    )
    payload = feedback_payload_from_args(root_path, args)
    if not payload["query"]:
        return {"returncode": 2, "error": "Feedback requires query."}
    row = record_feedback(idx, ptr["rootId"], payload)
    return {"returncode": 0, "root": str(root_path), "feedback": row}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the MIMRY MCP stdio server.")
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    mcp.run()


if __name__ == "__main__":
    main()
