from __future__ import annotations

import argparse
import sys

from .commands import (
    cmd_cache_wipe,
    cmd_context,
    cmd_explain,
    cmd_feedback,
    cmd_find,
    cmd_index,
    cmd_adapters,
    cmd_init,
    cmd_path,
    cmd_preflight,
    cmd_related,
    cmd_refresh,
    cmd_roots,
    cmd_semantic,
    cmd_status,
    cmd_symbol,
    cmd_why,
)
from .graphify_wrapper import cmd_graphify
from .installer import cmd_hook_check, cmd_install, cmd_uninstall


def build_parser():
    p = argparse.ArgumentParser(
        prog="mimry", description="Local repo intelligence memory. Defaults --root to the current working directory."
    )
    p.add_argument("--root", default=".", help="Repo/folder to operate on (default: current working directory)")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("init", help="Initialize MIMRY metadata and ignore .mimry/ in Git worktrees")
    s.add_argument("--root-type", default="repo")
    s.add_argument(
        "--skip-graphify", action="store_true", help="Create MIMRY metadata without building graph artifacts"
    )
    s.set_defaults(func=cmd_init)
    sub.add_parser("index").set_defaults(func=cmd_index)
    sub.add_parser("reindex").set_defaults(func=cmd_index)
    sub.add_parser("refresh", help="Run internal graph build, MIMRY index, then status").set_defaults(func=cmd_refresh)
    s = sub.add_parser("preflight", help="Fast readiness check and task context generation")
    s.add_argument("task", help="Task description to build the context pack around")
    s.add_argument(
        "--force-refresh",
        action="store_true",
        help="Run the slow full refresh path (Graphify build + MIMRY index) before context generation",
    )
    s.set_defaults(func=cmd_preflight)
    sub.add_parser("status").set_defaults(func=cmd_status)
    s = sub.add_parser("adapters", help="List built-in and planned MIMRY adapter plugins")
    s.add_argument("--active-only", action="store_true")
    s.set_defaults(func=cmd_adapters)
    s = sub.add_parser("find")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument(
        "--semantic",
        action="store_true",
        help="Blend local-only semantic chunks into normal FTS/Graphify/feedback ranking",
    )
    s.set_defaults(func=cmd_find)
    s = sub.add_parser(
        "semantic", help="Local-only semantic search over bounded path/symbol/adapter/content-hint chunks"
    )
    s.add_argument("query", nargs="?", help='Query text, or "index"/"status"')
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_semantic)
    s = sub.add_parser("related")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_related)
    s = sub.add_parser("symbol")
    s.add_argument("name")
    s.set_defaults(func=cmd_symbol)
    s = sub.add_parser("context")
    s.add_argument("query")
    s.add_argument(
        "--semantic", action="store_true", help="Blend local-only semantic chunks into context file selection"
    )
    s.set_defaults(func=cmd_context)
    f = sub.add_parser("feedback", help="Record or inspect local agent usage feedback for this root")
    f.add_argument("feedback_action", nargs="?", choices=("list", "show", "stats"))
    f.add_argument("feedback_id", nargs="?", help="Feedback ID for `mimry feedback show <id>`")
    f.add_argument("--query", help="Task query the agent worked on")
    f.add_argument(
        "--context", help="Context pack path used for suggestions, usually .mimry/mimry-out/context/latest.md"
    )
    f.add_argument("--suggested", help="Comma-separated suggested files; defaults to parsing --context when available")
    f.add_argument("--opened", help="Comma-separated files opened/inspected")
    f.add_argument("--changed", help="Comma-separated files changed")
    f.add_argument("--missed", help="Comma-separated important files MIMRY missed")
    f.add_argument("--ignored", help="Comma-separated suggested files ignored as not useful")
    f.add_argument("--verification", help="Short verification summary, e.g. 'uv run pytest -q passed'")
    f.add_argument("--outcome", choices=("passed", "failed", "blocked", "partial", "unknown"), default="unknown")
    f.add_argument("--notes", help="Optional bounded note; no source contents or secrets")
    f.add_argument("--json", help="Read feedback payload JSON from a file")
    f.add_argument("--limit", type=int, default=10, help="Limit for `mimry feedback list`")
    f.set_defaults(func=cmd_feedback)
    s = sub.add_parser("explain", help="Explain top files, symbols, relationship evidence, and verification for a task")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_explain)
    s = sub.add_parser("path", help="Find a Graphify relationship path between two files/symbols/queries")
    s.add_argument("source")
    s.add_argument("target")
    s.set_defaults(func=cmd_path)
    s = sub.add_parser("why", help="Explain why a file or symbol ranked for a task query")
    s.add_argument("surface")
    s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=25)
    s.set_defaults(func=cmd_why)
    sub.add_parser("roots").set_defaults(func=cmd_roots)
    i = sub.add_parser(
        "install", help="Install MIMRY as an agent skill for Claude Code, Codex, Hermes, or Agent Skills"
    )
    i.add_argument("--platform", help="Target platform: claude-code, codex, hermes, agents")
    i.add_argument(
        "--project", action="store_true", help="Install into the current project instead of the user profile"
    )
    i.add_argument("--dry-run", action="store_true", help="Show the destination without writing files")
    i.add_argument(
        "--always-on", action="store_true", help="With --project, also install project always-on instructions"
    )
    i.add_argument("--hooks", action="store_true", help="With --project, also install supported PreToolUse hooks")
    i.add_argument("--status", action="store_true", help="Check install health for the target platform/scope")
    i.add_argument("--list-platforms", action="store_true", help="List supported install platforms and destinations")
    i.set_defaults(func=cmd_install)
    u = sub.add_parser("uninstall", help="Remove a MIMRY agent skill install")
    u.add_argument("--platform", required=True, help="Target platform: claude-code, codex, hermes, agents")
    u.add_argument("--project", action="store_true", help="Remove from the current project instead of the user profile")
    u.add_argument(
        "--always-on", action="store_true", help="With --project, also remove project always-on instructions"
    )
    u.add_argument("--hooks", action="store_true", help="With --project, also remove supported PreToolUse hooks")
    u.set_defaults(func=cmd_uninstall)
    sub.add_parser("hook-check", help="Internal PreToolUse hook helper").set_defaults(func=cmd_hook_check)
    cache = sub.add_parser("cache")
    cs = cache.add_subparsers(required=True)
    w = cs.add_parser("wipe")
    w.add_argument("--all", action="store_true")
    w.add_argument("--current", action="store_true")
    w.set_defaults(func=cmd_cache_wipe)
    g = sub.add_parser("graphify", help="Safe MIMRY-owned wrapper around pinned Graphify")
    gs = g.add_subparsers(dest="graphify_command", required=True)
    gs.add_parser("status").set_defaults(func=cmd_graphify)
    gb = gs.add_parser("build")
    gb.add_argument("--dry-run", action="store_true", help="Show the safe Graphify command without running it")
    gb.add_argument(
        "--execute", action="store_true", help="Run the safe local graph build with output under the MIMRY cache"
    )
    gb.set_defaults(func=cmd_graphify)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
