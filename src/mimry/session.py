"""Session feedback: learn from what the agent did after a find.

Each find, preflight and context run appends one line to
``.mimry/session-queries.jsonl``. The hooks append what the agent read
(``opened``) and edited (``changed``) to ``.mimry/session-files.jsonl``.
A query's window runs from its time to the next query's time, or to the
session's turn-end mark (``session-stops.jsonl``, written by the Stop
hook) when that comes first. A closed window with file events becomes
one feedback record, the same record ``mimry feedback`` stores. Nothing
here may fail the command or the hook.

This module imports only the standard library at top level, because the
hooks load it on every tool call. The feedback and index modules are
imported inside the functions that need them.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SESSION_WINDOW_IDLE = 2 * 60 * 60
SESSION_QUERY_LIMIT = 200
# Session start lines kept; one is written per session.
SESSION_START_LIMIT = 500
SESSION_FILE_LIMIT = 2000
# File events older than this are dropped at the next flush.
SESSION_FILE_KEEP = 24 * 60 * 60
SESSION_IGNORED_LIMIT = 5
# How long a flush waits for the operation lock. A finished command
# must not wait on feedback bookkeeping, so the flush only tries. This
# is the smallest positive wait, since the lock helper rejects zero.
SESSION_LOCK_WAIT = 0.001
# The file log's append lock. A hook waits for it up to APPEND_WAIT, so
# an event is never appended without it; a lock older than APPEND_STALE
# came from a process that died holding it.
APPEND_LOCK = "session-files.lock"
APPEND_WAIT = 0.25
APPEND_STALE = 5.0
# The last shell commands that named mimry, with the session that ran
# them. A find run from such a command is tagged with that session.
HOOK_COMMAND_LOG = "session-commands.jsonl"
HOOK_COMMAND_LIMIT = 20
HOOK_COMMAND_WINDOW = 2 * 60
SESSION_NOTE = "recorded from session"
COMPACT_FILES = 10
COMPACT_QUERY_LIMIT = 300
COMPACT_NOTE_LIMIT = 1500
QUERY_LOG = "session-queries.jsonl"
FILE_LOG = "session-files.jsonl"
START_LOG = "session-starts.jsonl"
# The Stop hook's marks: the turn ended, so the open window closes.
STOP_LOG = "session-stops.jsonl"
SESSION_STOP_LIMIT = 500
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


def _rows_text(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


@contextmanager
def _append_lock(state: Path) -> Iterator[None]:
    """Hold the file log's append lock.

    Hooks take it for each append. A rewrite takes it only to re-read
    the rows appended since its snapshot and to replace the file, so
    neither waits long. Raises TimeoutError when another process holds
    it past APPEND_WAIT; the caller then treats the event as lost.
    """
    lock = state / APPEND_LOCK
    deadline = time.monotonic() + APPEND_WAIT
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > APPEND_STALE
            except FileNotFoundError:
                continue  # released between the two calls
            if stale:
                lock.unlink(missing_ok=True)
            elif time.monotonic() >= deadline:
                raise TimeoutError(f"{APPEND_LOCK} is held by another process") from None
            else:
                time.sleep(0.002)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_rows(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    seen: int | None = None,
    limit: int | None = None,
) -> None:
    """Replace the log with ``rows``.

    With ``seen``, the rows past its first ``seen`` are read again under
    the append lock and kept, so a row a hook appended after the
    caller's snapshot is not lost. ``limit`` then keeps the newest
    rows. The snapshot is written before the lock is taken.
    """
    if limit is not None:
        rows = rows[-limit:]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_rows_text(rows), encoding="utf-8")
    if seen is None:
        os.replace(tmp, path)
        return
    with _append_lock(path.parent):
        tail = _read_rows(path)[seen:]
        if tail and limit is not None and len(rows) + len(tail) > limit:
            tmp.write_text(_rows_text([*rows, *tail][-limit:]), encoding="utf-8")
        elif tail:
            with tmp.open("a", encoding="utf-8") as handle:
                handle.write(_rows_text(tail))
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
        rels = [rel for rel in (_tracked(root, raw) for raw in raw_paths) if rel is not None]
        if not rels:
            return
        # Under the append lock: a rewrite of this log cannot drop it.
        with _append_lock(state):
            for rel in rels:
                _append(
                    state / FILE_LOG,
                    {"time": when, "session": session, "action": action, "path": rel},
                )
    except Exception:
        # The log is optional; never fail the hook or the command.
        pass


def log_session_start(root: Path, session: str) -> None:
    """Append the session's first hook call; queries count from here.

    Queries carry no session id, so a query belongs to the session when
    it came after this line. Never raises.
    """
    try:
        state = _state_dir(root)
        if state is None or not session_feedback_enabled():
            return
        when = datetime.now(UTC).isoformat()
        rows = [*_read_rows(state / START_LOG), {"time": when, "session": session}]
        _write_rows(state / START_LOG, rows[-SESSION_START_LIMIT:])
    except Exception:
        pass


def log_session_stop(root: Path, session: str) -> None:
    """Mark the end of the session's turn, so its open window can close.

    The window of the session's newest query then ends here, and the
    next flush records it instead of waiting for the next query. Never
    raises.
    """
    try:
        state = _state_dir(root)
        if state is None or not session or not session_feedback_enabled():
            return
        row = {"time": datetime.now(UTC).isoformat(), "session": session}
        # Under the append lock, so a flush rewrite keeps the row.
        with _append_lock(state):
            _append(state / STOP_LOG, row)
    except Exception:
        pass


def session_started(state: Path, session: str) -> float | None:
    """When the session's first logged hook call happened, or None."""
    stamps = [
        _stamp(row) for row in _read_rows(state / START_LOG) if row.get("session") == session
    ]
    stamps = [stamp for stamp in stamps if stamp is not None]
    return min(stamps) if stamps else None


