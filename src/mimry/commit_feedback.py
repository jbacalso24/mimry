"""Record feedback from a commit and the task pack written before it.

The post-commit hook runs this, so the agent does not have to report
which files it changed. Every skip returns a one-line reason instead of
raising, because the hook must never fail a commit.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .feedback import (
    COMMIT_NOTE_PREFIX,
    ensure_feedback_schema,
    feedback_payload,
    normalize_paths,
    parse_context_suggested_paths,
    record_feedback,
)
from .paths import TASK_PACK_PATTERN, output_dir
from .scanner import _git_output

MAX_COMMIT_FILES = 50


def _pack_query(path: Path) -> str:
    """The first non-empty line under the pack's Query heading."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    in_query = False
    for line in text.splitlines():
        if line.startswith("## Query"):
            in_query = True
        elif in_query and line.strip():
            return line.strip()
    return ""


def _committer_time(root: Path, rev: str) -> int | None:
    out = _git_output(root, "log", "-1", "--format=%ct", rev, "--")
    if out is None:
        return None
    try:
        return int(out.decode("ascii").strip())
    except ValueError:
        return None


def _already_recorded(idx: Path, root_id: str, payload: dict[str, Any], mtime: float) -> bool:
    """True when a row for this task or pack was written after the pack.

    Matches on the pack path or on the same (stored, redacted) query, so
    feedback an agent sent with ``latest.md`` still counts.
    """
    con = sqlite3.connect(idx / "mimry.sqlite")
    try:
        ensure_feedback_schema(con)
        rows = con.execute(
            "select created_at from feedback where root_id = ?"
            " and (context_path = ? or query = ?)",
            (root_id, payload["context_path"], payload["query"]),
        ).fetchall()
    finally:
        con.close()
    return any(datetime.fromisoformat(created).timestamp() >= mtime for (created,) in rows)


def record_commit_feedback(
    root: Path, idx: Path, root_id: str, rev: str = "HEAD"
) -> tuple[dict[str, Any] | None, str]:
    """Record one feedback row for ``rev``.

    Returns (row or None, reason).
    """
    root = Path(root).resolve()

    head = _git_output(root, "log", "-1", "--format=%H %ct %P", rev, "--")
    if head is None:
        return None, "not a commit"
    sha, commit_time_raw, *parents = head.decode("ascii").split()
    commit_time = int(commit_time_raw)
    if len(parents) > 1:
        return None, "merge commit, nothing to learn from"

    raw = _git_output(
        root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", "--relative", sha
    )
    if raw is None:
        return None, "could not read the commit's changed files"
    names = [n.decode("utf-8", errors="replace") for n in raw.split(b"\0") if n]
    changed = normalize_paths(root, names)
    if not changed:
        return None, "commit changes no files under this folder"
    if len(changed) > MAX_COMMIT_FILES:
        return None, f"commit changes {len(changed)} files, too broad to learn from"

    since = (_committer_time(root, parents[0]) if parents else 0) or 0
    until = commit_time + 1
    context_dir = output_dir(root) / "context"
    candidates: list[tuple[Path, float]] = []
    if context_dir.is_dir():
        for entry in context_dir.iterdir():
            if not TASK_PACK_PATTERN.match(entry.name) or not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
            if since < mtime <= until:
                candidates.append((entry, mtime))
    if not candidates:
        return None, "no context pack since the previous commit"

    changed_set = set(changed)
    scored = []
    for pack, mtime in candidates:
        suggested = normalize_paths(root, parse_context_suggested_paths(pack))
        scored.append((len(changed_set.intersection(suggested)), mtime, pack, suggested))
    # A pack that shares no file with the commit still counts: every
    # changed file is then a miss, which is worth learning from.
    _, mtime, pack, suggested = max(scored, key=lambda item: (item[0], item[1]))

    query = _pack_query(pack)
    if not query:
        return None, "the context pack has no task query"

    payload = feedback_payload(
        root,
        {
            "query": query,
            "context_path": str(pack),
            "suggested": suggested,
            "changed": changed,
            "opened": [],
            "ignored": [],
            "verification": [],
            "outcome": "unknown",
            "notes": COMMIT_NOTE_PREFIX + sha[:12],
        },
    )
    if _already_recorded(idx, root_id, payload, mtime):
        return None, "feedback already recorded for this task"
    row = record_feedback(idx, root_id, payload, lock=False)
    return row, f"recorded from commit {sha[:12]}"
