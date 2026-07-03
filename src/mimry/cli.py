from __future__ import annotations

import argparse
import sys

from .commands import (
    cmd_cache_wipe,
    cmd_context,
    cmd_find,
    cmd_index,
    cmd_adapters,
    cmd_init,
    cmd_related,
    cmd_roots,
    cmd_status,
    cmd_symbol,
)
from .graphify_wrapper import cmd_graphify


def build_parser():
    p = argparse.ArgumentParser(
        prog="mimry", description="Local repo intelligence memory. Defaults --root to the current working directory."
    )
    p.add_argument("--root", default=".", help="Repo/folder to operate on (default: current working directory)")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("init")
    s.add_argument("--root-type", default="repo")
    s.add_argument("--skip-graphify", action="store_true", help="Create MIMRY metadata without bootstrapping Graphify")
    s.set_defaults(func=cmd_init)
    sub.add_parser("index").set_defaults(func=cmd_index)
    sub.add_parser("reindex").set_defaults(func=cmd_index)
    sub.add_parser("status").set_defaults(func=cmd_status)
    s = sub.add_parser("adapters", help="List built-in and planned MIMRY adapter plugins")
    s.add_argument("--active-only", action="store_true")
    s.set_defaults(func=cmd_adapters)
    s = sub.add_parser("find")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_find)
    s = sub.add_parser("related")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_related)
    s = sub.add_parser("symbol")
    s.add_argument("name")
    s.set_defaults(func=cmd_symbol)
    s = sub.add_parser("context")
    s.add_argument("query")
    s.set_defaults(func=cmd_context)
    sub.add_parser("roots").set_defaults(func=cmd_roots)
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
        "--execute", action="store_true", help="Run the safe local Graphify build with output under .mimry/graphify"
    )
    gb.set_defaults(func=cmd_graphify)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
