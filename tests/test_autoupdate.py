from __future__ import annotations

import os
import shutil
import subprocess
import time
from unittest.mock import patch

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry.autoupdate import (
    git_hooks_install,
    git_hooks_status,
    git_hooks_uninstall,
    watch,
)
from mimry.storage import load_pointer


def test_watch_stops_when_pointer_becomes_none(tmp_path, monkeypatch):
    """Test that watch stops early when load_pointer returns None."""
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path / "repo")
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0

    call_count = [0]
    original_load_pointer = load_pointer

    def mock_load_pointer(root):
        call_count[0] += 1
        if call_count[0] == 1:
            return original_load_pointer(root)
        return None

    with patch("mimry.autoupdate.load_pointer", side_effect=mock_load_pointer):
        with patch(
            "mimry.autoupdate.index_freshness",
            side_effect=AssertionError("Should not call with None"),
        ):
            result = watch(repo, max_cycles=3, sleep=lambda _: None)

    assert result == 2


def test_watch_rejects_interval_below_minimum(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path / "repo")
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0

    with patch("mimry.autoupdate.ui"):
        result = watch(repo, interval=0.1)
    assert result == 2


def test_watch_rejects_uninitialized_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("mimry.autoupdate.ui"):
        result = watch(repo)
    assert result == 2


def test_watch_new_file_indexed_after_settle(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path / "repo")
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0
    assert run_cli(repo, tmp_path / "cache", "index").returncode == 0

    ptr = load_pointer(repo)
    assert ptr is not None

    sleep_count = [0]
    edit_on_call = [2, 4]

    def mock_sleep(duration):
        sleep_count[0] += 1
        if sleep_count[0] in edit_on_call:
            (repo / f"new_{sleep_count[0]}.py").write_text("# new\n")

    result = watch(repo, interval=0.5, sleep=mock_sleep, max_cycles=10)
    assert result == 0


def test_watch_second_edit_triggers_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path / "repo")
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0
    assert run_cli(repo, tmp_path / "cache", "index").returncode == 0

    ptr = load_pointer(repo)
    assert ptr is not None

    sleep_count = [0]

    def mock_sleep(duration):
        sleep_count[0] += 1
        if sleep_count[0] == 2:
            (repo / "file1.py").write_text("# edit 1\n")
        elif sleep_count[0] == 4:
            (repo / "file1.py").write_text("# edit 2\n")

    result = watch(repo, interval=0.5, sleep=mock_sleep, max_cycles=10)

    assert result == 0


def test_watch_unstable_changes_never_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path / "repo")
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0
    assert run_cli(repo, tmp_path / "cache", "index").returncode == 0

    sleep_count = [0]

    def mock_sleep(duration):
        sleep_count[0] += 1
        (repo / "volatile.py").write_text(f"# version {sleep_count[0]}\n")

    result = watch(repo, interval=0.5, sleep=mock_sleep, max_cycles=5)

    assert result == 0


