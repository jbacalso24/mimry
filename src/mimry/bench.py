"""Measure MIMRY on a repository's own recent merged pull requests.

`mimry bench` replays the last N merged pull requests of a repository.
A task is one pull request: its title (plus the first paragraph of the
body, for a squash commit) is the query, and the files it added or
modified are the gold set.  The checkout is the commit's base, so the
index never sees the change it is asked to find.

Everything runs locally.  It reads git history, works in a throwaway
worktree with its own MIMRY cache, and makes no network or model calls.
The user's working tree and index are never touched.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from . import ui
from .benchmark import _env, _paths, _run, percentile, recall_at_k, token_proxy
from .config_manifest_adapter import CONFIG_FILENAMES

DEFAULT_TASKS = 20
MAX_FILES = 30
READ_LINES = 2000
TOP = 10
INDEX_TIMEOUT = 300
LOG_FORMAT = "%H%x1f%P%x1f%s%x1f%b%x1e"
RECORD_END = "\x1e"
FIELD_SEP = "\x1f"
SQUASH_SUBJECT = re.compile(r"^(?P<title>.+) \(#\d+\)$")
LOCK_FILES = frozenset(
    {
        "uv.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Cargo.lock",
        "go.sum",
        "bun.lock",
        "bun.lockb",
    }
)
# Release, version-bump and dependency-bot commits, matched on the
# subject without its " (#N)" suffix.  A version number is required
# after "release" or "bump", so "Bump the cache" is still measured.
RELEASE_OR_BUMP_SUBJECT = re.compile(
    r"^(?:(?:prepare )?release(?: version)? v?\d|bump(?: version)?(?: to)? v?\d"
    r"|chore\(release\)"
    r"|v?\d+\.\d+\.\d+$"
    r"|(?:bump|update|upgrade) .* (?:from|to) v?\d+(?:\.\d+)+"
    r"|(?:build|chore)\(deps)",
    re.IGNORECASE,
)
SKIP_TOO_BROAD = f"touching more than {MAX_FILES} files"
SKIP_NO_MODIFIED = "only added files"
SKIP_RELEASE = "release or bump commit"
SKIP_MANIFESTS_ONLY = "changed only manifests or lock files"
REMOVE_ATTEMPTS = 5
REMOVE_PAUSE_SECONDS = 0.5
# A commit bullet or trailer: the only lines a squash body may hold
# when its first paragraph says nothing about the change.
BULLET_OR_TRAILER = re.compile(r"^(?:[*-]\s|(?:co-authored-by|signed-off-by):)", re.IGNORECASE)
NO_CANDIDATES = (
    "No qualifying commits: mimry bench looks for merge commits or squash commits"
    ' whose subject ends with "(#N)" on the first-parent history of HEAD.'
)
CAVEATS = (
    'Changed files stand in for "the files the task needed".',
    "Tokens are an estimate (4 bytes per token) of reading each file's first 2,000 lines"
    " in MIMRY's order.",
    "Files the task added are not counted: MIMRY cannot rank them from the base commit.",
)


class BenchError(RuntimeError):
    """A condition that stops the benchmark."""


@dataclass(frozen=True)
class Task:
    commit: str
    base: str
    query: str
    gold: tuple[str, ...]


@dataclass
class Selection:
    tasks: list[Task] = field(default_factory=list)  # newest first
    candidates: int = 0
    passed_over: Counter[str] = field(default_factory=Counter)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise BenchError(f"git {args[0]} failed: {detail}")
    return result.stdout


def _repo_top(root: Path) -> Path:
    try:
        return Path(_git(root, "rev-parse", "--show-toplevel").strip())
    except BenchError as exc:
        raise BenchError(f"{root} is not inside a git repository") from exc


def _first_parent_log(repo: Path) -> Iterator[str]:
    """Commit records on HEAD's first-parent chain, newest first.

    Read lazily, so a small --tasks stops git early on a long history.
    """
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "log", "--first-parent", f"--format={LOG_FORMAT}", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    try:
        pending = ""
        while chunk := proc.stdout.read(1 << 16):
            *records, pending = (pending + chunk).split(RECORD_END)
            yield from records
        yield pending
        if proc.wait():
            raise BenchError("could not read HEAD's history (is HEAD a commit?)")
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _task_query(parents: list[str], subject: str, body: str) -> str | None:
    """The query for a pull-request commit, or None when it is not one.

    A merge commit's query is the merged branch's title: the first
    non-empty body line, else the subject.  A squash commit's query is
    its subject without " (#N)", plus the first paragraph of its body.
    """
    text = body.replace("\r\n", "\n").strip()
    if len(parents) > 1:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[0] if lines else subject
    match = SQUASH_SUBJECT.match(subject)
    if len(parents) != 1 or not match:
        return None
    paragraph = text.split("\n\n", 1)[0]
    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    if lines and all(BULLET_OR_TRAILER.match(line) for line in lines):
        paragraph = ""
    return " ".join(f"{match.group('title')} {paragraph}".split())


def _excluded(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("CHANGELOG") or name in LOCK_FILES


def _manifest_or_lock(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in LOCK_FILES or name in CONFIG_FILENAMES


def _release_subject(subject: str) -> bool:
    match = SQUASH_SUBJECT.match(subject)
    return RELEASE_OR_BUMP_SUBJECT.match(match.group("title") if match else subject) is not None


def _names(output: str) -> list[str]:
    return [name for name in output.split("\0") if name]


def _build_task(repo: Path, commit: str, base: str, query: str) -> tuple[Task | None, str]:
    # --no-renames: a renamed file counts as its new path being added.
    changed = _names(_git(repo, "diff", "--name-only", "--no-renames", "-z", base, commit))
    if len(changed) > MAX_FILES:
        return None, SKIP_TOO_BROAD
    if changed and all(_manifest_or_lock(name) for name in changed):
        return None, SKIP_MANIFESTS_ONLY
    # Added files are absent at the base, so MIMRY cannot rank them.
    modified = _names(
        _git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=M", "-z", base, commit)
    )
    gold = tuple(name for name in modified if not _excluded(name))
    if not gold:
        return None, SKIP_NO_MODIFIED
    return Task(commit=commit, base=base, query=query, gold=gold), ""


def select_tasks(repo: Path, wanted: int) -> Selection:
    """The newest `wanted` pull requests that make usable tasks.

    Commits that look like pull requests but cannot be a task are
    counted as passed over, with the reason.  Plain commits are ignored.
    """
    selection = Selection()
    for record in _first_parent_log(repo):
        # Not strip(): it would also eat the \x1f of an empty body.
        parts = record.lstrip("\n").split(FIELD_SEP, 3)
        if len(parts) != 4:
            continue
        commit, parents_raw, subject, body = parts
        parents = parents_raw.split()
        query = _task_query(parents, subject, body)
        if query is None:
            continue
        selection.candidates += 1
        if _release_subject(subject):
            selection.passed_over[SKIP_RELEASE] += 1
            continue
        task, reason = _build_task(repo, commit, parents[0], query)
        if task is None:
            selection.passed_over[reason] += 1
            continue
        selection.tasks.append(task)
        if len(selection.tasks) == wanted:
            break
    return selection


def _read_tokens(repo: Path) -> Callable[[str], int]:
    """Charge for reading a file the way an agent's file tool does."""

    def charge(rel: str) -> int:
        path = repo / rel
        if not path.is_file():
            return 0
        text = path.read_bytes().decode("utf-8", errors="replace")
        return token_proxy("".join(text.splitlines(keepends=True)[:READ_LINES]))

    return charge


