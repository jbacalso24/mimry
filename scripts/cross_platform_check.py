#!/usr/bin/env python3
"""Prove the graph engine computes identically on every platform.

graph.json is checksummed by the generation manifest, so a platform that produced a
different byte sequence for identical input would break generation coherence and make
cached indexes non-portable. This builds a graph from fixed in-memory input -- no
filesystem, no parser, no clock -- and prints one hash. Every platform must print the
same one.

Pure stdlib on purpose: it runs anywhere Python 3.11+ exists, with nothing installed.

    python3 scripts/cross_platform_check.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimry.core.build import GraphEngine  # noqa: E402
from mimry.core.cluster import assign_communities  # noqa: E402
from mimry.core.report import build_manifest, render_report  # noqa: E402
from mimry.core.resolve import (  # noqa: E402
    resolve_calls,
    resolve_doc_links,
    resolve_imports,
    resolve_table_refs,
)

FILES = [
    {"file_id": "f1", "rel_path": "backend/api/auth.py", "extension": ".py", "hash": "a1", "mtime": 1.5},
    {
        "file_id": "f2",
        "rel_path": "backend/services/session_service.py",
        "extension": ".py",
        "hash": "a2",
        "mtime": 2.5,
    },
    {"file_id": "f3", "rel_path": "web/src/app/checkout/page.tsx", "extension": ".tsx", "hash": "a3", "mtime": 3.5},
    {"file_id": "f4", "rel_path": "web/src/lib/payments.ts", "extension": ".ts", "hash": "a4", "mtime": 4.5},
    {"file_id": "f5", "rel_path": "db/schema.sql", "extension": ".sql", "hash": "a5", "mtime": 5.5},
    {"file_id": "f6", "rel_path": "docs/auth-design.md", "extension": ".md", "hash": "a6", "mtime": 6.5},
]
SYMBOLS = [
    {"symbol_id": "s1", "file_id": "f1", "name": "refresh_session", "kind": "function", "language": "python"},
    {"symbol_id": "s2", "file_id": "f2", "name": "renew_login", "kind": "function", "language": "python"},
    {"symbol_id": "s3", "file_id": "f4", "name": "redirectToPayment", "kind": "function", "language": "ts"},
    {"symbol_id": "s4", "file_id": "f5", "name": "sessions", "kind": "sql_table", "language": "sql"},
]
EDGES = [
    {
        "source_type": "file",
        "source_id": f["file_id"],
        "target_type": "symbol",
        "target_id": s["symbol_id"],
        "edge_type": "defines",
        "confidence": 1.0,
    }
    for f in FILES
    for s in SYMBOLS
    if s["file_id"] == f["file_id"]
]
IMPORTS = {
    "backend/api/auth.py": ["backend.services.session_service"],
    "web/src/app/checkout/page.tsx": ["../../lib/payments"],
}
CALLS = {"backend/api/auth.py": [{"name": "renew_login", "line": 5}]}
SYMBOLS_BY_FILE = {
    "backend/api/auth.py": [{"name": "refresh_session", "kind": "function", "line_start": 4, "line_end": 6}],
    "backend/services/session_service.py": [
        {"name": "renew_login", "kind": "function", "line_start": 1, "line_end": 3}
    ],
}
DOC_LINKS = {"docs/auth-design.md": ["../backend/api/auth.py"]}
TABLE_REFS = {"backend/services/session_service.py": ["sessions"]}


def build() -> dict:
    rel_paths = {f["rel_path"] for f in FILES}
    by_path = {f["rel_path"]: f["file_id"] for f in FILES}

    graph = GraphEngine().build_graph(FILES, SYMBOLS, EDGES, imports=IMPORTS)
    node_ids = {n["id"] for n in graph["nodes"]}

    import_edges = resolve_imports(IMPORTS, rel_paths)
    for e in import_edges:
        graph["edges"].append(
            {
                "source": f"file:{by_path[e['importer']]}",
                "target": f"file:{by_path[e['target']]}",
                "relation": "imports",
                "confidence": e["confidence"],
            }
        )

    symbol_ids = {(s["file_id"], s["name"]): s["symbol_id"] for s in SYMBOLS}
    file_of = {f["rel_path"]: f["file_id"] for f in FILES}
    for c in resolve_calls(CALLS, SYMBOLS_BY_FILE, import_edges):
        src = symbol_ids.get((file_of.get(c["caller_file"], ""), c["caller_symbol"] or ""))
        dst = symbol_ids.get((file_of.get(c["target_file"], ""), c["target_symbol"]))
        if src and dst:
            graph["edges"].append(
                {
                    "source": f"symbol:{src}",
                    "target": f"symbol:{dst}",
                    "relation": "calls",
                    "confidence": c["confidence"],
                }
            )

    for d in resolve_doc_links(DOC_LINKS, rel_paths):
        graph["edges"].append(
            {
                "source": f"file:{by_path[d['source']]}",
                "target": f"file:{by_path[d['target']]}",
                "relation": "references",
                "confidence": d["confidence"],
            }
        )

    table_symbols = {"sessions": [("db/schema.sql", "s4")]}
    for t in resolve_table_refs(TABLE_REFS, table_symbols):
        graph["edges"].append(
            {
                "source": f"file:{by_path[t['source']]}",
                "target": f"symbol:{t['target_symbol_id']}",
                "relation": "references",
                "confidence": t["confidence"],
            }
        )

    graph["edges"] = sorted(
        (e for e in graph["edges"] if e["source"] in node_ids and e["target"] in node_ids),
        key=lambda e: (e["source"], e["target"], e["relation"]),
    )
    graph["nodes"] = sorted(assign_communities(graph["nodes"], graph["edges"]), key=lambda n: n["id"])
    return graph


def main() -> int:
    graph = build()
    payload = {
        "graph": graph,
        "report": render_report(graph, commit="fixed-for-determinism"),
        "manifest": build_manifest(FILES),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()

    relations = sorted({e["relation"] for e in graph["edges"]})
    print(f"python      {sys.version.split()[0]}  ({sys.platform})")
    print(f"nodes/edges {len(graph['nodes'])}/{len(graph['edges'])}")
    print(f"relations   {', '.join(relations)}")
    print(f"report ascii {payload['report'].isascii()}")
    print(f"DIGEST      {digest}")

    assert payload["report"].isascii(), "report must be ASCII on every platform"
    assert "\r" not in payload["report"], "report must not contain CR"
    assert all("\\" not in n["source_file"] for n in graph["nodes"]), "paths must stay POSIX"
    assert build() == graph, "graph build must be deterministic within a process"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