def test_watch_keyboard_interrupt_exits_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = copy_fixture(tmp_path / "repo")
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0
    assert run_cli(repo, tmp_path / "cache", "index").returncode == 0

    interrupt_on_call = [0]

    def mock_sleep(duration):
        interrupt_on_call[0] += 1
        if interrupt_on_call[0] == 1:
            raise KeyboardInterrupt()

    result = watch(repo, interval=0.5, sleep=mock_sleep)

    assert result == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_install_creates_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    result = git_hooks_install(repo)
    assert result == 0

    hooks_dir = repo / ".git" / "hooks"
    assert (hooks_dir / "post-commit").exists()
    assert (hooks_dir / "post-merge").exists()
    assert (hooks_dir / "post-rewrite").exists()
    assert (hooks_dir / "post-checkout").exists()

    for hook_name in ("post-commit", "post-merge", "post-rewrite", "post-checkout"):
        hook_path = hooks_dir / hook_name
        content = hook_path.read_bytes()
        assert b"\r" not in content, f"{hook_name} has CRLF line endings"
        text = content.decode("utf-8")
        assert "# >>> mimry auto-update >>>" in text
        assert "refresh" in text
        assert "# <<< mimry auto-update <<<" in text


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_preserves_existing_byte_for_byte(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_file = hooks_dir / "post-commit"
    original = b"#!/bin/sh\necho mine\n"
    hook_file.write_bytes(original)
    hook_file.chmod(0o755)

    result = git_hooks_install(repo)
    assert result == 0

    result = git_hooks_uninstall(repo)
    assert result == 0

    content = hook_file.read_bytes()
    assert content == original


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_install_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    result = git_hooks_install(repo)
    assert result == 0
    hooks_dir = repo / ".git" / "hooks"
    first_content = (hooks_dir / "post-commit").read_bytes()

    result = git_hooks_install(repo)
    assert result == 0
    second_content = (hooks_dir / "post-commit").read_bytes()

    assert first_content == second_content
    assert first_content.count(b"# >>> mimry auto-update >>>") == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_linked_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    subprocess.run(
        ["git", "worktree", "add", "../worktree", "-b", "test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    worktree = tmp_path / "worktree"
    result = git_hooks_install(worktree)
    assert result == 0

    common_hooks = repo / ".git" / "hooks"
    assert (common_hooks / "post-commit").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_skip_python_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_file = hooks_dir / "post-commit"
    hook_file.write_text("#!/usr/bin/env python\nprint('hello')\n")
    hook_file.chmod(0o755)

    result = git_hooks_install(repo)
    assert result == 0

    content = hook_file.read_text()
    assert "# >>> mimry auto-update >>>" not in content
    assert "print('hello')" in content


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_refuse_non_git_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()

    result = git_hooks_install(repo)
    assert result == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_refuse_managed_hooks_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    husky_dir = repo / ".husky"
    husky_dir.mkdir()

    subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", ".husky"],
        capture_output=True,
        check=True,
    )

    result = git_hooks_install(repo)
    assert result == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_status(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    result = git_hooks_status(repo)
    assert result == 0

    result = git_hooks_install(repo)
    assert result == 0

    result = git_hooks_status(repo)
    assert result == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_e2e_commit_triggers_refresh(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache_dir))

    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    assert run_cli(repo, cache_dir, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache_dir, "index").returncode == 0

    result = git_hooks_install(repo)
    assert result == 0

    (repo / "test.py").write_text("# test\n")
    subprocess.run(["git", "add", "test.py"], cwd=repo, capture_output=True, check=True)

    start = time.time()
    result = subprocess.run(
        ["git", "commit", "-m", "test"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "MIMRY_CACHE_HOME": str(cache_dir)},
    )
    elapsed = time.time() - start

    assert result.returncode == 0, result.stderr
    assert elapsed < 5, f"commit took {elapsed:.1f}s, hook must not block"

    start_time = time.time()
    while time.time() - start_time < 60:
        from mimry.freshness import index_freshness
        from mimry.storage import load_pointer as lp

        ptr = lp(repo)
        if ptr:
            fresh = index_freshness(repo, ptr)
            if fresh["state"] == "current":
                break
        time.sleep(0.5)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_hooks_preserves_partial_hook(tmp_path, monkeypatch):
    """Test partial hooks without end marker are preserved."""
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_file = hooks_dir / "post-commit"
    original = b"#!/bin/sh\n# >>> mimry auto-update >>>\necho half\n"
    hook_file.write_bytes(original)
    hook_file.chmod(0o755)

    result = git_hooks_install(repo)
    assert result == 0

    # Verify post-commit is unchanged
    content = hook_file.read_bytes()
    assert content == original, "post-commit hook should be unchanged"

    # Verify other three hooks were installed
    for hook_name in ("post-merge", "post-rewrite", "post-checkout"):
        hook_path = hooks_dir / hook_name
        assert hook_path.exists(), f"{hook_name} should exist"
        hook_content = hook_path.read_text()
        assert "# >>> mimry auto-update >>>" in hook_content
        assert "# <<< mimry auto-update <<<" in hook_content