def score_task(
    ranked: list[str], gold: tuple[str, ...] | list[str], charge: Callable[[str], int]
) -> dict[str, Any]:
    """Score one ranked list. Pure: `charge` supplies the read cost.

    Tokens are charged for every file up to the last gold file in the
    top 10, in rank order, and are None when no gold file is in it.
    """
    top = ranked[:TOP]
    gold_ranks = {path: (top.index(path) + 1 if path in top else None) for path in gold}
    found = sorted(rank for rank in gold_ranks.values() if rank is not None)
    grades = dict.fromkeys(gold, 3)
    return {
        "first_gold_rank": found[0] if found else None,
        "gold_ranks": gold_ranks,
        "recall_at_5": recall_at_k(ranked, grades, 5),
        "recall_at_10": recall_at_k(ranked, grades, TOP),
        "tokens": sum(charge(path) for path in top[: found[-1]]) if found else None,
    }


def _measure(checkout: Path, env: dict[str, str], task: Task) -> dict[str, Any]:
    _git(checkout, "checkout", "-q", "-f", "--detach", task.base)
    _run(checkout, env, "index", timeout=INDEX_TIMEOUT)
    stdout, _, find_ms = _run(checkout, env, "find", "--limit", str(TOP), "--", task.query)
    ranked = _paths(stdout)
    score = score_task(ranked, task.gold, _read_tokens(checkout))
    return {
        "commit": task.commit,
        "base": task.base,
        "query": task.query,
        "files": len(task.gold),
        "gold": list(task.gold),
        "ranked": ranked[:TOP],
        "find_ms": round(find_ms, 1),
        **score,
    }


