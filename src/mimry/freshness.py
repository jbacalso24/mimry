from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import security
from .paths import canonical_rel_path
from .scanner import SCANNER_FILE_SIZE_LIMIT, read_snapshot, scan
from .security import filter_index_records, path_has_ignored_part, should_ignore_path
from .state import UNINDEXABLE_FILE, StateCorruptionError, load_json_state, validate_generation
from .storage import load_jsonl


def file_sha256(path: Path, *, root: Path | None = None) -> str | None:
    """Hash one proven regular, non-symlink snapshot without following races."""
    try:
        data, _ = read_snapshot(path, root=root)
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


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
    live_excluded: list[str] = []
    indexed_paths = {f.get("rel_path") for f in files if f.get("rel_path")}
    # Resolve canonical identities back to their native filesystem spellings.
    # scan() also preserves the existing fail-closed NFC collision check.
    native_paths = {canonical_rel_path(path, root): path for path in scan(root, inspect_sensitive_content=False)}

    for f in files:
        rel_path = f["rel_path"]
        p = native_paths.get(rel_path, root / rel_path)
        # Path policy must be applied before filesystem existence. Otherwise a
        # deleted record from a newly excluded tree leaks through `missing`.
        if path_has_ignored_part(rel_path):
            policy_excluded.append(rel_path)
            continue
        # A replaced symlink must never inherit trust from the indexed regular
        # file, even when its target has identical bytes.
        if p.is_symlink():
            changed.append(rel_path)
            live_excluded.append(rel_path)
            continue
        if not p.exists():
            missing.append(rel_path)
            continue
        # Policy changes must invalidate old generations. Otherwise a file that
        # became ignored after it was indexed remains searchable indefinitely.
        if should_ignore_path(p, root):
            policy_excluded.append(rel_path)
            continue
        try:
            data, st = read_snapshot(p, root=root)
        except OSError:
            # Unreadable, non-regular, outside-root, and raced files are stale and
            # hidden from cached context until a safe refresh proves them again.
            changed.append(rel_path)
            live_excluded.append(rel_path)
            continue
        expected_hash = f.get("hash") or stored_hashes.get(rel_path, {}).get("hash")
        is_oversized = st.st_size > SCANNER_FILE_SIZE_LIMIT
        size_changed = st.st_size != f.get("size")
        content_changed = (
            is_oversized
            or size_changed
            or (hashlib.sha256(data).hexdigest() != expected_hash if expected_hash else st.st_mtime != f.get("mtime"))
        )
        if not content_changed:
            continue
        changed.append(rel_path)
        if is_oversized:
            # read_snapshot() is deliberately bounded. Once an indexed file
            # grows past that boundary, bytes outside the acquired snapshot
            # cannot be classified safely, so cached readers must fail closed.
            live_excluded.append(rel_path)
            continue
        # Do not rescan unchanged indexed bytes, but fail closed when changed
        # bytes newly contain a secret: keep the stale record out of every
        # context/status consumer immediately.
        if security.has_sensitive_content(p, data=data):
            live_excluded.append(rel_path)

    # Files MIMRY refused to index (secret-bearing or unreadable) are absent from
    # files.jsonl by design. Counting them as changed would keep the index stale
    # forever, which pins graph health to stale and disables `mimry path`.
    unindexable = _unindexable_paths(idx)

    if files_path.exists():
        # Indexed files were secret-scanned from the exact bytes whose hashes we
        # compare above. Re-running content heuristics over every cached file made
        # a current preflight parse the repository repeatedly. Discover paths
        # cheaply, then secret-scan only genuinely new candidates before calling
        # them a source change.
        for rel, p in native_paths.items():
            if rel in indexed_paths or rel in unindexable:
                continue
            try:
                data, _ = read_snapshot(p, root=root)
            except OSError:
                continue
            if not security.has_sensitive_content(p, data=data):
                changed.append(rel)

    changed = sorted(set(changed))
    missing = sorted(set(missing))
    state = "missing" if not files_path.exists() else ("stale" if changed or missing or policy_excluded else "current")
    graph_path = idx / "graph.json"
    graph, _ = load_json_state(graph_path, default={"nodes": [], "edges": []})
    if not isinstance(graph, dict):
        raise StateCorruptionError(graph_path, "expected a JSON object")
    visible_files, visible_symbols = filter_index_records(files, symbols)
    policy_and_live_excluded = set(policy_excluded) | set(live_excluded)
    # A deleted indexed file is just as unsafe to serve from stale cache as a
    # live policy exclusion: its source no longer exists to validate the cached
    # files/symbols/graph/feedback/semantic evidence. Keep status stale and pass
    # its path through the same bounded reader deny set without forcing refresh.
    excluded_paths = policy_and_live_excluded | set(missing)
    visible_files = [record for record in visible_files if record.get("rel_path") not in excluded_paths]
    visible_file_ids = {record.get("file_id") for record in visible_files}
    visible_symbols = [record for record in visible_symbols if record.get("file_id") in visible_file_ids]
    visible_paths = {record.get("rel_path") for record in visible_files}
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
            folder: [path for path in paths if path in visible_paths]
            for folder, paths in graph.get("clusters", {}).items()
            if not path_has_ignored_part(folder)
        },
    }
    return {
        "index_path": idx,
        "files": visible_files,
        "symbols": visible_symbols,
        "changed": changed,
        "missing": missing,
        "policy_excluded_count": len(policy_and_live_excluded),
        # Bounded path-only deny set for stale readers of cached JSONL, graph,
        # feedback, and semantic artifacts. Never include file content here.
        "excluded_paths": sorted(excluded_paths),
        # Count only: these are paths policy intentionally keeps out of agent context.
        "unindexable_count": len(unindexable),
        "state": state,
        "graph": visible_graph,
        "generation_id": ptr.get("generationId"),
        "layout": "generation" if ptr.get("generationId") else "legacy",
    }
