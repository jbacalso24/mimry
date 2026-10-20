"""Session feedback: learn from what the agent did after a find.

Each find, preflight and context run appends one line to
``.mimry/session-queries.jsonl``. The hooks append what the agent read
(``opened``) and edited (``changed``) to ``.mimry/session-files.jsonl``.
A query's window runs from its time to the next query's time. A closed
window with file events becomes one feedback record, the same record
``mimry feedback`` stores. Nothing here may fail the command or the
hook.

This module imports only the standard library at top level, because the
hooks load it on every tool call. The feedback and index modules are
imported inside the functions that need them.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SESSION_WINDOW_IDLE = 2 * 60 * 60
SESSION_QUERY_LIMIT = 200
SESSION_FILE_LIMIT = 2000
# File events older than this are dropped at the next flush.
SESSION_FILE_KEEP = 24 * 60 * 60
SESSION_IGNORED_LIMIT = 5
SESSION_NOTE = "recorded from session"
COMPACT_FILES = 10
COMPACT_QUERY_LIMIT = 300
COMPACT_NOTE_LIMIT = 1500
QUERY_LOG = "session-queries.jsonl"
FILE_LOG = "session-files.jsonl"
PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$")


def session_feedback_enabled() -> bool:
    """MIMRY_SESSION_FEEDBACK=0 turns off both logs and the flush."""
    return os.environ.get("MIMRY_SESSION_FEEDBACK", "1").strip() != "0"


def _state_dir(root: Path) -> Path | None:
    """The repo's .mimry folder, or None. Never creates it."""
    state = Path(root) / ".mimry"
    return state if state.is_dir() else None