def _new_sandbox() -> Path:
    """A fresh temp folder with the default ACL.

    Not tempfile.mkdtemp: its 0o700 mode becomes an owner-only ACL on
    Windows, and a leftover folder with that ACL cannot be deleted.
    """
    while True:
        path = Path(tempfile.gettempdir()) / f"mimry-bench-{secrets.token_hex(4)}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path


def _clear_read_only(func, path, _exc) -> None:
    """rmtree error handler: git objects are read-only on Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


# Python 3.12 renamed the rmtree error callback's keyword.
_RMTREE_ERROR_ARG = "onexc" if sys.version_info >= (3, 12) else "onerror"


def _remove_tree(path: Path) -> bool:
    """Delete `path`, retrying while a finished process holds files.

    Return True when the folder is gone.
    """
    for _ in range(REMOVE_ATTEMPTS):
        with contextlib.suppress(OSError):
            shutil.rmtree(path, **{_RMTREE_ERROR_ARG: _clear_read_only})
        if not path.exists():
            return True
        time.sleep(REMOVE_PAUSE_SECONDS)
    return False


def _delete_sandbox(path: Path) -> None:
    if not _remove_tree(path):
        print(f"warning: could not delete {path}; delete it by hand", file=sys.stderr)


def _remove_checkout(repo: Path, checkout: Path, sandbox: Path) -> None:
    git_failed = False
    try:
        _git(repo, "worktree", "remove", "--force", str(checkout))
    except BenchError as exc:
        git_failed = True
        print(f"warning: git could not remove the worktree: {exc}", file=sys.stderr)
    _delete_sandbox(sandbox)
    if git_failed:
        # Drop the admin entry left behind by a removal that failed.
        with contextlib.suppress(BenchError):
            _git(repo, "worktree", "prune")


def _run_tasks(repo: Path, tasks: list[Task], *, keep: bool) -> list[dict[str, Any]]:
    """Run oldest-first `tasks` in one detached, isolated worktree."""
    sandbox = _new_sandbox()
    checkout = sandbox / "repo"
    added = False
    try:
        _git(repo, "worktree", "add", "--detach", str(checkout), tasks[0].base)
        added = True
        env = _env(sandbox)
        _run(checkout, env, "init", "--skip-graph")
        results = []
        for number, task in enumerate(tasks, start=1):
            print(f"[{number}/{len(tasks)}] {ui.shorten(task.query, 60)}", file=sys.stderr)
            results.append(_measure(checkout, env, task))
        return results
    finally:
        if keep and added:
            print(
                f"Kept the checkout at {checkout}. Remove it with:"
                f" git -C {repo} worktree remove --force {checkout}",
                file=sys.stderr,
            )
        elif added:
            _remove_checkout(repo, checkout, sandbox)
        else:
            _delete_sandbox(sandbox)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(results: list[dict[str, Any]], passed_over: Counter[str]) -> dict[str, Any]:
    count = len(results)
    located = [r for r in results if r["first_gold_rank"] is not None]
    finds = [r["find_ms"] for r in results]
    total = sum(passed_over.values())
    return {
        "tasks": count,
        "passed_over": total,
        "passed_over_reasons": dict(passed_over),
        # The same counts under the names they had before "passed over".
        "skipped": total,
        "skipped_reasons": dict(passed_over),
        "located": len(located),
        "mean_recall_at_5": _mean([r["recall_at_5"] for r in results]),
        "mean_recall_at_10": _mean([r["recall_at_10"] for r in results]),
        "median_first_gold_rank": median([r["first_gold_rank"] for r in located])
        if located
        else None,
        "share_gold_at_rank_1": (
            sum(r["first_gold_rank"] == 1 for r in results) / count if count else None
        ),
        "median_tokens_located": median([r["tokens"] for r in located]) if located else None,
        "median_find_ms": median(finds) if finds else None,
        "p95_find_ms": percentile(finds, 0.95) if finds else None,
    }


def _cell(value: Any, fmt: str = "{}") -> str:
    return "-" if value is None else fmt.format(value)


def _render(repo: Path, results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    ui.ok(f"Replayed {summary['tasks']} merged pull requests from {repo.name}")
    rows = [("Task", "Files", "First gold", "Recall@5", "Tokens", "find ms")]
    for r in results:
        rows.append(
            (
                ui.shorten(r["query"], 48),
                str(r["files"]),
                _cell(r["first_gold_rank"]),
                f"{r['recall_at_5']:.2f}",
                _cell(r["tokens"], "{:,}"),
                f"{r['find_ms']:,.0f}",
            )
        )
    ui.table(rows)
    reasons = ", ".join(f"{n} {reason}" for reason, n in summary["passed_over_reasons"].items())
    passed_over = ui.count(summary["passed_over"], "commit") + " while selecting"
    summary_rows = [
        ("tasks", str(summary["tasks"])),
        ("passed over", passed_over + (f" ({reasons})" if reasons else "")),
        ("mean recall@5", _cell(summary["mean_recall_at_5"], "{:.2f}")),
        ("mean recall@10", _cell(summary["mean_recall_at_10"], "{:.2f}")),
        ("median first gold rank", _cell(summary["median_first_gold_rank"], "{:g}")),
        (
            "gold file at rank 1",
            _cell(summary["share_gold_at_rank_1"], "{:.0%}"),
        ),
        ("median tokens (located)", _cell(summary["median_tokens_located"], "{:,.0f}")),
        (
            "median find time (process start included)",
            _cell(summary["median_find_ms"], "{:,.0f} ms"),
        ),
        ("p95 find time (process start included)", _cell(summary["p95_find_ms"], "{:,.0f} ms")),
    ]
    print()
    ui.title("Summary")
    ui.table(summary_rows)
    print()
    for caveat in CAVEATS:
        print(caveat)


def run_bench(
    root: Path, *, tasks: int = DEFAULT_TASKS, as_json: bool = False, keep: bool = False
) -> int:
    """Benchmark the repo that holds `root`. Returns the exit code."""
    try:
        repo = _repo_top(root)
        selection = select_tasks(repo, tasks)
        if not selection.tasks:
            if selection.candidates:
                note = (
                    f"No task left: all {selection.candidates} candidate commits were passed over."
                )
            else:
                note = NO_CANDIDATES
            if as_json:
                payload = {
                    "tasks": [],
                    "summary": summarize([], selection.passed_over),
                    "note": note,
                }
                print(json.dumps(payload, indent=2))
            else:
                print(note)
            return 0
        # Selection is newest first; start the checkout at the oldest.
        results = _run_tasks(repo, list(reversed(selection.tasks)), keep=keep)
    except (RuntimeError, OSError) as exc:
        ui.fail("mimry bench stopped", str(exc))
        return 2
    summary = summarize(results, selection.passed_over)
    if as_json:
        print(json.dumps({"tasks": results, "summary": summary}, indent=2))
    else:
        _render(repo, results, summary)
    return 0
