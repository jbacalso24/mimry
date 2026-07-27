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
from .feedback import feedback_payload_from_args, feedback_stats, list_feedback, record_feedback, show_feedback
from .freshness import index_freshness
from .graphify_artifacts import (
    graphify_evidence_for_path,
    graphify_health,
    graphify_relationship_lines,
    graphify_report_excerpt,
    graphify_shortest_path,
    graphify_surface_matches,
)
from .graphify_wrapper import graphify_source, pinned_commit_for_status, run_graphify_build, sync_visible_graph_output
from .indexer import write_index
from .paths import context_file, graph_output_dir, idx_path, legacy_context_file, mdir, now, output_dir, roots_file
from .routing import route_payload, verification_commands, write_brief
from .search import find_rows, print_rows
from .security import redact_sensitive_text, safe_root, sanitize_query
from .semantic import build_semantic_index, semantic_health, semantic_rows
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
    """Ensure repo-local MIMRY metadata/output is ignored in initialized Git worktrees."""
    git_root = _git_toplevel(root)
    if git_root is None:
        return False

    try:
        rel = root.relative_to(git_root)
    except ValueError:
        return False

    if rel == Path("."):
        patterns = [".mimry/", "mimry-out/"]
        check_paths = [Path(".mimry/pointer.json"), Path("mimry-out/context/latest.md")]
    else:
        prefix = rel.as_posix()
        patterns = [f"/{prefix}/.mimry/", f"/{prefix}/mimry-out/"]
        check_paths = [rel / ".mimry" / "pointer.json", rel / "mimry-out" / "context" / "latest.md"]

    gitignore = git_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    additions = []
    for pattern, check_path in zip(patterns, check_paths, strict=True):
        if pattern in lines:
            continue
        ignored = subprocess.run(
            ["git", "-C", str(git_root), "check-ignore", "--quiet", "--", check_path.as_posix()],
            text=True,
            capture_output=True,
            check=False,
        )
        if ignored.returncode != 0:
            additions.append(pattern)

    if not additions:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    gitignore.write_text(f"{existing}{prefix}" + "\n".join(additions) + "\n", encoding="utf-8")
    return True


def cmd_init(a):
    root = Path(a.root).resolve()
    safe_root(root)
    root.mkdir(parents=True, exist_ok=True)
    gitignore_updated = ensure_mimry_gitignore(root)
    if load_pointer(root):
        print("MIMRY is already initialized for this root.")
        if gitignore_updated:
            print("Added MIMRY metadata/output ignore entries to the target Git worktree .gitignore.")
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
    output_dir(root).mkdir(parents=True, exist_ok=True)
    (output_dir(root) / "context").mkdir(parents=True, exist_ok=True)
    (graph_output_dir(root)).mkdir(parents=True, exist_ok=True)
    (mdir(root) / "config.toml").write_text(
        'version = "0.1.0"\nroot_type = "repo"\nstore_full_text = false\n', encoding="utf-8"
    )
    (mdir(root) / "AGENT_RULES.md").write_text(
        "# MIMRY Agent Rules\n\nUse MIMRY before repeated grep or blind file reading.\n", encoding="utf-8"
    )
    save_pointer(root, ptr)
    register_root(ptr)
    if not getattr(a, "skip_graphify", False):
        print("Building MIMRY graph artifacts in the private cache ...")
        graphify_status = run_graphify_build(root, execute=True)
        if graphify_status != 0:
            print(
                "MIMRY initialized, but the internal graph build failed. Run `mimry refresh` after fixing dependencies."
            )
            return graphify_status
    sync_visible_graph_output(root, ptr)
    hygiene = (
        "Added MIMRY metadata/output ignore entries to the target Git worktree .gitignore."
        if gitignore_updated
        else "No .gitignore change needed."
    )
    print(
        "MIMRY initialized.\nCreated:\n- .mimry/config.toml\n- .mimry/AGENT_RULES.md\n- .mimry/pointer.json\n- .mimry/mimry-out/\nGenerated output:\n- .mimry/mimry-out/context/latest.md\n- .mimry/mimry-out/graph/\nCache:\n- Heavy MIMRY index/cache artifacts are stored outside the target repo.\nGit hygiene:\n- "
        + hygiene
        + '\nNext: Run `mimry refresh`, then `mimry context "<task>"`.'
    )
    return 0


def require(root):
    safe_root(root)
    ptr = load_pointer(root)
    if not ptr:
        raise SystemExit("MIMRY is not initialized here. Run `mimry init` first.")
    return ptr


def cmd_index(a):
    root = Path(a.root).resolve()
    stats = write_index(root, require(root))
    print(
        f"MIMRY indexing complete.\nIndexed files: {stats['files']}\nSymbols: {stats['symbols']}\nGraph edges: {stats['edges']}\nGraph engine: {stats['graph_engine']}\nSemantic: current ({stats['semantic_chunks']} chunks, backend {stats['semantic_backend']})\nIndex saved: {stats['index']}"
    )
    return 0


