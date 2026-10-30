"""A project install leaves nothing for `git status` to show (#623).

Claude Code's hooks go in settings.local.json, which Git's global ignore
file hides, and so do the skill folders. Codex has no per-machine hooks
file, so a hooks file MIMRY alone made is excluded through
.git/info/exclude. Hooks an older install wrote to the shared
settings.json move out of it. Every test runs with a scratch profile,
XDG config and empty git global config, so no real file is touched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import mimry.installer as installer
from mimry.cli import main

USER_GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user"}]}
NO_MCP = ["--no-mcp"]


@pytest.fixture
def user(tmp_path, monkeypatch):
    profile = tmp_path / "userprofile"
    profile.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setattr(installer, "_home", lambda: profile)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("MIMRY_NO_GLOBAL_GITIGNORE", raising=False)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def repo(user, monkeypatch):
    project = user / "repo"
    project.mkdir()
    _git(project, "init", "-q")
    (project / "a.txt").write_text("hi\n", encoding="utf-8")
    _git(project, "add", ".")
    _git(project, "commit", "-qm", "x")
    monkeypatch.chdir(project)
    return project


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _porcelain(repo: Path) -> str:
    return _git(repo, "status", "--porcelain")


def _exclude_text(repo: Path) -> str:
    return (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")


def _global_ignore(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "git" / "ignore"


def _legacy_settings(repo: Path, *, user_owned: bool = True) -> Path:
    """A settings.json as an older MIMRY wrote its hooks into it.

    With user_owned, a user hook and a key of the user's own sit beside
    the MIMRY entries.
    """
    entries = installer._mimry_hook_entries(installer.platforms()["claude-code"])
    if user_owned:
        hooks = {event: [USER_GROUP, *groups] for event, groups in entries.items()}
        data: dict = {"model": "sonnet", "hooks": hooks}
    else:
        data = {"hooks": entries}
    path = repo / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _mark_created(repo: Path, relative: str) -> None:
    """Record a path as MIMRY-created in the install record."""
    state_file = repo / ".claude" / "skills" / "mimry" / ".mimry_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["created"] = sorted({*state.get("created", []), relative})
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def test_reproduce_flow_leaves_git_status_empty(repo):
    assert main(["install", "--project", "--platform", "claude-code", "--hooks", *NO_MCP]) == 0
    assert main(["install", "--project", "--platform", "codex", "--hooks", *NO_MCP]) == 0

    assert _porcelain(repo) == ""
    assert "/.codex/hooks.json" in _exclude_text(repo).splitlines()
    assert (repo / ".claude" / "settings.local.json").is_file()
    assert not (repo / ".claude" / "settings.json").exists()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_missing_lines_go_inside_an_existing_block(user, newline):
    ignore = _global_ignore(user)
    ignore.parent.mkdir(parents=True)
    ignore.write_bytes(f"# MIMRY{newline}.mimry/{newline}".encode())
    missing = [p for p in installer._global_ignore_patterns() if p != ".mimry/"]

    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    text = ignore.read_bytes().decode("utf-8")
    lines = text.split(newline)
    assert lines[:2] == ["# MIMRY", ".mimry/"]
    assert set(lines[2:-1]) == set(missing)
    assert lines[-1] == ""
    assert text.count("\n") == text.count(newline)
    assert "**/.claude/settings.local.json" in lines
    assert not any(line.endswith(("settings.json", "hooks.json")) for line in lines)

    before = ignore.read_bytes()
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    assert ignore.read_bytes() == before


def test_lines_after_the_block_are_kept_byte_for_byte(user):
    ignore = _global_ignore(user)
    ignore.parent.mkdir(parents=True)
    ignore.write_bytes(b"# MIMRY\r\n.mimry/\r\nnode_modules/\r\n")

    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    raw = ignore.read_bytes()
    assert raw.endswith(b"\r\nnode_modules/\r\n")
    assert raw.startswith(b"# MIMRY\r\n.mimry/\r\n")
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_no_global_gitignore_flag_writes_nothing(user):
    assert main(["install", "--platform", "claude-code", "--no-global-gitignore", *NO_MCP]) == 0

    assert not _global_ignore(user).exists()


def test_env_opt_out_writes_nothing(user, monkeypatch):
    monkeypatch.setenv("MIMRY_NO_GLOBAL_GITIGNORE", "1")

    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    assert not _global_ignore(user).exists()


def test_config_opt_out_writes_nothing(user, repo):
    (repo / ".mimry").mkdir()
    (repo / ".mimry" / "config.toml").write_text("no_global_gitignore = true\n", encoding="utf-8")

    assert main(["install", "--project", "--platform", "claude-code", *NO_MCP]) == 0

    assert not _global_ignore(user).exists()


@pytest.mark.parametrize(
    "command",
    [
        ["install", "--refresh"],
        ["install", "--project", "--platform", "claude-code", *NO_MCP],
    ],
    ids=["refresh", "install"],
)
def test_legacy_hooks_move_to_the_local_file_and_keep_the_user_settings(repo, command):
    assert main(["install", "--project", "--platform", "claude-code", *NO_MCP]) == 0
    legacy = _legacy_settings(repo)

    assert main(command) == 0

    settings = json.loads(legacy.read_text(encoding="utf-8"))
    assert settings["model"] == "sonnet"
    assert settings["hooks"]
    assert all(entries == [USER_GROUP] for entries in settings["hooks"].values())
    cfg = installer.platforms()["claude-code"]
    assert installer._has_hook(repo, cfg) is True
    assert installer._has_mimry_hooks_at(repo / ".claude" / "settings.local.json") is True


def test_legacy_settings_the_installer_created_is_removed_once_empty(repo):
    assert main(["install", "--project", "--platform", "claude-code", *NO_MCP]) == 0
    legacy = _legacy_settings(repo, user_owned=False)
    _mark_created(repo, ".claude/settings.json")

    assert main(["install", "--refresh"]) == 0

    assert not legacy.exists()
    assert installer._has_hook(repo, installer.platforms()["claude-code"]) is True


def test_codex_hooks_file_with_a_user_hook_is_not_excluded(repo):
    hooks = repo / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps({"hooks": {"Stop": [USER_GROUP]}}), encoding="utf-8")

    assert main(["install", "--project", "--platform", "codex", "--hooks", *NO_MCP]) == 0

    assert "/.codex/hooks.json" not in _exclude_text(repo).splitlines()
    assert ".codex/" in _porcelain(repo)


def test_uninstall_removes_hooks_from_both_claude_files_and_the_git_lines(user, repo):
    ignore = _global_ignore(user)
    assert main(["install", "--project", "--platform", "claude-code", "--hooks", *NO_MCP]) == 0
    assert main(["install", "--project", "--platform", "codex", "--hooks", *NO_MCP]) == 0
    legacy = _legacy_settings(repo)

    assert (
        main(
            [
                "uninstall",
                "--platform",
                "claude-code",
                "--project",
                "--hooks",
                "--git-ignore",
                *NO_MCP,
            ]
        )
        == 0
    )
    assert main(["uninstall", "--platform", "codex", "--project", "--hooks", *NO_MCP]) == 0

    assert not (repo / ".claude" / "settings.local.json").exists()
    assert not (repo / ".codex" / "hooks.json").exists()
    assert "/.codex/hooks.json" not in _exclude_text(repo).splitlines()
    settings = json.loads(legacy.read_text(encoding="utf-8"))
    assert settings == {"model": "sonnet", "hooks": {e: [USER_GROUP] for e in settings["hooks"]}}
    assert "**/.claude/settings.local.json" not in ignore.read_text(encoding="utf-8")
    assert "# MIMRY" not in ignore.read_text(encoding="utf-8")
