from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from .adapters import list_adapters
from .cache_safety import UnsafeCachePathError, validated_cache_home, validated_current_index_path
from .constants import SCHEMA_VERSION
from .freshness import index_freshness
from .graphify_artifacts import graphify_health, graphify_relationship_lines, graphify_report_excerpt
from .graphify_wrapper import graphify_source, pinned_commit_for_status, run_graphify_build
from .indexer import write_index
from .paths import context_file, idx_path, mdir, now, roots_file
from .search import find_rows, print_rows
from .security import safe_root
from .storage import load_jsonl, load_pointer, register_root, save_pointer


def _git_toplevel(root: Path) -> Path | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if res.returncode != 0:
        return None
    return Path(res.stdout.strip()).resolve()


def ensure_mimry_gitignore(root: Path) -> bool:
    """Ensure repo-local MIMRY metadata is ignored in initialized Git worktrees."""
    git_root = _git_toplevel(root)
    if git_root is None:
        return False

    try:
        rel = root.relative_to(git_root)
    except ValueError:
        return False
    pattern = ".mimry/" if rel == Path(".") else f"/{rel.as_posix()}/.mimry/"
    pointer_rel = Path(".mimry/pointer.json") if rel == Path(".") else rel / ".mimry" / "pointer.json"
    ignored = subprocess.run(
        ["git", "-C", str(git_root), "check-ignore", "--quiet", "--", pointer_rel.as_posix()],
        text=True,
        capture_output=True,
        check=False,
    )
    if ignored.returncode == 0:
        return False

    gitignore = git_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    if pattern in lines:
        return False

    prefix = "" if not existing or existing.endswith("\n") else "\n"
    gitignore.write_text(f"{existing}{prefix}{pattern}\n", encoding="utf-8")
    return True


