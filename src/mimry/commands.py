from __future__ import annotations

import itertools
import json
import re
import shutil
import sys
import time
import webbrowser
from functools import wraps
from importlib import metadata
from pathlib import Path

from . import __version__, ui
from .adapters import list_adapters
from .bench import run_bench
from .cache_safety import (
    UnsafeCachePathError,
    validated_cache_home,
    validated_current_root_cache_path,
)
from .commit_feedback import record_commit_feedback
from .core.artifacts import (
    ambiguity_message,
    load_graph,
    report_path,
)
from .core.export import export_cypher, export_graphml, export_html, export_obsidian
from .core.languages import language_counts
from .digest import canonical_digest, canonical_state
from .feedback import (
    COMMIT_NOTE_PREFIX,
    feedback_payload_from_args,
    feedback_stats,
    list_feedback,
    record_feedback,
    show_feedback,
)
from .history import history_payload, path_text, plain
from .indexer import write_index
from .install_info import repair_command
from .lifecycle import (
    build_graph_first,
    change_summary,
    command_root,
    health,
    indexed_root,
    not_set_up,
    print_grammar_warning,
    print_index_result,
    print_parse_error_warning,
    print_problems,
    print_rows_capped,
    print_skipped_counts,
    refresh_if_stale,
    require,
    root_name,
    run_init,
    run_refresh,
    stale_changes,
    stale_summary,
    totals,
)
from .plan import (
    PlanStore,
    canonical_plan,
    plan_digest,
    render_markdown,
    render_terminal,
    validate_plan,
    write_plan_output,
)
from .queries import (
    calls_payload,
    index_and_graph_health,
    inferred_mark,
    oversized_message,
    print_results,
    print_results_section,
    print_verification,
    run_explain,
    run_impact,
    run_path,
    run_preflight,
    run_why,
    symbol_matches,
    write_context_pack,
)
from .routing import route_payload, write_brief
from .search import find_rows
from .security import (
    redact_sensitive_text,
    safe_root,
    sanitize_query,
)
from .semantic import semantic_health, semantic_rows
from .source_snippets import with_source_snippets
from .state import (
    StateLockTimeoutError,
    grammar_error_of,
    grammar_unavailable_count,
    parse_error_summary,
)
from .storage import (
    active_index_pointer,
    connect,
    load_pointer,
    load_root_registry,
    prune_root_registry,
    register_root,
    save_pointer,
)
from .usage import usage_stats


def cmd_init(a):
    return run_init(a)


def cmd_plan_new(a):
    plan = PlanStore(Path(a.root)).create(a.root_plan, a.name)
    write_plan_output(
        f"{ui.headline('ok', 'Created plan ' + ui.quote(a.root_plan))}\n"
        f"  Plan ID: {plan['planId']}\n"
        f"  Root node ID: {plan['root']}\n"
        f"  Break it down with `mimry plan split {plan['planId']} {plan['root']} --child"
        ' "<step>"`.\n'
    )
    return 0


def cmd_plan_split(a):
    _, child_ids = PlanStore(Path(a.root)).split(a.plan_id, a.node_id, a.child)
    lines = [ui.headline("ok", f"Split {a.node_id} into {ui.count(len(child_ids), 'step')}")]
    lines.extend(
        f"  Child {index}: {child_id}" for index, child_id in enumerate(child_ids, start=1)
    )
    write_plan_output("\n".join(lines) + "\n")
    return 0


