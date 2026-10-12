from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_mimry_cli import _row, copy_fixture, run_cli

from mimry.feedback import parse_context_suggested_paths
from mimry.paths import context_file
from mimry.usage import log_path, log_preflight, read_log, usage_stats

DAY = 86400
NOW = datetime(2026, 10, 1, tzinfo=UTC).timestamp()
OLD = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo: Path, *args: str, at: float | None = None) -> str:
    env = os.environ.copy()
    if at is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{int(at)} +0000"
    res = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "MIMRY Test")
    _git(path, "config", "user.email", "test@example.com")
    return path


def _commit(repo: Path, files: dict[str, str], at: float) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change", at=at)


def _log(root: Path, *entries: tuple[float, list[str]]) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for at, top in entries:
            handle.write(json.dumps({"at": at, "top": top}) + "\n")


def test_preflights_are_counted_only_inside_the_window(tmp_path):
    _log(
        tmp_path,
        (NOW - 2 * DAY, ["a.py"]),
        (NOW - 5 * DAY, ["b.py"]),
        (NOW - 40 * DAY, ["c.py"]),
    )
    stats = usage_stats(tmp_path, now=NOW)
    assert stats["days"] == 30
    assert stats["preflights"] == 2


@requires_git
def test_commits_are_matched_to_preflights_in_their_window(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, {"a.py": "1"}, NOW - 20 * DAY)
    _commit(repo, {"b.py": "1"}, NOW - 18 * DAY)
    _commit(repo, {"c.py": "1"}, NOW - 16 * DAY)
    _commit(repo, {"d.py": "1"}, NOW - 14 * DAY)
    # Written after the commits so the log does not affect them.
    _log(
        repo,
        (NOW - 18 * DAY - 3600, ["b.py", "x.py"]),  # in B's window, top five hits B
        (NOW - 16 * DAY - 3600, ["zzz.py"]),  # in C's window, top five misses C
    )
    stats = usage_stats(repo, now=NOW)
    assert stats["commits"] == 4
    assert stats["commits_with_preflight"] == 2
    assert stats["top_five_hits"] == 1


@requires_git
def test_same_second_commits_only_the_first_after_the_preflight_counts(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    t = NOW - 5 * DAY
    _commit(repo, {"parent.py": "1"}, t - 10)
    _commit(repo, {"first.py": "1"}, t)
    _commit(repo, {"second.py": "1"}, t)
    # Preflight at T+0.4 is after the first commit's parent (T-10), but
    # not surely after the second commit's parent (the first commit, T).
    _log(repo, (t + 0.4, ["first.py"]))
    stats = usage_stats(repo, now=NOW)
    assert stats["commits"] == 3
    assert stats["commits_with_preflight"] == 1
    assert stats["top_five_hits"] == 1


@requires_git
def test_merge_commits_are_not_counted(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, {"main.py": "1"}, NOW - 20 * DAY)
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, {"feat.py": "1"}, NOW - 19 * DAY)
    _git(repo, "checkout", "-q", "-")
    _commit(repo, {"main2.py": "1"}, NOW - 18 * DAY)
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feature", at=NOW - 17 * DAY)
    assert _git(repo, "rev-list", "--merges", "--count", "HEAD") == "1"
    assert usage_stats(repo, now=NOW)["commits"] == 3


@requires_git
def test_changes_outside_a_subdirectory_root_are_dropped(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, {"sub/a.py": "1"}, NOW - 20 * DAY)
    _commit(repo, {"top.py": "1"}, NOW - 19 * DAY)
    assert usage_stats(repo / "sub", now=NOW)["commits"] == 1


def test_not_a_git_repo_has_no_commit_numbers_and_skips_bad_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "plain"
    root.mkdir()
    _log(root, (NOW - DAY, ["a.py"]))
    with log_path(root).open("a", encoding="utf-8") as handle:
        handle.write('{not json\n[1, 2]\n{"at": "soon", "top": []}\n\n')
    stats = usage_stats(root, now=NOW)
    assert stats["commits"] == 0
    assert stats["commits_with_preflight"] is None
    assert stats["top_five_hits"] is None
    assert stats["preflights"] == 1
    assert [e["top"] for e in read_log(root)] == [["a.py"]]


def test_log_preflight_keeps_five_paths_and_no_other_fields(tmp_path):
    root = tmp_path / "repo"
    rows = [{"path": f"f{i}.py", "why": "secret phrase"} for i in range(7)]
    log_preflight(root, rows)
    text = log_path(root).read_text(encoding="utf-8")
    assert "secret phrase" not in text
    [entry] = read_log(root)
    assert set(entry) == {"at", "top"}
    assert entry["top"] == [f"f{i}.py" for i in range(5)]


@pytest.mark.parametrize("blocker", ["log_is_a_directory", "mimry_dir_is_a_file"])
def test_unwritable_log_does_not_raise(tmp_path, blocker):
    root = tmp_path / "repo"
    if blocker == "log_is_a_directory":
        log_path(root).mkdir(parents=True)
    else:
        root.mkdir()
        (root / ".mimry").write_text("not a folder")
    log_preflight(root, [{"path": "a.py"}])


def test_log_is_trimmed_to_ninety_days_once_it_passes_a_megabyte(tmp_path):
    root = tmp_path / "repo"
    old = time.time() - 200 * DAY
    line = json.dumps({"at": old, "top": ["a.py"] * 5}) + "\n"
    path = log_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(line * (1_000_000 // len(line) + 1), encoding="utf-8")
    log_preflight(root, [{"path": "new.py"}])
    [entry] = read_log(root)
    assert entry["top"] == ["new.py"]


@requires_git
def test_feedback_stats_prints_usage_through_the_cli(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = copy_fixture(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init", at=OLD)

    assert run_cli(repo, cache, "preflight", "fix auth session bug").returncode == 0
    suggested = parse_context_suggested_paths(context_file(repo))
    assert suggested
    picked = suggested[0]
    (repo / picked).write_text((repo / picked).read_text() + "\n# touched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat")

    stats = run_cli(repo, cache, "feedback", "stats")
    assert stats.returncode == 0, stats.stderr
    assert "Last 30 days" in stats.stdout
    assert _row(stats.stdout, "Preflights") == "1"
    assert "1 of 1 had a preflight first (100%)" in stats.stdout
    assert "held a changed file in 1 of 1 (100%)" in stats.stdout