def _relative(root: Path, raw: Any) -> str | None:
    """``raw`` as a POSIX path under root, or None when outside."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        absolute = os.path.abspath(Path(root) / text)
        rel = Path(os.path.relpath(absolute, os.path.abspath(root)))
    except ValueError:
        # A path on another drive has no relative form.
        return None
    if rel.is_absolute() or rel.parts[:1] == ("..",) or rel == Path("."):
        return None
    return rel.as_posix()


def _tracked(root: Path, raw: Any) -> str | None:
    """A repo path worth logging: inside the repo and not under .mimry.

    Agents read the context pack after a preflight; that is not code.
    """
    rel = _relative(root, raw)
    if rel is None or rel.split("/", 1)[0] == ".mimry":
        return None
    return rel


def _append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _unique(values) -> list[str]:
    """Distinct values, in the order first seen."""
    return list(dict.fromkeys(values))


def _stamp(row: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(row["time"])).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def log_file_event(root: Path, session: str, action: str, raw_paths: list[Any]) -> None:
    """Append an ``opened`` or ``changed`` event per repo path.

    Never raises.
    """
    try:
        state = _state_dir(root)
        if state is None or not session_feedback_enabled():
            return
        when = datetime.now(UTC).isoformat()
        for raw in raw_paths:
            rel = _tracked(root, raw)
            if rel is not None:
                _append(
                    state / FILE_LOG,
                    {"time": when, "session": session, "action": action, "path": rel},
                )
    except Exception:
        # The log is optional; never fail the hook or the command.
        pass


def edited_paths(tool_input: dict[str, Any]) -> list[str]:
    """The paths an edit names: file_path, notebook_path or a patch."""
    paths: list[str] = []
    for key in ("file_path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            paths.append(value)
    command = tool_input.get("command")
    if isinstance(command, str) and command.lstrip().startswith("*** Begin Patch"):
        for line in command.splitlines():
            match = PATCH_PATH_RE.match(line)
            if match:
                paths.append(match.group(1))
    return paths


def record_query(root: Path, query: str, shown: list[Any], context_path: Any = None) -> None:
    """Append the query, the paths shown and the context path.

    Never raises. The command may hold the shared index lock here, and
    an append is safe under it. The flush that needs the exclusive lock
    runs after the command returns.
    """
    try:
        state = _state_dir(root)
        if state is None or not session_feedback_enabled():
            return
        from .feedback import _redact_feedback_string

        text, _ = _redact_feedback_string(str(query or "").strip())
        _append(
            state / QUERY_LOG,
            {
                "time": datetime.now(UTC).isoformat(),
                "query": text[:2000],
                "shown": [rel for rel in (_tracked(root, p) for p in shown) if rel is not None],
                "context": _relative(root, context_path),
            },
        )
    except Exception:
        pass


def _flush_plan(root: Path, state: Path, now: float) -> dict[str, Any] | None:
    """What a flush would do now, or None if it would change nothing."""
    from .feedback import feedback_payload

    timed_queries = [(_stamp(q), q) for q in _read_rows(state / QUERY_LOG)]
    timed_events = [(_stamp(e), e) for e in _read_rows(state / FILE_LOG)]
    timed_queries = [(t, q) for t, q in timed_queries if t is not None]
    timed_events = [(t, e) for t, e in timed_events if t is not None]
    starts = [t for t, _ in timed_queries]
    queries = [q for _, q in timed_queries]
    # Only the last query can still be open; each earlier one has a
    # later query, which closes it.
    last_open = bool(queries) and now - starts[-1] <= SESSION_WINDOW_IDLE
    closed = len(queries) - 1 if last_open else len(queries)

    records: list[dict[str, Any]] = []
    for i in range(closed):
        begin = starts[i]
        end = starts[i + 1] if i + 1 < len(queries) else None
        window = [e for t, e in timed_events if t >= begin and (end is None or t < end)]
        opened = _unique(e["path"] for e in window if e.get("action") == "opened")
        changed = _unique(e["path"] for e in window if e.get("action") == "changed")
        if not opened and not changed:
            continue
        query = queries[i]
        shown = [str(p) for p in query.get("shown") or []]
        # Ignored only means something when the agent changed a file.
        ignored: list[str] = []
        if changed:
            fresh = [p for p in shown if p not in opened and p not in changed]
            ignored = fresh[:SESSION_IGNORED_LIMIT]
        records.append(
            feedback_payload(
                root,
                {
                    "query": query.get("query") or "",
                    "context_path": query.get("context"),
                    "suggested": shown,
                    "opened": opened,
                    "changed": changed,
                    "ignored": ignored,
                    "outcome": "unknown",
                    "notes": SESSION_NOTE,
                },
            )
        )

    # File events are kept by age, not by window: a window only counts
    # events after its own query, so a flushed one is never counted
    # twice, and the compaction note still sees the files read earlier.
    kept_queries = queries[closed:][-SESSION_QUERY_LIMIT:]
    kept_events = [e for t, e in timed_events if now - t <= SESSION_FILE_KEEP][
        -SESSION_FILE_LIMIT:
    ]
    if not records and len(kept_queries) == len(queries) and len(kept_events) == len(timed_events):
        return None
    return {"records": records, "queries": kept_queries, "events": kept_events}


def flush_session_feedback(root: Path) -> None:
    """Turn each closed window with file events into one record.

    Runs after a command that may have closed a window, and when a
    session starts. It takes the operation lock feedback uses, so no
    index lock may be held by this process. Does nothing without an
    index. Never raises.
    """
    try:
        if not session_feedback_enabled():
            return
        from .lifecycle import indexed_root

        root = indexed_root(Path(root).resolve())
        state = _state_dir(root)
        if state is None or _flush_plan(root, state, time.time()) is None:
            return
        from .feedback import record_feedback
        from .storage import active_index_pointer

        with active_index_pointer(root, exclusive=True) as ptr:
            if not ptr:
                return
            plan = _flush_plan(root, state, time.time())
            if plan is None:
                return
            # Logs first: if a rewrite fails, a window is lost rather
            # than recorded twice on the next flush.
            _write_rows(state / QUERY_LOG, plan["queries"])
            _write_rows(state / FILE_LOG, plan["events"])
            for payload in plan["records"]:
                record_feedback(Path(ptr["indexPath"]), ptr["rootId"], payload, lock=False)
    except Exception:
        # ponytail: two agent sessions in one repo share windows by
        # time. Per-session windows need the session id on each query.
        pass


def _shell_quoted(text: str) -> str:
    """``text`` in double quotes for a POSIX shell.

    A backslash escapes the characters that stay live inside double
    quotes, so the query cannot close them or run a command.
    """
    return '"' + re.sub(r'(["\\$`])', r"\\\1", text) + '"'


def _compact_text(query: str, edited: list[str], read: list[str]) -> str:
    if query:
        lead = f'Before the context was compacted you were working on: "{query}".'
    else:
        lead = "Before the context was compacted you were working on these files."
    parts = [lead]
    if edited:
        parts.append(f"Edited: {', '.join(edited)}.")
    if read:
        parts.append(f"Read: {', '.join(read)}.")
    if query:
        parts.append(f"Run mimry find {_shell_quoted(query)} to get the ranked files back.")
    return " ".join(parts)


def compact_note(root: Path, session: str) -> str | None:
    """The note a compacted session gets back: its last query and files.

    Query lines carry no session id, so the query is the newest one from
    the point where the session's work began. Reads the logs only and
    returns None when there is nothing to say. Never raises.
    """
    try:
        if not session or not session_feedback_enabled():
            return None
        state = _state_dir(root)
        if state is None:
            return None
        events = [
            (t, e)
            for t, e in ((_stamp(r), r) for r in _read_rows(state / FILE_LOG))
            if t is not None
        ]
        events = [(t, e) for t, e in events if e.get("session") == session]
        if not events:
            return None
        events.sort(key=lambda item: item[0])
        first = events[0][0]
        queries = [
            (t, q)
            for t, q in ((_stamp(r), r) for r in _read_rows(state / QUERY_LOG))
            if t is not None
        ]
        queries.sort(key=lambda item: item[0])
        # A find runs before the reads it leads to, so the work starts
        # at the query in effect when the first file event happened.
        # Queries carry no session id: the newest from there on is last.
        earlier = [t for t, _ in queries if t <= first]
        start = max(earlier) if earlier else first
        later = [q for t, q in queries if t >= start]
        query = ""
        if later:
            query = " ".join(str(later[-1].get("query") or "").split())[:COMPACT_QUERY_LIMIT]
        # Newest first, so the lists below keep the latest files.
        touched = [
            (e.get("action"), e["path"])
            for _, e in reversed(events)
            if isinstance(e.get("path"), str)
        ]
        changed = _unique(path for action, path in touched if action == "changed")
        opened = _unique(path for action, path in touched if action == "opened")
        if not changed and not opened:
            return None
        edited = changed[:COMPACT_FILES]
        read = [path for path in opened if path not in changed][:COMPACT_FILES]
        note = _compact_text(query, edited, read)
        # Capped query: dropping trailing paths always fits the limit.
        while len(note) > COMPACT_NOTE_LIMIT and (read or edited):
            if read:
                read = read[:-1]
            else:
                edited = edited[:-1]
            note = _compact_text(query, edited, read)
        return note
    except Exception:
        # The note is optional; a session that restarts still works.
        return None
