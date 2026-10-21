"""`mimry install` registers the MCP server and hooks of the mimry that
runs the install, not whichever one is first on PATH (#505).

PATH's copy is kept when it runs the same version, which is how a uv
tool's trampoline behaves. The version probe is monkeypatched in every
test, so no real `mimry` is run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mimry import __version__, installer


def _script(folder: Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("", encoding="utf-8")
    return str(path)


@pytest.fixture
def two_installs(tmp_path, monkeypatch):
    """This venv's mimry beside sys.executable, and another on PATH."""
    venv = tmp_path / "venv" / "Scripts"
    own = {"mimry": _script(venv, "mimry"), "mimry-mcp": _script(venv, "mimry-mcp")}
    monkeypatch.setattr(sys, "executable", str(venv / "python"))
    other = tmp_path / "other"
    on_path = {"mimry": _script(other, "mimry"), "mimry-mcp": _script(other, "mimry-mcp")}
    monkeypatch.setattr(installer, "which", lambda name: on_path.get(name))
    monkeypatch.setattr(installer.shutil, "which", lambda name: on_path.get(name))
    return {"own": own, "on_path": on_path}


def _probe(monkeypatch, version):
    monkeypatch.setattr(installer, "_path_mimry_version", lambda: version)


def test_same_version_on_path_wins_and_hooks_stay_bare(two_installs, monkeypatch, capsys):
    """A uv tool trampoline: another file, same mimry, PATH wins."""
    _probe(monkeypatch, __version__)
    on_path = two_installs["on_path"]

    assert installer._resolve_executable("mimry-mcp") == on_path["mimry-mcp"]
    assert installer._hook_executable() == "mimry"
    installer._warn_if_path_differs()
    assert "on PATH" not in capsys.readouterr().out


def test_different_version_uses_the_script_beside_and_warns(two_installs, monkeypatch, capsys):
    """#505: an older mimry first on PATH is not registered."""
    _probe(monkeypatch, "0.6.4")
    own = two_installs["own"]
    on_path = two_installs["on_path"]

    assert installer._resolve_executable("mimry-mcp") == own["mimry-mcp"]
    assert installer._hook_executable() == installer._posix_executable()
    installer._warn_if_path_differs()
    lines = [line for line in capsys.readouterr().out.splitlines() if "on PATH" in line]
    assert len(lines) == 1
    assert "0.6.4" in lines[0]
    assert on_path["mimry"] in lines[0]
    assert __version__ in lines[0]
    assert own["mimry"] in lines[0]


def test_unreadable_version_uses_the_script_beside_and_warns(two_installs, monkeypatch, capsys):
    """A probe that fails counts as not the same version."""
    _probe(monkeypatch, None)
    own = two_installs["own"]

    assert installer._resolve_executable("mimry-mcp") == own["mimry-mcp"]
    installer._warn_if_path_differs()
    lines = [line for line in capsys.readouterr().out.splitlines() if "on PATH" in line]
    assert len(lines) == 1
    assert "an unknown version" in lines[0]
    assert own["mimry"] in lines[0]


def test_throwaway_env_keeps_path_and_skips_the_probe(two_installs, tmp_path, monkeypatch):
    """A uvx environment can be deleted, so PATH stays first."""
    cached = tmp_path / "cache" / "archive-v0" / "abc" / "Scripts"
    _script(cached, "mimry-mcp")
    monkeypatch.setattr(sys, "executable", str(cached / "python"))

    def no_probe():
        raise AssertionError("the version probe must not run in a throwaway env")

    monkeypatch.setattr(installer, "_path_mimry_version", no_probe)

    assert installer._resolve_executable("mimry-mcp") == two_installs["on_path"]["mimry-mcp"]


def _throwaway_env(tmp_path, monkeypatch):
    """Run from a uv cache environment, with its own mimry scripts."""
    cached = tmp_path / "cache" / "builds-v0" / "abc" / "Scripts"
    _script(cached, "mimry")
    _script(cached, "mimry-mcp")
    monkeypatch.setattr(sys, "executable", str(cached / "python"))


