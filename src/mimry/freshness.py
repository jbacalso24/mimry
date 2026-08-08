from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .scanner import scan
from .security import filter_index_records, path_has_ignored_part, should_ignore
from .state import UNINDEXABLE_FILE, StateCorruptionError, load_json_state, validate_generation
from .storage import load_jsonl


def file_sha256(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _stored_hashes(idx: Path) -> dict[str, dict[str, Any]]:
    hashes_path = idx / "file-hashes.json"
    if not hashes_path.exists():
        return {}
    payload, _ = load_json_state(hashes_path)
    if not isinstance(payload, dict):
        raise StateCorruptionError(hashes_path, "expected a JSON object")
    return payload


def _unindexable_paths(idx: Path) -> set[str]:
    """Paths the indexer deliberately refused. Absent on generations written before this."""
    path = idx / UNINDEXABLE_FILE
    if not path.exists():
        return set()
    payload, _ = load_json_state(path)
    if not isinstance(payload, list):
        raise StateCorruptionError(path, "expected a JSON array")
    return {entry for entry in payload if isinstance(entry, str)}


def index_freshness(root: Path, ptr: dict[str, Any]) -> dict[str, Any]:
    validate_generation(ptr)
    idx = Path(ptr["indexPath"])
    files_path = idx / "files.jsonl"
    files = load_jsonl(files_path)
    symbols = load_jsonl(idx / "symbols.jsonl")
    stored_hashes = _stored_hashes(idx)
    changed: list[str] = []
    missing: list[str] = []
    policy_excluded: list[str] = []
    indexed_paths = {f.get("rel_path") for f in files if f.get("rel_path")}

    for f in files:
        rel_path = f["rel_path"]
        p = root / rel_path
        # Path policy must be applied before filesystem existence. Otherwise a
        # deleted record from a newly excluded tree leaks through `missing`.
        if path_has_ignored_part(rel_path):
            policy_excluded.append(rel_path)
            continue
        if not p.exists():
            missing.append(rel_path)
            continue
        # Policy changes must invalidate old generations. Otherwise a file that
        # became ignored after it was indexed remains searchable indefinitely.
        if should_ignore(p, root):
            policy_excluded.append(rel_path)
            continue
        expected_hash = f.get("hash") or stored_hashes.get(rel_path, {}).get("hash")
        if expected_hash:
            actual_hash = file_sha256(p)
            if actual_hash is None or actual_hash != expected_hash:
                changed.append(rel_path)
            continue
        try:
            st = p.stat()
        except OSError:
            changed.append(rel_path)
            continue
        if st.st_size != f.get("size") or st.st_mtime != f.get("mtime"):
            changed.append(rel_path)

    # Files MIMRY refused to index (secret-bearing or unreadable) are absent from
    # files.jsonl by design. Counting them as changed would keep the index stale
    # forever, which pins graph health to stale and disables `mimry path`.
    unindexable = _unindexable_paths(idx)

    if files_path.exists():
        for p in scan(root):
            rel = p.relative_to(root).as_posix()
            if rel not in indexed_paths and rel not in unindexable:
                changed.append(rel)

    changed = sorted(set(changed))
    missing = sorted(set(missing))
    state = "missing" if not files_path.exists() else ("stale" if changed or missing or policy_excluded else "current")
    graph_path = idx / "graph.json"
    graph, _ = load_json_state(graph_path, default={"nodes": [], "edges": []})
    if not isinstance(graph, dict):
        raise StateCorruptionError(graph_path, "expected a JSON object")
    visible_files, visible_symbols = filter_index_records(files, symbols)
    allowed_node_ids = {
        *(f"file:{record['file_id']}" for record in visible_files),
        *(f"symbol:{record['symbol_id']}" for record in visible_symbols),
    }
    visible_graph = {
        **graph,
        "nodes": [node for node in graph.get("nodes", []) if node.get("id") in allowed_node_ids],
        "edges": [
            edge
            for edge in graph.get("edges", [])
            if edge.get("source") in allowed_node_ids and edge.get("target") in allowed_node_ids
        ],
        "clusters": {
            folder: [path for path in paths if not should_ignore(root / path, root)]
            for folder, paths in graph.get("clusters", {}).items()
            if not should_ignore(root / folder, root)
        },
    }
    return {
        "index_path": idx,
        "files": visible_files,
        "symbols": visible_symbols,
        "changed": changed,
        "missing": missing,
        "policy_excluded_count": len(policy_excluded),
        # Count only: these are paths policy intentionally keeps out of agent context.
        "unindexable_count": len(unindexable),
        "state": state,
        "graph": visible_graph,
        "generation_id": ptr.get("generationId"),
        "layout": "generation" if ptr.get("generationId") else "legacy",
    }