def cmd_refresh(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    print("Refreshing MIMRY: internal graph build -> MIMRY index -> status")
    graphify_status = run_graphify_build(root, execute=True)
    if graphify_status != 0:
        print("Refresh stopped: internal graph build failed.")
        return graphify_status
    sync_visible_graph_output(root, ptr)
    stats = write_index(root, ptr)
    print(
        f"MIMRY indexing complete.\nIndexed files: {stats['files']}\nSymbols: {stats['symbols']}\nGraph edges: {stats['edges']}\nGraph engine: {stats['graph_engine']}\nSemantic: current ({stats['semantic_chunks']} chunks, backend {stats['semantic_backend']})\nIndex saved: {stats['index']}"
    )
    return cmd_status(a)


def cmd_status(a):
    root = Path(a.root).resolve()
    safe_root(root)
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
    semantic = semantic_health(Path(idx), ptr.get("rootId"), expected_files=len(files))
    print(
        f"MIMRY status\nRoot: {root}\nInitialized: yes\nIndex: {state}\nLast indexed: {ptr.get('lastIndexedAt') or 'never'}\nFiles indexed: {len(files)}\nSymbols indexed: {len(symbols)}\nGraph nodes/edges: {len(g.get('nodes', []))}/{len(g.get('edges', []))}\nChanged files: {len(changed)}\nDeleted files: {len(missing)}\nIndex path: {idx}"
    )
    print(
        "MIMRY graph artifact health"
        f"\nStatus: {graphify['status']}"
        f"\nSource: {graphify_source()}"
        f"\nPinned commit: {pinned_commit_for_status()}"
        f"\nArtifact output: {graphify['output_dir']}"
        f"\ngraph.json: {'yes' if graphify['graph_exists'] else 'missing'}"
        f" ({graphify['graph_nodes']} nodes/{graphify['graph_edges']} edges; generated {graphify['graph_generated_at'] or 'unknown'})"
        f"\nGRAPH_REPORT.md: {'yes' if graphify['report_exists'] else 'missing'}"
        f" (generated {graphify['report_generated_at'] or 'unknown'})"
        f"\nmanifest.json: {'yes' if graphify['manifest_exists'] else 'missing'}"
        f" ({graphify['manifest_entries']} entries; generated {graphify['manifest_generated_at'] or 'unknown'})"
        f"\nBuilt from commit: {graphify['built_from_commit'] or 'unknown'}"
        f"\nGraph source changes: {len(graphify['source_changed_files'])} changed, {len(graphify['source_missing_files'])} missing"
        f"\nMIMRY graph artifacts stale/missing: {'yes' if graphify['status'] != 'current' else 'no'}"
    )
    if graphify.get("using_legacy_output"):
        print("Compatibility: reading existing legacy repo-local graph artifacts; next refresh writes to the cache.")
    print(f"Semantic: {semantic['status']} ({semantic['chunks']} chunks, backend {semantic['backend']})")
    if state == "stale":
        if changed:
            print(
                "Changed paths: "
                + ", ".join(changed[:10])
                + ("" if len(changed) <= 10 else f", +{len(changed) - 10} more")
            )
        if missing:
            print(
                "Deleted paths: "
                + ", ".join(missing[:10])
                + ("" if len(missing) <= 10 else f", +{len(missing) - 10} more")
            )
        print("Recommended: Run `mimry reindex`.")
    if graphify["status"] != "current":
        print("Graph recommended: Run `mimry refresh`.")
    return 0 if state == "current" else 2


def _index_and_graphify_health(root: Path, ptr: dict):
    fresh = index_freshness(root, ptr)
    graphify = graphify_health(root, index_state=fresh["state"])
    return fresh, graphify


def _print_status_summary(ptr: dict, fresh: dict, graphify: dict):
    semantic = semantic_health(Path(fresh["index_path"]), ptr.get("rootId"), expected_files=len(fresh["files"]))
    print(
        "Status summary:"
        f"\n- Index: {fresh['state']}"
        f"\n- Last indexed: {ptr.get('lastIndexedAt') or 'never'}"
        f"\n- Files indexed: {len(fresh['files'])}"
        f"\n- Changed/deleted files: {len(fresh['changed'])}/{len(fresh['missing'])}"
        f"\n- Graph nodes/edges: {len(fresh['graph'].get('nodes', []))}/{len(fresh['graph'].get('edges', []))}"
        f"\n- MIMRY graph artifacts: {graphify['status']}"
        f" ({graphify['graph_nodes']} nodes/{graphify['graph_edges']} edges; output: `.mimry/mimry-out/graph/`; cache-backed)"
        f"\n- Semantic: {semantic['status']} ({semantic['chunks']} chunks, backend {semantic['backend']})"
    )


def _relative_status_path(root: Path, maybe_path: str | Path | None) -> str:
    if not maybe_path:
        return "unknown"
    path = Path(maybe_path)
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return str(maybe_path)


def _refresh_action(fresh: dict, graphify: dict) -> str:
    actions = []
    if fresh["state"] != "current":
        actions.append(f"index is {fresh['state']}")
    if graphify["status"] != "current":
        actions.append(f"MIMRY graph artifacts are {graphify['status']}")
    if not actions:
        return "none (index and MIMRY graph artifacts are current)"
    return "run `mimry refresh` (" + "; ".join(actions) + ")"


def _selected_file_records(fresh: dict, rows: list[dict]) -> dict[str, dict]:
    selected = {row["path"] for row in rows}
    return {f["rel_path"]: f for f in fresh["files"] if f.get("rel_path") in selected}


def _symbol_lines(fresh: dict, rows: list[dict], limit: int = 12) -> list[str]:
    selected = {row["path"] for row in rows}
    files_by_id = {f["file_id"]: f for f in fresh["files"]}
    lines = []
    for sym in fresh["symbols"]:
        file_rec = files_by_id.get(sym.get("file_id"))
        rel_path = file_rec.get("rel_path") if file_rec else None
        if rel_path not in selected:
            continue
        loc = f":{sym['line_start']}" if sym.get("line_start") else ""
        lines.append(f"- `{sym['name']}` ({sym['kind']}, {sym['language']}) — `{rel_path}{loc}`")
        if len(lines) >= limit:
            break
    return lines


def _is_likely_edit_surface(path: str) -> bool:
    lower = path.lower()
    if lower.startswith(("docs/", ".mimry/")) or lower.endswith((".md", ".mdx")):
        return False
    if (
        "/test" in lower
        or lower.startswith("tests/")
        or lower.endswith(("_test.py", ".test.ts", ".spec.ts", ".test.tsx"))
    ):
        return False
    if lower in {"package.json", "pyproject.toml", "readme.md", "agents.md", "claude.md"}:
        return False
    return lower.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".toml", ".json", ".yaml", ".yml"))


