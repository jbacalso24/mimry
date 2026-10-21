"""The prompt hook: `mimry hook-prompt` names the files a task ranks
first, and the Stop hook closes the session's feedback window."""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_mimry_cli import copy_fixture, run_cli

import mimry.search
from mimry import hook
from mimry.core.artifacts import graph_path
from mimry.feedback import list_feedback
from mimry.hook import (
    PROMPT_NOTE_LIMIT,
    PROMPT_QUERY_CHARS,
    _prompt_note,
    cmd_hook_check,
    cmd_hook_prompt,
    cmd_hook_stop,
)
from mimry.session import QUERY_LOG
from mimry.storage import load_pointer

PROMPT = "where does the code create a user session after login"
SESSION = "prompt-session"


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    monkeypatch.delenv("MIMRY_PROMPT_HOOK", raising=False)
    monkeypatch.delenv("MIMRY_SESSION_FEEDBACK", raising=False)
    repo = copy_fixture(tmp_path)
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    return repo


def _feed(monkeypatch, payload: dict | bytes) -> None:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))


def _prompt(monkeypatch, capsys, repo: Path, prompt: str, session: str = SESSION) -> str:
    capsys.readouterr()
    _feed(monkeypatch, {"prompt": prompt, "session_id": session, "cwd": str(repo)})
    assert cmd_hook_prompt(SimpleNamespace(root=str(repo))) == 0
    return capsys.readouterr().out