def cmd_plan_tree(a):
    plan = PlanStore(Path(a.root)).load(a.plan_id)
    if a.json:
        output = (
            json.dumps(canonical_plan(plan), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    elif a.md:
        output = render_markdown(plan)
    else:
        output = render_terminal(plan)
    write_plan_output(output)
    return 0


def cmd_plan_check(a):
    plan = PlanStore(Path(a.root)).load(a.plan_id)
    errors = validate_plan(plan)
    if errors:
        ui.fail(
            f"Plan {a.plan_id} has {ui.count(len(errors), 'problem')}",
            *(f"{error.code}: {error.message}" for error in errors),
        )
        return 2
    write_plan_output(ui.headline("ok", f"Plan {a.plan_id} is valid") + "\n")
    return 0


def cmd_plan_digest(a):
    write_plan_output(plan_digest(PlanStore(Path(a.root)).load(a.plan_id)) + "\n")
    return 0


def cmd_plan_list(a):
    inventory = PlanStore(Path(a.root)).list()
    if not inventory:
        write_plan_output('No plans yet. Create one with `mimry plan new "<goal>"`.\n')
        return 0
    rows = [(item["planId"], item["name"], item["rootText"]) for item in inventory]
    write_plan_output(
        f"Plans ({len(rows)})\n" + "".join(line + "\n" for line in ui.table_lines(rows))
    )
    return 0


def _requires_text(attr: str, noun: str, example: str):
    """Refuse a blank task or query as a usage error (exit 2)."""

    def decorate(command):
        @wraps(command)
        def guarded(a):
            if not (getattr(a, attr, None) or "").strip():
                ui.fail(f"Give a {noun}", f"For example: {example}")
                return 2
            return command(a)

        return guarded

    return decorate


def _index_reader(command):
    """Keep the resolved generation alive for the whole command."""

    @wraps(command)
    def guarded(a):
        root = command_root(a)
        with active_index_pointer(root):
            return command(a)

    return guarded


LANGUAGE_DETAIL_LIMIT = 6


def _languages_text(counts: dict[str, int], limit: int | None = None) -> str:
    """Counts like "Python 120, TypeScript 40", "other" last.

    With a limit, the rest are summed up as "and N more".
    """
    ranked = sorted(counts.items(), key=lambda item: (item[0] == "other", -item[1], item[0]))
    shown = ranked if limit is None else ranked[:limit]
    text = ", ".join(f"{name} {count:,}" for name, count in shown)
    hidden = len(ranked) - len(shown)
    return f"{text}, and {hidden} more" if hidden else text


def _install_problem() -> str | None:
    """What is wrong with this MIMRY install, or None.

    An editable install runs new code but keeps the dependencies of the
    version it was made from, and an interrupted reinstall can delete
    package metadata, so the CLI can start while part of the install is
    missing.
    """
    try:
        installed = metadata.version("mimry")
        requirements = metadata.requires("mimry") or []
    except metadata.PackageNotFoundError:
        return "This MIMRY install is incomplete - its package metadata is missing"
    if installed != __version__:
        return (
            f"This MIMRY install is out of sync - the code is {__version__}, but its"
            f" dependencies were installed for {installed}"
        )
    missing = []
    for requirement in requirements:
        if ";" in requirement:
            # ponytail: markers go unevaluated (that needs `packaging`);
            # no direct dependency carries one today.
            continue
        name = re.match(r"[A-Za-z0-9._-]+", requirement).group(0)
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        return f"This MIMRY install is incomplete - missing {', '.join(missing)}"
    return None


def _print_status(
    root: Path, ptr: dict, fresh: dict, graph: dict, semantic: dict, *, verbose: bool
) -> None:
    name = root_name(root)
    if fresh["state"] == "missing":
        ui.warn(f"{name} has not been indexed yet")
        ui.detail("Run `mimry index` to build the index.")
        return
    if fresh["state"] == "stale":
        ui.warn(f"{name} is out of date - {stale_summary(fresh)} since the last index")
        edited, new = stale_changes(fresh)
        rows = [
            *(("changed", path) for path in edited),
            *(("new", path) for path in new),
            *(("deleted", path) for path in fresh["missing"]),
        ]
        print_rows_capped(rows)
    else:
        ui.ok(f"{name} is up to date")
    commit = graph.get("built_from_commit")
    commit = None if commit == "unknown" else commit
    ui.detail(
        ui.facts(
            f"Indexed {ui.ago(ptr.get('lastIndexedAt'))}",
            f"at commit {commit[:7]}" if commit else None,
        )
    )
    stored = fresh["stored_totals"]
    ui.detail(totals(stored["files"], stored["symbols"], stored["links"]))
    languages = language_counts(fresh["files"], fresh["symbols"])
    if languages:
        ui.detail(f"Languages: {_languages_text(languages, LANGUAGE_DETAIL_LIMIT)}")
    print_skipped_counts(
        fresh["secret_skipped_count"],
        fresh["unreadable_count"],
        len(fresh["oversized_files"]),
        fresh["oversized_files"].values(),
    )
    if verbose and fresh["secret_skipped_paths"]:
        print_rows_capped([("secret", path) for path in fresh["secret_skipped_paths"]])
    if verbose and fresh["oversized_files"]:
        print_rows_capped([("too large", path) for path in sorted(fresh["oversized_files"])])
    print_grammar_warning(grammar_unavailable_count(ptr), grammar_error_of(ptr))
    print_parse_error_warning(*parse_error_summary(ptr))
    fixes = print_problems(fresh, graph, semantic)
    if verbose:
        ui.table(
            [
                ("Folder", str(root)),
                ("Index", str(fresh["index_path"])),
                ("Indexed at", ptr.get("lastIndexedAt") or "never"),
                (
                    "Graph",
                    (
                        f"{graph['status']}, {ui.count(graph['graph_nodes'], 'node')},"
                        f" {graph['output_dir']}"
                    ),
                ),
                (
                    "Search",
                    (
                        f"{semantic['status']}, {ui.count(semantic['chunks'], 'chunk')},"
                        f" {semantic['backend']}"
                    ),
                ),
                ("Commit", commit or "unknown"),
                *([("Languages", _languages_text(languages))] if languages else []),
            ]
        )
    for fix in fixes:
        ui.detail(f"Run `{fix}` to update it.")


def cmd_index(a):
    root = command_root(a)
    full = getattr(a, "full", False)
    started = time.perf_counter()
    stats = write_index(root, require(root, validate=False), full=full)
    print_index_result(
        root, stats, time.perf_counter() - started, full=full, verbose=getattr(a, "verbose", False)
    )
    return 0


def cmd_refresh(a):
    return run_refresh(a)


@_index_reader
def cmd_status(a):
    root = Path(a.root).resolve()
    safe_root(root)
    problem = _install_problem()
    if problem:
        ui.warn(problem)
        ui.detail(f"Run `{repair_command()}` to repair it.")
    ptr = load_pointer(root)
    if not ptr:
        ui.warn(f"MIMRY is not set up for {root_name(root)} yet")
        ui.detail("Run `mimry init` to set it up.")
        return 1
    fresh, graph, semantic = health(root, ptr, verify=getattr(a, "verify", False))
    _print_status(root, ptr, fresh, graph, semantic, verbose=getattr(a, "verbose", False))
    return 0 if fresh["state"] == "current" else 2


def _fresh_index(command):
    """Index a repo that was never indexed, reindex a stale one, and
    build missing graph files before the command reads them.
    """

    @wraps(command)
    def guarded(a):
        root = command_root(a)
        started = time.perf_counter()
        stats = refresh_if_stale(root, include_missing=True)
        if stats is not None and not getattr(a, "json", False):
            elapsed = ui.took(time.perf_counter() - started)
            if stats.get("set_up_worktree"):
                ui.ok(
                    "Set up MIMRY for this worktree - indexed"
                    f" {ui.count(stats['files'], 'file')} ({elapsed})"
                )
            elif stats.get("changes") is None:
                ui.ok(f"Indexed first - {ui.count(stats['files'], 'file')} ({elapsed})")
            else:
                ui.ok(f"Reindexed first - {change_summary(stats)} ({elapsed})")
        _build_graph_first(root, a)
        return command(a)

    return guarded


def _build_graph_first(root: Path, a) -> None:
    """Rebuild missing graph files, as `mimry refresh` would.

    If the rebuild fails, say so and let the command's own graph check
    report what is left, which still tells the user to run refresh.
    """
    try:
        elapsed = build_graph_first(root)
    except Exception as exc:
        ui.warn(f"Could not build the graph first: {exc}")
        return
    if elapsed is not None and not getattr(a, "json", False):
        ui.ok(f"Built the graph first ({ui.took(elapsed)})")


def _relative_status_path(root: Path, maybe_path: str | Path | None) -> str:
    if not maybe_path:
        return "unknown"
    path = Path(maybe_path)
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return str(maybe_path)


@_requires_text("task", "task", 'mimry preflight "fix the login timeout"')
def cmd_preflight(a):
    return run_preflight(a)


def cmd_feedback(a):
    root = Path(a.root).resolve()
    if getattr(a, "from_commit", None):
        return _cmd_feedback_from_commit(root, a.from_commit)
    action = getattr(a, "feedback_action", None)
    with active_index_pointer(root, exclusive=action not in {"stats", "list", "show"}) as ptr:
        if not ptr:
            raise SystemExit(not_set_up(root))
        return _cmd_feedback_active(a, root, ptr, action)


def _cmd_feedback_from_commit(root: Path, rev: str) -> int:
    """Hook entry point: never fails for an expected reason."""
    try:
        with active_index_pointer(root, exclusive=True) as ptr:
            if not ptr:
                ui.detail("MIMRY is not set up here; no commit feedback recorded.")
                return 0
            row, reason = record_commit_feedback(root, Path(ptr["indexPath"]), ptr["rootId"], rev)
    except StateLockTimeoutError:
        ui.detail("Another MIMRY command is using this index; commit feedback skipped.")
        return 0
    if row is None:
        ui.detail(reason)
        return 0
    commit = row["notes"].removeprefix(COMMIT_NOTE_PREFIX)
    ui.ok(f"Saved feedback for {ui.quote(ui.shorten(row['query'], 70))}")
    ui.detail(
        ui.facts(
            f"{len(row['changed_paths']):,} changed",
            f"{len(row['missed_paths']):,} missed",
            f"commit {commit}",
        )
    )
    return 0


USAGE_DAYS = 30


def _percent(part: int, whole: int) -> int:
    """Whole percent, halves rounding up."""
    return (100 * part + whole // 2) // whole


def _print_usage(root: Path) -> None:
    usage = usage_stats(root, days=USAGE_DAYS)
    commits = usage["commits"]
    matched = usage["commits_with_preflight"]
    hits = usage["top_five_hits"]
    rows = [("Preflights", f"{usage['preflights']:,}")]
    reasons = []
    if matched is None:
        reasons.append("Not a git repository.")
    elif not commits:
        reasons.append(f"No commits in the last {USAGE_DAYS} days.")
    else:
        rows.append(
            (
                "Commits",
                f"{matched:,} of {commits:,} had a preflight first"
                f" ({_percent(matched, commits)}%)",
            )
        )
        if matched:
            rows.append(
                (
                    "Top five",
                    f"held a changed file in {hits:,} of {matched:,} ({_percent(hits, matched)}%)",
                )
            )
        else:
            reasons.append("No commit had a preflight first.")
    ui.title(f"Last {usage['days']} days")
    ui.table(rows)
    for reason in reasons:
        ui.detail(reason)


def _cmd_feedback_active(a, root: Path, ptr: dict, action: str | None):
    idx = Path(ptr["indexPath"])
    verbose = getattr(a, "verbose", False)

    if action == "stats":
        _print_usage(root)
        print()
        stats = feedback_stats(idx, ptr["rootId"])
        if not stats["records"]:
            print("No feedback recorded yet.")
            ui.detail(
                'After a task, run `mimry feedback --query "<task>" --opened <files> --outcome'
                " passed`."
            )
            return 0
        ui.title(ui.count(stats["records"], "feedback record"))
        outcomes = sorted(stats["outcomes"].items(), key=lambda item: (-item[1], item[0]))
        ui.table(
            [
                ("Outcomes", ui.facts(*(f"{n:,} {outcome}" for outcome, n in outcomes)) or "none"),
                (
                    "Files",
                    ui.facts(*(f"{n:,} {label}" for label, n in stats["path_counts"].items() if n))
                    or "none",
                ),
                (
                    "Sources",
                    ui.facts(
                        f"{stats['from_commits']:,} from commits",
                        f"{stats['records'] - stats['from_commits']:,} from agents",
                    ),
                ),
            ]
        )
        return 0

    if action == "list":
        rows = list_feedback(idx, ptr["rootId"], getattr(a, "limit", 10))
        if not rows:
            print("No feedback recorded yet.")
            return 0
        ui.title(f"Recent feedback ({len(rows)})")
        print()
        ui.table(
            [
                (
                    ui.ago(row["created_at"]),
                    row["outcome"],
                    ui.shorten(row["query"], 60),
                    ui.faint(row["feedback_id"]),
                )
                for row in rows
            ]
        )
        print()
        ui.detail("Show one with `mimry feedback show <id>`.", 0)
        return 0

    if action == "show":
        row = show_feedback(idx, ptr["rootId"], a.feedback_id)
        if not row:
            ui.fail(
                f"No feedback record with ID {a.feedback_id}",
                "List them with `mimry feedback list`.",
            )
            return 1
        print(json.dumps(row, indent=2, sort_keys=True))
        return 0

    payload = feedback_payload_from_args(root, a)
    if not payload["query"]:
        ui.fail(
            "Feedback needs the task it is about",
            'Pass --query "<task>", or --from-json with a query field.',
        )
        return 2
    redacted_fields = payload.get("redacted_fields") or []
    row = record_feedback(idx, ptr["rootId"], payload, lock=False)
    ui.ok(f"Saved feedback for {ui.quote(ui.shorten(row['query'], 70))}")
    kinds = ("suggested", "opened", "changed", "missed", "ignored")
    ui.detail(
        ui.facts(
            f"Outcome: {row['outcome']}",
            *(f"{len(row[f'{kind}_paths']):,} {kind}" for kind in kinds if row[f"{kind}_paths"]),
        )
    )
    if row["changed_paths"] or row["opened_paths"] or row["missed_paths"]:
        ui.detail("Future searches for similar tasks will rank these files higher.")
    if row["ignored_paths"]:
        ui.detail("Ignored suggestions will rank lower next time.")
    if redacted_fields:
        ui.warn("Removed what looked like secrets from: " + ", ".join(redacted_fields))
    if verbose:
        for kind in kinds:
            if row[f"{kind}_paths"]:
                ui.detail(f"{kind.capitalize()}: {_format_paths(row[f'{kind}_paths'])}")
    ui.detail(ui.faint(f"ID: {row['feedback_id']}"))
    return 0


def _format_paths(paths: list[str], limit: int = 6) -> str:
    if not paths:
        return "none"
    suffix = "" if len(paths) <= limit else f", +{len(paths) - limit} more"
    return ", ".join(paths[:limit]) + suffix


def _print_semantic_degrade(idx: Path, root_id: str | None) -> bool:
    """Warn if semantic search cannot answer; return whether it can."""
    semantic_info = semantic_health(idx, root_id)
    if semantic_info["status"] == "current":
        return True
    ui.warn(f"Semantic search is {semantic_info['status']}")
    ui.detail("Run `mimry refresh` to rebuild it.")
    return False


def _print_search(heading: str, query: str, rows: list[dict], *, verbose: bool) -> int:
    """Print the matches; exit 1 when there are none."""
    if not rows:
        print(f"No files match {ui.quote(redact_sensitive_text(query))}.")
        ui.detail("Try different words, or `mimry symbol <name>` to look up a function or class.")
        return 1
    ui.title(f"{heading} {ui.quote(redact_sensitive_text(query))}")
    print()
    print_results(rows, verbose=verbose)
    return 0


@_fresh_index
def cmd_semantic(a):
    root = Path(a.root).resolve()
    query = sanitize_query(getattr(a, "query", None) or "")
    if query == "index":
        # Semantic state is generation-bound; rebuild through normal
        # atomic publication instead of mutating the active SQLite
        # generation in place.
        stats = write_index(root, require(root, validate=False))
        ui.ok(f"Semantic search is ready ({ui.count(stats['semantic_chunks'], 'chunk')})")
        return 0
    with active_index_pointer(root) as ptr:
        if not ptr:
            raise SystemExit(not_set_up(root))
        idx = Path(ptr["indexPath"])
        if query == "status":
            semantic_info = semantic_health(idx, ptr.get("rootId"))
            if _print_semantic_degrade(idx, ptr.get("rootId")):
                ui.ok(
                    f"Semantic search is ready ({ui.count(semantic_info['chunks'], 'chunk')},"
                    f" {semantic_info['backend']})"
                )
            return 0
        if not query:
            ui.fail(
                "Semantic search needs a query",
                'For example: mimry semantic "where do we retry payments"',
            )
            return 2
        rows, semantic_info = semantic_rows(idx, ptr.get("rootId"), query, getattr(a, "limit", 10))
        if not _print_semantic_degrade(idx, ptr.get("rootId")):
            return 0
        return _print_search(
            "Similar content for", query, rows, verbose=getattr(a, "verbose", False)
        )


@_requires_text("query", "query", 'mimry find "login timeout"')
@_fresh_index
def cmd_find(a):
    start = Path(getattr(a, "find_directory", a.root)).resolve()
    root = indexed_root(start)
    # Pin the owning generation, not an unindexed child directory.
    # Keep the original scope when main retries after a schema rebuild.
    a.find_directory = start
    a.root = str(root)
    with active_index_pointer(root):
        return _find_in_directory(a, root, start)


def _find_in_directory(a, root, start):
    ptr = require(root)
    idx = Path(ptr["indexPath"])
    semantic = getattr(a, "semantic", False)
    if semantic:
        _print_semantic_degrade(idx, ptr.get("rootId"))
    query = sanitize_query(a.query)
    languages = getattr(a, "language", [])
    path_globs = getattr(a, "path_glob", [])
    if not path_globs and not getattr(a, "everywhere", False) and start != root:
        relative = start.relative_to(root).as_posix()
        # The directory is literal, not a user-supplied glob.
        # Escape special characters before applying recursive scope.
        literal = "".join(
            "[[]" if char == "[" else f"[{char}]" if char in "*?" or char.isspace() else char
            for char in relative
        )
        path_globs = [literal + "/**"]
    exclude_globs = getattr(a, "exclude_glob", [])
    offset = getattr(a, "offset", None)
    try:
        rows = find_rows(
            idx,
            query,
            a.limit,
            root=root,
            root_id=ptr.get("rootId"),
            semantic=semantic,
            languages=languages,
            path_globs=path_globs,
            exclude_globs=exclude_globs,
            offset=offset,
        )
    except ValueError as exc:
        ui.fail("Invalid search scope", str(exc))
        return 2
    scope = []
    if languages:
        scope.append("language=" + ",".join(languages))
    if path_globs:
        scope.append("path=" + ",".join(path_globs))
    if exclude_globs:
        scope.append("exclude=" + ",".join(exclude_globs))
    if offset is not None:
        scope.append(f"offset={offset}")
    if scope:
        ui.detail("Scope: " + "; ".join(scope))
    if getattr(a, "snippets", False):
        try:
            rows = with_source_snippets(
                rows,
                root=root,
                pointer=ptr,
                query=query,
                lines_per_result=a.snippet_lines,
                chars_per_result=a.snippet_chars,
                total_lines=a.snippet_total_lines,
                total_chars=a.snippet_total_chars,
            )
        except ValueError as exc:
            ui.fail("Invalid snippet limits", str(exc))
            return 2
    return _print_search("Best matches for", query, rows, verbose=getattr(a, "verbose", False))


@_requires_text("query", "query", 'mimry related "login timeout"')
@_fresh_index
@_index_reader
def cmd_related(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    query = sanitize_query(a.query)
    rows = find_rows(
        Path(ptr["indexPath"]), query, a.limit, True, root=root, root_id=ptr.get("rootId")
    )
    return _print_search("Files related to", query, rows, verbose=getattr(a, "verbose", False))


@_requires_text("query", "task", 'mimry route "fix the login timeout"')
@_fresh_index
@_index_reader
def cmd_route(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    payload = route_payload(root, ptr, a.query, limit=getattr(a, "limit", 8))
    if getattr(a, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    gates = payload["risk_approval_gates"]
    risk = payload["risk_level"] if payload["risk_level"] not in ("", "none") else "none detected"
    ui.title(f"How to approach {ui.quote(sanitize_query(a.query))}")
    print()
    ui.table(
        [
            ("Agent", f"{payload['recommended_agent']} ({payload['confidence']} confidence)"),
            ("Why", "; ".join(payload["why"])),
            ("Context packs", ", ".join(payload["skill_context_packs"]) or "none"),
            ("Risk", risk + (f" - needs approval: {'; '.join(gates)}" if gates else "")),
        ]
    )
    print_results_section(
        "Start with", payload["likely_files"], verbose=getattr(a, "verbose", False)
    )
    print_verification(payload["suggested_verification"])
    print()
    print(f"Next: {payload['next']}")
    return 0


@_requires_text("query", "task", 'mimry brief "fix the login timeout" --agent <role>')
@_fresh_index
@_index_reader
def cmd_brief(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    try:
        path, payload = write_brief(root, ptr, a.query, a.agent, limit=getattr(a, "limit", 8))
    except ValueError as exc:
        ui.fail("Could not write the brief", str(exc))
        return 2
    ui.ok(f"Wrote a {payload['agent']} brief for {ui.quote(sanitize_query(a.query))}")
    ui.detail(ui.display_path(path, root))
    ui.detail(
        f"Suggested agent for this task: {payload['recommended_agent']} ({payload['confidence']}"
        " confidence)"
    )
    return 0


@_requires_text("query", "task", 'mimry explain "fix the login timeout"')
@_fresh_index
@_index_reader
def cmd_explain(a):
    return run_explain(a)


@_fresh_index
@_index_reader
def cmd_path(a):
    return run_path(a)


@_fresh_index
@_index_reader
def cmd_impact(a):
    return run_impact(a)


HISTORY_PATHS_SHOWN = 8
HISTORY_SHALLOW_NOTE = "shallow history: only the commits this clone has are searched"


@_requires_text("query", "query", 'mimry history "share extension removed"')
@_fresh_index
def cmd_history(a):
    root = command_root(a)
    if a.limit < 1:
        ui.fail("Give a limit of 1 or more", 'For example: mimry history "login" --limit 5')
        return 2
    query = sanitize_query(a.query)
    # Exclusive: the commit tables are written in place when HEAD moved.
    with active_index_pointer(root, exclusive=True) as ptr:
        if not ptr:
            raise SystemExit(not_set_up(root))
        con = connect(Path(ptr["indexPath"]))
        try:
            payload = history_payload(con, root, query, a.limit)
        finally:
            con.close()
    if "error" in payload:
        error = payload["error"]
        if error["code"] == "history_failed":
            ui.fail("Could not read the commit history", error["message"])
        else:
            ui.fail(error["message"])
        return 1
    commits = payload["commits"]
    shallow = payload["shallow"]
    if a.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if commits else 1
    if not commits:
        print(f"No commits match {ui.quote(query)}.")
        ui.detail("Try different words, or `mimry find` for files that exist now.")
        if shallow:
            ui.detail(HISTORY_SHALLOW_NOTE)
        return 1
    ui.title(f"Commits for {ui.quote(query)}")
    if shallow:
        ui.detail(HISTORY_SHALLOW_NOTE)
    print()
    for index, commit in enumerate(commits):
        if index:
            print()
        print(f"  {commit['sha'][:7]}  {commit['date']}  {plain(commit['subject'])}")
        shown = commit["paths"][:HISTORY_PATHS_SHOWN]
        for line in ui.table_lines(
            [(path["status"], path_text(path)) for path in shown], indent=6
        ):
            print(line)
        if len(commit["paths"]) > len(shown):
            print(f"      +{len(commit['paths']) - len(shown)} more")
    return 0


def _location(path: str, line) -> str:
    return f"{path}:{line}" if line else path


@_fresh_index
@_index_reader
def cmd_callers(a):
    return _print_calls(a, "callers")


@_fresh_index
@_index_reader
def cmd_callees(a):
    return _print_calls(a, "callees")


def _print_calls(a, relation: str) -> int:
    root = Path(a.root).resolve()
    ptr = require(root)
    name = sanitize_query(a.name)
    file = sanitize_query(a.file) if a.file else None
    payload = calls_payload(root, ptr, name, relation, a.depth, file, getattr(a, "line", None))
    if getattr(a, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if "error" in payload else 0
    if "error" in payload and payload["error"]["code"] == "graph_not_current":
        ui.warn(payload["error"]["message"])
        ui.detail("Run `mimry refresh`, then try again.")
        return 1
    target = payload["target"]
    if target is None:
        if payload["ambiguous"]:
            print(ambiguity_message(name, payload["candidates"]))
            ui.table(
                [(_location(c["path"], c["line"]), c["kind"]) for c in payload["candidates"]],
                indent=4,
            )
            return 0
        print(payload["error"]["message"])
        if payload["error"]["code"] == "unknown_line":
            ui.detail("Candidates:")
            ui.table(
                [(_location(c["path"], c["line"]), c["kind"]) for c in payload["candidates"]],
                indent=4,
            )
        elif payload["error"]["code"] == "unknown_file":
            if payload["error"]["suggestions"]:
                ui.detail("Did you mean:")
                ui.table([(path,) for path in payload["error"]["suggestions"]], indent=4)
        elif payload["candidates"]:
            ui.detail("Did you mean:")
            ui.table(
                [(c["name"], _location(c["path"], c["line"])) for c in payload["candidates"]],
                indent=4,
            )
        return 1
    results = payload["results"]
    if not results:
        if relation == "callers":
            print(
                f"No static callers of {name} found. Calls through imports MIMRY could not"
                " resolve, and dynamic calls, are not tracked; search the code before"
                " treating it as unused."
            )
        else:
            print(
                f"No static callees of {name} found. Calls through imports MIMRY could not"
                " resolve, and dynamic calls, are not tracked."
            )
        return 0
    noun = "caller" if relation == "callers" else "callee"
    within = f" within {a.depth} calls" if a.depth > 1 else ""
    ui.title(
        f"{ui.count(len(results), noun)} of {name}{within}"
        f" ({_location(target['path'], target['line'])})"
    )
    for path, group in itertools.groupby(results, key=lambda r: r["path"]):
        print()
        ui.detail(path)
        ui.table([_call_row(r, a.depth) for r in group], indent=4)
    print()
    ui.detail(ui.faint(payload["note"]))
    return 0


def _call_row(result: dict, depth: int) -> tuple:
    name = result["name"] + inferred_mark(result["confidence"])
    cells = [name, result["kind"], f":{result['line']}" if result["line"] else ""]
    if depth > 1:
        cells.append("direct" if result["depth"] == 1 else f"{result['depth']} calls away")
    return tuple(cells)


@_fresh_index
@_index_reader
def cmd_why(a):
    return run_why(a)


SYMBOL_DISPLAY_LIMIT = 50


@_fresh_index
@_index_reader
def cmd_symbol(a):
    ptr = require(Path(a.root).resolve())
    name = sanitize_query(a.name)
    matches = symbol_matches(ptr, name)
    if not matches:
        size_message = oversized_message(Path(a.root).resolve(), ptr, name)
        if size_message:
            ui.warn(size_message)
            return 1
        print(f"No symbols match {ui.quote(name)}.")
        ui.detail(f'Try part of the name, or `mimry find "{name}"` to search files.')
        return 1
    if matches[0]["kind"] == "file":
        title = (
            f"No symbols match {ui.quote(name)}; {ui.count(len(matches), 'file')} named like it"
        )
        ui.title(title)
    else:
        ui.title(f"{ui.count(len(matches), 'symbol')} matching {ui.quote(name)}")
    print()
    ui.table(
        [
            (
                m["name"],
                m["kind"],
                m["path"] + (f":{m['line_start']}" if m["line_start"] else ""),
            )
            for m in matches[:SYMBOL_DISPLAY_LIMIT]
        ]
    )
    if len(matches) > SYMBOL_DISPLAY_LIMIT:
        ui.detail(
            ui.faint(
                f"+{len(matches) - SYMBOL_DISPLAY_LIMIT:,} more - use a longer name to narrow it"
                " down"
            )
        )
    return 0


@_requires_text("query", "task", 'mimry context "fix the login timeout"')
@_fresh_index
@_index_reader
def cmd_context(a):
    root = Path(a.root).resolve()
    ptr = require(root)
    if getattr(a, "semantic", False):
        _print_semantic_degrade(Path(ptr["indexPath"]), ptr.get("rootId"))
    rows, pack_path = write_context_pack(
        root, ptr, a.query, semantic=getattr(a, "semantic", False)
    )
    ui.ok(f"Wrote a context pack for {ui.quote(sanitize_query(a.query))}")
    ui.detail(ui.facts(ui.display_path(pack_path, root), ui.count(len(rows), "relevant file")))
    return 0


def _extensions(extensions: list[str], limit: int = 4) -> str:
    return ", ".join(extensions[:limit]) + (
        f", +{len(extensions) - limit} more" if len(extensions) > limit else ""
    )


def cmd_adapters(a):
    adapters = list_adapters(include_planned=not getattr(a, "active_only", False))
    verbose = getattr(a, "verbose", False)
    for status, heading in (("active", "Active adapters"), ("planned", "Planned adapters")):
        group = [adapter for adapter in adapters if adapter["status"] == status]
        if not group:
            continue
        if status != "active":
            print()
        ui.title(f"{heading} ({len(group)})")
        if not verbose:
            ui.table(
                [
                    (adapter["name"], adapter["kind"], _extensions(adapter["extensions"]))
                    for adapter in group
                ]
            )
            continue
        for adapter in group:
            print()
            ui.detail(f"{adapter['name']} ({adapter['kind']}): {', '.join(adapter['extensions'])}")
            ui.table(
                [
                    ("Reads with", adapter["parser"]),
                    ("Extracts", ", ".join(adapter["emits"])),
                    ("Helps with", adapter["agent_use"]),
                ],
                indent=4,
            )
    if not verbose:
        print()
        print("Use --verbose to see what each adapter extracts.")
    return 0


def cmd_roots(a):
    if getattr(a, "prune", False):
        removed = prune_root_registry()
        if removed:
            ui.ok(f"Forgot {ui.count(len(removed), 'folder')} that no longer exist")
        else:
            ui.ok("Nothing to prune - every registered folder still exists")
        return 0
    reg = load_root_registry(repair=False)
    roots = reg.get("roots", [])
    id_counts: dict[str, int] = {}
    for root in roots:
        id_counts[str(root["rootId"])] = id_counts.get(str(root["rootId"]), 0) + 1
    present = [root for root in roots if Path(root["rootPath"]).expanduser().exists()]
    gone = len(roots) - len(present)
    verbose = getattr(a, "verbose", False)
    if not present:
        print("No indexed folders yet. Run `mimry init` in a repo to add one.")
    else:
        ui.title(f"Indexed folders ({len(present)})")
        rows = []
        for root in sorted(present, key=lambda r: str(r.get("lastIndexedAt") or ""), reverse=True):
            path = Path(root["rootPath"])
            indexed = (
                f"indexed {ui.ago(root['lastIndexedAt'])}"
                if root.get("lastIndexedAt")
                else "not indexed yet"
            )
            if id_counts[str(root["rootId"])] > 1:
                indexed += " (shares an index ID with another folder)"
            row = (path.name or str(path), ui.display_path(path), indexed)
            rows.append((*row, root["rootId"]) if verbose else row)
        ui.table(rows)
    if gone:
        ui.warn(
            f"{ui.count(gone, 'registered folder')} no longer {'exists' if gone == 1 else 'exist'}"
        )
        ui.detail("Run `mimry roots --prune` to forget them.")
    return 0


def cmd_cache_wipe(a):
    root = Path(a.root).resolve()
    try:
        if a.all:
            validated_cache_home(root)
            ui.fail(
                "`cache wipe --all` is turned off",
                "MIMRY can't yet coordinate wiping every repo's cache safely.",
                "Run `mimry cache wipe --current` in each repo instead.",
            )
            return 2
        initial = load_pointer(root, validate_active_generation=False)
        if not initial:
            raise SystemExit(not_set_up(root))
        if Path(initial["rootPath"]).expanduser().resolve(strict=False) != root:
            raise UnsafeCachePathError(
                f"Refusing to wipe cache: pointer root {initial['rootPath']} does not match"
                f" current root {root}"
            )
        base = validated_current_root_cache_path(
            Path(initial["indexPath"]), initial["rootId"], initial.get("generationId"), root
        )
        base.mkdir(parents=True, exist_ok=True)
        operation_lock = base / "operation.lock"
        with active_index_pointer(root, exclusive=True, validate=False) as ptr:
            if not ptr or ptr.get("rootId") != initial.get("rootId"):
                raise UnsafeCachePathError(
                    "Refusing to wipe cache: current root pointer changed while waiting for lock"
                )
            reset = {**ptr, "indexPath": str(base), "lastIndexedAt": None}
            reset.pop("generationId", None)
            save_pointer(root, reset)
            register_root(reset)
            for child in list(base.iterdir()):
                if child == operation_lock:
                    continue
                if child.is_symlink() or child.is_file():
                    child.unlink()
                else:
                    shutil.rmtree(child)
            leftovers = [child for child in base.iterdir() if child != operation_lock]
            if leftovers:
                raise OSError(f"cache wipe left entries behind: {', '.join(map(str, leftovers))}")
        ui.ok(f"Cleared the index cache for {root_name(root)}")
        ui.detail(ui.faint(str(base)))
        ui.detail("Run `mimry index` to rebuild it.")
        return 0
    except UnsafeCachePathError as e:
        ui.fail("Refusing to wipe the cache", str(e))
        return 2
    except OSError as e:
        ui.fail("The cache was only partly cleared", str(e))
        return 2


def cmd_report(a):
    """Print the current MIMRY graph report.

    Missing graph files are built first. A stale index is not reindexed.
    If the graph is still not current, warn the user but still print the
    report.
    """
    root = Path(a.root).resolve()
    _build_graph_first(root, a)
    ptr = require(root)
    _, graph = index_and_graph_health(root, ptr)

    if graph["status"] != "current":
        ui.warn(f"The graph files are {graph['status']}, so the report may be out of date")
        ui.detail("Run `mimry refresh` to rebuild the graph.")

    report_file = report_path(root)
    if report_file.exists():
        print(report_file.read_text(encoding="utf-8"), end="")
        return 0

    ui.warn("GRAPH_REPORT.md not found")
    ui.detail("Run `mimry refresh` to build the graph and report.")
    return 2


def cmd_digest(a):
    """Print the canonical semantic digest of the active index
    generation.

    This hashes normalized semantic state, not raw SQLite bytes or the
    generation manifest, so two identical repositories checked out at
    different absolute paths -- or indexed on different platforms --
    print the same value. See `mimry.digest` for the
    canonical/operational split.
    """
    root = Path(a.root).resolve()
    safe_root(root)
    ptr = load_pointer(root)
    if not ptr:
        print(not_set_up(root), file=sys.stderr)
        return 1
    idx = Path(ptr["indexPath"])
    if getattr(a, "json", False):
        print(json.dumps(canonical_state(idx, ptr.get("rootId")), sort_keys=True, indent=2))
        return 0
    print(canonical_digest(idx, ptr.get("rootId")))
    return 0


def cmd_bench(a):
    """Replay the repo's merged pull requests (see mimry.bench)."""
    return run_bench(Path(a.root), tasks=a.tasks, as_json=a.json, keep=a.keep)


def cmd_watch(a):
    """Poll for changes and refresh when they settle."""
    from .autoupdate import watch

    root = Path(a.root).resolve()
    return watch(root, interval=a.interval)


def cmd_git_hooks(a):
    """Manage git hooks for auto-refresh."""
    from .autoupdate import (
        git_hooks_install,
        git_hooks_status,
        git_hooks_uninstall,
    )

    root = Path(a.root).resolve()
    actions = {
        "install": git_hooks_install,
        "uninstall": git_hooks_uninstall,
        "status": git_hooks_status,
    }
    return actions[a.action](root)


_EXPORT_NAMES = {
    "html": "graph.html",
    "graphml": "graph.graphml",
    "cypher": "graph.cypher",
    "obsidian": "obsidian",
}


def cmd_export(a):
    """Export graph in multiple formats."""
    from .core.export import ExportTargetError

    root = Path(a.root).resolve()
    _build_graph_first(root, a)
    ptr = require(root)
    _, graph = index_and_graph_health(root, ptr)

    if graph["status"] != "current":
        ui.warn(f"The graph files are {graph['status']}, so export cannot proceed")
        ui.detail("Run `mimry refresh`, then try again.")
        return 1

    # Load the full graph
    graph_data = load_graph(root)
    if not graph_data.get("nodes"):
        ui.warn("No graph data found. Run `mimry refresh` to build the graph.")
        return 1

    # Determine output path
    output_path = (
        Path(a.out)
        if a.out
        else root / ".mimry" / "mimry-out" / "export" / _EXPORT_NAMES[a.format]
    )

    # Export
    try:
        if a.format == "html":
            export_html(graph_data, root, output_path, repo_name=root.name)
        elif a.format == "graphml":
            export_graphml(graph_data, output_path)
        elif a.format == "cypher":
            export_cypher(graph_data, output_path)
        elif a.format == "obsidian":
            export_obsidian(graph_data, root, output_path, force=a.force)

        node_count = len(graph_data.get("nodes", []))
        edge_count = len(graph_data.get("edges", graph_data.get("links", [])))

        ui.ok(f"Exported to {ui.display_path(output_path)}")
        ui.detail(f"{ui.count(node_count, 'node')} and {ui.count(edge_count, 'edge')}")

        if a.format == "html" and a.open:
            try:
                webbrowser.open(output_path.resolve().as_uri())
            except webbrowser.Error:
                ui.detail("Could not open browser automatically")

        return 0
    except (ExportTargetError, OSError) as e:
        ui.fail(str(e))
        return 1
