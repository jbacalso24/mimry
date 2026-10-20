"""Tokens MIMRY saved, counted from what the hooks already see.

A ranged Read (``offset`` or ``limit`` set) of a file MIMRY named in the
session saves the tokens of the rest of the file. A file is named when
it was in the shown paths of a query logged since the session's first
event, or when the hook printed its outline this session. A symbol
answer counts as one search answered, never as tokens.

Tokens are estimated as characters / 4, rounded, and every place that
shows a number says so. Nothing else is counted.

This module imports only the standard library and ``session``, because
the status line runs it on every refresh.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .session import _append, _read_rows, _state_dir, _write_rows, session_feedback_enabled

SAVINGS_LOG = "session-savings.jsonl"
SAVINGS_LINE_LIMIT = 5000
# A bigger file is not read for a count, as with the outline.
SAVINGS_MAX_BYTES = 4_000_000
# A whole-file Read of Claude Code stops at this many lines.
READ_LINE_CAP = 2000
CHARS_PER_TOKEN = 4
ESTIMATE_NOTE = (
    "Estimated: characters / 4, rounded, for the first 2000 lines of each file read whole, "
    "minus the lines each ranged Read asked for. A search answered counts as one search."
)


def tokens_in(text: str) -> int:
    """The estimated token count of ``text``."""
    return round(len(text) / CHARS_PER_TOKEN)


def _count(value: Any, default: int) -> int | None:
    """An int argument: the default if absent, None if not a number."""
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_row(state: Path, row: dict[str, Any]) -> None:
    path = state / SAVINGS_LOG
    _append(path, row)
    rows = _read_rows(path)
    if len(rows) > SAVINGS_LINE_LIMIT:
        _write_rows(path, rows[-SAVINGS_LINE_LIMIT:])


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record_ranged_read(root: Path, session: str, rel: str, offset: Any, limit: Any) -> None:
    """Log the tokens a ranged Read of ``rel`` saves. Never raises.

    The caller has checked that MIMRY named ``rel`` in this session.
    Saved is the whole file's first 2000 lines minus the lines asked
    for, never below zero.
    """
    try:
        state = _state_dir(root)
        if state is None or not session or not session_feedback_enabled():
            return
        start = _count(offset, 1)
        count = _count(limit, READ_LINE_CAP)
        if start is None or count is None or count < 1:
            return
        path = Path(root) / rel
        # Check cheaply first; read the bytes only for a small file.
        if not path.is_file() or path.stat().st_size > SAVINGS_MAX_BYTES:
            return
        text = path.read_bytes().decode("utf-8", errors="replace")
        lines = io.StringIO(text).readlines()
        first = max(start, 1) - 1
        whole = tokens_in("".join(lines[:READ_LINE_CAP]))
        read = tokens_in("".join(lines[first : first + count]))
        _append_row(
            state,
            {
                "time": _now(),
                "session": session,
                "path": rel,
                "whole": whole,
                "read": read,
                "saved": max(0, whole - read),
            },
        )
    except Exception:
        # The count is optional; never fail the hook over it.
        pass


def record_answered_search(root: Path, session: str) -> None:
    """Log one search a symbol answer answered. Never raises."""
    try:
        state = _state_dir(root)
        if state is None or not session_feedback_enabled():
            return
        _append_row(state, {"time": _now(), "session": session, "answered": 1})
    except Exception:
        pass


def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    saved = [row for row in rows if isinstance(row.get("saved"), int)]
    answered = [row for row in rows if isinstance(row.get("answered"), int)]
    return {
        "estimated_tokens_saved": sum(row["saved"] for row in saved),
        "ranged_reads": len(saved),
        "searches_answered": sum(row["answered"] for row in answered),
    }


def savings_report(root: Path, session: str = "") -> dict[str, Any]:
    """Session totals (if ``session`` is known) and all-time totals."""
    state = _state_dir(root)
    rows = _read_rows(state / SAVINGS_LOG) if state is not None else []
    report: dict[str, Any] = {
        "session": None,
        "all_time": _totals(rows),
        "estimate": ESTIMATE_NOTE,
    }
    if session:
        mine = [row for row in rows if row.get("session") == session]
        report["session"] = {"id": session, **_totals(mine)}
    return report


def _summary(label: str, totals: dict[str, int]) -> str:
    reads = totals["ranged_reads"]
    answered = totals["searches_answered"]
    return (
        f"{label}: about {totals['estimated_tokens_saved']:,} tokens saved (estimated), "
        f"{reads:,} ranged read{'' if reads == 1 else 's'} counted, "
        f"{answered:,} search{'' if answered == 1 else 'es'} answered from the index."
    )


def format_report(report: dict[str, Any]) -> list[str]:
    lines = []
    if report["session"] is not None:
        lines.append(_summary("This session", report["session"]))
    lines.append(_summary("All time", report["all_time"]))
    lines.append(report["estimate"])
    return lines


def _short(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def statusline_text(root: Path, session: str) -> str:
    """One status line for ``session``, or "" when nothing to show."""
    if not session:
        return ""
    totals = savings_report(root, session)["session"]
    parts = []
    if totals["ranged_reads"]:
        saved = _short(totals["estimated_tokens_saved"])
        parts.append(f"about {saved} tokens saved this session (estimated)")
    answered = totals["searches_answered"]
    if answered:
        parts.append(f"{answered:,} search{'' if answered == 1 else 'es'} answered")
    return "MIMRY " + ", ".join(parts) if parts else ""


def find_repo(start: Path) -> Path | None:
    """The folder at or above ``start`` holding .mimry, or None."""
    try:
        here = Path(start).resolve()
    except OSError:
        return None
    for folder in (here, *here.parents):
        if (folder / ".mimry").is_dir():
            return folder
    return None


def statusline_from_json(raw: str) -> str:
    """Status line for Claude Code's JSON on stdin, or "" otherwise."""
    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return ""
        session = str(data.get("session_id") or "")
        workspace = data.get("workspace")
        folder = workspace.get("current_dir") if isinstance(workspace, dict) else None
        folder = folder or data.get("cwd") or os.getcwd()
        root = find_repo(Path(str(folder)))
        if root is None:
            return ""
        return statusline_text(root, session)
    except Exception:
        return ""


def run_statusline() -> int:
    """``mimry stats --statusline``: print one line. Never fails."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    line = statusline_from_json(raw)
    try:
        if line:
            print(line)
    except Exception:
        pass
    return 0