def named_in_session(root: Path, session: str, rel: str) -> bool:
    """Whether a query logged since the session began showed ``rel``.

    Reads the logs only. Never raises.
    """
    try:
        state = _state_dir(root)
        if state is None or not session or not session_feedback_enabled():
            return False
        begin = session_started(state, session)
        if begin is None:
            return False
        for query in _read_rows(state / QUERY_LOG):
            stamp = _stamp(query)
            shown = query.get("shown")
            if stamp is not None and stamp >= begin and isinstance(shown, list) and rel in shown:
                return True
        return False
    except Exception:
        return False


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


def log_hook_command(root: Path, session: str, command: str) -> None:
    """Remember a shell command that names mimry, with its session.

    The PreToolUse hook calls this before the command runs, so the find
    it starts can be tagged with the session. Never raises.
    """
    try:
        state = _state_dir(root)
        if state is None or not session or not session_feedback_enabled():
            return
        rows = [
            *_read_rows(state / HOOK_COMMAND_LOG),
            {"time": datetime.now(UTC).isoformat(), "session": session, "command": command[:2000]},
        ]
        _write_rows(state / HOOK_COMMAND_LOG, rows[-HOOK_COMMAND_LIMIT:])
    except Exception:
        pass


def _query_session(state: Path, query: str) -> str:
    """The session of a recent hook command naming ``query``, or "".

    A command counts when it ran within HOOK_COMMAND_WINDOW and names
    the query. Other processes' queries stay untagged.
    """
    text = query.strip()
    if not text:
        return ""
    now = time.time()
    for row in reversed(_read_rows(state / HOOK_COMMAND_LOG)):
        stamp = _stamp(row)
        if stamp is None or now - stamp > HOOK_COMMAND_WINDOW:
            continue
        if text in str(row.get("command") or ""):
            return str(row.get("session") or "")
    return ""


