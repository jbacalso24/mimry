from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from .adapters import list_adapters
from .constants import SCHEMA_VERSION
from .graphify_artifacts import graphify_relationship_lines, graphify_report_excerpt
from .graphify_wrapper import run_graphify_build
from .indexer import write_index
from .paths import context_file, idx_path, mdir, now, roots_file
from .search import find_rows, print_rows
from .security import safe_root
from .storage import load_jsonl, load_pointer, register_root, save_pointer


def cmd_init(a):
    root = Path(a.root).resolve()
    safe_root(root)
    root.mkdir(parents=True, exist_ok=True)
    if load_pointer(root):
        print("MIMRY is already initialized for this root.")
        return 0
    rid = str(uuid.uuid4())
    ptr = {
        "rootId": rid,
        "rootPath": str(root),
        "rootType": a.root_type,
        "indexPath": str(idx_path(rid)),
        "createdAt": now(),
        "lastIndexedAt": None,
        "schemaVersion": SCHEMA_VERSION,
    }
    mdir(root).mkdir(parents=True, exist_ok=True)
    (mdir(root) / "context").mkdir(exist_ok=True)
    (mdir(root) / "config.toml").write_text(
        'version = "0.1.0"\nroot_type = "repo"\nstore_full_text = false\n', encoding="utf-8"
    )
    (mdir(root) / "AGENT_RULES.md").write_text(
        "# MIMRY Agent Rules\n\nUse MIMRY before repeated grep or blind file reading.\n", encoding="utf-8"
    )
    save_pointer(root, ptr)
    register_root(ptr)
    if not getattr(a, "skip_graphify", False):
        print("Bootstrapping Graphify under .mimry/graphify ...")
        graphify_status = run_graphify_build(root, execute=True)
        if graphify_status != 0:
            print(
                "MIMRY initialized, but Graphify bootstrap failed. Run `mimry graphify build --execute` after fixing Graphify."
            )
            return graphify_status
    print(
        "MIMRY initialized.\nCreated:\n- .mimry/config.toml\n- .mimry/AGENT_RULES.md\n- .mimry/pointer.json\n- .mimry/graphify/\nNext: Run `mimry index`."
    )
    return 0


def require(root):
    ptr = load_pointer(root)
    if not ptr:
        raise SystemExit("MIMRY is not initialized here. Run `mimry init` first.")
    return ptr


def cmd_index(a):
    root = Path(a.root).resolve()
    stats = write_index(root, require(root))
    print(
        f"MIMRY indexing complete.\nIndexed files: {stats['files']}\nSymbols: {stats['symbols']}\nGraph edges: {stats['edges']}\nGraph engine: {stats['graph_engine']}\nIndex saved: {stats['index']}"
    )
    return 0


def cmd_status(a):
    root = Path(a.root).resolve()
    ptr = load_pointer(root)
    if not ptr:
        print("MIMRY status\nInitialized: no\nRecommended: Run `mimry init`.")
        return 1
    idx = Path(ptr["indexPath"])
    files = load_jsonl(idx / "files.jsonl")
    symbols = load_jsonl(idx / "symbols.jsonl")
    changed = []
    missing = []
    for f in files:
        p = root / f["rel_path"]
        if not p.exists():
            missing.append(f["rel_path"])
        elif p.stat().st_size != f["size"] or p.stat().st_mtime != f["mtime"]:
            changed.append(f["rel_path"])
    state = "missing" if not (idx / "files.jsonl").exists() else ("stale" if changed or missing else "current")
    g = (
        json.loads((idx / "graph.json").read_text(encoding="utf-8"))
        if (idx / "graph.json").exists()
        else {"nodes": [], "edges": []}
    )
    print(
        f"MIMRY status\nRoot: {root}\nInitialized: yes\nIndex: {state}\nLast indexed: {ptr.get('lastIndexedAt') or 'never'}\nFiles indexed: {len(files)}\nSymbols indexed: {len(symbols)}\nGraph nodes/edges: {len(g.get('nodes', []))}/{len(g.get('edges', []))}\nChanged files: {len(changed)}\nDeleted files: {len(missing)}\nIndex path: {idx}"
    )
    if state == "stale":
        print("Recommended: Run `mimry reindex`.")
    return 0 if state == "current" else 2


def cmd_find(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    print_rows(f"Search results for: {a.query}", find_rows(Path(ptr["indexPath"]), a.query, a.limit, root=root))
    return 0


def cmd_related(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    print_rows(f"Related files for: {a.query}", find_rows(Path(ptr["indexPath"]), a.query, a.limit, True, root=root))
    return 0


def cmd_symbol(a):
    ptr = require(Path(a.root).resolve())
    print(f"Symbol search: {a.name}")
    files = {f["file_id"]: f for f in load_jsonl(Path(ptr["indexPath"]) / "files.jsonl")}
    for i, s in enumerate(
        [s for s in load_jsonl(Path(ptr["indexPath"]) / "symbols.jsonl") if a.name.lower() in s["name"].lower()], 1
    ):
        print(
            f"{i}. {s['name']} ({s['kind']}, {s['language']}) — {files.get(s['file_id'], {}).get('rel_path', s['file_id'])}:{s.get('line_start') or ''}"
        )
    return 0


def cmd_context(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    rows = find_rows(Path(ptr["indexPath"]), a.query, 8, True, root=root)
    paths = [r["path"] for r in rows]
    relationship_lines = graphify_relationship_lines(root, paths)
    report_excerpt = graphify_report_excerpt(root)
    lines = [
        "# MIMRY Context Pack",
        "",
        "## Query",
        a.query,
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
            "Use `mimry symbol <name>` for concrete symbols.",
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
            "- Re-run `mimry reindex` after changes.",
            "",
            "## Suggested Verification",
            "- Run project tests/typecheck/build for affected files.",
            "",
            "## Source of Truth Reminder",
            "Original files, tests, builds, and human verification remain final truth.",
            "",
        ]
    )
    context_file(root).parent.mkdir(parents=True, exist_ok=True)
    context_file(root).write_text("\n".join(lines), encoding="utf-8")
    print(f"Context pack generated.\nOutput: {context_file(root)}")
    return 0


def cmd_adapters(a):
    adapters = list_adapters(include_planned=not getattr(a, "active_only", False))
    print("MIMRY adapters")
    for adapter in adapters:
        exts = ", ".join(adapter["extensions"])
        print(f"- {adapter['name']} [{adapter['status']}/{adapter['kind']}] {exts}")
        print(f"  parser: {adapter['parser']}")
        print(f"  emits: {', '.join(adapter['emits'])}")
        print(f"  agent use: {adapter['agent_use']}")
    return 0


def cmd_roots(a):
    reg = json.loads(roots_file().read_text(encoding="utf-8")) if roots_file().exists() else {"roots": []}
    print("MIMRY roots")
    [print(f"- {r['rootId']} {r['rootType']} {r['rootPath']} -> {r['indexPath']}") for r in reg.get("roots", [])]
    return 0


def cmd_cache_wipe(a):
    from .paths import cache_home

    if a.all:
        shutil.rmtree(cache_home(), ignore_errors=True)
        print(f"Wiped all MIMRY cache: {cache_home()}")
        return 0
    ptr = require(Path(a.root).resolve())
    shutil.rmtree(Path(ptr["indexPath"]), ignore_errors=True)
    print(f"Wiped current root cache: {ptr['indexPath']}")
    return 0
