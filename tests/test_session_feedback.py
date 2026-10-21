"""Session feedback: what the agent opened and edited after a find.

Runs find and the hooks the way the agent does, through the CLI and the
hook entry points, then checks the stored feedback records.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry import feedback, session
from mimry.cli import main
from mimry.feedback import feedback_payload_from_args, list_feedback, parse_context_suggested_paths
from mimry.hook import cmd_hook_check, cmd_hook_compact, cmd_hook_refresh, cmd_hook_stop
from mimry.paths import TASK_PACK_PATTERN, cache_home
from mimry.search import find_rows
from mimry.session import (
    FILE_LOG,
    QUERY_LOG,
    flush_session_feedback,
    log_file_event,
    record_query,
)
from mimry.state import file_lock
from mimry.storage import load_pointer

QUERY = "login session timeout"
FOLLOW_UP = "login timeout"
RANK_QUERY = "fix login session timeout"
SESSION = "session-under-test"
RECORD_KEYS = (
    "query",
    "context_path",
    "suggested_paths",
    "opened_paths",
    "changed_paths",
    "missed_paths",
    "ignored_paths",
    "verification",
    "outcome",
)


def _repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path)
    assert _cli(repo, "init", "--skip-graph").returncode == 0
    assert _cli(repo, "index").returncode == 0
    return repo


def _cli(repo: Path, *args: str):
    return run_cli(repo, repo.parent / "cache", *args)


def _find(repo: Path, query: str) -> list[str]:
    """Run a find and return the paths it printed, in order."""
    res = _cli(repo, "find", query)
    assert res.returncode == 0, res.stderr
    return re.findall(r"^\s*\d+\s{2}(\S+)$", res.stdout, flags=re.MULTILINE)


def _find_in_session(monkeypatch, repo: Path, query: str, session: str = SESSION) -> list[str]:
    """A find run the way the agent runs it.

    The Bash hook sees the command first, so the query has the session.
    """
    _feed(
        monkeypatch,
        {
            "session_id": session,
            "tool_name": "Bash",
            "tool_input": {"command": f'mimry find "{query}"'},
        },
    )
    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0
    return _find(repo, query)


def _feed(monkeypatch, payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))


def _read(monkeypatch, repo: Path, path: Path, session: str = SESSION) -> int:
    _feed(
        monkeypatch,
        {
            "session_id": session,
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
        },
    )
    return cmd_hook_check(SimpleNamespace(root=str(repo)))


def _edit(monkeypatch, repo: Path, rel: str) -> None:
    target = repo / rel
    target.write_text(target.read_text(encoding="utf-8") + "\n# session edit\n", encoding="utf-8")
    _feed(
        monkeypatch,
        {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION,
            "tool_name": "Edit",
            "tool_input": {"file_path": str(target)},
        },
    )
    assert cmd_hook_refresh(SimpleNamespace(root=str(repo))) == 0


def _query_lines(repo: Path) -> list[dict]:
    path = repo / ".mimry" / "session-queries.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _age_queries(repo: Path, hours: float) -> None:
    """Move every query line back in time, so its window goes idle."""
    path = repo / ".mimry" / "session-queries.jsonl"
    rows = _query_lines(repo)
    for row in rows:
        row["time"] = (datetime.fromisoformat(row["time"]) - timedelta(hours=hours)).isoformat()
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _rows(repo: Path) -> list[dict]:
    ptr = load_pointer(repo)
    return list_feedback(Path(ptr["indexPath"]), ptr["rootId"], limit=1000)


def _rank(repo: Path, query: str, path: str) -> int:
    ptr = load_pointer(repo)
    rows = find_rows(Path(ptr["indexPath"]), query, 50, root=repo, root_id=ptr["rootId"])
    paths = [row["path"] for row in rows]
    return paths.index(path) if path in paths else len(paths)


def test_session_record_matches_manual_feedback_and_ranks_edited_file_higher(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert len(shown) >= 4
    # The top file is ignored, so the edited file can rise to the top.
    opened, edited, ignored_candidate = [shown[2], shown[3]], shown[1], shown[0]
    rank_before = _rank(repo, RANK_QUERY, edited)

    for path in opened:
        assert _read(monkeypatch, repo, repo / path) == 0
    _edit(monkeypatch, repo, edited)
    assert _find(repo, FOLLOW_UP)

    [record] = _rows(repo)
    changed = [edited]
    ignored = [p for p in shown if p not in opened and p not in changed][:5]
    assert ignored == [ignored_candidate]
    manual = feedback_payload_from_args(
        repo,
        SimpleNamespace(
            query=QUERY,
            context=None,
            suggested=shown,
            opened=opened,
            changed=changed,
            missed=None,
            ignored=ignored,
            verification=None,
            outcome="unknown",
            notes=None,
            from_json=None,
        ),
    )
    for key in RECORD_KEYS:
        assert record[key] == manual[key], key
    assert record["notes"] == "recorded from session"
    assert _rank(repo, RANK_QUERY, edited) < rank_before


def test_window_without_file_events_makes_no_record(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _find(repo, QUERY)
    _find(repo, FOLLOW_UP)
    assert _rows(repo) == []


def test_open_window_waits_for_a_later_query_or_the_idle_time(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0

    flush_session_feedback(repo)
    assert _rows(repo) == []

    _age_queries(repo, hours=3)
    flush_session_feedback(repo)
    [record] = _rows(repo)
    assert record["opened_paths"] == [shown[0]]


def test_ignored_is_empty_when_nothing_was_changed(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    _find(repo, FOLLOW_UP)

    [record] = _rows(repo)
    assert record["opened_paths"] == [shown[0]]
    assert record["changed_paths"] == []
    assert record["ignored_paths"] == []


def test_paths_outside_the_repo_and_under_mimry_are_not_logged(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    outside = tmp_path / "outside" / "notes.py"
    outside.parent.mkdir()
    outside.write_text("x = 1\n", encoding="utf-8")
    _find(repo, QUERY)

    assert _read(monkeypatch, repo, outside) == 0
    assert _read(monkeypatch, repo, repo / ".." / "outside" / "notes.py") == 0
    assert _read(monkeypatch, repo, repo / ".mimry" / "pointer.json") == 0
    assert not (repo / ".mimry" / "session-files.jsonl").exists()


def test_session_feedback_off_switch_writes_no_logs_and_no_records(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setenv("MIMRY_SESSION_FEEDBACK", "0")
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    _edit(monkeypatch, repo, shown[1])
    _find(repo, FOLLOW_UP)
    flush_session_feedback(repo)

    assert not (repo / ".mimry" / "session-queries.jsonl").exists()
    assert not (repo / ".mimry" / "session-files.jsonl").exists()
    assert _rows(repo) == []


def test_broken_session_logs_never_fail_find_or_the_hook(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    (repo / ".mimry" / "session-queries.jsonl").mkdir()
    (repo / ".mimry" / "session-files.jsonl").mkdir()

    res = _cli(repo, "find", QUERY)
    assert res.returncode == 0, res.stderr
    assert "Best matches for" in res.stdout
    assert _read(monkeypatch, repo, repo / "src" / "auth" / "session.py") == 0
    _edit(monkeypatch, repo, "src/app.ts")
    flush_session_feedback(repo)
    assert _rows(repo) == []


def test_query_log_keeps_the_redacted_query(tmp_path):
    repo = tmp_path
    (repo / ".mimry").mkdir()
    record_query(repo, "fix login api_key=privacy-canary-session", ["src/auth/session.py"])

    text = (repo / ".mimry" / "session-queries.jsonl").read_text(encoding="utf-8")
    assert "privacy-canary-session" not in text
    assert "[REDACTED]" in text


def test_session_start_closes_the_last_window(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    _age_queries(repo, hours=3)

    _feed(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "next-session"})
    assert cmd_hook_refresh(SimpleNamespace(root=str(repo))) == 0

    [record] = _rows(repo)
    assert record["opened_paths"] == [shown[0]]


def test_preflight_window_records_its_context_pack(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    res = _cli(repo, "preflight", QUERY)
    assert res.returncode == 0, res.stderr
    context = repo / ".mimry" / "mimry-out" / "context"
    [pack] = [p for p in context.iterdir() if TASK_PACK_PATTERN.match(p.name)]
    suggested = parse_context_suggested_paths(pack)
    assert suggested
    assert _read(monkeypatch, repo, repo / suggested[0]) == 0
    _find(repo, FOLLOW_UP)

    [record] = _rows(repo)
    assert record["context_path"] == pack.resolve().relative_to(repo.resolve()).as_posix()
    assert record["suggested_paths"] == suggested
    assert record["opened_paths"] == [suggested[0]]


def _restored(monkeypatch, capsys, repo: Path, session: str = SESSION) -> str:
    """The compact note for ``session``, or "" when there is none."""
    capsys.readouterr()
    _feed(
        monkeypatch,
        {"hook_event_name": "SessionStart", "source": "compact", "session_id": session},
    )
    assert cmd_hook_compact(SimpleNamespace(root=str(repo))) == 0
    out = capsys.readouterr().out
    if not out:
        return ""
    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["hookEventName"] == "SessionStart"
    return hook["additionalContext"]


def _append_rows(repo: Path, log: str, rows: list[dict]) -> None:
    with (repo / ".mimry" / log).open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_compact_note_names_the_query_the_edits_and_the_reads(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find_in_session(monkeypatch, repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[2]) == 0
    assert _read(monkeypatch, repo, repo / shown[3]) == 0
    _edit(monkeypatch, repo, shown[1])

    assert _restored(monkeypatch, capsys, repo) == (
        f'Before the context was compacted you were working on: "{QUERY}". '
        f"Edited: {shown[1]}. Read: {shown[3]}, {shown[2]}. "
        f'Run mimry find "{QUERY}" to get the ranked files back.'
    )


def test_compact_note_uses_the_query_whose_window_holds_the_files(tmp_path, monkeypatch, capsys):
    # Each find closes the window before it; the logs keep the open one.
    repo = _repo(tmp_path, monkeypatch)
    shown = _find_in_session(monkeypatch, repo, QUERY)
    _find_in_session(monkeypatch, repo, FOLLOW_UP)
    assert _read(monkeypatch, repo, repo / shown[2]) == 0

    note = _restored(monkeypatch, capsys, repo)
    assert f'you were working on: "{FOLLOW_UP}".' in note
    assert f'"{QUERY}"' not in note
    assert f'Run mimry find "{FOLLOW_UP}"' in note
    assert f"Read: {shown[2]}." in note


def test_compact_note_keeps_files_from_flushed_windows(tmp_path, monkeypatch, capsys):
    # A later find flushes the first window, but its reads stay in the
    # working set, and the flushed window is recorded only once.
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[2]) == 0
    _find(repo, FOLLOW_UP)
    assert _read(monkeypatch, repo, repo / shown[3]) == 0
    _find(repo, FOLLOW_UP)

    assert f"Read: {shown[3]}, {shown[2]}." in _restored(monkeypatch, capsys, repo)
    assert sorted(row["opened_paths"] for row in _rows(repo)) == sorted([[shown[2]], [shown[3]]])


def test_compact_note_leaves_out_other_sessions(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    assert _read(monkeypatch, repo, repo / shown[1], session="other-session") == 0

    note = _restored(monkeypatch, capsys, repo)
    assert f"Read: {shown[0]}." in note
    assert shown[1] not in note
    other = _restored(monkeypatch, capsys, repo, session="other-session")
    assert f"Read: {shown[1]}." in other
    assert shown[0] not in other


def test_compact_note_is_silent_without_session_data(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    assert _restored(monkeypatch, capsys, repo) == ""

    # A query alone says nothing about what the session worked on.
    _find(repo, QUERY)
    assert _restored(monkeypatch, capsys, repo) == ""


def test_compact_note_caps_each_list_at_ten_files(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    base = datetime.now().astimezone()
    _append_rows(
        repo,
        QUERY_LOG,
        [{"time": (base - timedelta(seconds=1)).isoformat(), "query": QUERY, "shown": []}],
    )
    rows = []
    for i in range(12):
        when = base + timedelta(seconds=2 * i)
        rows.append(
            {
                "time": when.isoformat(),
                "session": SESSION,
                "action": "opened",
                "path": f"read{i}.py",
            }
        )
        later = when + timedelta(seconds=1)
        rows.append(
            {
                "time": later.isoformat(),
                "session": SESSION,
                "action": "changed",
                "path": f"edit{i}.py",
            }
        )
    _append_rows(repo, FILE_LOG, rows)

    note = _restored(monkeypatch, capsys, repo)
    newest = range(11, 1, -1)
    assert f"Edited: {', '.join(f'edit{i}.py' for i in newest)}." in note
    assert f"Read: {', '.join(f'read{i}.py' for i in newest)}." in note
    assert "edit1.py" not in note and "read0.py" not in note
    assert len(note) <= 1500


def test_compact_note_quotes_the_query_for_a_shell(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    tricky = 'fix "login" $(touch pwned) `id` \\ end'
    base = datetime.now().astimezone()
    _append_rows(
        repo,
        QUERY_LOG,
        [
            {
                "time": (base - timedelta(seconds=1)).isoformat(),
                "query": tricky,
                "shown": [],
                "session": SESSION,
            }
        ],
    )
    _append_rows(
        repo,
        FILE_LOG,
        [{"time": base.isoformat(), "session": SESSION, "action": "opened", "path": "src/app.ts"}],
    )

    note = _restored(monkeypatch, capsys, repo)
    command = note.rsplit("Run mimry find ", 1)[1].rsplit(" to get", 1)[0]
    assert command == '"fix \\"login\\" \\$(touch pwned) \\`id\\` \\\\ end"'
    if shutil.which("sh") is None:
        pytest.skip("needs a POSIX sh to run the quoted command")
    printed = subprocess.run(
        ["sh", "-c", f"printf '%s' {command}"], capture_output=True, text=True, check=True
    ).stdout
    assert printed == tricky


def test_compact_note_respects_the_off_switch(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    monkeypatch.setenv("MIMRY_SESSION_FEEDBACK", "0")

    assert _restored(monkeypatch, capsys, repo) == ""


def test_broken_logs_give_no_compact_note_and_exit_zero(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    (repo / ".mimry" / QUERY_LOG).mkdir()
    (repo / ".mimry" / FILE_LOG).mkdir()

    assert _restored(monkeypatch, capsys, repo) == ""


def test_installed_claude_code_config_restores_context_in_the_foreground(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    res = run_cli(repo, cache, "install", "--project", "--platform", "claude-code", "--hooks")
    assert res.returncode == 0, res.stderr

    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    [group] = [g for g in settings["hooks"]["SessionStart"] if g["matcher"] == "compact"]
    [handler] = group["hooks"]
    assert handler["command"].endswith(" hook-compact")
    assert "async" not in handler


def test_compact_hook_reads_the_logs_and_writes_nothing(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    # The window is idle, so a flush would close it.
    _age_queries(repo, hours=3)
    logs = [repo / ".mimry" / QUERY_LOG, repo / ".mimry" / FILE_LOG]
    before = [path.read_bytes() for path in logs]
    _feed(monkeypatch, {"hook_event_name": "SessionStart", "session_id": SESSION})
    capsys.readouterr()

    assert main(["--root", str(repo), "hook-compact"]) == 0

    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["additionalContext"].startswith("Before the context was compacted")
    assert [path.read_bytes() for path in logs] == before
    assert _rows(repo) == []


def test_flush_gives_up_on_a_busy_operation_lock_and_records_later(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    _age_queries(repo, hours=3)
    lock = cache_home() / "indexes" / load_pointer(repo)["rootId"] / "operation.lock"

    with file_lock(lock):
        started = time.monotonic()
        flush_session_feedback(repo)
        assert time.monotonic() - started < 1
        assert _rows(repo) == []

    flush_session_feedback(repo)
    [record] = _rows(repo)
    assert record["opened_paths"] == [shown[0]]


def test_flush_keeps_a_hook_event_appended_after_its_snapshot(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    # Two queries with an opened event between them. The first window
    # is closed; the second stays open.
    record_query(repo, "race one", [])
    time.sleep(0.01)
    log_file_event(repo, SESSION, "opened", [repo / "src" / "auth" / "session.py"])
    time.sleep(0.01)
    record_query(repo, "race two", [])
    time.sleep(0.01)

    real_write_rows = session._write_rows

    def write_after_a_hook_append(path, rows, **kwargs):
        # A hook appends between the flush's snapshot and its rewrite.
        if path.name == FILE_LOG:
            log_file_event(repo, SESSION, "changed", [repo / "src" / "app.ts"])
        return real_write_rows(path, rows, **kwargs)

    monkeypatch.setattr(session, "_write_rows", write_after_a_hook_append)
    flush_session_feedback(repo)

    [record] = _rows(repo)
    assert record["opened_paths"] == ["src/auth/session.py"]
    events = [
        json.loads(line)
        for line in (repo / ".mimry" / FILE_LOG).read_text(encoding="utf-8").splitlines()
    ]
    assert ("changed", "src/app.ts") in [(e["action"], e["path"]) for e in events]


def test_a_query_from_another_process_is_not_named_in_a_hooked_session(
    tmp_path, monkeypatch, capsys
):
    # The session's own find is tagged by its Bash hook. Another process
    # then runs a find with no session id, and the note for the session
    # must not name that query.
    repo = _repo(tmp_path, monkeypatch)
    shown = _find_in_session(monkeypatch, repo, QUERY)
    assert _query_lines(repo)[-1]["session"] == SESSION
    _edit(monkeypatch, repo, shown[1])
    record_query(repo, "billing invoice total card", [shown[0]])

    note = _restored(monkeypatch, capsys, repo)
    assert f'you were working on: "{QUERY}".' in note
    assert "billing" not in note


def test_flush_keeps_a_hook_event_appended_while_the_log_is_replaced(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    record_query(repo, "race one", [])
    time.sleep(0.01)
    log_file_event(repo, SESSION, "opened", [repo / "src" / "auth" / "session.py"])
    time.sleep(0.01)
    record_query(repo, "race two", [])
    time.sleep(0.01)

    real_replace = os.replace
    appends: list[threading.Thread] = []

    def replace_while_a_hook_appends(src, dst):
        # The hook's append starts after the flush read its snapshot, in
        # the window where it used to be lost. It has to wait for the
        # replace and then land in the new file.
        if Path(dst).name == FILE_LOG:
            hook = threading.Thread(
                target=log_file_event,
                args=(repo, SESSION, "changed", [repo / "src" / "app.ts"]),
            )
            hook.start()
            appends.append(hook)
            time.sleep(0.05)
        return real_replace(src, dst)

    monkeypatch.setattr(session.os, "replace", replace_while_a_hook_appends)
    flush_session_feedback(repo)
    assert appends
    for hook in appends:
        hook.join(5)

    [record] = _rows(repo)
    assert record["opened_paths"] == ["src/auth/session.py"]
    events = [
        json.loads(line)
        for line in (repo / ".mimry" / FILE_LOG).read_text(encoding="utf-8").splitlines()
    ]
    assert ("changed", "src/app.ts") in [(e["action"], e["path"]) for e in events]


def test_a_busy_flush_builds_no_feedback_record(tmp_path, monkeypatch):
    # The busy path returns before any feedback work. Payloads are built
    # only after the lock is taken.
    repo = _repo(tmp_path, monkeypatch)
    shown = _find(repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    _age_queries(repo, hours=3)
    lock = cache_home() / "indexes" / load_pointer(repo)["rootId"] / "operation.lock"

    built: list[tuple] = []
    real_payload = feedback.feedback_payload

    def spy(*args, **kwargs):
        built.append(args)
        return real_payload(*args, **kwargs)

    monkeypatch.setattr(feedback, "feedback_payload", spy)
    with file_lock(lock):
        flush_session_feedback(repo)
    assert built == []
    assert _rows(repo) == []


def test_a_held_append_lock_costs_the_event_not_the_hook(tmp_path, monkeypatch, capsys):
    # The wait is bounded: the hook runs in the agent's path.
    assert session.APPEND_WAIT <= 2.0
    assert session.APPEND_STALE <= 5
    # A lock another process holds past the wait drops the event. The
    # hook still exits 0 and prints nothing that is not JSON, and leaves
    # the other process's lock alone.
    repo = _repo(tmp_path, monkeypatch)
    lock = repo / ".mimry" / session.APPEND_LOCK
    lock.write_text("", encoding="utf-8")
    capsys.readouterr()

    started = time.monotonic()
    assert _read(monkeypatch, repo, repo / "src" / "auth" / "session.py") == 0
    assert time.monotonic() - started < session.APPEND_WAIT + 1
    out = capsys.readouterr().out
    assert out == "" or isinstance(json.loads(out), dict)
    assert lock.exists()
    log = repo / ".mimry" / FILE_LOG
    assert not log.exists() or "src/auth/session.py" not in log.read_text(encoding="utf-8")


def test_a_busy_append_lock_on_windows_is_retried_not_dropped(tmp_path, monkeypatch):
    # On Windows the lock's O_EXCL create fails with PermissionError
    # while the previous holder's unlink is still pending. That is a
    # busy lock, so the hook retries and its event lands.
    (tmp_path / ".mimry").mkdir()
    lock = tmp_path / ".mimry" / session.APPEND_LOCK
    lock.touch()  # the previous holder's lock, still being removed
    monkeypatch.setattr(session.os, "name", "nt")
    real_open = os.open
    refusals: list[str] = []

    def busy_then_free(path, flags, *args, **kwargs):
        if str(path).endswith(session.APPEND_LOCK) and len(refusals) < 2:
            refusals.append(str(path))
            if len(refusals) == 2:
                lock.unlink()  # the pending unlink completes
            raise PermissionError(13, "lock is being removed")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(session.os, "open", busy_then_free)
    log_file_event(tmp_path, SESSION, "changed", [tmp_path / "src" / "app.py"])

    assert len(refusals) == 2
    log = tmp_path / ".mimry" / FILE_LOG
    assert "src/app.py" in log.read_text(encoding="utf-8")


def test_a_permission_error_off_windows_is_still_raised_at_once(tmp_path, monkeypatch):
    # Off Windows a PermissionError on the lock means the folder is not
    # writable, not a busy lock. It is raised without waiting.
    state = tmp_path / ".mimry"
    state.mkdir()
    monkeypatch.setattr(session.os, "name", "posix")

    def refuse(path, flags, *args, **kwargs):
        raise PermissionError(13, "folder is not writable")

    monkeypatch.setattr(session.os, "open", refuse)
    started = time.monotonic()
    with pytest.raises(PermissionError):
        with session._append_lock(state):
            pass
    assert time.monotonic() - started < session.APPEND_WAIT


def test_an_unwritable_folder_on_windows_is_raised_not_waited_on(tmp_path, monkeypatch):
    # On Windows the create is refused and no lock file exists, so the
    # folder is unwritable (a sandbox): raise at once, never spin.
    state = tmp_path / ".mimry"
    state.mkdir()
    monkeypatch.setattr(session.os, "name", "nt")

    def refuse(path, flags, *args, **kwargs):
        raise PermissionError(13, "folder is not writable")

    monkeypatch.setattr(session.os, "open", refuse)
    started = time.monotonic()
    with pytest.raises(PermissionError):
        with session._append_lock(state):
            pass
    assert time.monotonic() - started < session.APPEND_WAIT


def test_each_flushed_window_holds_only_its_own_sessions_events(tmp_path, monkeypatch):
    # Two sessions run interleaved finds. A file that one session opens
    # comes after the other session's find, so only its own window
    # may count it.
    repo = _repo(tmp_path, monkeypatch)
    mine = _find_in_session(monkeypatch, repo, QUERY, session="sessA")
    _find_in_session(monkeypatch, repo, FOLLOW_UP, session="sessB")
    log_file_event(repo, "sessA", "opened", [repo / mine[0]])
    _age_queries(repo, hours=3)

    flush_session_feedback(repo)

    [record] = _rows(repo)
    assert record["query"] == QUERY
    assert record["opened_paths"] == [mine[0]]


def test_a_hooked_session_without_a_tagged_query_names_its_mcp_query(
    tmp_path, monkeypatch, capsys
):
    # The query came from an MCP tool, which no hook sees, so it has
    # no session id. A query recorded before the session began must
    # not be named.
    repo = _repo(tmp_path, monkeypatch)
    record_query(repo, "old query before the session", [])
    time.sleep(0.01)
    _read(monkeypatch, repo, repo / "src" / "auth" / "session.py")  # session starts
    time.sleep(0.01)
    shown = _find(repo, QUERY)  # the MCP find: no Bash hook, so untagged
    time.sleep(0.01)
    _edit(monkeypatch, repo, shown[1])

    note = _restored(monkeypatch, capsys, repo)
    assert f'you were working on: "{QUERY}".' in note
    assert "old query" not in note


def _mcp_query(monkeypatch, repo: Path, query: str, session: str = SESSION) -> None:
    """An MCP find the way the agent runs it.

    The hook sees the call, then the tool records the query it got.
    """
    _feed(
        monkeypatch,
        {
            "session_id": session,
            "tool_name": "mcp__mimry__mimry_find",
            "tool_input": {"query": query},
        },
    )
    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0
    record_query(repo, query, [])


def test_compact_note_names_its_own_mcp_query_not_another_sessions(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    assert _read(monkeypatch, repo, repo / "src" / "auth" / "session.py") == 0
    time.sleep(0.01)
    _mcp_query(monkeypatch, repo, QUERY)
    time.sleep(0.01)
    assert _read(monkeypatch, repo, repo / "src" / "auth" / "session.py") == 0
    other = "other-session"
    assert _read(monkeypatch, repo, repo / "src" / "app.ts", session=other) == 0
    _mcp_query(monkeypatch, repo, "app shell render", session=other)
    time.sleep(0.01)
    target = repo / "src" / "app.ts"
    target.write_text(target.read_text(encoding="utf-8") + "\n// other edit\n", encoding="utf-8")
    _feed(
        monkeypatch,
        {
            "hook_event_name": "PostToolUse",
            "session_id": other,
            "tool_name": "Edit",
            "tool_input": {"file_path": str(target)},
        },
    )
    assert cmd_hook_refresh(SimpleNamespace(root=str(repo))) == 0

    note = _restored(monkeypatch, capsys, repo)
    assert f'you were working on: "{QUERY}".' in note
    assert "app shell" not in note
    assert "src/app.ts" not in note


def test_a_session_with_a_tagged_mcp_query_never_takes_an_untagged_one(
    tmp_path, monkeypatch, capsys
):
    repo = _repo(tmp_path, monkeypatch)
    assert _read(monkeypatch, repo, repo / "src" / "auth" / "session.py") == 0
    time.sleep(0.01)
    _mcp_query(monkeypatch, repo, QUERY)
    time.sleep(0.01)
    # A query no hook saw: a CLI run outside a hooked session, untagged.
    record_query(repo, "untagged cli query", [])
    time.sleep(0.01)
    _edit(monkeypatch, repo, "src/auth/session.py")

    note = _restored(monkeypatch, capsys, repo)
    assert f'you were working on: "{QUERY}".' in note
    assert "untagged cli query" not in note


def _stop(monkeypatch, repo: Path, session: str = SESSION) -> None:
    """The turn ends, the way the Stop hook runs it."""
    _feed(monkeypatch, {"hook_event_name": "Stop", "session_id": session})
    assert cmd_hook_stop(SimpleNamespace(root=str(repo))) == 0


def test_stop_keeps_the_last_query_for_the_compact_note_and_records_once(
    tmp_path, monkeypatch, capsys
):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find_in_session(monkeypatch, repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0

    _stop(monkeypatch, repo)
    note = _restored(monkeypatch, capsys, repo)
    assert note == (
        f'Before the context was compacted you were working on: "{QUERY}". '
        f"Read: {shown[0]}. "
        f'Run mimry find "{QUERY}" to get the ranked files back.'
    )
    [record] = _rows(repo)
    assert record["opened_paths"] == [shown[0]]

    _stop(monkeypatch, repo)
    assert _restored(monkeypatch, capsys, repo) == note
    assert len(_rows(repo)) == 1


def test_a_later_turn_records_its_own_window_once_after_a_stop(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    shown = _find_in_session(monkeypatch, repo, QUERY)
    assert _read(monkeypatch, repo, repo / shown[0]) == 0
    _stop(monkeypatch, repo)

    later = _find_in_session(monkeypatch, repo, FOLLOW_UP)
    assert _read(monkeypatch, repo, repo / later[0]) == 0
    _stop(monkeypatch, repo)
    _stop(monkeypatch, repo)

    recorded = {row["query"]: row["opened_paths"] for row in _rows(repo)}
    assert recorded == {QUERY: [shown[0]], FOLLOW_UP: [later[0]]}


def _at(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _query(ts: float, text: str, shown: list[str] | None = None) -> dict:
    row = {"time": _at(ts), "query": text, "shown": shown or [], "context": None}
    return {**row, "session": SESSION}


def _opened(ts: float, path: str) -> dict:
    return {"time": _at(ts), "session": SESSION, "action": "opened", "path": path}


def _stop_row(ts: float) -> dict:
    return {"time": _at(ts), "session": SESSION}


def _plan(tmp_path: Path, queries: list[dict], events: list[dict], stops: list[dict], now: float):
    """A flush plan from logs written with exact stamps.

    Before Python 3.13 the Windows clock advances only every ~15.6 ms,
    so a file event and a turn-end mark can carry one stamp. These tests
    write that tie directly, since the wall clock cannot be made to do
    it.
    """
    state = tmp_path / ".mimry"
    state.mkdir()
    for name, rows in ((QUERY_LOG, queries), (FILE_LOG, events), (session.STOP_LOG, stops)):
        text = "".join(json.dumps(row, sort_keys=True) + chr(10) for row in rows)
        (state / name).write_text(text, encoding="utf-8")
    return session._flush_plan(state, now)


def test_an_event_in_the_same_clock_tick_as_the_stop_belongs_to_its_window(tmp_path):
    # The Read and the Stop hook run back to back, so the Read's stamp
    # can equal the Stop mark's. The Stop runs after the turn's tool
    # calls, so the event belongs to the window and must be recorded.
    t0 = 1_800_000_000.0
    plan = _plan(
        tmp_path,
        [_query(t0, QUERY, ["a.py"])],
        [_opened(t0 + 0.5, "a.py")],
        [_stop_row(t0 + 0.5)],
        now=t0 + 1,
    )
    assert plan is not None
    assert [w["query"] for w in plan["windows"]] == [QUERY]
    assert plan["windows"][0]["opened"] == ["a.py"]


def test_a_stop_in_the_same_clock_tick_as_its_query_closes_that_window(tmp_path):
    # Query, read and stop share one stamp. The stop still ends the
    # query's window, so the query is recorded now, not left open.
    t0 = 1_800_000_000.0
    plan = _plan(
        tmp_path,
        [_query(t0, QUERY, ["a.py"])],
        [_opened(t0, "a.py")],
        [_stop_row(t0)],
        now=t0 + 1,
    )
    assert plan is not None
    assert [w["query"] for w in plan["windows"]] == [QUERY]
    assert plan["windows"][0]["opened"] == ["a.py"]


def test_an_event_in_the_tick_of_the_next_query_belongs_to_the_next_query(tmp_path):
    # A window ends at the next query exclusively, so a read stamped
    # with that query's own stamp belongs to the next query, not to the
    # earlier one.
    t0 = 1_800_000_000.0
    plan = _plan(
        tmp_path,
        [
            _query(t0, QUERY),
            _query(t0 + 1, FOLLOW_UP),
            _query(t0 + 2, "third query"),
        ],
        [_opened(t0 + 1, "a.py")],
        [],
        now=t0 + 3,
    )
    assert plan is not None
    assert [w["query"] for w in plan["windows"]] == [FOLLOW_UP]


def test_a_session_is_tagged_only_by_a_query_equal_to_its_own(tmp_path, monkeypatch, capsys):
    # AA's find is logged first. BB's MCP query only contains the same
    # words. AA's find stays AA's, and BB's note does not name it.
    repo = _repo(tmp_path, monkeypatch)
    _feed(
        monkeypatch,
        {
            "session_id": "AA",
            "tool_name": "Bash",
            "tool_input": {"command": 'mimry find "apply_feedback"'},
        },
    )
    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0
    time.sleep(0.01)
    _mcp_query(monkeypatch, repo, "apply_feedback_v2 cleanup", session="BB")
    time.sleep(0.01)
    # No file matches in the fixture, but the query is logged anyway.
    _cli(repo, "find", "apply_feedback")
    time.sleep(0.01)
    assert _read(monkeypatch, repo, repo / "src" / "auth" / "session.py", session="BB") == 0
    assert _read(monkeypatch, repo, repo / "src" / "app.ts", session="AA") == 0

    assert _query_lines(repo)[-1]["query"] == "apply_feedback"
    assert _query_lines(repo)[-1]["session"] == "AA"
    note_b = _restored(monkeypatch, capsys, repo, session="BB")
    assert 'you were working on: "apply_feedback_v2 cleanup".' in note_b
    assert '"apply_feedback"' not in note_b
    note_a = _restored(monkeypatch, capsys, repo, session="AA")
    assert 'you were working on: "apply_feedback".' in note_a


def test_a_shell_query_that_only_contains_the_text_does_not_tag_it(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _feed(
        monkeypatch,
        {
            "session_id": "BB",
            "tool_name": "Bash",
            "tool_input": {"command": "mimry find apply_feedback_v2"},
        },
    )
    assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0

    record_query(repo, "apply_feedback", [])
    assert _query_lines(repo)[-1]["query"] == "apply_feedback"
    assert "session" not in _query_lines(repo)[-1]


def test_an_exact_match_is_tagged_with_the_newest_session_that_ran_it(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    for session_id in ("old-session", "new-session"):
        _feed(
            monkeypatch,
            {
                "session_id": session_id,
                "tool_name": "Bash",
                "tool_input": {"command": f'mimry find "{QUERY}"'},
            },
        )
        assert cmd_hook_check(SimpleNamespace(root=str(repo))) == 0

    record_query(repo, QUERY, [])
    assert _query_lines(repo)[-1]["session"] == "new-session"