def _file_role(path: str) -> str:
    lower = path.lower()
    if _is_likely_edit_surface(path):
        return "likely edit surface"
    if (
        lower.startswith("tests/")
        or "/test" in lower
        or lower.endswith(("_test.py", ".test.ts", ".spec.ts", ".test.tsx"))
    ):
        return "test/verification support"
    if lower.endswith((".md", ".mdx")) or lower.startswith("docs/"):
        return "docs/rules support"
    if lower in {"package.json", "pyproject.toml", "tsconfig.json"} or "config" in lower:
        return "config/manifest support"
    return "supporting context"


def _reading_order_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return ["- No relevant files were selected; rerun with a narrower query or inspect repo entrypoints directly."]
    source_rows = [r for r in rows if _is_likely_edit_surface(r["path"])]
    support_rows = [r for r in rows if not _is_likely_edit_surface(r["path"])]
    ordered = source_rows + support_rows
    lines = []
    for i, row in enumerate(ordered, 1):
        rationale = "primary code/edit path" if _is_likely_edit_surface(row["path"]) else _file_role(row["path"])
        lines.append(f"{i}. `{row['path']}` — {rationale}; {row['reason']}")
    return lines


def _surface_lines(rows: list[dict], *, edit: bool) -> list[str]:
    selected = [row for row in rows if _is_likely_edit_surface(row["path"]) is edit]
    if not selected:
        label = "edit surfaces" if edit else "non-edit supporting files"
        return [f"- No obvious {label} selected by this query."]
    return [f"- `{row['path']}` — {_file_role(row['path'])}; score {row['score']}" for row in selected]


FRAMEWORK_FACT_MARKERS = (
    "nextjs app router",
    "nextjs api route",
    "fastapi endpoint",
    "fastapi app instance",
    "fastapi router instance",
    "expo router route",
    "react native mobile surface",
    "expo config",
    "sql schema table",
    "markdown headings",
    "markdown frontmatter",
    "markdown wiki links",
    "markdown doc links",
)


def _framework_detail_lines(record: dict, limit: int = 8) -> list[str]:
    metadata = record.get("metadata_text", "")
    if not metadata:
        return []
    parts = [part.strip() for part in metadata.split(" | ") if part.strip()]
    details = []
    for part in parts:
        lower = part.lower()
        if any(marker in lower for marker in FRAMEWORK_FACT_MARKERS):
            details.append(part[:500])
        if len(details) >= limit:
            break
    return details


def _verification_commands(fresh: dict) -> list[str]:
    return verification_commands(fresh)


