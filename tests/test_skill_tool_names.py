"""The skill and its MCP reference name only default tools.

A tool outside the default set must say it needs MIMRY_MCP_TOOLS=all on
the same line, so an agent never calls a tool that is not there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mimry.installer import _REFERENCES, platforms, skill_body
from mimry.mcp_server import CORE_TOOLS

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SKILL = ROOT / "plugins" / "mimry" / "skills" / "mimry"
CAVEAT = "MIMRY_MCP_TOOLS=all"
# A tool name, not a file such as `.mimry_version` or `mimry-out`.
TOOL_NAME = re.compile(r"(?<![\w.\-/])mimry_[a-z_]+")


def _unlisted_tool_lines(text: str) -> list[str]:
    """Lines that name a non-default tool without the caveat."""
    bad = []
    for line in text.splitlines():
        names = TOOL_NAME.findall(line)
        if any(name not in CORE_TOOLS for name in names) and CAVEAT not in line:
            bad.append(line.strip())
    return bad


def test_the_default_tool_set_includes_impact():
    assert "mimry_impact" in CORE_TOOLS


@pytest.mark.parametrize("key", sorted(platforms()))
def test_the_installed_skill_names_only_default_tools(key):
    assert _unlisted_tool_lines(skill_body(key)) == []


def test_the_committed_plugin_skill_names_only_default_tools():
    text = (PLUGIN_SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert _unlisted_tool_lines(text) == []


def test_the_mcp_reference_names_only_default_tools():
    assert _unlisted_tool_lines(_REFERENCES["mcp.md"]) == []


def test_the_committed_plugin_mcp_reference_names_only_default_tools():
    text = (PLUGIN_SKILL / "references" / "mcp.md").read_text(encoding="utf-8")

    assert _unlisted_tool_lines(text) == []
