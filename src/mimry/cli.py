from __future__ import annotations

import os
import sys


def build_parser():
    # Imported here, not at module load: `hook-check` runs before every
    # agent tool call and must not pay for the indexing stack it never
    # uses.
    import argparse

    from . import __version__
    from .commands import (
        cmd_adapters,
        cmd_brief,
        cmd_cache_wipe,
        cmd_context,
        cmd_digest,
        cmd_explain,
        cmd_export,
        cmd_feedback,
        cmd_find,
        cmd_git_hooks,
        cmd_index,
        cmd_init,
        cmd_path,
        cmd_plan_check,
        cmd_plan_digest,
        cmd_plan_list,
        cmd_plan_new,
        cmd_plan_split,
        cmd_plan_tree,
        cmd_preflight,
        cmd_refresh,
        cmd_related,
        cmd_report,
        cmd_roots,
        cmd_route,
        cmd_semantic,
        cmd_status,
        cmd_symbol,
        cmd_watch,
        cmd_why,
    )
    from .hook import cmd_hook_check
    from .installer import cmd_install, cmd_uninstall

    p = argparse.ArgumentParser(
        prog="mimry",
        description=(
            "Local repo intelligence memory. Defaults --root to the current working directory."
        ),
    )
    p.add_argument("--version", action="version", version=f"mimry {__version__}")
    p.add_argument(
        "--root",
        default=".",
        help="Repo/folder to operate on (default: current working directory)",
    )
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser(
        "init", help="Initialize MIMRY metadata and ignore .mimry/ in Git worktrees"
    )
    s.add_argument("--root-type", default="repo")
    s.add_argument(
        "--skip-graph",
        dest="skip_graph",
        action="store_true",
        help="Create MIMRY metadata without building graph artifacts",
    )
    s.set_defaults(func=cmd_init)
    full_help = "Re-read and re-parse every file instead of reusing unchanged ones"
    for name in ("index", "reindex"):
        s = sub.add_parser(name)
        s.add_argument("--full", action="store_true", help=full_help)
        s.set_defaults(func=cmd_index)
    s = sub.add_parser("refresh", help="Run internal graph build, MIMRY index, then status")
    s.add_argument("--full", action="store_true", help=full_help)
    s.set_defaults(func=cmd_refresh)
    s = sub.add_parser(
        "preflight",
        help="Readiness check and task context; reindexes first when files changed",
    )
    s.add_argument("task", help="Task description to build the context pack around")
    s.add_argument(
        "--force-refresh",
        action="store_true",
        help="Run the full refresh (graph build + MIMRY index) before context generation",
    )
    s.set_defaults(func=cmd_preflight)
    s = sub.add_parser("status")
    s.add_argument(
        "--verify",
        action="store_true",
        help="Re-hash every file instead of trusting unchanged metadata, like git",
    )
    s.set_defaults(func=cmd_status)
    s = sub.add_parser("watch", help="Poll for changes and refresh when they settle")
    s.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default 2s, minimum 0.5s)",
    )
    s.set_defaults(func=cmd_watch)
    s = sub.add_parser("git-hooks", help="Manage git hooks for auto-refresh")
    s.add_argument("action", choices=("install", "uninstall", "status"))
    s.set_defaults(func=cmd_git_hooks)
    s = sub.add_parser("export", help="Export graph in multiple formats")
    s.add_argument(
        "--format",
        required=True,
        choices=["html", "graphml", "cypher", "obsidian"],
        help="Export format",
    )
    s.add_argument(
        "--out",
        help="Output path (default: .mimry/mimry-out/export/<format>)",
    )
    s.add_argument(
        "--force",
        action="store_true",
        help="Write into a non-empty --out directory MIMRY did not create; nothing is deleted",
    )
    s.add_argument(
        "--open",
        action="store_true",
        help="Open HTML file in browser (HTML format only)",
    )
    s.set_defaults(func=cmd_export)
    s = sub.add_parser("adapters", help="List built-in and planned MIMRY adapter plugins")
    s.add_argument("--active-only", action="store_true")
    s.set_defaults(func=cmd_adapters)
    s = sub.add_parser("find")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument(
        "--semantic",
        action="store_true",
        help="Blend local-only semantic chunks into normal FTS/graph/feedback ranking",
    )
    s.set_defaults(func=cmd_find)
    s = sub.add_parser(
        "semantic",
        help="Local-only semantic search over bounded path/symbol/adapter/content-hint chunks",
    )
    s.add_argument("query", nargs="?", help='Query text, or "index"/"status"')
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_semantic)
    s = sub.add_parser("related")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_related)
    s = sub.add_parser(
        "route",
        help="Recommend an agent role, context packs, risks, files, and verification for a task",
    )
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument(
        "--json", action="store_true", help="Print the structured route payload as JSON"
    )
    s.set_defaults(func=cmd_route)
    s = sub.add_parser("brief", help="Write a role-aware markdown agent brief for a task")
    s.add_argument("query")
    s.add_argument(
        "--agent",
        required=True,
        help="Agent role: backend/frontend/mobile/reviewer/qa/docs/tooly/general",
    )
    s.add_argument("--limit", type=int, default=8)
    s.set_defaults(func=cmd_brief)
    s = sub.add_parser("symbol")
    s.add_argument("name")
    s.set_defaults(func=cmd_symbol)
    s = sub.add_parser("context")
    s.add_argument("query")
    s.add_argument(
        "--semantic",
        action="store_true",
        help="Blend local-only semantic chunks into context file selection",
    )
    s.set_defaults(func=cmd_context)
    f = sub.add_parser(
        "feedback", help="Record or inspect local agent usage feedback for this root"
    )
    f.add_argument("feedback_action", nargs="?", choices=("list", "show", "stats"))
    f.add_argument("feedback_id", nargs="?", help="Feedback ID for `mimry feedback show <id>`")
    f.add_argument("--query", help="Task query the agent worked on")
    f.add_argument(
        "--context",
        help="Context pack path used for suggestions, usually .mimry/mimry-out/context/latest.md",
    )
    f.add_argument(
        "--suggested",
        help="Comma-separated suggested files; defaults to parsing --context when available",
    )
    f.add_argument("--opened", help="Comma-separated files opened/inspected")
    f.add_argument("--changed", help="Comma-separated files changed")
    f.add_argument("--missed", help="Comma-separated important files MIMRY missed")
    f.add_argument("--ignored", help="Comma-separated suggested files ignored as not useful")
    f.add_argument(
        "--verification", help="Short verification summary, e.g. 'uv run pytest -q passed'"
    )
    f.add_argument(
        "--outcome",
        choices=("passed", "failed", "blocked", "partial", "unknown"),
        default="unknown",
    )
    f.add_argument("--notes", help="Optional bounded note; no source contents or secrets")
    f.add_argument("--json", help="Read feedback payload JSON from a file")
    f.add_argument("--limit", type=int, default=10, help="Limit for `mimry feedback list`")
    f.set_defaults(func=cmd_feedback)
    s = sub.add_parser(
        "explain",
        help="Explain top files, symbols, relationship evidence, and verification for a task",
    )
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_explain)
    s = sub.add_parser(
        "path", help="Find a graph relationship path between two files/symbols/queries"
    )
    s.add_argument("source")
    s.add_argument("target")
    s.set_defaults(func=cmd_path)
    s = sub.add_parser("why", help="Explain why a file or symbol ranked for a task query")
    s.add_argument("surface")
    s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=25)
    s.set_defaults(func=cmd_why)
    s = sub.add_parser("report", help="Print the current MIMRY graph report")
    s.set_defaults(func=cmd_report)
    s = sub.add_parser(
        "digest",
        help=(
            "Print the canonical semantic digest of the active index (reproducible across roots"
            " and platforms)"
        ),
    )
    s.add_argument(
        "--json",
        action="store_true",
        help="Print the normalized canonical state instead of its hash",
    )
    s.set_defaults(func=cmd_digest)
    plan = sub.add_parser("plan", help="Store and render deterministic recursive plan trees")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    s = plan_sub.add_parser("new", help="Create a repo-scoped plan tree")
    s.add_argument("root_plan", help="Root plan text")
    s.add_argument("--name", help="Stable human-readable slug (normalized deterministically)")
    s.set_defaults(func=cmd_plan_new)
    s = plan_sub.add_parser("split", help="Split a leaf into ordered child subplans")
    s.add_argument("plan_id")
    s.add_argument("node_id")
    s.add_argument(
        "--child",
        action="append",
        required=True,
        help="Child subplan; repeat to define sibling order",
    )
    s.set_defaults(func=cmd_plan_split)
    s = plan_sub.add_parser("tree", help="Render a plan tree")
    s.add_argument("plan_id")
    projection = s.add_mutually_exclusive_group()
    projection.add_argument("--json", action="store_true", help="Print canonical JSON")
    projection.add_argument("--md", action="store_true", help="Print Markdown")
    s.set_defaults(func=cmd_plan_tree)
    s = plan_sub.add_parser("check", help="Validate plan structure without changing it")
    s.add_argument("plan_id")
    s.set_defaults(func=cmd_plan_check)
    s = plan_sub.add_parser("digest", help="Print the plan's canonical semantic SHA-256")
    s.add_argument("plan_id")
    s.set_defaults(func=cmd_plan_digest)
    plan_sub.add_parser("list", help="List local plan trees deterministically").set_defaults(
        func=cmd_plan_list
    )
    s = sub.add_parser("roots", help="List the folders MIMRY has indexed on this machine")
    s.add_argument(
        "--prune", action="store_true", help="Forget registered folders that no longer exist"
    )
    s.set_defaults(func=cmd_roots)
    i = sub.add_parser(
        "install",
        help="Install MIMRY as an agent skill for Claude Code, Codex, Hermes, or Agent Skills",
    )
    i.add_argument("--platform", help="Target platform: claude-code, codex, hermes, agents")
    i.add_argument(
        "--project",
        action="store_true",
        help="Install into the current project instead of the user profile",
    )
    i.add_argument(
        "--dry-run", action="store_true", help="Show the destination without writing files"
    )
    i.add_argument(
        "--always-on",
        action="store_true",
        help="With --project, also install project always-on instructions",
    )
    i.add_argument(
        "--hooks",
        action="store_true",
        help="With --project, also install supported PreToolUse hooks",
    )
    i.add_argument(
        "--status", action="store_true", help="Check install health for the target platform/scope"
    )
    i.add_argument(
        "--list-platforms",
        action="store_true",
        help="List supported install platforms and destinations",
    )
    i.add_argument(
        "--no-mcp",
        action="store_true",
        help="Do not register the MIMRY MCP server with the agent",
    )
    i.set_defaults(func=cmd_install)
    u = sub.add_parser("uninstall", help="Remove a MIMRY agent skill install")
    u.add_argument(
        "--platform", required=True, help="Target platform: claude-code, codex, hermes, agents"
    )
    u.add_argument(
        "--project",
        action="store_true",
        help="Remove from the current project instead of the user profile",
    )
    u.add_argument(
        "--always-on",
        action="store_true",
        help="With --project, also remove project always-on instructions",
    )
    u.add_argument(
        "--hooks",
        action="store_true",
        help="With --project, also remove supported PreToolUse hooks",
    )
    u.add_argument(
        "--no-mcp",
        action="store_true",
        help="Leave the MCP server registration in place",
    )
    u.set_defaults(func=cmd_uninstall)
    sub.add_parser("hook-check", help="Internal PreToolUse hook helper").set_defaults(
        func=cmd_hook_check
    )
    # The same server as `mimry-mcp`, reachable as `uvx mimry mcp`:
    # package runners such as uvx only launch the script named after the
    # package.
    sub.add_parser("mcp", help="Run the MIMRY MCP stdio server (same as mimry-mcp)").set_defaults(
        func=_cmd_mcp
    )
    cache = sub.add_parser("cache")
    cs = cache.add_subparsers(required=True)
    w = cs.add_parser("wipe")
    w.add_argument("--all", action="store_true")
    w.add_argument("--current", action="store_true")
    w.set_defaults(func=cmd_cache_wipe)
    for name, command in sub.choices.items():
        if name not in {"hook-check", "plan", "cache", "mcp"}:
            command.add_argument(
                "-v",
                "--verbose",
                action="store_true",
                help="Also show paths, scores and internal details",
            )
    return p


