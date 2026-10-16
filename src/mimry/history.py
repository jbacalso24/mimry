"""Commit history: the recent commits and the files each one touched.

The commits live in the index database, so `mimry history` answers from
the same SQLite file as `find`. They are read with one bounded
`git log` call, and kept current by adding only the commits between the
indexed HEAD and the new one.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .intent import TOKEN_RE, query_terms, text_terms
from .security import redact_sensitive_text

# Newest commits kept. Older history is not indexed.
HISTORY_COMMIT_LIMIT = 5000
# Stored commit body, after redaction.
HISTORY_BODY_CHARS = 2000
# Bump when stored commit data changes, so the next index rebuilds it.
HISTORY_FORMAT = 1

GIT_MISSING = "git is not installed, so commit history is unavailable"
NOT_A_REPO = "This folder is not a git repository, so it has no commit history"
NO_COMMITS = "This repository has no commits yet"

HISTORY_SCHEMA = """
create table if not exists history_state(
    id integer primary key check (id = 1), head text, shallow integer not null default 0,
    unavailable text, format integer not null default 0);
create table if not exists commits(
    id integer primary key, sha text not null unique, date text not null,
    subject text not null, body text not null);
create table if not exists commit_files(
    commit_id integer not null, status text not null, path text not null, old_path text);
