"""Local usage counts for ``mimry feedback stats``.

Preflights are logged here instead of in the feedback table, so the
numbers work without git hooks. Commits come from ``git log``. Nothing
leaves the machine, and the log holds no query text.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from .paths import output_dir
from .scanner import _git_output
from .state import atomic_write_text

TOP_N = 5
DAY = 86400
MAX_LOG_BYTES = 1_000_000
KEEP_DAYS = 90


def log_path(root: Path) -> Path:
    return output_dir(root) / "preflights.jsonl"


def log_preflight(root: Path, rows: list[dict]) -> None:
    """Append one preflight to the log. Never raises."""
    now = time.time()
    entry = {"at": now, "top": [r["path"] for r in rows[:TOP_N]]}
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    path = log_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
        if path.stat().st_size > MAX_LOG_BYTES:
            # ponytail: a line another process appends during this
            # rewrite can be lost, which only undercounts the stats.
            kept = [e for e in _entries(path) if e["at"] >= now - KEEP_DAYS * DAY]
            atomic_write_text(
                path,
                "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in kept),
            )
    except OSError:
        return


def read_log(root: Path) -> list[dict]:
    return _entries(log_path(root))


def _entries(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries = []
    # Split on newline only: json.dumps escapes everything else, and
    # str.splitlines would also break on characters like U+2028.
    for line in text.split("\n"):
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if _valid(entry):
            entries.append(entry)
    return entries


def _valid(entry: Any) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("top"), list):
        return False
    at = entry.get("at")
    return isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at)


def usage_stats(root: Path, *, days: int = 30, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    since = now - days * DAY
    entries = read_log(root)
    stats: dict[str, Any] = {
        "days": days,
        "preflights": sum(1 for e in entries if e["at"] >= since),
        "commits": 0,
        "commits_with_preflight": None,
        "top_five_hits": None,
    }
    commits = _commits_since(root, since)
    if commits is None:
        return stats

    matched = hits = 0
    for _sha, ct, parent_ct, paths in commits:
        if parent_ct is None:
            continue
        # Git times are whole seconds, so the parent's real time is in
        # [parent_ct, parent_ct + 1). A preflight before parent_ct + 1
        # may predate the parent, so only entries from parent_ct + 1
        # count. The commit's real time is in [ct, ct + 1), so that end
        # stays inclusive.
        window = [e for e in entries if parent_ct + 1 <= e["at"] <= ct + 1]
        if not window:
            continue
        matched += 1
        changed = set(paths)
        # The best overlap wins; among equal overlaps the newest counts.
        best = max(window, key=lambda e: (len(changed & set(e["top"])), e["at"]))
        if changed & set(best["top"]):
            hits += 1
    stats.update(commits=len(commits), commits_with_preflight=matched, top_five_hits=hits)
    return stats


def _commits_since(root: Path, since: float) -> list[tuple] | None:
    """(sha, committer time, parent time, changed paths) per commit.

    Returns None when git cannot read the repo. A parent time is None
    when it is unknown, and a root commit's parent time is 0.
    """
    out = _git_output(
        root,
        "-c",
        "core.quotePath=false",
        "log",
        "--no-merges",
        f"--since=@{int(since)}",
        "--format=%x00%H %ct %P",
        "--name-only",
        "--relative",
    )
    if out is None:
        return None
    times: dict[str, int] = {}
    parsed = []
    for chunk in out.decode("utf-8", errors="replace").split("\x00"):
        lines = chunk.split("\n")
        header = lines[0].split()
        if len(header) < 2 or not header[1].isdigit():
            continue
        sha, ct = header[0], int(header[1])
        times[sha] = ct
        paths = [line for line in lines[1:] if line]
        first_parent = header[2] if len(header) > 2 else None
        parsed.append((sha, ct, first_parent, paths))

    missing = sorted(
        {parent for _, _, parent, paths in parsed if paths and parent and parent not in times}
    )
    if missing:
        shown = _git_output(root, "show", "-s", "--format=%H %ct", *missing)
        for line in (shown or b"").decode("utf-8", errors="replace").split("\n"):
            fields = line.split()
            if len(fields) == 2 and fields[1].isdigit():
                times[fields[0]] = int(fields[1])

    commits = []
    for sha, ct, parent, paths in parsed:
        if not paths:
            continue
        parent_ct = 0 if parent is None else times.get(parent)
        commits.append((sha, ct, parent_ct, paths))
    return commits