def test_throwaway_env_same_version_on_path_is_silent(two_installs, tmp_path, monkeypatch, capsys):
    """A same-version PATH copy in a throwaway env is kept, quietly."""
    _throwaway_env(tmp_path, monkeypatch)
    _probe(monkeypatch, __version__)
    on_path = two_installs["on_path"]

    assert installer._resolve_executable("mimry-mcp") == on_path["mimry-mcp"]
    installer._warn_if_path_differs()
    assert "on PATH" not in capsys.readouterr().out


def test_throwaway_env_older_path_copy_is_kept_and_not_warned(
    two_installs, tmp_path, monkeypatch, capsys
):
    """#525: keep PATH's older copy. #539: install_skill refuses it.

    So there is no warning to show here.
    """
    _throwaway_env(tmp_path, monkeypatch)
    _probe(monkeypatch, "0.6.4")
    on_path = two_installs["on_path"]

    assert installer._resolve_executable("mimry-mcp") == on_path["mimry-mcp"]
    installer._warn_if_path_differs()
    assert "on PATH" not in capsys.readouterr().out


def test_throwaway_env_without_path_copy_uses_the_script_beside_silently(
    two_installs, tmp_path, monkeypatch, capsys
):
    """Nothing on PATH: the throwaway script is used, silently."""
    _throwaway_env(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, "which", lambda name: None)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    cached = Path(sys.executable).parent

    assert installer._resolve_executable("mimry-mcp") == str(cached / "mimry-mcp")
    installer._warn_if_path_differs()
    assert "on PATH" not in capsys.readouterr().out