def _detected_supporting_file_lines(fresh: dict, rows: list[dict], limit: int = 8) -> list[str]:
    already = {row["path"] for row in rows}
    candidates = []
    for f in fresh["files"]:
        rel_path = f.get("rel_path", "")
        if rel_path in already:
            continue
        role = _file_role(rel_path)
        if role in {"test/verification support", "docs/rules support", "config/manifest support"}:
            candidates.append(f"- `{rel_path}` — detected {role}; read if it constrains the change or verification.")
        if len(candidates) >= limit:
            break
    return candidates


def _risk_lines(fresh: dict, rows: list[dict]) -> list[str]:
    selected_paths = [row["path"] for row in rows]
    dirty = fresh["changed"] or fresh["missing"]
    lines = [
        "- Generated/cache paths (`.mimry/`, `.git/`, caches, build outputs) are support artifacts; do not edit them as source fixes.",
        "- Secrets/privacy-sensitive files are skipped by scanner policy; do not paste secret values into context packs or final reports.",
        "- Source files/tests/build output are the truth; MIMRY scores are navigation hints, not proof.",
        "- Tests/docs/config files are supporting evidence unless the task explicitly requires changing them.",
    ]
    risky_selected = [
        p for p in selected_paths if p.startswith(".mimry/") or "/cache" in p.lower() or p.lower().endswith(".lock")
    ]
    if risky_selected:
        lines.append(
            "- Selected generated/cache/fallback-looking paths: " + ", ".join(f"`{p}`" for p in risky_selected[:8])
        )
    if dirty:
        changed = ", ".join(f"`{p}`" for p in (fresh["changed"] + fresh["missing"])[:8])
        lines.append(
            f"- Index detected changed/deleted files ({changed}); avoid broad dirty work until refreshed/verified."
        )
    return lines


def _graphify_context_lines(root: Path, rows: list[dict], graphify: dict) -> list[str]:
    paths = [r["path"] for r in rows]
    relationship_lines = graphify_relationship_lines(root, paths)
    report_excerpt = graphify_report_excerpt(root)
    if graphify["status"] != "current":
        status = graphify["status"]
        return [
            f"- MIMRY relationship data is missing or stale (`{status}`); run `mimry refresh` before relying on graph paths.",
            "- No relationship path was invented. Use source imports/callers directly if this remains empty.",
        ]
    lines = relationship_lines or [
        "- MIMRY relationship data is current, but no path connected the selected files for this query.",
        "- No relationship path was invented; rerun with a narrower symbol/file query if graph navigation matters.",
    ]
    if report_excerpt:
        lines += ["", "### Graphify Report Signals", report_excerpt]
    return lines


