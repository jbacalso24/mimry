"""Searches through MIMRY and with grep, counted by the hook (#658).

Only the hook sees every search, so the hook counts them. A Grep or Glob
call, or a shell grep, rg or git grep over the tree, counts as grep. A
mimry query in the shell, or a MIMRY MCP query tool, counts as mimry.
A filter on a pipe, a Read and any other shell command do not count.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from test_session_feedback import QUERY, SESSION, _cli, _feed, _find, _repo

from mimry import session
from mimry.hook import cmd_hook_check
from mimry.session import SEARCH_LOG

OTHER = "other-session"


def _hook(monkeypatch, repo: Path, tool_name: str, tool_input: dict, session_id: str = SESSION):
    _feed(
        monkeypatch,
        {"session_id": session_id, "tool_name": tool_name, "tool_input": tool_input},
    )
    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0


def _bash(monkeypatch, repo: Path, command: str, session_id: str = SESSION) -> None:
    _hook(monkeypatch, repo, "Bash", {"command": command}, session_id)


def _rows(repo: Path) -> list[dict]:
    path = repo / ".mimry" / SEARCH_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _kinds(repo: Path) -> list[tuple[str, str]]:
    return [(row["session"], row["kind"]) for row in _rows(repo)]


def _stats(repo: Path, *args: str) -> str:
    res = _cli(repo, "stats", *args)
    assert res.returncode == 0, res.stderr
    return res.stdout


def test_grep_and_glob_tools_count_as_grep(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _hook(monkeypatch, repo, "Grep", {"pattern": "def login"})
    _hook(monkeypatch, repo, "Glob", {"pattern": "**/*.py"})

    assert _kinds(repo) == [(SESSION, "grep"), (SESSION, "grep")]


def test_mimry_in_bash_counts_as_mimry(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo, f'mimry find "{QUERY}"')

    assert _kinds(repo) == [(SESSION, "mimry")]


def test_mimry_mcp_query_counts_as_mimry(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _hook(monkeypatch, repo, "mcp__mimry__mimry_find", {"query": QUERY})

    assert _kinds(repo) == [(SESSION, "mimry")]


def test_shell_grep_over_the_tree_counts_as_grep(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo, 'rg "def login"')
    _bash(monkeypatch, repo, "git grep login")

    assert _kinds(repo) == [(SESSION, "grep"), (SESSION, "grep")]


def test_pipe_filter_read_and_other_shell_calls_count_nothing(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo, "git log | rg foo")
    _bash(monkeypatch, repo, "ls -la")
    shown = _find(repo, QUERY)
    _hook(monkeypatch, repo, "Read", {"file_path": str(repo / shown[0])})

    assert _kinds(repo) == []


def test_powershell_searches_count_like_shell_ones(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    for command in (
        "Get-ChildItem -Recurse | Select-String login",
        "rg -n login -g '*.py' | Select-Object -First 40",
        "Get-Content notes.txt | Select-String login",
    ):
        _hook(monkeypatch, repo, "PowerShell", {"command": command})

    assert _kinds(repo) == [(SESSION, "grep"), (SESSION, "grep")]


def test_stats_prints_the_share_per_session_and_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _hook(monkeypatch, repo, "Grep", {"pattern": "def login"})
    _bash(monkeypatch, repo, f'mimry find "{QUERY}"')
    _hook(monkeypatch, repo, "mcp__mimry__mimry_find", {"query": QUERY})
    _bash(monkeypatch, repo, 'rg "def login"')
    _hook(monkeypatch, repo, "Grep", {"pattern": "login"}, session_id=OTHER)

    out = _stats(repo, "--session", SESSION)
    assert "Searches: 2 through MIMRY, 2 with grep/Glob (50% MIMRY)" in out.splitlines()
    assert "Searches: 2 through MIMRY, 3 with grep/Glob (40% MIMRY)" in out.splitlines()

    report = json.loads(_stats(repo, "--json", "--session", OTHER))
    assert report["session"]["searches"] == {"mimry": 0, "grep": 1, "share": 0.0}
    assert report["all_time"]["searches"] == {"mimry": 2, "grep": 3, "share": 0.4}


def test_no_searches_prints_no_line_and_null_when_untracked(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _find(repo, QUERY)

    report = json.loads(_stats(repo, "--json", "--session", SESSION))
    assert report["tracked"] is False
    assert report["session"]["searches"] is None
    assert report["all_time"]["searches"] is None
    assert "Searches:" not in _stats(repo)


def test_tracked_scope_with_no_search_has_null_share_and_no_line(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo, "ls")
    shown = _find(repo, QUERY)
    _hook(monkeypatch, repo, "Read", {"file_path": str(repo / shown[0])})

    report = json.loads(_stats(repo, "--json", "--session", SESSION))
    assert report["session"]["searches"] == {"mimry": 0, "grep": 0, "share": None}
    assert report["all_time"]["searches"] == {"mimry": 0, "grep": 0, "share": None}
    assert "Searches:" not in _stats(repo, "--session", SESSION)


def test_search_log_stays_bounded(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(session, "SEARCH_LOG_MAX_BYTES", 200)
    monkeypatch.setattr(session, "SEARCH_LINE_LIMIT", 3)
    for i in range(10):
        _hook(monkeypatch, repo, "Grep", {"pattern": f"symbol{i}"})

    rows = _rows(repo)
    assert len(rows) == 3
    assert all(row["kind"] == "grep" for row in rows)


def test_folder_without_mimry_is_left_alone(tmp_path, monkeypatch):
    _feed(
        monkeypatch,
        {"session_id": SESSION, "tool_name": "Grep", "tool_input": {"pattern": "def login"}},
    )
    assert cmd_hook_check(SimpleNamespace(root=str(tmp_path))) == 0
    assert list(tmp_path.iterdir()) == []
