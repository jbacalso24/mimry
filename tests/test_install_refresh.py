"""`mimry install --refresh` rewrites what is installed.

It installs nothing new.

Every test runs with a scratch user profile, so the real ~/.claude and
~/.codex are never read or written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mimry.installer as installer
from mimry import __version__
from mimry.cli import main

NO_MCP = ["--no-mcp", "--no-global-gitignore"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch profile covering every location a platform reads."""
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(profile / name.lower()))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return profile


@pytest.fixture
def repo(tmp_path, monkeypatch, home):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(installer, "_project_repo_root", lambda start: project)
    return project


def _mark_stale(skill: Path) -> None:
    """Leave the skill text and version marker an older MIMRY wrote."""
    skill.write_text(skill.read_text(encoding="utf-8") + "\nOld guidance.\n", encoding="utf-8")
    (skill.parent / ".mimry_version").write_text("0.3.1\n", encoding="utf-8")


def _status(platform: str, root: Path, project: bool) -> int:
    return installer.install_status_with_code(platform, project=project, root=root)[1]


def _snapshot(*roots: Path) -> dict[Path, tuple[int, bytes]]:
    return {
        path: (path.stat().st_mtime_ns, path.read_bytes())
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
    }


def test_refresh_brings_stale_user_installs_to_this_version(home, monkeypatch, capsys):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    assert main(["install", "--platform", "codex", *NO_MCP]) == 0
    claude = installer.platforms()["claude-code"].global_path
    codex = installer.platforms()["codex"].global_path
    _mark_stale(claude)
    _mark_stale(codex)
    assert _status("claude-code", home, False) == 2
    assert _status("codex", home, False) == 2

    def no_registration(*args, **kwargs):
        raise AssertionError("refresh must not touch MCP registration or Git's ignore file")

    monkeypatch.setattr(installer, "register_mcp", no_registration)
    monkeypatch.setattr(installer, "_add_global_ignore", no_registration)
    capsys.readouterr()

    assert main(["install", "--refresh"]) == 0

    assert "OK Refreshed the MIMRY skill for Claude Code and Codex" in capsys.readouterr().out
    assert _status("claude-code", home, False) == 0
    assert _status("codex", home, False) == 0
    assert (claude.parent / ".mimry_version").read_text(encoding="utf-8") == f"{__version__}\n"
    installed = {key for key, cfg in installer.platforms().items() if cfg.global_path.exists()}
    assert installed == {"claude-code", "codex"}


def test_refresh_rewrites_only_the_always_on_rules_and_hooks_that_were_added(home, repo, capsys):
    base = ["--root", str(repo)]
    assert main([*base, "install", "--platform", "claude-code", "--project", "--always-on"]) == 0
    assert main([*base, "install", "--platform", "claude-code", "--project", "--hooks"]) == 0
    assert main([*base, "install", "--platform", "codex", "--project", *NO_MCP]) == 0
    claude_skill = repo / ".claude" / "skills" / "mimry" / "SKILL.md"
    codex_skill = repo / ".codex" / "skills" / "mimry" / "SKILL.md"
    _mark_stale(claude_skill)
    _mark_stale(codex_skill)
    claude_md = repo / "CLAUDE.md"
    claude_md.write_text(
        claude_md.read_text(encoding="utf-8").replace("Start every task", "Start old task"),
        encoding="utf-8",
    )
    settings = repo / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["PreToolUse"][0]["matcher"] = "Stale"
    settings.write_text(json.dumps(data), encoding="utf-8")
    assert _status("claude-code", repo, True) == 2

    assert main(["--root", str(repo), "install", "--refresh"]) == 0

    assert _status("claude-code", repo, True) == 0
    assert _status("codex", repo, True) == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Bash|Glob|Grep"
    # Codex never had always-on rules or hooks, and refresh adds none.
    assert not (repo / "AGENTS.md").exists()
    assert not (repo / ".codex" / "hooks.json").exists()


def test_second_refresh_writes_nothing(home, repo, capsys):
    base = ["--root", str(repo)]
    assert main([*base, "install", "--platform", "claude-code", "--project", "--always-on"]) == 0
    assert main([*base, "install", "--platform", "codex", "--project", *NO_MCP]) == 0
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    _mark_stale(repo / ".claude" / "skills" / "mimry" / "SKILL.md")
    _mark_stale(installer.platforms()["claude-code"].global_path)
    assert main(["install", "--refresh"]) == 0
    before = _snapshot(home, repo)
    capsys.readouterr()

    assert main(["install", "--refresh"]) == 0

    assert "OK Installed skills are up to date" in capsys.readouterr().out
    assert _snapshot(home, repo) == before


def test_refresh_with_nothing_installed_says_so_and_writes_nothing(home, repo, capsys):
    assert main(["install", "--refresh"]) == 0

    assert "OK No MIMRY skills are installed" in capsys.readouterr().out
    assert not any(home.rglob("*"))
    assert not any(repo.rglob("*"))


def test_verbose_refresh_lists_the_paths_it_rewrote(home, capsys):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    _mark_stale(installer.platforms()["claude-code"].global_path)
    capsys.readouterr()

    assert main(["install", "--refresh", "-v"]) == 0

    out = capsys.readouterr().out
    assert "Refreshed the MIMRY skill for Claude Code" in out
    assert "SKILL.md" in out


@pytest.mark.parametrize(
    "flags",
    [
        ["--platform", "codex"],
        ["--project"],
        ["--status"],
        ["--dry-run"],
        ["--list-platforms"],
        ["--always-on"],
        ["--hooks"],
    ],
)
def test_refresh_rejects_flags_it_cannot_combine_with(home, flags):
    with pytest.raises(SystemExit) as refused:
        main(["install", "--refresh", *flags])

    assert "--refresh only updates the installs that are already there" in str(refused.value)
    assert not any(home.rglob("*"))