def _note(out: str) -> str:
    hook_output = json.loads(out)["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    return hook_output["additionalContext"]


def _query_count(repo: Path) -> int:
    path = repo / ".mimry" / QUERY_LOG
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _query_rows(repo: Path) -> list[dict]:
    path = repo / ".mimry" / QUERY_LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _records(repo: Path) -> list[dict]:
    ptr = load_pointer(repo)
    return list_feedback(Path(ptr["indexPath"]), ptr["rootId"], limit=1000)


def test_prompt_note_names_at_most_five_files_in_a_short_block(repo, monkeypatch, capsys):
    note = _note(_prompt(monkeypatch, capsys, repo, PROMPT))

    assert len(note) <= PROMPT_NOTE_LIMIT
    lines = note.splitlines()
    files = [line for line in lines if line.startswith("- ")]
    assert 1 <= len(files) <= 5
    assert any(line.startswith("- src/auth/session.py: lines ") for line in files)
    assert lines[-1] == 'Run `mimry preflight "<task>"` for the full context pack.'


def test_prompt_note_drops_files_past_the_limit_and_never_cuts_a_line():
    rows = [
        {"path": f"pkg/{'deep/' * 40}file{n}.py", "score": 9, "ranges": [{"start": 1, "end": 3}]}
        for n in range(5)
    ]
    note = _prompt_note(rows)

    assert note is not None
    assert len(note) <= PROMPT_NOTE_LIMIT
    files = [line for line in note.splitlines() if line.startswith("- ")]
    assert 1 <= len(files) < 5
    assert all(line.endswith("lines 1-3") for line in files)
    assert _prompt_note([{"path": "a.py", "score": 0}]) is None


@pytest.mark.parametrize(
    "prompt",
    [
        "/review the login session code please now",
        "fix the bug",
        "see https://example.com/a/b/c please ```x = 1``` ok",
        "thanks that looks great, can you tell me a joke about cats today",
    ],
    ids=["slash-command", "three-words", "three-words-after-stripping", "chat"],
)
def test_prompt_hook_prints_nothing_for_a_guarded_prompt(repo, monkeypatch, capsys, prompt):
    before = _query_count(repo)

    assert _prompt(monkeypatch, capsys, repo, prompt) == ""
    assert _query_count(repo) == before


def test_an_identifier_alone_names_the_task(repo, monkeypatch, capsys):
    # No word is in a file name, but create_session is an identifier.
    note = _note(_prompt(monkeypatch, capsys, repo, "what does create_session return to callers"))

    assert "session.py" in note


@pytest.mark.parametrize(
    "unit, count",
    [("a.", 50_000), ("a/", 50_000), ("a-", 50_000), ("a", 200_000)],
    ids=["dot-run", "slash-run", "dash-run", "single-word"],
)
def test_a_long_unbroken_token_keeps_the_prompt_hook_fast(repo, monkeypatch, capsys, unit, count):
    prompt = "please fix the code in " + unit * count + " fix it"

    started = time.perf_counter()
    out = _prompt(monkeypatch, capsys, repo, prompt)

    assert time.perf_counter() - started < 0.2
    assert out == ""


def test_a_code_token_past_the_query_chars_is_ignored(repo, monkeypatch, capsys):
    # Only the first PROMPT_QUERY_CHARS count. A token there names a
    # file; the same token past them names none.
    filler = "lorem ipsum dolor sit amet " * (PROMPT_QUERY_CHARS // 27 + 1)
    before = _query_count(repo)

    assert "session.py" in _note(_prompt(monkeypatch, capsys, repo, "create_session " + filler))
    assert _prompt(monkeypatch, capsys, repo, filler + "what does create_session return") == ""
    assert _query_count(repo) == before + 1


def test_prompt_hook_prints_nothing_when_switched_off(repo, monkeypatch, capsys):
    monkeypatch.setenv("MIMRY_PROMPT_HOOK", "0")

    assert _prompt(monkeypatch, capsys, repo, PROMPT) == ""


def test_prompt_hook_prints_nothing_without_an_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "app.py").write_text("def create_session():\n    return 1\n", encoding="utf-8")

    assert _prompt(monkeypatch, capsys, plain, PROMPT) == ""
    assert not (plain / ".mimry").exists()


def test_prompt_hook_prints_nothing_for_invalid_json(repo, monkeypatch, capsys):
    capsys.readouterr()
    _feed(monkeypatch, b"{not json")

    assert cmd_hook_prompt(SimpleNamespace(root=str(repo))) == 0
    assert capsys.readouterr().out == ""


def test_prompt_hook_prints_nothing_when_the_lookup_raises(repo, monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(mimry.search, "find_rows", broken)

    assert _prompt(monkeypatch, capsys, repo, PROMPT) == ""


def test_prompt_hook_prints_nothing_past_the_index_size_limits(repo, monkeypatch, capsys):
    monkeypatch.setattr(hook, "PROMPT_MAX_FILES", 1)
    assert _prompt(monkeypatch, capsys, repo, PROMPT) == ""

    monkeypatch.setattr(hook, "PROMPT_MAX_FILES", 3000)
    monkeypatch.setattr(hook, "PROMPT_GRAPH_MAX_BYTES", 1)
    graph_path(repo).parent.mkdir(parents=True, exist_ok=True)
    graph_path(repo).write_text("{}", encoding="utf-8")
    assert _prompt(monkeypatch, capsys, repo, PROMPT) == ""


def test_repeated_prompt_in_one_session_gets_no_second_note(repo, monkeypatch, capsys):
    assert _note(_prompt(monkeypatch, capsys, repo, PROMPT))
    # The same words, reordered and capitalised, are the same prompt.
    reordered = "Session after login: where does the code create a user"

    assert _prompt(monkeypatch, capsys, repo, PROMPT) == ""
    assert _prompt(monkeypatch, capsys, repo, reordered) == ""
    assert _note(_prompt(monkeypatch, capsys, repo, PROMPT, session="other-session"))


def test_query_is_recorded_with_the_hook_session(repo, monkeypatch, capsys):
    _prompt(monkeypatch, capsys, repo, PROMPT)

    last = _query_rows(repo)[-1]
    assert last["session"] == SESSION
    assert last["query"] == PROMPT
    assert last["shown"]


def test_stop_hook_closes_the_window_of_the_prompt_note(repo, monkeypatch, capsys):
    note = _note(_prompt(monkeypatch, capsys, repo, PROMPT))
    first = next(line for line in note.splitlines() if line.startswith("- "))
    shown = first[2:].split(":")[0]
    read = {"file_path": str(repo / shown)}
    _feed(monkeypatch, {"tool_name": "Read", "session_id": SESSION, "tool_input": read})
    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0
    # The turn has not ended, so its window is still open.
    assert _records(repo) == []

    _feed(monkeypatch, {"hook_event_name": "Stop", "session_id": SESSION})
    assert cmd_hook_stop(SimpleNamespace(root=str(repo))) == 0

    [record] = _records(repo)
    assert record["opened_paths"] == [shown]


def test_stop_hook_flushes_the_session_feedback(repo, monkeypatch):
    flushed = []
    monkeypatch.setattr(hook, "flush_session_feedback", flushed.append)
    _feed(monkeypatch, {"hook_event_name": "Stop", "session_id": SESSION})

    assert cmd_hook_stop(SimpleNamespace(root=str(repo))) == 0
    assert flushed == [repo.resolve()]


def test_stop_hook_never_fails_the_turn(repo, monkeypatch):
    _feed(monkeypatch, b"not json at all")

    assert cmd_hook_stop(SimpleNamespace(root=str(repo))) == 0
