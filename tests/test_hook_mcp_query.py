"""The PreToolUse hook for MIMRY's own MCP query tools.

An MCP call never reaches the Bash path, so the hook logs its query with
the session, and record_query tags the query with it. The hook prints
nothing for these calls.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry import hook as hook_module
from mimry import installer, mcp_tools
from mimry import session as mimry_session
from mimry.constants import MCP_QUERY_NAME
from mimry.hook import cmd_hook_check, mimry_queries
from mimry.session import HOOK_COMMAND_LOG, _query_hash, record_query

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


def _key(repo: Path) -> bytes:
    return (repo / ".mimry" / "session-key").read_bytes()


def _apply_feedback_find(monkeypatch, repo: Path, session_id: str) -> None:
    _hook(
        monkeypatch,
        repo,
        {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "mimry find apply_feedback"},
        },
    )


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
    assert [row["queries"] for row in _command_rows(repo)] == [[_query_hash(QUERY, _key(repo))]]
    assert "command" not in _command_rows(repo)[0]
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
        "PowerShell",
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
    assert MATCHER == f"^(?:Bash|PowerShell|Glob|Grep|Read|{MCP_QUERY_NAME})$"
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


# PowerShell tool calls use the same parser. What differs from POSIX
# shell is the call operator, a quoted exe path, and the separators.
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('mimry find "two words"', ["two words"]),
        ("mimry find 'two words'", ["two words"]),
        ("& mimry.exe find x", ["x"]),
        ("& 'C:\\Tools\\mimry.exe' find \"two words\"", ["two words"]),
        ("mimry find x; other", ["x"]),
        ("mimry find x | Select-Object -First 5", ["x"]),
    ],
)
def test_mimry_queries_reads_powershell_quoting(command, expected):
    assert mimry_queries(command) == expected


def test_mimry_queries_stops_each_query_at_the_next_mimry_word():
    # shlex reads a newline as a space, so these lines form one segment.
    # Each query's arguments end where the next mimry word starts.
    assert mimry_queries("mimry find login flow\n" * 3) == ["login", "flow"] * 3
    assert mimry_queries("mimry find mimry hook") == ["mimry", "hook"]


def test_mimry_queries_stays_linear_on_many_mimry_words_in_one_segment():
    import time

    # CPU time, not wall time: a busy machine is not a complexity bug.
    timings = {}
    for n in (2000, 8000):
        command = "mimry find login flow\n" * n
        started = time.process_time()
        found = mimry_queries(command)
        timings[n] = (len(found), time.process_time() - started)
    (small, _), (large, elapsed) = timings[2000], timings[8000]
    assert large <= 4 * small
    assert elapsed < 2.0, f"8000 mimry words took {elapsed:.1f}s"


def test_the_commands_log_keeps_no_query_text(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    secret_query = "apply_feedback password=tomato-canary"
    _hook(
        monkeypatch,
        repo,
        {
            "session_id": SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": f'mimry find "{secret_query}"'},
        },
    )

    text = (repo / ".mimry" / HOOK_COMMAND_LOG).read_text(encoding="utf-8")
    assert "tomato-canary" not in text
    assert "mimry find" not in text
    record_query(repo, secret_query, [])
    assert _query_rows(repo)[-1]["session"] == SESSION


def test_the_commands_log_holds_neither_the_query_nor_its_plain_hash(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _apply_feedback_find(monkeypatch, repo, SESSION)

    assert _command_rows(repo)
    text = (repo / ".mimry" / HOOK_COMMAND_LOG).read_text(encoding="utf-8")
    assert hashlib.sha256(b"apply_feedback").hexdigest() not in text
    assert "apply_feedback" not in text


def test_each_repo_gets_its_own_key_so_the_same_query_hashes_differently(tmp_path, monkeypatch):
    repos = [_repo(tmp_path / name, monkeypatch) for name in ("one", "two")]
    for repo in repos:
        _apply_feedback_find(monkeypatch, repo, SESSION)

    keys = [_key(repo) for repo in repos]
    assert len(keys[0]) == len(keys[1]) == 32
    assert keys[0] != keys[1]
    assert _query_hash("apply_feedback", keys[0]) != _query_hash("apply_feedback", keys[1])
    assert _command_rows(repos[0])[0]["queries"] != _command_rows(repos[1])[0]["queries"]


def test_two_first_uses_at_once_agree_on_one_key_and_both_tag(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    state = repo / ".mimry"
    (state / "session-key").unlink(missing_ok=True)

    workers = 8
    barrier = threading.Barrier(workers)
    keys: list[bytes | None] = [None] * workers

    def first_use(index: int) -> None:
        barrier.wait()
        keys[index] = mimry_session._session_key(state)

    threads = [threading.Thread(target=first_use, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert keys[0] is not None
    assert all(key == keys[0] for key in keys)
    assert _key(repo) == keys[0]
    assert sorted(path.name for path in state.glob("session-key*")) == ["session-key"]

    for session_id, query in (
        ("first-session", "first query"),
        ("second-session", "second query"),
    ):
        _hook(
            monkeypatch,
            repo,
            {
                "session_id": session_id,
                "tool_name": "Bash",
                "tool_input": {"command": f'mimry find "{query}"'},
            },
        )
        record_query(repo, query, [])
        assert _query_rows(repo)[-1]["session"] == session_id


@pytest.mark.parametrize("case", ["missing", "read-only"])
def test_without_a_writable_mimry_the_log_is_skipped_and_the_tag_is_empty(
    tmp_path, monkeypatch, case
):
    repo = _repo(tmp_path, monkeypatch)
    state = repo / ".mimry"
    if case == "missing":
        shutil.rmtree(state)
    else:
        (state / "session-key").unlink(missing_ok=True)
        real_open = os.open

        def refuse_key(path, flags, *args, **kwargs):
            if "session-key" in os.fspath(path):
                raise PermissionError(13, "read-only folder", os.fspath(path))
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", refuse_key)

    mimry_session.log_hook_command(repo, SESSION, [QUERY])
    assert mimry_session._query_session(state, QUERY) == ""
    record_query(repo, QUERY, [])

    assert _command_rows(repo) == []
    assert not (state / "session-key").exists()


def _no_hard_links(monkeypatch, case: str) -> None:
    if case == "eperm":

        def refuse(src, dst, *args, **kwargs):
            raise PermissionError(errno.EPERM, "hard links are not supported here", os.fspath(src))

        monkeypatch.setattr(os, "link", refuse)
    else:
        monkeypatch.delattr(os, "link")


@pytest.mark.parametrize("case", ["eperm", "no-os-link"])
def test_the_key_is_still_made_when_the_volume_has_no_hard_links(tmp_path, monkeypatch, case):
    state = tmp_path / ".mimry"
    state.mkdir()
    _no_hard_links(monkeypatch, case)

    key = mimry_session._session_key(state)

    assert key is not None and len(key) == 32
    assert (state / "session-key").read_bytes() == key
    assert mimry_session._session_key(state) == key
    assert sorted(path.name for path in state.iterdir()) == ["session-key"]


def test_logging_still_tags_when_the_volume_has_no_hard_links(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    state = repo / ".mimry"
    (state / "session-key").unlink(missing_ok=True)
    _no_hard_links(monkeypatch, "eperm")

    mimry_session.log_hook_command(repo, SESSION, [QUERY])

    assert [row["queries"] for row in _command_rows(repo)] == [[_query_hash(QUERY, _key(repo))]]
    assert _command_rows(repo)[0]["session"] == SESSION
    assert mimry_session._query_session(state, QUERY) == SESSION


def _key_read_refused(monkeypatch, state: Path, refusals: int | None) -> None:
    """Make reads of the key raise PermissionError, as a rename does.

    Only the first ``refusals`` reads fail; None fails every read.
    """
    key_path = state / "session-key"
    real_read_bytes = Path.read_bytes
    reads = {"count": 0}

    def read_bytes(self):
        if self == key_path:
            reads["count"] += 1
            if refusals is None or reads["count"] <= refusals:
                raise PermissionError(errno.EACCES, "the key is being renamed", os.fspath(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_a_key_read_that_races_a_peers_rename_is_retried(tmp_path, monkeypatch):
    state = tmp_path / ".mimry"
    state.mkdir()
    key = os.urandom(32)
    (state / "session-key").write_bytes(key)
    _key_read_refused(monkeypatch, state, refusals=2)

    assert mimry_session._session_key(state) == key
    assert sorted(path.name for path in state.iterdir()) == ["session-key"]


def test_a_key_read_refused_every_time_gives_none_and_leaves_no_part_file(tmp_path, monkeypatch):
    state = tmp_path / ".mimry"
    state.mkdir()
    (state / "session-key").write_bytes(os.urandom(32))
    _key_read_refused(monkeypatch, state, refusals=None)

    assert mimry_session._session_key(state) is None
    assert sorted(path.name for path in state.iterdir()) == ["session-key"]


@pytest.mark.parametrize(
    "name", ["mcp__mimry__mimry_find", "mcp__mimry__mimry_preflight", "mcp__mimry__mimry_context"]
)
def test_mcp_secret_label_query_is_tagged_and_stored_redacted(tmp_path, monkeypatch, name):
    repo = _repo(tmp_path, monkeypatch)
    secret = "secret: rotate"
    assert (
        _hook(
            monkeypatch,
            repo,
            {"session_id": SESSION, "tool_name": name, "tool_input": {"query": secret}},
        )
        == 0
    )

    tool = getattr(mcp_tools, name.removeprefix("mcp__mimry__"))
    assert "error" not in tool(secret, str(repo))

    row = _query_rows(repo)[-1]
    assert row["session"] == SESSION
    assert row["query"] == "secret: [REDACTED]"


def test_cli_find_with_a_secret_label_query_is_tagged_and_stored_redacted(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    secret = "secret: rotate"
    _hook(
        monkeypatch,
        repo,
        {
            "session_id": SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": f'mimry find "{secret}"'},
        },
    )

    res = run_cli(repo, tmp_path / "cache", "find", secret)

    # Exit 1 is "no match": the query is still recorded.
    assert res.returncode in (0, 1), res.stderr
    row = _query_rows(repo)[-1]
    assert row["session"] == SESSION
    assert row["query"] == "secret: [REDACTED]"


def test_powershell_find_is_logged_and_its_later_find_is_tagged(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()

    assert (
        _hook(
            monkeypatch,
            repo,
            {
                "session_id": SESSION,
                "tool_name": "PowerShell",
                "tool_input": {"command": 'mimry find "fix login"'},
            },
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert [row["queries"] for row in _command_rows(repo)] == [
        [_query_hash("fix login", _key(repo))]
    ]
    assert _command_rows(repo)[0]["session"] == SESSION

    res = run_cli(repo, tmp_path / "cache", "find", "fix login")
    assert res.returncode == 0, res.stderr
    assert _query_rows(repo)[-1]["session"] == SESSION


@pytest.mark.parametrize(
    "command",
    [
        "rg -n fetch_user .",
        "Select-String -Path src\\*.py -Pattern fetch_user",
        "Get-Content fetch_user.txt",
    ],
)
def test_powershell_call_without_mimry_prints_and_logs_nothing(
    tmp_path, monkeypatch, capsys, command
):
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    # The search answer only runs once a context pack exists.
    context = repo / ".mimry" / "mimry-out" / "context"
    context.mkdir(parents=True, exist_ok=True)
    (context / "latest.md").write_text("", encoding="utf-8")

    # The nudge parses POSIX shell: PowerShell calls must not reach it.
    # cmd_hook_check swallows errors, so record the calls instead.
    reached: list[str] = []

    def record(name):
        def run(*args, **kwargs):
            reached.append(name)
            return None

        return run

    monkeypatch.setattr(hook_module, "_symbol_answer", record("_symbol_answer"))
    monkeypatch.setattr(hook_module, "broad_search_query", record("broad_search_query"))
    code = _hook(
        monkeypatch,
        repo,
        {"session_id": SESSION, "tool_name": "PowerShell", "tool_input": {"command": command}},
    )

    assert code == 0
    assert reached == []
    assert capsys.readouterr().out == ""
    assert _command_rows(repo) == []