def record_query(
    root: Path, query: str, shown: list[Any], context_path: Any = None, session: str = ""
) -> None:
    """Append the query, the paths shown and the context path.

    The row names ``session`` when a hook passes it, else the session of
    the hook that saw the shell command that ran it. Never raises. The
    command may hold the shared index lock here, and an append is safe
    under it. The flush that needs the exclusive lock runs after the
    command returns.
    """
    try:
        state = _state_dir(root)
        if state is None or not session_feedback_enabled():
            return
        from .feedback import _redact_feedback_string

        raw = str(query or "")
        text, _ = _redact_feedback_string(raw.strip())
        row: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(),
            "query": text[:2000],
            "shown": [rel for rel in (_tracked(root, p) for p in shown) if rel is not None],
            "context": _relative(root, context_path),
        }
        tag = session or _query_session(state, raw)
        if tag:
            row["session"] = tag
        _append(state / QUERY_LOG, row)
    except Exception:
        pass


def _session_of(row: dict[str, Any]) -> str:
    return str(row.get("session") or "")


def _window_end(
    timed: list[tuple[float, dict[str, Any]]],
    index: int,
    session: str,
    stops: list[float],
) -> float | None:
    """When the query at ``index`` stops owning events, or None if open.

    A query with a session id ends at that session's next query, or at
    the first turn-end mark of the session after it, whichever comes
    first. One without ends at the next query of any session, as before
    sessions were recorded. ``stops`` are the session's mark times,
    oldest first.
    """
    begin = timed[index][0]
    end = None
    for next_begin, query in timed[index + 1 :]:
        if not session or _session_of(query) == session:
            end = next_begin
            break
    for stop in stops:
        if stop > begin:
            if end is None or stop < end:
                end = stop
            break
    return end


def _flush_plan(state: Path, now: float) -> dict[str, Any] | None:
    """What a flush would do now, or None if it would change nothing.

    Reads the logs only and imports nothing beyond the standard library,
    so a flush that finds the lock busy costs no more than this. Each
    closed window with file events becomes one entry of ``windows``: the
    query and what it was followed by. With a session id on the query,
    only that session's events count. ``events_seen`` counts the file
    event rows read, so the rewrite can keep the rows a hook appends
    after this snapshot.
    """
    timed_queries = [
        (t, q) for t, q in ((_stamp(r), r) for r in _read_rows(state / QUERY_LOG)) if t is not None
    ]
    raw_events = _read_rows(state / FILE_LOG)
    timed_events = [(t, e) for t, e in ((_stamp(r), r) for r in raw_events) if t is not None]
    # A mark older than the idle window cannot close an open window: the
    # window's query is that recent, and the mark follows it.
    raw_stops = _read_rows(state / STOP_LOG)
    kept_stops: list[dict[str, Any]] = []
    stops_by_session: dict[str, list[float]] = {}
    for row in raw_stops:
        stamp = _stamp(row)
        if stamp is not None and now - stamp <= SESSION_WINDOW_IDLE:
            kept_stops.append(row)
            stops_by_session.setdefault(_session_of(row), []).append(stamp)
    kept_stops = kept_stops[-SESSION_STOP_LIMIT:]
    for stamps in stops_by_session.values():
        stamps.sort()

    windows: list[dict[str, Any]] = []
    kept_queries: list[dict[str, Any]] = []
    for index, (begin, query) in enumerate(timed_queries):
        session = _session_of(query)
        end = _window_end(timed_queries, index, session, stops_by_session.get(session, []))
        if end is None and now - begin <= SESSION_WINDOW_IDLE:
            kept_queries.append(query)
            continue
        window = [
            e
            for t, e in timed_events
            if t >= begin
            and (end is None or t < end)
            and (not session or _session_of(e) == session)
        ]
        opened = _unique(e["path"] for e in window if e.get("action") == "opened")
        changed = _unique(e["path"] for e in window if e.get("action") == "changed")
        if not opened and not changed:
            continue
        shown = [str(p) for p in query.get("shown") or []]
        # Ignored only means something when the agent changed a file.
        ignored: list[str] = []
        if changed:
            fresh = [p for p in shown if p not in opened and p not in changed]
            ignored = fresh[:SESSION_IGNORED_LIMIT]
        windows.append(
            {
                "query": query.get("query") or "",
                "context_path": query.get("context"),
                "suggested": shown,
                "opened": opened,
                "changed": changed,
                "ignored": ignored,
                "outcome": "unknown",
                "notes": SESSION_NOTE,
            }
        )

    # File events are kept by age, not by window: a window only counts
    # events after its own query, so a flushed one is never counted
    # twice, and the compaction note still sees the files read earlier.
    kept_queries = kept_queries[-SESSION_QUERY_LIMIT:]
    kept_events = [e for t, e in timed_events if now - t <= SESSION_FILE_KEEP][
        -SESSION_FILE_LIMIT:
    ]
    if (
        not windows
        and len(kept_queries) == len(timed_queries)
        and len(kept_events) == len(timed_events)
        and len(kept_stops) == len(raw_stops)
    ):
        return None
    return {
        "windows": windows,
        "queries": kept_queries,
        "events": kept_events,
        "events_seen": len(raw_events),
        "stops": kept_stops,
        "stops_seen": len(raw_stops),
    }