def test_no_sibling_uses_path_then_bare_name(tmp_path, monkeypatch):
    """No script beside the interpreter: PATH, then the bare name."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "Scripts" / "python"))
    on_path = _script(tmp_path / "other", "mimry-mcp")

    monkeypatch.setattr(installer, "which", lambda name: on_path)
    assert installer._resolve_executable("mimry-mcp") == on_path

    monkeypatch.setattr(installer, "which", lambda name: None)
    assert installer._resolve_executable("mimry-mcp") == "mimry-mcp"


@pytest.fixture
def fresh_probe(monkeypatch):
    """The real probe, with its cache emptied around the test."""
    installer._path_mimry_version.cache_clear()
    yield
    installer._path_mimry_version.cache_clear()


def _fake_run(monkeypatch, *, stdout="", returncode=0, raises=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(installer.subprocess, "run", run)
    return calls


def test_probe_parses_the_version_and_runs_once(fresh_probe, monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "which", lambda name: str(tmp_path / "mimry"))
    calls = _fake_run(monkeypatch, stdout="mimry 0.7.0\n")

    assert installer._path_mimry_version() == "0.7.0"
    assert installer._path_mimry_version() == "0.7.0"
    assert calls == [[str(tmp_path / "mimry"), "--version"]]


@pytest.mark.parametrize(
    "run_kwargs",
    [
        {"raises": subprocess.TimeoutExpired("mimry", 5)},
        {"raises": OSError("gone")},
        {"returncode": 1, "stdout": "mimry 0.7.0\n"},
        {"stdout": "something else entirely"},
    ],
)
def test_probe_failures_count_as_unknown(fresh_probe, monkeypatch, tmp_path, run_kwargs):
    monkeypatch.setattr(installer, "which", lambda name: str(tmp_path / "mimry"))
    _fake_run(monkeypatch, **run_kwargs)

    assert installer._path_mimry_version() is None


def test_probe_skips_the_run_when_mimry_is_not_on_path(fresh_probe, monkeypatch):
    monkeypatch.setattr(installer, "which", lambda name: None)
    calls = _fake_run(monkeypatch, stdout="mimry 0.7.0\n")

    assert installer._path_mimry_version() is None
    assert calls == []


@pytest.fixture
def user(tmp_path, monkeypatch):
    """A scratch profile and repo, so installs write nowhere real."""
    profile = tmp_path / "userprofile"
    profile.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(profile / "xdg"))
    monkeypatch.setattr(installer, "_home", lambda: profile)
    # The suite turns MCP registration off; these installs need it.
    monkeypatch.delenv("MIMRY_NO_MCP_REGISTRATION", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    return {"profile": profile, "repo": repo}


def _mcp_command(profile):
    servers = json.loads((profile / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    return servers["mcpServers"]["mimry"]["command"]


# Where each install writes: a refused one must leave it absent.
REFUSED_INSTALLS = [
    pytest.param("claude-code", True, True, lambda u: u["repo"] / ".claude", id="project-hooks"),
    pytest.param("cursor", False, False, lambda u: u["profile"] / ".cursor", id="global-mcp"),
]


@pytest.mark.parametrize("platform, project, hooks, written", REFUSED_INSTALLS)
@pytest.mark.parametrize("dry_run", [False, True], ids=["install", "dry-run"])
def test_throwaway_older_path_refuses_before_writing(
    two_installs, tmp_path, monkeypatch, user, platform, project, hooks, written, dry_run
):
    """#539: a throwaway install must not register an older mimry.

    The refusal names both versions and the way out. It writes nothing,
    dry run included.
    """
    _throwaway_env(tmp_path, monkeypatch)
    _probe(monkeypatch, "0.6.4")
    on_path = two_installs["on_path"]

    with pytest.raises(SystemExit) as refused:
        installer.install_skill(
            platform, project=project, root=user["repo"], hooks=hooks, dry_run=dry_run
        )

    message = refused.value.code
    assert isinstance(message, str)
    for expected in (
        "0.6.4",
        __version__,
        on_path["mimry"],
        "uv tool upgrade mimry",
        "pipx upgrade mimry",
        "uv tool install mimry",
    ):
        assert expected in message
    assert not written(user).exists()
    assert not any(user["profile"].rglob("*"))
    assert not any(user["repo"].rglob("*"))


def test_throwaway_unknown_path_version_refuses_before_writing(
    two_installs, tmp_path, monkeypatch, user
):
    """An unreadable PATH copy counts as a different version."""
    _throwaway_env(tmp_path, monkeypatch)
    _probe(monkeypatch, None)
    on_path = two_installs["on_path"]

    with pytest.raises(SystemExit) as refused:
        installer.install_skill("cursor", project=False, root=user["repo"])

    message = refused.value.code
    assert "an unknown version" in message
    assert on_path["mimry"] in message
    assert not any(user["profile"].rglob("*"))


def test_throwaway_same_version_installs_and_registers_path_copy(
    two_installs, tmp_path, monkeypatch, user, capsys
):
    """Same version on PATH: register that copy."""
    _throwaway_env(tmp_path, monkeypatch)
    _probe(monkeypatch, __version__)
    on_path = two_installs["on_path"]

    installer.install_skill("cursor", project=False, root=user["repo"])

    assert (user["profile"] / ".cursor" / "skills" / "mimry" / "SKILL.md").is_file()
    assert _mcp_command(user["profile"]) == on_path["mimry-mcp"]
    assert "on PATH" not in capsys.readouterr().out


def test_outside_throwaway_older_path_registers_own_copy_and_warns(
    two_installs, monkeypatch, user, capsys
):
    """#505: a non-throwaway install keeps its own copy and says so."""
    _probe(monkeypatch, "0.6.4")
    own = two_installs["own"]

    installer.install_skill("cursor", project=False, root=user["repo"])

    assert (user["profile"] / ".cursor" / "skills" / "mimry" / "SKILL.md").is_file()
    assert _mcp_command(user["profile"]) == own["mimry-mcp"]
    lines = [line for line in capsys.readouterr().out.splitlines() if "on PATH" in line]
    assert len(lines) == 1
    assert "0.6.4" in lines[0]
