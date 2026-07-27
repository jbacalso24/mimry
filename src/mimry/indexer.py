from __future__ import annotations

import json

from .graphify_core import GraphifyCore
from .paths import idx_path, now
from .scanner import adapt, scan
from .security import contains_sensitive_data
from .semantic import build_semantic_index
from .storage import connect, register_root, save_pointer, write_jsonl


def write_index(root, ptr):
    files = []
    symbols = []
    edges = []
    imports = {}
    exports = {}
    for p in scan(root):
        try:
            f, sy, ed, im, ex = adapt(p, root)
        except (OSError, UnicodeError, ValueError):
            continue
        if contains_sensitive_data((f, sy, ed, im, ex)):
            continue
        files.append(f)
        symbols += sy
        edges += ed
        if im:
            imports[f["rel_path"]] = im
        if ex:
            exports[f["rel_path"]] = ex
    idx = idx_path(ptr["rootId"])
    ptr = {**ptr, "indexPath": str(idx), "lastIndexedAt": now()}
    idx.mkdir(parents=True, exist_ok=True)
    graph = GraphifyCore().build_graph(files, symbols, edges)
    con = connect(idx)
    with con:
        con.execute("delete from files")
        con.execute("delete from symbols")
        con.execute("delete from files_fts")
        for f in files:
            con.execute(
                "insert or replace into files values(?,?,?,?,?,?,?,?)",
                (
                    f["file_id"],
                    f["rel_path"],
                    f["filename"],
                    f["extension"],
                    f["adapter"],
                    f["parse_status"],
                    f["content_hint"],
                    f["metadata_text"],
                ),
            )
            con.execute(
                "insert into files_fts values(?,?,?,?,?,?)",
                (f["file_id"], f["rel_path"], f["filename"], f["extension"], f["content_hint"], f["metadata_text"]),
            )
        for s in symbols:
            con.execute(
                "insert or replace into symbols values(?,?,?,?,?,?)",
                (s["symbol_id"], s["file_id"], s["name"], s["kind"], s["language"], s["line_start"]),
            )
    con.close()
    write_jsonl(idx / "files.jsonl", files)
    write_jsonl(idx / "symbols.jsonl", symbols)
    write_jsonl(idx / "imports.jsonl", [{"file": k, "imports": v} for k, v in imports.items()])
    write_jsonl(idx / "exports.jsonl", [{"file": k, "exports": v} for k, v in exports.items()])
    (idx / "dependencies.json").write_text(json.dumps(imports, indent=2) + "\n", encoding="utf-8")
    (idx / "graph.json").write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    (idx / "file-hashes.json").write_text(
        json.dumps(
            {f["rel_path"]: {"hash": f["hash"], "mtime": f["mtime"], "size": f["size"]} for f in files}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    semantic = build_semantic_index(idx, ptr["rootId"])
    save_pointer(root, ptr)
    register_root(ptr)
    return {
        "files": len(files),
        "symbols": len(symbols),
        "edges": len(edges),
        "index": str(idx),
        "graph_engine": graph["engine"],
        "semantic_chunks": semantic["chunks"],
        "semantic_backend": semantic["backend"],
    }
