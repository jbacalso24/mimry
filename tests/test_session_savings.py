"""Tokens saved: a ranged Read after a find, and `mimry stats`.

The count follows the honesty rule in ``mimry.savings``: only a ranged
Read of a file MIMRY named this session counts, and a symbol answer
counts as a search answered, never as tokens.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_hook_symbols import outline_repo  # noqa: F401
from test_session_feedback import QUERY, SESSION, _cli, _feed, _find, _repo

from mimry import savings
from mimry.cli import main
from mimry.hook import cmd_hook_check
from mimry.savings import _short, tokens_in
from mimry.session import FILE_LOG, QUERY_LOG, START_LOG


def _send(monkeypatch, repo: Path, payload: dict) -> int:
    _feed(monkeypatch, payload)
    return cmd_hook_check(SimpleNamespace(root=str(repo)))


def _bash(monkeypatch, repo: Path, session: str = SESSION) -> None:
    """The first Bash call: the hook logs the session start here."""
    payload = {"session_id": session, "tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert _send(monkeypatch, repo, payload) == 0


def _ranged(monkeypatch, repo: Path, path: Path, session: str = SESSION, **tool_input) -> int:
    payload = {
        "session_id": session,
        "tool_name": "Read",
        "tool_input": {"file_path": str(path), **tool_input},
    }
    return _send(monkeypatch, repo, payload)


def _savings_rows(repo: Path) -> list[dict]:
    path = repo / ".mimry" / savings.SAVINGS_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _expected(path: Path, start: int, count: int) -> dict[str, int]:
    """Whole, read and saved tokens, worked out from the file itself."""
    text = path.read_bytes().decode("utf-8")
    lines = io.StringIO(text).readlines()
    whole = tokens_in("".join(lines[:2000]))
    read = tokens_in("".join(lines[start - 1 : start - 1 + count]))
    return {"whole": whole, "read": read, "saved": max(0, whole - read)}


def _log_texts(repo: Path) -> list[str | None]:
    """The text of each session log, None where a log does not exist."""
    paths = [repo / ".mimry" / name for name in (QUERY_LOG, FILE_LOG, START_LOG)]
    return [path.read_text(encoding="utf-8") if path.exists() else None for path in paths]


def _stats(repo: Path, *args: str) -> str:
    res = _cli(repo, "stats", *args)
    assert res.returncode == 0, res.stderr
    return res.stdout


def test_find_then_ranged_read_counts_the_tokens_saved(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo)
    shown = _find(repo, QUERY)
    target = repo / shown[0]

    assert _ranged(monkeypatch, repo, target, offset=3, limit=5) == 0

    [row] = _savings_rows(repo)
    assert row["session"] == SESSION
    assert row["path"] == shown[0]
    expected = _expected(target, start=3, count=5)
    assert {key: row[key] for key in ("whole", "read", "saved")} == expected
    assert row["saved"] == expected["whole"] - expected["read"]

    saved = expected["saved"]
    session_line = (
        f"This session: about {saved:,} tokens saved (estimated), 1 ranged read counted, "
        "0 searches answered from the index."
    )
    all_time = session_line.replace("This session", "All time")
    out = _stats(repo, "--session", SESSION)
    assert session_line in out
    assert all_time in out
    assert "Estimated: characters / 4" in out


def test_stats_json_totals_match_the_log(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo)
    shown = _find(repo, QUERY)
    _ranged(monkeypatch, repo, repo / shown[0], offset=1, limit=2)

    report = json.loads(_stats(repo, "--json", "--session", SESSION))

    [row] = _savings_rows(repo)
    assert report["session"] == {
        "id": SESSION,
        "estimated_tokens_saved": row["saved"],
        "ranged_reads": 1,
        "searches_answered": 0,
    }
    assert report["all_time"]["estimated_tokens_saved"] == row["saved"]
    assert report["all_time"]["ranged_reads"] == 1


def test_ranged_read_of_a_file_mimry_never_named_counts_nothing(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo)
    _find(repo, QUERY)
    unnamed = repo / "unnamed_notes.txt"
    unnamed.write_text("line\n" * 300, encoding="utf-8")

    assert _ranged(monkeypatch, repo, unnamed, offset=1, limit=10) == 0

    assert _savings_rows(repo) == []


def test_whole_file_read_of_a_named_file_counts_nothing(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo)
    shown = _find(repo, QUERY)

    assert _ranged(monkeypatch, repo, repo / shown[0]) == 0

    assert _savings_rows(repo) == []


def test_file_over_the_size_cap_counts_nothing(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo)
    shown = _find(repo, QUERY)
    monkeypatch.setattr(savings, "SAVINGS_MAX_BYTES", 10)

    assert _ranged(monkeypatch, repo, repo / shown[0], offset=1, limit=2) == 0

    assert _savings_rows(repo) == []


def test_ranged_read_after_outline_counts_tokens(outline_repo, monkeypatch, capsys):  # noqa: F811
    repo = outline_repo
    big = str(repo / "app" / "big.py")
    outline = {"session_id": SESSION, "tool_name": "Read", "tool_input": {"file_path": big}}
    assert _send(monkeypatch, repo, outline) == 0
    assert (
        "Its symbols:"
        in json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    )

    assert _ranged(monkeypatch, repo, Path(big), offset=10, limit=20) == 0

    [row] = _savings_rows(repo)
    assert row["path"] == "app/big.py"
    assert {key: row[key] for key in ("whole", "read", "saved")} == _expected(
        Path(big), start=10, count=20
    )


def test_symbol_answer_counts_a_search_and_no_tokens(outline_repo, monkeypatch, capsys):  # noqa: F811
    repo = outline_repo
    payload = {
        "session_id": SESSION,
        "tool_name": "Grep",
        "tool_input": {"pattern": "gamma"},
    }
    assert _send(monkeypatch, repo, payload) == 0
    out = capsys.readouterr().out
    assert "MIMRY knows gamma" in out

    [row] = _savings_rows(repo)
    assert row == {"time": row["time"], "session": SESSION, "answered": 1}
    report = json.loads(_stats(repo, "--json", "--session", SESSION))
    assert report["session"]["searches_answered"] == 1
    assert report["session"]["estimated_tokens_saved"] == 0
    assert report["session"]["ranged_reads"] == 0


def test_statusline_prints_the_session_line_and_nothing_without_one(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo)
    shown = _find(repo, QUERY)
    _ranged(monkeypatch, repo, repo / shown[0], offset=3, limit=5)
    [row] = _savings_rows(repo)

    def statusline(payload) -> tuple[int, str]:
        raw = json.dumps(payload).encode("utf-8")
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
        code = main(["stats", "--statusline"])
        return code, capsys.readouterr().out

    workspace = {"workspace": {"current_dir": str(repo)}}
    code, out = statusline({"session_id": SESSION, **workspace})
    assert code == 0
    assert out == f"MIMRY about {_short(row['saved'])} tokens saved this session (estimated)\n"

    assert statusline({"session_id": "someone-else", **workspace}) == (0, "")
    assert statusline({"workspace": {"current_dir": str(repo)}}) == (0, "")


def test_statusline_with_bad_stdin_prints_nothing_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"not json {")))
    assert main(["stats", "--statusline"]) == 0
    assert capsys.readouterr().out == ""


def test_session_feedback_off_switch_logs_no_savings(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _bash(monkeypatch, repo)
    shown = _find(repo, QUERY)
    monkeypatch.setenv("MIMRY_SESSION_FEEDBACK", "0")
    before = _log_texts(repo)

    assert _ranged(monkeypatch, repo, repo / shown[0], offset=3, limit=5) == 0
    assert _savings_rows(repo) == []
    assert _log_texts(repo) == before


def test_stats_with_no_savings_reports_zero(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    out = _stats(repo, "--session", SESSION)
    assert "This session: about 0 tokens saved (estimated), 0 ranged reads counted" in out


@pytest.mark.parametrize(
    "tokens, text", [(0, "0"), (999, "999"), (1234, "1.2k"), (2_000_000, "2.0M")]
)
def test_short_token_counts(tokens, text):
    assert _short(tokens) == text