def _write_context_pack(root: Path, ptr: dict, query: str, *, limit: int = 8, semantic: bool = False) -> list[dict]:
    query = sanitize_query(query)
    rows = find_rows(
        Path(ptr["indexPath"]), query, limit, True, root=root, root_id=ptr.get("rootId"), semantic=semantic
    )
    fresh, graphify = _index_and_graphify_health(root, ptr)
    semantic_state = semantic_health(Path(ptr["indexPath"]), ptr.get("rootId"), expected_files=len(fresh["files"]))
    file_records = _selected_file_records(fresh, rows)
    verification_commands = _verification_commands(fresh)
    lines = [
        "# MIMRY Context Pack",
        "",
        "## Query",
        redact_sensitive_text(query),
        "",
        "## Status Summary",
        f"- Root: `{root}`",
        f"- Index: {fresh['state']} (last indexed: {ptr.get('lastIndexedAt') or 'never'}; files: {len(fresh['files'])}; symbols: {len(fresh['symbols'])})",
        f"- Index changes: {len(fresh['changed'])} changed / {len(fresh['missing'])} deleted",
        f"- MIMRY graph artifacts: {graphify['status']} ({graphify['graph_nodes']} nodes / {graphify['graph_edges']} edges; output: `.mimry/mimry-out/graph/`; cache-backed)",
        f"- Semantic: {semantic_state['status']} ({semantic_state['chunks']} chunks, backend {semantic_state['backend']}; mode: {'on' if semantic else 'off'})",
        f"- MIMRY graph files: graph.json {'present' if graphify['graph_exists'] else 'missing'}, GRAPH_REPORT.md {'present' if graphify['report_exists'] else 'missing'}, manifest.json {'present' if graphify['manifest_exists'] else 'missing'}",
        f"- Refresh action: {_refresh_action(fresh, graphify)}",
        "",
        "## Summary",
        f"MIMRY found {len(rows)} relevant file(s). Use this as an agent handoff: read in order, verify source/tests, and avoid unsupported edits.",
        "",
        "## Relevant Files",
    ]
    for i, r in enumerate(rows, 1):
        role = _file_role(r["path"])
        adapter = file_records.get(r["path"], {}).get("adapter", "unknown")
        lines += [
            f"### {i}. `{r['path']}`",
            f"Score: {r['score']}",
            f"Reason: {r['reason']}",
            f"Role: {role}",
            f"Evidence: adapter `{adapter}`; relative path only; open source before editing.",
        ]
        details = r.get("details") or ""
        if details:
            lines += [f"Details: {details}"]
        framework_details = _framework_detail_lines(file_records.get(r["path"], {}))
        if framework_details:
            lines += ["Framework facts:", *[f"- {detail}" for detail in framework_details]]
        lines += [""]
    lines += [
        "## Relevant Symbols / Entities",
        *(
            _symbol_lines(fresh, rows)
            or [
                "- No indexed symbols/entities matched the selected files. Use `mimry symbol <name>` for a narrower lookup."
            ]
        ),
        "",
        "## Graphify Relationships / Communities",
        *_graphify_context_lines(root, rows, graphify),
        "",
        "## Suggested Reading Order",
        *_reading_order_lines(rows),
        "",
        "## Likely Edit Surfaces",
        *_surface_lines(rows, edit=True),
        "",
        "## Likely Non-Edit Supporting Files",
        *_surface_lines(rows, edit=False),
        *_detected_supporting_file_lines(fresh, rows),
        "",
        "## Risk Notes",
        *_risk_lines(fresh, rows),
        "",
        "## Suggested Verification Commands",
        *(
            [f"- `{cmd}`" for cmd in verification_commands]
            or ["- No project-specific commands detected; run the nearest tests/typecheck/build for affected files."]
        ),
        "",
        "## Source of Truth Reminder",
        "MIMRY narrows context; semantic search is a local fuzzy-recall supplement only. Source files, tests, build output, and human/operator verification remain the source of truth.",
        "",
        "## Final Report Checklist",
        "- Context query used and context path read.",
        "- Key source files inspected directly (with paths).",
        "- Files changed and why.",
        "- Verification commands run with exact results.",
        "- After verification, run `mimry feedback ...` with suggested/opened/changed/missed/outcome so future agents get better rankings.",
        "- MIMRY refreshed after meaningful changes, or reason not refreshed.",
        "- Yellow marks/blockers, especially stale MIMRY graph/index data or risky paths.",
        "",
    ]
    context_file(root).parent.mkdir(parents=True, exist_ok=True)
    context_file(root).write_text("\n".join(lines), encoding="utf-8")
    _write_legacy_context_redirect(root)
    return rows


def _write_legacy_context_redirect(root: Path) -> None:
    """Prevent stale pre-migration context packs from misleading agents.

    MIMRY now keeps generated output under `.mimry/mimry-out/` so ordinary
    project roots do not accumulate visible generated folders. Older checkouts
    may still have `mimry-out/context/latest.md`; overwrite that file with a
    small redirect if it already exists instead of leaving stale task context.
    """
    legacy = legacy_context_file(root)
    current = context_file(root)
    if not legacy.exists():
        return
    try:
        current_rel = current.relative_to(root).as_posix()
    except ValueError:
        current_rel = str(current)
    legacy.write_text(
        "# MIMRY context moved\n\n"
        "This legacy context path is no longer the source of truth.\n\n"
        f"Read `{current_rel}` instead.\n",
        encoding="utf-8",
    )


def cmd_preflight(a):
    root = Path(a.root).resolve()
    safe_root(root)
    root.mkdir(parents=True, exist_ok=True)

    init_ran = False
    ptr = load_pointer(root)
    if not ptr:
        init_ran = True
        init_status = cmd_init(SimpleNamespace(root=str(root), root_type="repo", skip_graphify=True))
        if init_status != 0:
            return init_status
        ptr = require(root)

    fresh, graphify = _index_and_graphify_health(root, ptr)
    reasons = []
    if fresh["state"] != "current":
        reasons.append(f"index {fresh['state']}")
    if graphify["status"] != "current":
        reasons.append(f"MIMRY graph artifacts {graphify['status']}")
    force_refresh = getattr(a, "force_refresh", False)
    if force_refresh:
        reasons.append("forced")

    refresh_ran = False
    index_ran = False
    if force_refresh:
        refresh_ran = True
        print("Preflight refresh: running (" + ", ".join(reasons) + ")")
        graphify_status = run_graphify_build(root, execute=True)
        if graphify_status != 0:
            print("Preflight stopped: internal graph build failed.")
            return graphify_status
        sync_visible_graph_output(root, ptr)
        stats = write_index(root, ptr)
        index_ran = True
        print(
            f"MIMRY indexing complete. Files: {stats['files']}; Symbols: {stats['symbols']}; "
            f"Graph edges: {stats['edges']}; Index: {stats['index']}"
        )
        ptr = require(root)
        fresh, graphify = _index_and_graphify_health(root, ptr)
    elif fresh["state"] in {"missing", "stale"}:
        print(f"Preflight index: running (index {fresh['state']}; skipping slow Graphify build)")
        stats = write_index(root, ptr)
        index_ran = True
        print(
            f"MIMRY indexing complete. Files: {stats['files']}; Symbols: {stats['symbols']}; "
            f"Graph edges: {stats['edges']}; Index: {stats['index']}"
        )
        ptr = require(root)
        fresh, graphify = _index_and_graphify_health(root, ptr)
    else:
        if reasons:
            print(
                "Preflight refresh: skipped (fast mode; "
                + ", ".join(reasons)
                + '; run `mimry preflight --force-refresh "<task>"` or `mimry refresh` for a full refresh)'
            )
        else:
            print("Preflight refresh: skipped (index and MIMRY graph artifacts are current)")

    rows = _write_context_pack(root, ptr, a.task)

    print("MIMRY preflight complete")
    print(f"Root: {root}")
    print(f"Init ran: {'yes' if init_ran else 'no'}")
    print(f"Refresh ran: {'yes' if refresh_ran else 'no'}")
    print(f"Index ran: {'yes' if index_ran else 'no'}")
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


