"""`mimry install` registers the MCP server and hooks of the mimry that
runs the install, not whichever one is first on PATH (#505)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mimry import installer


def _script(folder: Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("", encoding="utf-8")
    return str(path)


@pytest.fixture
def venv_and_old(tmp_path, monkeypatch):
    """This install's mimry-mcp, and an older one on PATH."""
    venv = tmp_path / "venv" / "Scripts"
    own = _script(venv, "mimry-mcp")
    monkeypatch.setattr(sys, "executable", str(venv / "python"))
    old = _script(tmp_path / "old", "mimry-mcp")
    monkeypatch.setattr(installer, "which", lambda name: old if name == "mimry-mcp" else None)
    return {"own": own, "old": old, "venv": venv}


def test_registers_the_script_beside_the_running_interpreter(venv_and_old):
    """#505: an older mimry-mcp first on PATH does not win."""
    assert installer._resolve_executable("mimry-mcp") == venv_and_old["own"]


@pytest.mark.parametrize("bucket", ["archive-v0", "builds-v0"])
def test_path_wins_in_a_uv_cache_environment(tmp_path, monkeypatch, bucket):
    """A uvx environment can be deleted, so PATH stays first."""
    cached = tmp_path / "cache" / bucket / "abc" / "Scripts"
    _script(cached, "mimry-mcp")
    monkeypatch.setattr(sys, "executable", str(cached / "python"))
    old = _script(tmp_path / "old", "mimry-mcp")
    monkeypatch.setattr(installer, "which", lambda name: old)

    assert installer._resolve_executable("mimry-mcp") == old


def test_falls_back_to_path_then_bare_name(tmp_path, monkeypatch):
    """No script beside the interpreter: PATH, then the bare name."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "Scripts" / "python"))
    old = _script(tmp_path / "old", "mimry-mcp")

    monkeypatch.setattr(installer, "which", lambda name: old)
    assert installer._resolve_executable("mimry-mcp") == old

    monkeypatch.setattr(installer, "which", lambda name: None)
    assert installer._resolve_executable("mimry-mcp") == "mimry-mcp"


def test_warns_when_path_mimry_is_another_file(tmp_path, monkeypatch, capsys):
    """#505: one line naming both paths when PATH's mimry differs."""
    own = _script(tmp_path / "venv" / "Scripts", "mimry")
    old = _script(tmp_path / "old", "mimry")
    monkeypatch.setattr(installer, "_resolve_mimry_exe", lambda: own)
    monkeypatch.setattr(installer, "which", lambda name: old)

    installer._warn_if_path_differs()

    lines = [line for line in capsys.readouterr().out.splitlines() if "on PATH" in line]
    assert len(lines) == 1
    assert old in lines[0]
    assert own in lines[0]


def test_no_warning_when_path_mimry_is_this_install(tmp_path, monkeypatch, capsys):
    own = _script(tmp_path / "venv" / "Scripts", "mimry")
    monkeypatch.setattr(installer, "_resolve_mimry_exe", lambda: own)
    monkeypatch.setattr(installer, "which", lambda name: own)

    installer._warn_if_path_differs()

    assert "on PATH" not in capsys.readouterr().out
