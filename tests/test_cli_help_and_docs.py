"""Help text, README tool counts and the Windows alias listing."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from mimry import installer, mcp_tools  # noqa: F401  registers the MCP tools
from mimry.cli import build_parser, main
from mimry.mcp_protocol import registered_names
from mimry.mcp_server import CORE_TOOLS

ROOT = Path(__file__).resolve().parents[1]


def _subparser(name: str) -> argparse.ArgumentParser:
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise AssertionError(name)


def _option(command: str, dest: str) -> argparse.Action:
    for action in _subparser(command)._actions:
        if action.dest == dest:
            return action
    raise AssertionError(f"{command} has no {dest}")


@pytest.mark.parametrize(
    ("command", "dest"),
    [
        ("why", "query"),
        ("why", "limit"),
        ("route", "limit"),
        ("brief", "limit"),
        ("explain", "limit"),
        ("semantic", "limit"),
        ("init", "root_type"),
        ("history", "query"),
    ],
)
def test_option_has_help_text(command, dest):
    assert _option(command, dest).help


def test_history_has_a_description():
    assert _subparser("history").description


def test_update_help_prints_the_name_it_was_run_as(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["update", "--help"])

    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith("usage: mimry update ")


def test_adapters_active_only_is_accepted_but_not_listed(capsys):
    assert build_parser().parse_args(["adapters", "--active-only"]).active_only is True
    assert _option("adapters", "active_only").help == argparse.SUPPRESS

    with pytest.raises(SystemExit):
        main(["adapters", "--help"])
    assert "--active-only" not in capsys.readouterr().out


def test_windows_alias_is_accepted_everywhere_and_listed_only_on_windows():
    cfg = installer.platforms()["claude-code"]

    assert installer.canonical_platform("windows") == "claude-code"
    assert "windows" not in installer._listed_aliases(cfg, windows=False)
    assert "claude" in installer._listed_aliases(cfg, windows=False)
    assert "windows" in installer._listed_aliases(cfg, windows=True)


def test_readme_tool_counts_match_the_mcp_registry():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    core = re.search(r"lists (\d+) core tools", text)
    everything = re.search(r"list all (\d+) tools", text)

    assert core and everything, "README no longer states the MCP tool counts"
    assert int(core.group(1)) == len(CORE_TOOLS)
    assert int(everything.group(1)) == len(registered_names())
    for name in CORE_TOOLS:
        assert f"`{name}`" in text
