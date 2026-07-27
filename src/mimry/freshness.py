from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .scanner import scan
from .state import StateCorruptionError, load_json_state, validate_generation
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


def index_freshness(root: Path, ptr: dict[str, Any]) -> dict[str, Any]:
    validate_generation(ptr)
    idx = Path(ptr["indexPath"])
    files_path = idx / "files.jsonl"
    files = load_jsonl(files_path)
    symbols = load_jsonl(idx / "symbols.jsonl")
    stored_hashes = _stored_hashes(idx)
    changed: list[str] = []
    missing: list[str] = []
    indexed_paths = {f.get("rel_path") for f in files if f.get("rel_path")}

    for f in files:
        rel_path = f["rel_path"]
        p = root / rel_path
        if not p.exists():
            missing.append(rel_path)
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

    if files_path.exists():
        for p in scan(root):
            rel = p.relative_to(root).as_posix()
            if rel not in indexed_paths:
                changed.append(rel)

    changed = sorted(set(changed))
    missing = sorted(set(missing))
    state = "missing" if not files_path.exists() else ("stale" if changed or missing else "current")
    graph_path = idx / "graph.json"
    graph, _ = load_json_state(graph_path, default={"nodes": [], "edges": []})
    if not isinstance(graph, dict):
        raise StateCorruptionError(graph_path, "expected a JSON object")
    return {
        "index_path": idx,
        "files": files,
        "symbols": symbols,
        "changed": changed,
        "missing": missing,
        "state": state,
        "graph": graph,
        "generation_id": ptr.get("generationId"),
        "layout": "generation" if ptr.get("generationId") else "legacy",
    }
