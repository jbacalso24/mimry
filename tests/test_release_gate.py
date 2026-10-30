"""Unit checks for the release gate's helpers (#624).

The gate is a script under scripts/, so it is loaded by path. These
cover the two helpers that decide which tool folder and which version a
check looks at.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

GATE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_gate.py"


def _load_gate():
    name = "release_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def test_isolated_env_drops_inherited_uv_tool_folders(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UV_TOOL_DIR", "D:/runner/uv-tool-dir")
    monkeypatch.setenv("UV_TOOL_BIN_DIR", "D:/runner/uv-tool-bin")
    monkeypatch.setenv("UV_TOOL_FOO", "inherited")
    home = tmp_path / "home"

    env = gate.isolated_env(home)

    assert env["UV_TOOL_DIR"] == str(home / "uv-tools")
    assert env["UV_TOOL_BIN_DIR"] == str(home / "uv-bin")
    assert "UV_TOOL_FOO" not in env


def test_isolated_env_keeps_the_shared_uv_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UV_CACHE_DIR", "D:/runner/uv-cache")

    env = gate.isolated_env(tmp_path / "home")

    assert env["UV_CACHE_DIR"] == "D:/runner/uv-cache"


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("mimry-0.8.5-py3-none-any.whl", "0.8.5"),
        ("mimry-0.8.4-py3-none-any.whl", "0.8.4"),
    ],
)
def test_wheel_version_reads_the_version_from_the_file_name(name: str, version: str):
    assert gate.wheel_version(Path("dist") / name) == version


def test_wheel_version_refuses_a_name_that_is_not_a_mimry_wheel():
    with pytest.raises(ValueError):
        gate.wheel_version(Path("dist") / "mimry-0.8.5.tar.gz")