def cmd_init(a):
    root = Path(a.root).resolve()
    safe_root(root)
    root.mkdir(parents=True, exist_ok=True)
    gitignore_updated = ensure_mimry_gitignore(root)
    if load_pointer(root):
        print("MIMRY is already initialized for this root.")
        if gitignore_updated:
            print("Added a MIMRY metadata ignore entry to the target Git worktree .gitignore.")
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
    hygiene = (
        "Added a MIMRY metadata ignore entry to the target Git worktree .gitignore."
        if gitignore_updated
        else "No .gitignore change needed."
    )
    print(
        "MIMRY initialized.\nCreated:\n- .mimry/config.toml\n- .mimry/AGENT_RULES.md\n- .mimry/pointer.json\n- .mimry/graphify/\nGit hygiene:\n- "
        + hygiene
        + '\nNext: Run `mimry refresh`, then `mimry context "<task>"`.'
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


def cmd_refresh(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    print("Refreshing MIMRY: Graphify build -> MIMRY index -> status")
    graphify_status = run_graphify_build(root, execute=True)
    if graphify_status != 0:
        print("Refresh stopped: Graphify build failed.")
        return graphify_status
    stats = write_index(root, ptr)
    print(
        f"MIMRY indexing complete.\nIndexed files: {stats['files']}\nSymbols: {stats['symbols']}\nGraph edges: {stats['edges']}\nGraph engine: {stats['graph_engine']}\nIndex saved: {stats['index']}"
    )
    return cmd_status(a)


def cmd_status(a):
    root = Path(a.root).resolve()
    ptr = load_pointer(root)
    if not ptr:
        print("MIMRY status\nInitialized: no\nRecommended: Run `mimry init`.")
        return 1
    fresh = index_freshness(root, ptr)
    idx = fresh["index_path"]
    files = fresh["files"]
    symbols = fresh["symbols"]
    changed = fresh["changed"]
    missing = fresh["missing"]
    state = fresh["state"]
    g = fresh["graph"]
    graphify = graphify_health(root, index_state=state)
    print(
        f"MIMRY status\nRoot: {root}\nInitialized: yes\nIndex: {state}\nLast indexed: {ptr.get('lastIndexedAt') or 'never'}\nFiles indexed: {len(files)}\nSymbols indexed: {len(symbols)}\nGraph nodes/edges: {len(g.get('nodes', []))}/{len(g.get('edges', []))}\nChanged files: {len(changed)}\nDeleted files: {len(missing)}\nIndex path: {idx}"
    )
    print(
        "Graphify health"
        f"\nStatus: {graphify['status']}"
        f"\nSource: {graphify_source()}"
        f"\nPinned commit: {pinned_commit_for_status()}"
        f"\nOutput dir: {graphify['output_dir']}"
        f"\ngraph.json: {'yes' if graphify['graph_exists'] else 'missing'}"
        f" ({graphify['graph_nodes']} nodes/{graphify['graph_edges']} edges; generated {graphify['graph_generated_at'] or 'unknown'})"
        f"\nGRAPH_REPORT.md: {'yes' if graphify['report_exists'] else 'missing'}"
        f" (generated {graphify['report_generated_at'] or 'unknown'})"
        f"\nmanifest.json: {'yes' if graphify['manifest_exists'] else 'missing'}"
        f" ({graphify['manifest_entries']} entries; generated {graphify['manifest_generated_at'] or 'unknown'})"
        f"\nBuilt from commit: {graphify['built_from_commit'] or 'unknown'}"
        f"\nGraphify source changes: {len(graphify['source_changed_files'])} changed, {len(graphify['source_missing_files'])} missing"
        f"\nGraphify output stale/missing: {'yes' if graphify['status'] != 'current' else 'no'}"
    )
    if state == "stale":
        print("Recommended: Run `mimry reindex`.")
    if graphify["status"] != "current":
        print("Graphify recommended: Run `mimry graphify build --execute` or `mimry refresh`.")
    return 0 if state == "current" else 2


def _index_and_graphify_health(root: Path, ptr: dict):
    fresh = index_freshness(root, ptr)
    graphify = graphify_health(root, index_state=fresh["state"])
    return fresh, graphify


def _print_status_summary(ptr: dict, fresh: dict, graphify: dict):
    print(
        "Status summary:"
        f"\n- Index: {fresh['state']}"
        f"\n- Last indexed: {ptr.get('lastIndexedAt') or 'never'}"
        f"\n- Files indexed: {len(fresh['files'])}"
        f"\n- Changed/deleted files: {len(fresh['changed'])}/{len(fresh['missing'])}"
        f"\n- Graph nodes/edges: {len(fresh['graph'].get('nodes', []))}/{len(fresh['graph'].get('edges', []))}"
        f"\n- Graphify: {graphify['status']}"
        f" ({graphify['graph_nodes']} nodes/{graphify['graph_edges']} edges; output {graphify['output_dir']})"
    )


def _write_context_pack(root: Path, ptr: dict, query: str, *, limit: int = 8) -> list[dict]:
    rows = find_rows(Path(ptr["indexPath"]), query, limit, True, root=root)
    paths = [r["path"] for r in rows]
    relationship_lines = graphify_relationship_lines(root, paths)
    report_excerpt = graphify_report_excerpt(root)
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
        lines += [f"### {i}. `{r['path']}`", f"Score: {r['score']}", f"Reason: {r['reason']}"]
        if r.get("details"):
            lines += [f"Details: {r['details']}"]
        lines += [""]
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
    return rows


def cmd_preflight(a):
    root = Path(a.root).resolve()
    safe_root(root)
    root.mkdir(parents=True, exist_ok=True)

    init_ran = False
    ptr = load_pointer(root)
    if not ptr:
        init_ran = True
        init_status = cmd_init(SimpleNamespace(root=str(root), root_type="repo", skip_graphify=False))
        if init_status != 0:
            return init_status
        ptr = require(root)

    fresh, graphify = _index_and_graphify_health(root, ptr)
    reasons = []
    if fresh["state"] != "current":
        reasons.append(f"index {fresh['state']}")
    if graphify["status"] != "current":
        reasons.append(f"Graphify {graphify['status']}")
    if getattr(a, "force_refresh", False):
        reasons.append("forced")

    refresh_ran = bool(reasons)
    if refresh_ran:
        print("Preflight refresh: running (" + ", ".join(reasons) + ")")
        graphify_status = run_graphify_build(root, execute=True)
        if graphify_status != 0:
            print("Preflight stopped: Graphify build failed.")
            return graphify_status
        stats = write_index(root, ptr)
        print(
            f"MIMRY indexing complete. Files: {stats['files']}; Symbols: {stats['symbols']}; "
            f"Graph edges: {stats['edges']}; Index: {stats['index']}"
        )
        ptr = require(root)
        fresh, graphify = _index_and_graphify_health(root, ptr)
    else:
        print("Preflight refresh: skipped (index and Graphify are current)")

    rows = _write_context_pack(root, ptr, a.task)

    print("MIMRY preflight complete")
    print(f"Root: {root}")
    print(f"Init ran: {'yes' if init_ran else 'no'}")
    print(f"Refresh ran: {'yes' if refresh_ran else 'no'}")
    _print_status_summary(ptr, fresh, graphify)
    print(f"Context: {context_file(root)}")
    print("Top files:")
    if rows:
        for i, row in enumerate(rows[:5], 1):
            print(f"{i}. {row['path']} (score {row['score']}) — {row['reason']}")
    else:
        print("- none")
    print(f"Next: read {context_file(root)} before opening files.")
    return 0


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
    _write_context_pack(root, ptr, a.query)
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
    root = Path(a.root).resolve()
    try:
        if a.all:
            cache = validated_cache_home(root)
            shutil.rmtree(cache, ignore_errors=True)
            print(f"Wiped all MIMRY cache: {cache}")
            return 0
        ptr = require(root)
        idx = validated_current_index_path(Path(ptr["indexPath"]), root)
        shutil.rmtree(idx, ignore_errors=True)
        print(f"Wiped current root cache: {idx}")
        return 0
    except UnsafeCachePathError as e:
        print(f"Refusing cache wipe: {e}")
        return 2
