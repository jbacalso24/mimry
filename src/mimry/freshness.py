from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .storage import load_jsonl


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _stored_hashes(idx: Path) -> dict[str, dict[str, Any]]:
    hashes_path = idx / "file-hashes.json"
    if not hashes_path.exists():
        return {}
    try:
        payload = json.loads(hashes_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def index_freshness(root: Path, ptr: dict[str, Any]) -> dict[str, Any]:
    idx = Path(ptr["indexPath"])
    files_path = idx / "files.jsonl"
    files = load_jsonl(files_path)
    symbols = load_jsonl(idx / "symbols.jsonl")
    stored_hashes = _stored_hashes(idx)
    changed: list[str] = []
    missing: list[str] = []

    for f in files:
        rel_path = f["rel_path"]
        p = root / rel_path
        if not p.exists():
            missing.append(rel_path)
            continue
        expected_hash = f.get("hash") or stored_hashes.get(rel_path, {}).get("hash")
        if expected_hash:
            if file_sha256(p) != expected_hash:
                changed.append(rel_path)
            continue
        st = p.stat()
        if st.st_size != f.get("size") or st.st_mtime != f.get("mtime"):
            changed.append(rel_path)

    state = "missing" if not files_path.exists() else ("stale" if changed or missing else "current")
    graph_path = idx / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.exists() else {"nodes": [], "edges": []}
    return {
        "index_path": idx,
        "files": files,
        "symbols": symbols,
        "changed": changed,
        "missing": missing,
        "state": state,
        "graph": graph,
    }