def _cmd_mcp(a) -> int:
    from .mcp_server import main as run_mcp_server

    # Tools default to the server's working directory, so --root picks
    # the repo.
    os.chdir(a.root)
    run_mcp_server([])
    return 0


def _configure_console() -> None:
    # Human output must not crash on strict Windows code pages.
    # Structured artifacts keep their explicit UTF-8 writers and remain
    # untouched.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def _hook_check_root(args) -> str | None:
    """Root if argv is exactly ``[--root R] hook-check``, else None."""
    rest = list(args)
    root = "."
    if len(rest) >= 2 and rest[0] == "--root":
        root, rest = rest[1], rest[2:]
    elif rest and rest[0].startswith("--root="):
        root, rest = rest[0].split("=", 1)[1], rest[1:]
    return root if rest == ["hook-check"] else None


def main(argv=None):
    hook_root = _hook_check_root(sys.argv[1:] if argv is None else argv)
    if hook_root is not None:
        from types import SimpleNamespace

        from .hook import cmd_hook_check

        return cmd_hook_check(SimpleNamespace(root=hook_root))
    from .plan import PlanError
    from .state import (
        IndexSchemaMigrationError,
        StateCorruptionError,
        StateLockTimeoutError,
        UnsupportedIndexSchemaError,
    )
    from .storage import RootIdentityError

    _configure_console()
    a = build_parser().parse_args(argv)
    from . import ui

    try:
        return a.func(a)
    except UnsupportedIndexSchemaError as exc:
        ui.fail("This index was built by a newer version of MIMRY", str(exc))
    except IndexSchemaMigrationError as exc:
        ui.fail("This index was built by an older version of MIMRY", str(exc))
    except StateCorruptionError as exc:
        ui.fail("MIMRY found damaged index data", str(exc))
    except StateLockTimeoutError as exc:
        ui.fail("Another MIMRY command is still using this index", str(exc))
    except RootIdentityError as exc:
        ui.fail("This .mimry folder belongs to a different location", str(exc))
    except PlanError as exc:
        ui.fail("Plan error", str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
