"""The Claude Code plugin's files, generated from the package.

``.claude-plugin/marketplace.json`` and ``plugins/mimry`` are committed
output. ``scripts/build_plugin.py`` writes ``plugin_files()`` to disk,
and ``tests/test_plugin.py`` fails when a committed file drifts from it.
"""

from __future__ import annotations

import json

from . import __version__
from .installer import _REFERENCES, hook_table, skill_body

NAME = "mimry"
DESCRIPTION = (
    "Finds the files a coding task touches, so your AI agent reads less. Local CLI and MCP server."
)
HOMEPAGE = "https://github.com/jbacalso24/mimry"
PLUGIN_DIR = "plugins/mimry"


def _json(data: object) -> str:
    return json.dumps(data, indent=2) + "\n"


def _uvx(*args: str) -> list[str]:
    # Pinned, so the plugin and the package are always the same version.
    return ["--from", f"mimry=={__version__}", *args]


def _hooks() -> dict:
    hooks = {}
    for event, (matcher, subcommand, background) in hook_table("claude-code").items():
        handler: dict[str, object] = {
            "type": "command",
            "command": "uvx",
            "args": _uvx(NAME, subcommand),
        }
        if background:
            handler["async"] = True
        hooks[event] = [{"matcher": matcher, "hooks": [handler]}]
    return {"hooks": hooks}


def plugin_files() -> dict[str, str]:
    """Every committed plugin file, keyed by its POSIX repo path."""
    files = {
        ".claude-plugin/marketplace.json": _json(
            {
                "name": NAME,
                "description": (
                    "MIMRY: finds the files a coding task touches, so your agent reads less."
                ),
                "owner": {"name": "Judison Bacalso"},
                "plugins": [
                    {"name": NAME, "source": f"./{PLUGIN_DIR}", "description": DESCRIPTION}
                ],
            }
        ),
        f"{PLUGIN_DIR}/.claude-plugin/plugin.json": _json(
            {
                "name": NAME,
                "version": __version__,
                "description": DESCRIPTION,
                "homepage": HOMEPAGE,
                "repository": HOMEPAGE,
                "license": "MIT",
                "author": {"name": "Judison Bacalso", "url": "https://github.com/jbacalso24"},
            }
        ),
        f"{PLUGIN_DIR}/.mcp.json": _json(
            {"mcpServers": {NAME: {"command": "uvx", "args": _uvx("mimry-mcp")}}}
        ),
        f"{PLUGIN_DIR}/hooks/hooks.json": _json(_hooks()),
        f"{PLUGIN_DIR}/skills/mimry/SKILL.md": skill_body("bundled"),
    }
    for name, content in _REFERENCES.items():
        files[f"{PLUGIN_DIR}/skills/mimry/references/{name}"] = content
    return files