create index if not exists commit_files_commit on commit_files(commit_id);
create virtual table if not exists commits_fts using fts5(subject, body, paths);
"""

_RECORD = "\x1e"
_FIELD = "\x1f"
_LOG_FORMAT = "--format=%x1e%H%x1f%aI%x1f%s%x1f%b"
_STATUS_WORDS = {"A": "added", "D": "removed", "R": "renamed", "M": "changed", "T": "changed"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def ensure_history_schema(con: sqlite3.Connection) -> None:
    con.executescript(HISTORY_SCHEMA)


def status_word(status: str) -> str:
    return _STATUS_WORDS.get(status, "changed")


def plain(text: str) -> str:
    """One terminal-safe line: control characters become spaces."""
    return _CONTROL.sub(" ", text).strip()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)


def _source(root: Path) -> tuple[str | None, str | None, bool]:
    """Return (HEAD sha, unavailable reason or None, shallow clone?)."""
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        return None, GIT_MISSING, False
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        return None, NOT_A_REPO, False
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    if head.returncode != 0:
        return None, NO_COMMITS, False
    shallow = _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == b"true"
    return head.stdout.decode("utf-8").strip(), None, shallow


def _is_ancestor(root: Path, old: str, head: str) -> bool:
    return _git(root, "merge-base", "--is-ancestor", old, head).returncode == 0


def _read_log(root: Path, head: str, since: str | None) -> list[dict[str, Any]] | None:
    """Commits newest first, each with the files it changed.

    Renames count only when the content is identical (100%). Inexact
    rename detection makes git fetch blobs one at a time in a partial
    clone; exact detection compares object IDs and fetches nothing.
    --relative keeps a subfolder root to its own paths, and commits with
    no path left in scope are dropped.
    Returns None when git fails, so the stored history is left alone.
    """
    spec = f"{since}..{head}" if since else head
    result = _git(
        root,
        "log",
        "--no-merges",
        "--no-show-signature",
        "--no-color",
        "--relative",
        "--name-status",
        "--find-renames=100%",
        "-z",
        f"--max-count={HISTORY_COMMIT_LIMIT}",
        _LOG_FORMAT,
        spec,
    )
    if result.returncode != 0:
        return None
    return _parse_log(result.stdout.decode("utf-8", errors="replace"))


def _parse_log(text: str) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    for chunk in text.split(_RECORD)[1:]:
        header, _, rest = chunk.partition("\0")
        fields = header.split(_FIELD, 3)
        if len(fields) != 4:
            continue
        sha, date, subject, body = fields
        tokens = rest.removeprefix("\n").split("\0")
        files: list[tuple[str, str, str | None]] = []
        index = 0
        while index < len(tokens) and tokens[index]:
            status = tokens[index][:1]
            if status in ("R", "C"):
                if index + 2 >= len(tokens):
                    break
                files.append((status, tokens[index + 2], tokens[index + 1]))
                index += 3
            else:
                if index + 1 >= len(tokens):
                    break
                files.append((status, tokens[index + 1], None))
                index += 2
        if files:
            commits.append(
                {"sha": sha, "date": date, "subject": subject, "body": body, "files": files}
            )
    return commits


def _state(con: sqlite3.Connection) -> tuple[str | None, bool]:
    """The HEAD the commits were read up to, and whether it was shallow.

    Another HISTORY_FORMAT reads as no HEAD, so the commits are rebuilt.
    """
    row = con.execute("select head, shallow, format from history_state where id = 1").fetchone()
    if row is None or row[2] != HISTORY_FORMAT:
        return None, False
    return row[0], bool(row[1])


def _set_state(
    con: sqlite3.Connection, head: str | None, shallow: bool, unavailable: str | None
) -> None:
    con.execute(
        "insert or replace into history_state(id, head, shallow, unavailable, format) "
        "values(1, ?, ?, ?, ?)",
        (head, int(shallow), unavailable, HISTORY_FORMAT),
    )


def _clear(con: sqlite3.Connection) -> None:
    con.execute("delete from commit_files")
    con.execute("delete from commits_fts")
    con.execute("delete from commits")


def _paths_text(files: list[tuple[str, str, str | None]]) -> str:
    """Paths split into words the way file search splits them.

    Each distinct status word is added once, so "removed" in a query
    finds the commits that deleted files without a big deletion
    outweighing every other commit.
    """
    parts: list[str] = []
    for _status, path, old_path in files:
        for name in (path, old_path):
            if name:
                parts.append(name)
                parts.append(" ".join(sorted(text_terms(name))))
    parts.extend(sorted({status_word(status) for status, _path, _old in files}))
    return " ".join(parts)


def _query_terms(query: str) -> list[str]:
    """Search terms, plus any numbers that query_terms drops.

    query_terms drops one-character tokens. A version such as 0.4.1 is
    three of them, so they are kept, and a commit that names that
    version matches all three.
    """
    numbers = [token for token in TOKEN_RE.findall(query) if token.isdigit()]
    return list(dict.fromkeys([*query_terms(query), *numbers]))


def _append(con: sqlite3.Connection, commits_newest_first: list[dict[str, Any]]) -> None:
    """Store commits oldest first, so a larger id is a newer commit."""
    next_id = con.execute("select coalesce(max(id), 0) from commits").fetchone()[0]
    for commit in reversed(commits_newest_first):
        next_id += 1
        subject = redact_sensitive_text(commit["subject"]).strip()
        body = redact_sensitive_text(commit["body"]).strip()[:HISTORY_BODY_CHARS]
        con.execute(
            "insert into commits(id, sha, date, subject, body) values(?,?,?,?,?)",
            (next_id, commit["sha"], commit["date"], subject, body),
        )
        con.execute(
            "insert into commits_fts(rowid, subject, body, paths) values(?,?,?,?)",
            (next_id, subject, body, _paths_text(commit["files"])),
        )
        con.executemany(
            "insert into commit_files(commit_id, status, path, old_path) values(?,?,?,?)",
            [(next_id, status, path, old) for status, path, old in commit["files"]],
        )


def _trim(con: sqlite3.Connection) -> None:
    """Drop all but the newest HISTORY_COMMIT_LIMIT commits."""
    row = con.execute(
        "select id from commits order by id desc limit 1 offset ?", (HISTORY_COMMIT_LIMIT,)
    ).fetchone()
    if row is None:
        return
    cutoff = row[0]
    con.execute("delete from commit_files where commit_id <= ?", (cutoff,))
    con.execute("delete from commits_fts where rowid <= ?", (cutoff,))
    con.execute("delete from commits where id <= ?", (cutoff,))


def update_history(con: sqlite3.Connection, root: Path) -> dict[str, Any]:
    """Bring the commit tables up to date with root's HEAD.

    Adds only the commits since the indexed HEAD when that HEAD is still
    an ancestor. Otherwise it rebuilds: a rewritten or rebased history,
    or no history yet. The caller holds the index lock, since this
    writes the index.
    """
    head, unavailable, shallow = _source(root)
    if unavailable is not None:
        _clear(con)
        _set_state(con, None, False, unavailable)
        con.commit()
        return {"status": "unavailable", "reason": unavailable, "shallow": False, "added": 0}
    stored_head, stored_shallow = _state(con)
    if stored_head == head and stored_shallow == shallow:
        return {"status": "current", "reason": None, "shallow": shallow, "added": 0}
    incremental = stored_head is not None and _is_ancestor(root, stored_head, head)
    commits = _read_log(root, head, stored_head if incremental else None)
    if commits is None:
        # The stored head stays, so the next run tries again.
        return {
            "status": "error",
            "reason": "git log failed while reading commit history",
            "shallow": shallow,
            "added": 0,
        }
    if not incremental:
        _clear(con)
    _append(con, commits)
    _trim(con)
    _set_state(con, head, shallow, None)
    con.commit()
    return {
        "status": "updated" if incremental else "rebuilt",
        "reason": None,
        "shallow": shallow,
        "added": len(commits),
    }


def read_history(con: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
    """The commits matching query, best first, newer first on a tie."""
    match = " OR ".join(f'"{term}"*' for term in _query_terms(query) if term)
    if not match:
        return []
    rows = con.execute(
        "select c.id, c.sha, c.date, c.subject from commits_fts "
        "join commits c on c.id = commits_fts.rowid "
        "where commits_fts match ? order by bm25(commits_fts), c.id desc limit ?",
        (match, limit),
    ).fetchall()
    commits = []
    for commit_id, sha, date, subject in rows:
        files = con.execute(
            "select status, path, old_path from commit_files where commit_id = ? order by rowid",
            (commit_id,),
        ).fetchall()
        commits.append(
            {
                "sha": sha,
                "date": date[:10],
                "subject": subject,
                "paths": [
                    {
                        "status": "renamed" if status == "R" else status_word(status),
                        "path": path,
                        "oldPath": old_path,
                    }
                    for status, path, old_path in files
                ],
            }
        )
    return commits


def status_text(path: dict[str, Any]) -> str:
    """The status column: removed, added, renamed from, or changed."""
    if path["status"] == "renamed":
        return f"renamed from {plain(path['oldPath'])}"
    return path["status"]
