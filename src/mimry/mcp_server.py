"""MIMRY MCP server over stdio.

The handshake is answered at once. The tool code imports in a
background thread, and tools/list and tools/call wait for it, so an
agent connecting on a cold start gets its first answer without paying
for the whole stack.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import threading

from mimry import __version__
from mimry.mcp_protocol import serve

# Sent to the agent when it connects, so it knows when to reach for
# MIMRY instead of grep without reading the skill first.
INSTRUCTIONS = (
    "MIMRY is a local index of this repository: files, symbols and how they connect."
    " Start a repository task with mimry_preflight and the task in your own words, then read"
    " the context pack it names before opening files; it ranks the files the task likely"
    " touches and says why. Use mimry_find for a focused search and mimry_symbol for a"
    " function or class by name. For an exact string you already know, use rg or git grep"
    " instead. Results are navigation, not proof: check the source before relying on them."
)


def _start_tool_import():
    """Import mimry.mcp_tools in the background; return a waiter."""
    outcome: dict[str, BaseException] = {}

    def _import() -> None:
        # BaseException, so a SystemExit during import is reported too
        # instead of leaving tools/list silently empty.
        try:
            importlib.import_module("mimry.mcp_tools")
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=_import, name="mimry-tool-import", daemon=True)
    thread.start()

    def load_tools() -> None:
        thread.join()
        if "error" in outcome:
            exc = outcome["error"]
            raise RuntimeError(f"could not load MIMRY tools: {type(exc).__name__}: {exc}") from exc

    return load_tools


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the MIMRY MCP stdio server.")
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    load_tools = _start_tool_import()
    info = {"name": "MIMRY", "version": __version__, "instructions": INSTRUCTIONS}
    serve(info, load_tools)


if __name__ == "__main__":
    main()