def flush_session_feedback(root: Path) -> None:
    """Turn each closed window with file events into one record.

    Runs after a command that may have closed a window, and when a
    session starts. It takes the operation lock feedback uses, so no
    index lock may be held by this process. A busy lock means the flush
    does nothing and the windows wait for the next one. Does nothing
    without an index. Never raises.
    """
    try:
        if not session_feedback_enabled():
            return
        root = Path(root).resolve()
        if not (root / ".mimry" / "pointer.json").is_file():
            from .lifecycle import indexed_root

            root = indexed_root(root)
        state = _state_dir(root)
        if state is None or _flush_plan(state, time.time()) is None:
            return
        from .storage import active_index_pointer

        # Only try the lock: when a refresh holds it, the windows stay
        # for the next flush and this command has already printed.
        with active_index_pointer(root, exclusive=True, lock_timeout=SESSION_LOCK_WAIT) as ptr:
            if not ptr:
                return
            plan = _flush_plan(state, time.time())
            if plan is None:
                return
            # Logs first: if a rewrite fails, a window is lost rather
            # than recorded twice on the next flush. Hooks append to the
            # file log under its append lock, so its rewrite keeps the
            # rows appended after the snapshot.
            _write_rows(state / QUERY_LOG, plan["queries"])
            _write_rows(
                state / FILE_LOG,
                plan["events"],
                seen=plan["events_seen"],
                limit=SESSION_FILE_LIMIT,
            )
            _write_rows(
                state / STOP_LOG,
                plan["stops"],
                seen=plan["stops_seen"],
                limit=SESSION_STOP_LIMIT,
            )
            from .feedback import feedback_payload, record_feedback

            for window in plan["windows"]:
                payload = feedback_payload(root, window)
                record_feedback(Path(ptr["indexPath"]), ptr["rootId"], payload, lock=False)
    except Exception:
        # Two sessions in one repo can still share a window when a query
        # has no session id; those windows are matched by time.
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

    The query is the newest one this session ran, from the point where
    its work began. A query without this session's id is left out.
    Reads the logs only and returns None when there is nothing to say.
    Never raises.
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
        # A query the session's hook tagged is its own. Without any, the
        # query came from an MCP tool, which no hook sees, so the
        # untagged queries recorded since the session began count.
        all_queries = [
            (t, q)
            for t, q in ((_stamp(r), r) for r in _read_rows(state / QUERY_LOG))
            if t is not None
        ]
        queries = [(t, q) for t, q in all_queries if _session_of(q) == session]
        if not queries:
            begun = session_started(state, session)
            queries = [
                (t, q)
                for t, q in all_queries
                if not _session_of(q) and (begun is None or t >= begun)
            ]
        queries.sort(key=lambda item: item[0])
        # A find runs before the reads it leads to, so the work starts
        # at the query in effect when the first file event happened.
        # The newest of this session's queries from there on is last.
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
