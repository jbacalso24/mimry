from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mimry.scanner import scan_entries

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q")
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(
        root,
        "-c",
        "user.name=Developer",
        "-c",
        "user.email=developer@example.test",
        "commit",
        "-qm",
        "Initial source",
    )
    return root


@pytest.mark.parametrize("relative_marker", [False, True])
@pytest.mark.parametrize("probe_open", [False, True])
def test_nested_worktrees_are_pruned_but_can_be_scanned_as_the_root(
    tmp_path, relative_marker, probe_open
):
    repo = _repo(tmp_path / "repo")
    nested = repo / ".claude" / "worktrees" / "agent"
    _git(repo, "worktree", "add", "--detach", str(nested))
    if relative_marker:
        marker = nested / ".git"
        target = marker.read_text(encoding="utf-8").removeprefix("gitdir:").strip()
        marker.unlink()
        marker.write_text(f"gitdir: {os.path.relpath(target, nested)}\n", encoding="utf-8")

    paths = {name for name, _ in scan_entries(repo, probe_open=probe_open)}
    assert paths == {"app.py"}
    assert {name for name, _ in scan_entries(nested, probe_open=probe_open)} == {"app.py"}


def test_submodules_are_still_scanned(tmp_path):
    repo = _repo(tmp_path / "repo")
    module = _repo(tmp_path / "module")
    # A submodule's admin path may have a worktrees component.
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(module),
        "worktrees/module",
    )
    paths = {name for name, _ in scan_entries(repo)}
    assert "app.py" in paths
    assert "worktrees/module/app.py" in paths


def test_a_folder_named_worktrees_without_git_metadata_is_still_scanned(tmp_path):
    repo = _repo(tmp_path / "repo")
    ordinary = repo / "worktrees" / "notes"
    ordinary.mkdir(parents=True)
    (ordinary / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (ordinary / ".git").write_text("not a gitdir marker\n", encoding="utf-8")
    assert "worktrees/notes/notes.md" in {name for name, _ in scan_entries(repo)}
