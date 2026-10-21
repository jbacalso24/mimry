"""The PreToolUse hook for MIMRY's own MCP query tools.

An MCP call never reaches the Bash path, so the hook logs its query with
the session, and record_query tags the query with it. The hook prints
nothing for these calls.
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry import installer
from mimry.constants import MCP_QUERY_NAME
from mimry.hook import cmd_hook_check, mimry_queries
from mimry.session import HOOK_COMMAND_LOG, record_query

SESSION = "mcp-session-under-test"
QUERY = "login session timeout"
MATCHER = installer.hook_table("claude-code")["PreToolUse"][0][0]
FIND_NAMES = ("mcp__mimry__mimry_find", "mcp__plugin_mimry_mimry__mimry_find")


def _repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path)
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0
    return repo


def _feed_raw(monkeypatch, raw: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))


def _feed(monkeypatch, payload: dict) -> None:
    _feed_raw(monkeypatch, json.dumps(payload).encode("utf-8"))


def _hook(monkeypatch, repo: Path, payload: dict) -> int:
    _feed(monkeypatch, payload)
    return cmd_hook_check(SimpleNamespace(root=str(repo)))


def _command_rows(repo: Path) -> list[dict]:
    path = repo / ".mimry" / HOOK_COMMAND_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _query_rows(repo: Path) -> list[dict]:
    path = repo / ".mimry" / "session-queries.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("name", FIND_NAMES)
def test_mcp_find_is_logged_with_its_session_and_prints_nothing(
    tmp_path, monkeypatch, capsys, name
):
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()

    assert (
        _hook(
            monkeypatch,
            repo,
            {"session_id": SESSION, "tool_name": name, "tool_input": {"query": QUERY}},
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert [row["command"] for row in _command_rows(repo)] == [QUERY]
    assert _command_rows(repo)[0]["session"] == SESSION

    # The find the MCP tool then runs records the same text, tagged.
    record_query(repo, QUERY, [])
    assert _query_rows(repo)[-1]["session"] == SESSION


@pytest.mark.parametrize(
    ("name", "tool_input"),
    [
        pytest.param("mcp__mimry__mimry_symbol", {"name": "auth"}, id="no-query-argument"),
        pytest.param("mcp__mimry__mimry_find", {}, id="empty-input"),
        pytest.param("mcp__mimry__mimry_find", "find auth", id="string-input"),
        pytest.param("mcp__mimry__mimry_find", ["auth"], id="list-input"),
        pytest.param("mcp__mimry__mimry_find", {"query": ""}, id="blank-query"),
        pytest.param("mcp__mimry__mimry_find", {"query": "   "}, id="spaces-query"),
        pytest.param("mcp__mimry__mimry_find", {"query": 7}, id="numeric-query"),
        pytest.param("mcp__other__find", {"query": QUERY}, id="other-server"),
    ],
)
def test_mcp_call_without_a_query_prints_and_logs_nothing(
    tmp_path, monkeypatch, capsys, name, tool_input
):
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()

    code = _hook(
        monkeypatch, repo, {"session_id": SESSION, "tool_name": name, "tool_input": tool_input}
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    assert _command_rows(repo) == []


def test_malformed_payload_prints_nothing_and_exits_zero(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    _feed_raw(monkeypatch, b'{"tool_name": "mcp__mimry__mimry_find", "tool_input": {')

    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0

    assert capsys.readouterr().out == ""
    assert _command_rows(repo) == []


def test_pre_tool_use_matcher_covers_both_name_forms_and_the_search_tools():
    for name in (
        "Bash",
        "Glob",
        "Grep",
        "Read",
        "mcp__mimry__mimry_find",
        "mcp__plugin_mimry_mimry__mimry_find",
        "mcp__plugin_mimry_mimry__mimry_preflight",
        "mcp__mimry__mimry_related",
        "mcp__mimry__mimry_route",
        "mcp__mimry__mimry_explain",
    ):
        assert re.search(MATCHER, name), name


def test_pre_tool_use_matcher_leaves_out_other_tools():
    for name in (
        "Edit",
        "mcp__other__find",
        "mcp__other_mimry__mimry_find",
        "mcp__mimry__mimry_symbol",
        "mcp__mimry__mimry_find_more",
        "mcp__mimry__mimry_feedback",
    ):
        assert not re.search(MATCHER, name), name


def test_mcp_query_name_is_the_one_the_matcher_uses():
    assert MATCHER == f"^(?:Bash|Glob|Grep|Read|{MCP_QUERY_NAME})$"
    assert installer.hook_table("codex")["PreToolUse"] == [("Bash", "hook-check", False)]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('mimry find "login session timeout"', ["login session timeout"]),
        ('cd repo && uv run mimry preflight "fix login" --json', ["fix login"]),
        ("C:\\Tools\\mimry.exe context fetch_user", ["fetch_user"]),
        ('mimry find "a" | head', ["a"]),
        ("mimry feedback --query fetch_user", []),
        ("cat fetch_user.txt", []),
    ],
)
def test_mimry_queries_reads_the_query_argument_of_each_mimry_command(command, expected):
    assert mimry_queries(command) == expected
