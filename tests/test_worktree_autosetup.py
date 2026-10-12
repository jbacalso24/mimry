from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from mimry.mcp_server import mimry_find

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"
SETUP_LINE = "Set up MIMRY for this worktree"


def _mimry(cwd: Path, cache: Path, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path, cache: Path, *, set_up: bool) -> Path:
    """A committed git repo, with MIMRY set up and indexed if asked."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    if set_up:
        assert _mimry(repo, cache, "init", "--skip-graph").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    if set_up:
        assert _mimry(repo, cache, "index").returncode == 0
    return repo


def _worktree(repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "wt-branch", str(wt))
    return wt


def test_find_in_new_worktree_sets_mimry_up(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, set_up=True)
    wt = _worktree(repo, tmp_path)

    res = _mimry(wt, cache, "find", "auth session")

    assert res.returncode == 0, res.stderr
    assert SETUP_LINE in res.stdout
    assert "auth" in res.stdout.lower()
    assert (wt / ".mimry").is_dir()
    status = _mimry(wt, cache, "status")
    assert status.returncode == 0, status.stderr


def test_second_find_in_worktree_does_not_repeat_setup(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, set_up=True)
    wt = _worktree(repo, tmp_path)
    assert _mimry(wt, cache, "find", "auth session").returncode == 0

    res = _mimry(wt, cache, "find", "auth session")

    assert res.returncode == 0, res.stderr
    assert SETUP_LINE not in res.stdout


def test_worktree_copies_main_config(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, set_up=True)
    main_config = repo / ".mimry" / "config.toml"
    marker = "# local setting carried into worktrees\n"
    main_config.write_text(main_config.read_text(encoding="utf-8") + marker, encoding="utf-8")
    wt = _worktree(repo, tmp_path)

    res = _mimry(wt, cache, "find", "auth session")

    assert res.returncode == 0, res.stderr
    wt_config = (wt / ".mimry" / "config.toml").read_text(encoding="utf-8")
    assert wt_config == main_config.read_text(encoding="utf-8")
    assert marker in wt_config


def test_worktree_of_uninitialized_repo_stays_not_set_up(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, set_up=False)
    wt = _worktree(repo, tmp_path)

    res = _mimry(wt, cache, "find", "auth session")

    assert res.returncode != 0
    assert "MIMRY is not set up" in res.stderr
    assert not (wt / ".mimry" / "pointer.json").exists()


def test_mcp_find_in_new_worktree_reports_setup(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = _repo(tmp_path, cache, set_up=True)
    wt = _worktree(repo, tmp_path)

    payload = mimry_find("auth session", str(wt))
    again = mimry_find("auth session", str(wt))

    assert payload["set_up_worktree"] is True
    assert (wt / ".mimry" / "pointer.json").is_file()
    assert "set_up_worktree" not in again


def test_worktree_nested_in_its_main_checkout_gets_its_own_index(tmp_path):
    # Claude Code keeps agent worktrees under <repo>/.claude/worktrees/.
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, set_up=True)
    nested = repo / ".claude" / "worktrees" / "agent"
    _git(repo, "worktree", "add", "-q", "-b", "agent-branch", str(nested))

    res = _mimry(nested, cache, "find", "auth session")

    assert res.returncode == 0, res.stderr
    assert SETUP_LINE in res.stdout
    assert ".claude/worktrees" not in res.stdout
    assert (nested / ".mimry").is_dir()


def test_find_from_a_subfolder_of_a_new_worktree(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, set_up=True)
    wt = _worktree(repo, tmp_path)

    res = _mimry(wt / "src" / "auth", cache, "find", "auth session")

    assert res.returncode == 0, res.stderr
    assert SETUP_LINE in res.stdout
    assert "src/auth/" in res.stdout


def test_submodule_git_file_is_not_a_worktree_boundary(tmp_path):
    from mimry.commands import _linked_worktree_top

    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: C:/repo/.git/worktrees/linked\n", encoding="utf-8")

    assert not _linked_worktree_top(sub)
    assert _linked_worktree_top(linked)
    assert not _linked_worktree_top(tmp_path)