def _format_paths(paths: list[str], limit: int = 6) -> str:
    if not paths:
        return "none"
    suffix = "" if len(paths) <= limit else f", +{len(paths) - limit} more"
    return ", ".join(paths[:limit]) + suffix


def cmd_feedback(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    idx = Path(ptr["indexPath"])
    action = getattr(a, "feedback_action", None)

    if action == "stats":
        stats = feedback_stats(idx, ptr["rootId"])
        print("MIMRY feedback stats")
        print(f"Records: {stats['records']}")
        print("Outcomes:")
        if stats["outcomes"]:
            for outcome, count in sorted(stats["outcomes"].items()):
                print(f"- {outcome}: {count}")
        else:
            print("- none")
        print("Path counts:")
        for label, count in stats["path_counts"].items():
            print(f"- {label}: {count}")
        return 0

    if action == "list":
        rows = list_feedback(idx, ptr["rootId"], getattr(a, "limit", 10))
        print("MIMRY feedback records")
        for row in rows:
            print(f"- {row['feedback_id']} {row['created_at']} outcome={row['outcome']} query={row['query'][:120]}")
        if not rows:
            print("- none")
        return 0

    if action == "show":
        row = show_feedback(idx, ptr["rootId"], a.feedback_id)
        if not row:
            print(f"Feedback record not found: {a.feedback_id}")
            return 1
        print(json.dumps(row, indent=2, sort_keys=True))
        return 0

    payload = feedback_payload_from_args(root, a)
    if not payload["query"]:
        print("Feedback requires --query or --json with a query field.")
        return 2
    redacted_fields = payload.get("redacted_fields") or []
    row = record_feedback(idx, ptr["rootId"], payload)
    influences = []
    if row["changed_paths"]:
        influences.append("changed-file boost")
    if row["opened_paths"]:
        influences.append("opened-file boost")
    if row["missed_paths"]:
        influences.append("missed-file recovery boost")
    if row["ignored_paths"]:
        influences.append("ignored suggestion downrank")
    print("MIMRY feedback recorded")
    if redacted_fields:
        print("Warning: likely secret value(s) redacted from feedback fields: " + ", ".join(redacted_fields))
    print(f"Feedback ID: {row['feedback_id']}")
    print(f"Outcome: {row['outcome']}")
    print(f"Suggested: {_format_paths(row['suggested_paths'])}")
    print(f"Opened: {_format_paths(row['opened_paths'])}")
    print(f"Changed: {_format_paths(row['changed_paths'])}")
    print(f"Missed: {_format_paths(row['missed_paths'])}")
    print(f"Ignored: {_format_paths(row['ignored_paths'])}")
    print(
        "Ranking influence: " + (", ".join(influences) if influences else "none until more path evidence is recorded")
    )
    return 0


def _print_semantic_degrade(idx: Path, root_id: str | None) -> None:
    health = semantic_health(idx, root_id)
    if health["status"] != "current":
        print(
            f"Semantic index is {health['status']} ({health['chunks']} chunks, backend {health['backend']}). "
            "Run `mimry refresh` or `mimry index` to rebuild local semantic chunks."
        )


def cmd_semantic(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    idx = Path(ptr["indexPath"])
    query = sanitize_query(getattr(a, "query", None) or "")
    if query == "status":
        health = semantic_health(idx, ptr.get("rootId"))
        print(f"Semantic: {health['status']} ({health['chunks']} chunks, backend {health['backend']})")
        return 0
    if query == "index":
        stats = build_semantic_index(idx, ptr["rootId"])
        print(f"Semantic indexed: current ({stats['chunks']} chunks, backend {stats['backend']})")
        return 0
    if not query:
        print('Semantic search requires a query, e.g. `mimry semantic "vague phrase"`.')
        return 2
    rows, health = semantic_rows(idx, ptr.get("rootId"), query, getattr(a, "limit", 10))
    if health["status"] != "current":
        _print_semantic_degrade(idx, ptr.get("rootId"))
        return 0
    print_rows(f"Semantic results for: {query}", rows)
    return 0


def cmd_find(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    idx = Path(ptr["indexPath"])
    semantic = getattr(a, "semantic", False)
    if semantic:
        _print_semantic_degrade(idx, ptr.get("rootId"))
    query = sanitize_query(a.query)
    print_rows(
        f"Search results for: {query}",
        find_rows(idx, query, a.limit, root=root, root_id=ptr.get("rootId"), semantic=semantic),
    )
    return 0


def cmd_related(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    query = sanitize_query(a.query)
    print_rows(
        f"Related files for: {query}",
        find_rows(Path(ptr["indexPath"]), query, a.limit, True, root=root, root_id=ptr.get("rootId")),
    )
    return 0


def cmd_route(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    payload = route_payload(root, ptr, a.query, limit=getattr(a, "limit", 8))
    if getattr(a, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("MIMRY route")
    print(f"Recommended agent: {payload['recommended_agent']}")
    print(f"Confidence: {payload['confidence']}")
    print("Why:")
    for reason in payload["why"]:
        print(f"- {reason}")
    print("Skill/context packs:")
    for pack in payload["skill_context_packs"]:
        print(f"- {pack}")
    print("Likely files:")
    if payload["likely_files"]:
        for i, row in enumerate(payload["likely_files"], 1):
            print(f"{i}. {row['path']} (score {row['score']}) — {row['reason']}")
    else:
        print("- none")
    print(f"Risk level: {payload['risk_level']}")
    print("Risk/approval gates:")
    if payload["risk_approval_gates"]:
        for gate in payload["risk_approval_gates"]:
            print(f"- {gate}")
    else:
        print("- none detected")
    print("Suggested verification:")
    for cmd in payload["suggested_verification"]:
        print(f"- {cmd}")
    print(f"Next: {payload['next']}")
    return 0


def cmd_brief(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    try:
        path, payload = write_brief(root, ptr, a.query, a.agent, limit=getattr(a, "limit", 8))
    except ValueError as exc:
        print(str(exc))
        return 2
    print("MIMRY brief generated")
    print(f"Output: {path}")
    print(f"Agent: {payload['agent']}")
    print(f"Recommended route: {payload['recommended_agent']} ({payload['confidence']})")
    return 0


def _print_verification_hints(fresh: dict) -> None:
    commands = _verification_commands(fresh)
    print("Suggested verification:")
    if commands:
        for cmd in commands[:5]:
            print(f"- {cmd}")
    else:
        print("- Run the nearest tests/typecheck/build for affected files.")


def _print_candidate_matches(title: str, matches: list[dict]) -> None:
    print(title)
    if not matches:
        print("- none")
        return
    for match in matches[:5]:
        print(f"- `{match['label']}` in `{match['path']}` (score {match['score']})")


def _format_path_step(step: dict) -> str:
    source = step["from"]
    target = step["to"]
    edge = step["edge"]
    relation = edge.get("relation") or edge.get("type") or "relates"
    source_label = source.get("label") or source.get("id")
    target_label = target.get("label") or target.get("id")
    source_file = source.get("source_file") or source.get("path") or "?"
    target_file = target.get("source_file") or target.get("path") or "?"
    return f"- `{source_label}` (`{source_file}`) --{relation}--> `{target_label}` (`{target_file}`)"


def cmd_explain(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    fresh, graphify = _index_and_graphify_health(root, ptr)
    query = sanitize_query(a.query)
    rows = find_rows(Path(ptr["indexPath"]), query, getattr(a, "limit", 5), True, root=root, root_id=ptr.get("rootId"))
    print(f"MIMRY explain: {query}")
    _print_status_summary(ptr, fresh, graphify)
    print("Top relevant files:")
    if rows:
        for i, row in enumerate(rows, 1):
            print(f"{i}. {row['path']} (score {row['score']})")
            print(f"   why: {row['reason']}")
            if row.get("details"):
                print(f"   facts: {row['details'][:300]}")
    else:
        print("- none; try a narrower file/symbol/query.")

    print("Symbols/entities:")
    for line in _symbol_lines(fresh, rows, limit=6) or ["- none matched selected files; try `mimry symbol <name>`."]:
        print(line)

    print("Relationship paths:")
    relationship_lines = graphify_relationship_lines(root, [r["path"] for r in rows], max_lines=6)
    if graphify["status"] != "current":
        print(f"- Graphify is {graphify['status']}; run `mimry refresh` before relying on relationship paths.")
        print("- No relationship path was invented.")
    elif relationship_lines:
        for line in relationship_lines:
            print(line)
    else:
        print("- No Graphify relationship path connected these ranked files. No relationship path was invented.")

    source = next((r for r in rows if _is_likely_edit_surface(r["path"])), rows[0] if rows else None)
    print("Likely source of truth:")
    print(
        f"- `{source['path']}` — open source and tests before editing." if source else "- unknown from current index."
    )
    _print_verification_hints(fresh)
    return 0


def cmd_path(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    fresh, graphify = _index_and_graphify_health(root, ptr)
    source = sanitize_query(a.source)
    target = sanitize_query(a.target)
    print(f"MIMRY path: {source} -> {target}")
    if graphify["status"] != "current":
        print(f"No Graphify relationship path found: Graphify artifacts are {graphify['status']}.")
        print("No path was invented. Run `mimry refresh`, then retry with file paths or symbol names.")
        return 0
    result = graphify_shortest_path(root, source, target)
    if result["found"] and result["steps"]:
        print("Path found:")
        for step in result["steps"]:
            print(_format_path_step(step))
        print("Source of truth: Graphify artifacts plus indexed source files; verify by opening each file above.")
        return 0
    print("No Graphify relationship path found between the resolved surfaces.")
    print("No path was invented.")
    _print_candidate_matches("Source candidates:", result["source_matches"])
    _print_candidate_matches("Target candidates:", result["target_matches"])
    print("Fallback queries:")
    print(f'- mimry related "{source}"')
    print(f'- mimry related "{target}"')
    print(f'- mimry explain "{source} {target}"')
    _print_verification_hints(fresh)
    return 0


def cmd_why(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    fresh, graphify = _index_and_graphify_health(root, ptr)
    idx = Path(ptr["indexPath"])
    query = sanitize_query(a.query)
    rows = find_rows(idx, query, max(getattr(a, "limit", 25), 25), True, root=root, root_id=ptr.get("rootId"))
    surface = sanitize_query(a.surface)
    surface_lower = surface.lower()
    exact = None
    for row in rows:
        if surface_lower == row["path"].lower() or surface_lower in row["path"].lower():
            exact = row
            break
    if exact is None:
        files = {f["file_id"]: f for f in load_jsonl(idx / "files.jsonl")}
        for sym in load_jsonl(idx / "symbols.jsonl"):
            if surface_lower in sym.get("name", "").lower():
                path = files.get(sym["file_id"], {}).get("rel_path", "")
                exact = next((row for row in rows if row["path"] == path), None)
                if exact:
                    break
    print(f"MIMRY why: {surface}")
    print(f"Ranked for query: {query}")
    if exact:
        print(f"File: {exact['path']}")
        print(f"Score: {exact['score']}")
        print("Ranking signals:")
        for reason in exact["reason"].split(", "):
            print(f"- {reason}")
        if exact.get("details"):
            print(f"Indexed facts: {exact['details'][:500]}")
    else:
        print("This surface was not in the top ranked results for that query.")
        print("Ranking signals:")
        print("- no direct filename/symbol/Graphify/config signal found in the current result window")
        print("Fallback: try a narrower query or `mimry find`/`mimry symbol`.")
    print("Graphify evidence:")
    if graphify["status"] != "current":
        print(f"- Graphify is {graphify['status']}; run `mimry refresh` for current graph evidence.")
    else:
        evidence = graphify_evidence_for_path(root, surface)
        for line in evidence or ["- no matching Graphify node/edge evidence for this surface"]:
            print(line)
        matches = graphify_surface_matches(root, surface, limit=3)
        if matches:
            _print_candidate_matches("Resolved Graphify candidates:", matches)
    _print_verification_hints(fresh)
    return 0


def cmd_symbol(a):
    ptr = require(Path(a.root).resolve())
    name = sanitize_query(a.name)
    print(f"Symbol search: {name}")
    files = {f["file_id"]: f for f in load_jsonl(Path(ptr["indexPath"]) / "files.jsonl")}
    for i, s in enumerate(
        [s for s in load_jsonl(Path(ptr["indexPath"]) / "symbols.jsonl") if name.lower() in s["name"].lower()], 1
    ):
        print(
            f"{i}. {s['name']} ({s['kind']}, {s['language']}) — {files.get(s['file_id'], {}).get('rel_path', s['file_id'])}:{s.get('line_start') or ''}"
        )
    return 0


def cmd_context(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    if getattr(a, "semantic", False):
        _print_semantic_degrade(Path(ptr["indexPath"]), ptr.get("rootId"))
    _write_context_pack(root, ptr, a.query, semantic=getattr(a, "semantic", False))
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
