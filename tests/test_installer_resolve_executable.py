"""`_resolve_executable` prefers the copy installed with the running environment.

An install from a venv must register the `mimry-mcp` it installed, not whichever
older copy is first on PATH (issue #505: a 0.7.0 venv install registered the
0.6.4 uv-tool `mimry-mcp`). These tests drive the resolver with a fake
interpreter directory and a fake PATH, no install needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mimry import installer


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """A venv-like interpreter dir and a PATH dir, both on the real layout."""
    bin_dir = tmp_path / "venv-bin"
    path_dir = tmp_path / "path-bin"
    bin_dir.mkdir()
    path_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setenv("PATH", str(path_dir) + os.pathsep + os.environ.get("PATH", ""))
    return bin_dir, path_dir


def _chmod_x(path):
    path.chmod(0o755)


def test_sibling_of_interpreter_wins_over_path(fake_env):
    bin_dir, path_dir = fake_env
    sibling = bin_dir / "mimry-mcp"
    sibling.write_text("#!/bin/sh\n")
    _chmod_x(sibling)
    other = path_dir / "mimry-mcp"
    other.write_text("#!/bin/sh\n")
    _chmod_x(other)

    assert installer._resolve_executable("mimry-mcp") == str(sibling)


def test_warns_when_path_names_a_different_file(fake_env, capsys):
    bin_dir, path_dir = fake_env
    sibling = bin_dir / "mimry-mcp"
    sibling.write_text("#!/bin/sh\n")
    other = path_dir / "mimry-mcp"
    other.write_text("#!/bin/sh\n")
    _chmod_x(other)

    installer._resolve_executable("mimry-mcp")

    out = capsys.readouterr().out
    assert "mimry-mcp" in out
    assert str(other) in out
    assert str(sibling) in out


def test_no_warning_when_path_names_the_same_file(fake_env, capsys, monkeypatch):
    bin_dir, path_dir = fake_env
    sibling = bin_dir / "mimry-mcp"
    sibling.write_text("#!/bin/sh\n")
    # A PATH entry that is a symlink to the sibling names the same file.
    link = path_dir / "mimry-mcp"
    link.symlink_to(sibling)
    monkeypatch.setattr("shutil.which", lambda name: str(link))

    resolved = installer._resolve_executable("mimry-mcp")

    assert resolved == str(sibling)
    assert "different" not in capsys.readouterr().out


def test_falls_back_to_path_when_no_sibling_exists(fake_env):
    bin_dir, path_dir = fake_env
    other = path_dir / "mimry-mcp"
    other.write_text("#!/bin/sh\n")
    _chmod_x(other)

    assert installer._resolve_executable("mimry-mcp") == str(other)


def test_returns_bare_name_when_nothing_exists(fake_env):
    assert installer._resolve_executable("mimry-mcp") == "mimry-mcp"
