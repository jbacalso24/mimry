"""Claude Code's hooks are on by default.

Every test runs with a scratch user profile, so the real ~/.claude is
never read or written.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

import mimry.installer as installer
from mimry import doctor
from mimry.cli import main

NO_MCP = ["--no-mcp", "--no-global-gitignore"]
USER_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}
# One command per row of the hook table, plus the opt-in prompt hook.
DEFAULT_COMMANDS = sum(len(rows) for rows in installer.hook_table("claude-code").values()) + 1


@pytest.fixture
def home(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    for name in ("LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return profile


@pytest.fixture
def repo(tmp_path, monkeypatch, home):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(installer, "_project_repo_root", lambda start: project)
    return project


def _settings(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _mimry_commands(data: dict) -> list[str]:
    return [
        handler["command"]
        for entries in data.get("hooks", {}).values()
        for group in entries
        for handler in group["hooks"]
        if "hook-" in handler["command"]
    ]


def _record(home: Path) -> dict:
    return _load(home / ".claude" / "skills" / "mimry" / ".mimry_state.json")


def _write_record(home: Path, state: dict) -> None:
    path = home / ".claude" / "skills" / "mimry" / ".mimry_state.json"
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _seed_user_settings(home: Path) -> None:
    _settings(home).parent.mkdir(parents=True, exist_ok=True)
    _settings(home).write_text(
        json.dumps({"model": "opus", "hooks": {"PreToolUse": [USER_HOOK]}}), encoding="utf-8"
    )


def test_user_level_install_adds_hooks_and_keeps_the_users_own(home, capsys):
    _seed_user_settings(home)

    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    data = _load(_settings(home))
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"][0] == USER_HOOK
    assert len(_mimry_commands(data)) == DEFAULT_COMMANDS
    assert _record(home)["hooks"] is True
    assert _record(home)["prompt_hook"] is True


def test_a_second_user_level_install_does_not_duplicate_the_hooks(home, capsys):
    _seed_user_settings(home)
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    before = _settings(home).read_text(encoding="utf-8")

    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    assert _settings(home).read_text(encoding="utf-8") == before


def test_no_hooks_writes_none_and_records_the_opt_out(home, capsys):
    assert main(["install", "--platform", "claude-code", "--no-hooks", *NO_MCP]) == 0

    assert not _settings(home).exists()
    assert _record(home)["hooks"] is False
    assert _record(home)["prompt_hook"] is False


def test_no_prompt_hook_keeps_the_other_hooks(home, capsys):
    assert main(["install", "--platform", "claude-code", "--no-prompt-hook", *NO_MCP]) == 0

    commands = _mimry_commands(_load(_settings(home)))
    assert len(commands) == DEFAULT_COMMANDS - 1
    assert not any(command.endswith("hook-prompt") for command in commands)
    assert _record(home)["prompt_hook"] is False


def test_no_hooks_cannot_be_combined_with_the_prompt_hook(home):
    with pytest.raises(SystemExit):
        main(["install", "--platform", "claude-code", "--no-hooks", "--prompt-hook", *NO_MCP])
    assert not _settings(home).exists()


def test_refresh_adds_the_hooks_to_a_record_without_the_field(home, repo, capsys):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    _settings(home).unlink()
    state = _record(home)
    state.pop("hooks")
    state.pop("prompt_hook")
    _write_record(home, state)

    assert main(["--root", str(repo), "install", "--refresh"]) == 0

    assert len(_mimry_commands(_load(_settings(home)))) == DEFAULT_COMMANDS


@pytest.mark.parametrize("project", [False, True], ids=["user", "project"])
def test_refresh_does_not_add_hooks_to_an_opted_out_install(home, repo, project, capsys):
    flags = ["--project"] if project else []
    base = ["--root", str(repo)] if project else []
    assert (
        main([*base, "install", "--platform", "claude-code", *flags, "--no-hooks", *NO_MCP]) == 0
    )
    hooks_file = _settings(home) if not project else repo / ".claude" / "settings.local.json"
    assert not hooks_file.exists()

    assert main(["--root", str(repo), "install", "--refresh"]) == 0

    assert not hooks_file.exists()


@pytest.mark.parametrize("project", [False, True], ids=["user", "project"])
def test_refresh_adds_the_hooks_to_a_project_or_user_install_without_them(
    home, repo, project, capsys
):
    flags = ["--project"] if project else []
    base = ["--root", str(repo)]
    assert main([*base, "install", "--platform", "claude-code", *flags, *NO_MCP]) == 0
    hooks_file = _settings(home) if not project else repo / ".claude" / "settings.local.json"
    hooks_file.unlink()
    skill = (repo / ".claude" if project else home / ".claude") / "skills" / "mimry"
    state = _load(skill / ".mimry_state.json")
    state.pop("hooks")
    state.pop("prompt_hook")
    (skill / ".mimry_state.json").write_text(json.dumps(state), encoding="utf-8")

    assert main([*base, "install", "--refresh"]) == 0

    assert len(_mimry_commands(_load(hooks_file))) == DEFAULT_COMMANDS


def test_the_plugin_ships_the_user_level_hooks_so_none_are_added(home, repo, monkeypatch, capsys):
    monkeypatch.setattr(installer, "_plugin_installed", lambda: True)

    installer.install_skill("claude-code", project=False, root=repo, mcp=False)

    assert not _settings(home).exists()
    assert "Skipped the user-level hooks: the MIMRY Claude Code plugin ships its own" in (
        capsys.readouterr().out
    )
    assert _record(home)["hooks"] is True


def test_refresh_does_not_add_user_hooks_while_the_plugin_is_installed(
    home, repo, monkeypatch, capsys
):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    _settings(home).unlink()
    monkeypatch.setattr(installer, "_plugin_installed", lambda: True)

    assert main(["--root", str(repo), "install", "--refresh"]) == 0

    assert not _settings(home).exists()


def test_codex_hooks_stay_per_project(home, capsys):
    assert main(["install", "--platform", "codex", *NO_MCP]) == 0
    assert not (home / ".codex" / "hooks.json").exists()
    with pytest.raises(SystemExit):
        main(["install", "--platform", "codex", "--hooks", *NO_MCP])


def test_hook_commands_exit_quietly_in_a_folder_mimry_did_not_set_up(
    tmp_path, monkeypatch, capsys
):
    folder = tmp_path / "plain"
    folder.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    monkeypatch.chdir(folder)
    payloads = {
        "hook-check": {
            "tool_name": "Bash",
            "tool_input": {"command": "grep -rn parse_config src"},
            "session_id": "s1",
        },
        "hook-refresh": {"hook_event_name": "SessionStart", "session_id": "s1"},
        "hook-compact": {"session_id": "s1"},
        "hook-prompt": {
            "prompt": "Explain how the indexer refreshes the search graph for each query",
            "session_id": "s1",
            "cwd": str(folder),
        },
        "hook-stop": {"session_id": "s1"},
    }

    for command, payload in payloads.items():
        raw = json.dumps(payload).encode("utf-8")
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
        assert main([command]) == 0, command
        captured = capsys.readouterr()
        assert (captured.out, captured.err) == ("", ""), command

    assert list(folder.iterdir()) == []
    assert not cache.exists()


def test_doctor_names_the_refresh_when_user_hooks_are_missing(home, repo, capsys):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    _settings(home).unlink()

    check = doctor._registrations_check(repo)

    assert check["status"] == "warning"
    assert check["summary"] == "MIMRY is set up for Claude Code, but MIMRY's hooks are missing"
    assert check["suggestion"] == "Run `mimry install --refresh` to add the hooks."


def test_doctor_is_quiet_about_hooks_the_install_opted_out_of(home, repo, capsys):
    assert main(["install", "--platform", "claude-code", "--no-hooks", *NO_MCP]) == 0

    check = doctor._registrations_check(repo)

    assert check["status"] == "ok"


def test_uninstall_removes_the_user_hooks_and_keeps_the_rest(home, capsys):
    _seed_user_settings(home)
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    assert main(["uninstall", "--platform", "claude-code", "--no-mcp"]) == 0

    assert _load(_settings(home)) == {"model": "opus", "hooks": {"PreToolUse": [USER_HOOK]}}


def test_uninstall_removes_a_settings_file_mimry_created(home, capsys):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    assert main(["uninstall", "--platform", "claude-code", "--no-mcp"]) == 0

    assert not _settings(home).exists()


def _hooks_file_and_record(home: Path, repo: Path, project: bool) -> tuple[Path, Path]:
    if project:
        skill = repo / ".claude" / "skills" / "mimry"
        return repo / ".claude" / "settings.local.json", skill / ".mimry_state.json"
    return _settings(home), home / ".claude" / "skills" / "mimry" / ".mimry_state.json"


@pytest.mark.parametrize("project", [False, True], ids=["user", "project"])
def test_no_hooks_on_an_existing_install_removes_mimry_hooks_and_keeps_the_rest(
    home, repo, project, capsys
):
    base = ["--root", str(repo)] if project else []
    flags = ["--project"] if project else []
    hooks_file, record = _hooks_file_and_record(home, repo, project)
    hooks_file.parent.mkdir(parents=True, exist_ok=True)
    hooks_file.write_text(
        json.dumps({"model": "opus", "hooks": {"PreToolUse": [USER_HOOK]}}), encoding="utf-8"
    )
    assert main([*base, "install", "--platform", "claude-code", *flags, *NO_MCP]) == 0
    assert _mimry_commands(_load(hooks_file))
    capsys.readouterr()

    assert (
        main([*base, "install", "--platform", "claude-code", *flags, "--no-hooks", *NO_MCP]) == 0
    )

    assert _load(hooks_file) == {"model": "opus", "hooks": {"PreToolUse": [USER_HOOK]}}
    assert "Removed MIMRY's hooks from" in capsys.readouterr().out
    assert json.loads(record.read_text(encoding="utf-8"))["hooks"] is False


def test_no_hooks_removes_a_settings_file_mimry_made(home, capsys):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    assert _settings(home).exists()

    assert main(["install", "--platform", "claude-code", "--no-hooks", *NO_MCP]) == 0

    assert not _settings(home).exists()


def test_the_subagent_start_hook_is_added_without_a_matcher_and_removed_with_the_rest(
    home, capsys
):
    _seed_user_settings(home)

    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0

    groups = _load(_settings(home))["hooks"]["SubagentStart"]
    assert len(groups) == 1 and "matcher" not in groups[0]
    assert groups[0]["hooks"][0]["command"].endswith("hook-subagent")

    assert main(["install", "--platform", "claude-code", "--no-hooks", *NO_MCP]) == 0
    assert "SubagentStart" not in _load(_settings(home))["hooks"]


def test_uninstall_removes_the_subagent_start_hook(home, capsys):
    assert main(["install", "--platform", "claude-code", *NO_MCP]) == 0
    assert any(c.endswith("hook-subagent") for c in _mimry_commands(_load(_settings(home))))

    assert main(["uninstall", "--platform", "claude-code", "--no-mcp"]) == 0

    assert not _settings(home).exists() or "SubagentStart" not in _load(_settings(home)).get(
        "hooks", {}
    )
