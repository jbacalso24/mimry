"""Session feedback: what the agent opened and edited after a find.

Runs find and the hooks the way the agent does, through the CLI and the
hook entry points, then checks the stored feedback records.
"""

from __future__ import annotations

import io
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from test_mimry_cli import copy_fixture, run_cli

from mimry.feedback import feedback_payload_from_args, list_feedback, parse_context_suggested_paths
from mimry.hook import cmd_hook_check, cmd_hook_refresh
from mimry.paths import TASK_PACK_PATTERN
from mimry.search import find_rows
from mimry.session import flush_session_feedback, record_query
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


def _feed(monkeypatch, payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))


def _read(monkeypatch, repo: Path, path: Path) -> int:
    _feed(
        monkeypatch,
        {
            "session_id": SESSION,
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
