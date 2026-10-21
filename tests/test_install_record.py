"""What an install remembers, and what uninstall may delete.

An install writes a small state file beside its version marker. It holds
whether MCP was turned off with --no-mcp, and which files and folders
MIMRY created. Every test runs with a scratch user profile and fakes the
MCP registration, so the real agent CLIs and config files are never
touched.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import mimry.installer as installer
from mimry.cli import main

GLOBAL_LINES = "# MIMRY\n.mimry/\n"


@pytest.fixture
def user(tmp_path, monkeypatch):
    """A scratch user profile; the real ~/.claude is never touched."""
    profile = tmp_path / "userprofile"
    profile.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setattr(installer, "_home", lambda: profile)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # The suite turns MCP registration off for every test. These tests
    # check it, so turn it back on and fake the agent CLI instead.
    monkeypatch.delenv("MIMRY_NO_MCP_REGISTRATION", raising=False)
    monkeypatch.setattr(installer, "register_mcp", lambda cfg, *, dry_run: None)
    monkeypatch.setattr(installer, "unregister_mcp", lambda cfg: False)
    monkeypatch.setattr(installer, "mcp_registered", lambda cfg: False)
    monkeypatch.chdir(tmp_path)
    return profile


@pytest.fixture
def repo(tmp_path, monkeypatch, user):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


def _status(root: Path, *, project: bool) -> tuple[dict, int]:
    return installer.install_status_with_code("claude-code", project=project, root=root)


def _state(skill_dir: Path) -> dict:
    return json.loads((skill_dir / ".mimry_state.json").read_text(encoding="utf-8"))


def _global_skill(user: Path) -> Path:
    return user / ".claude" / "skills" / "mimry"


def _project_skill(repo: Path) -> Path:
    return repo / ".claude" / "skills" / "mimry"


# #280: install --status respects --no-mcp


def test_status_after_a_no_mcp_install_shows_the_choice_and_exits_0(user, capsys):
    installer.install_skill("claude-code", project=False, root=user, mcp=False)
    capsys.readouterr()

    result, code = _status(user, project=False)
    out = capsys.readouterr().out

    assert code == 0
    assert "mcp" not in result
    assert "not registered (--no-mcp)" in out
    assert "needs repair" not in out


def test_status_still_flags_a_missing_mcp_registration_without_the_choice(user):
    installer.install_skill("claude-code", project=False, root=user)

    result, code = _status(user, project=False)

    assert result["mcp"] is False
    assert code == 2


def test_status_counts_a_registration_made_by_hand_after_no_mcp(user, monkeypatch, capsys):
    installer.install_skill("claude-code", project=False, root=user, mcp=False)
    monkeypatch.setattr(installer, "mcp_registered", lambda cfg: True)
    capsys.readouterr()

    result, code = _status(user, project=False)

    assert result["mcp"] is True
    assert code == 0
    assert "MCP server" in capsys.readouterr().out


def test_refresh_keeps_the_no_mcp_choice_and_never_registers(user, monkeypatch):
    installer.install_skill("claude-code", project=False, root=user, mcp=False)
    registered = []
    monkeypatch.setattr(installer, "register_mcp", lambda cfg, *, dry_run: registered.append(cfg))

    assert main(["install", "--refresh"]) == 0

    assert registered == []
    assert _state(_global_skill(user))["no_mcp"] is True
    assert _status(user, project=False)[1] == 0


def test_a_plain_install_registers_mcp_and_clears_the_choice(user, monkeypatch):
    installer.install_skill("claude-code", project=False, root=user, mcp=False)
    registered = []
    monkeypatch.setattr(installer, "register_mcp", lambda cfg, *, dry_run: registered.append(cfg))

    installer.install_skill("claude-code", project=False, root=user)

    assert len(registered) == 1
    assert "no_mcp" not in _state(_global_skill(user))


def test_a_dry_run_records_nothing(user):
    installer.install_skill("claude-code", project=False, root=user, dry_run=True, mcp=False)

    assert not (_global_skill(user) / ".mimry_state.json").exists()


# #289: uninstall says what it did


def test_uninstall_says_hooks_were_taken_out_of_a_file_that_stays(repo, capsys):
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme": "dark"}', encoding="utf-8")
    installer.install_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)
    capsys.readouterr()

    installer.uninstall_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)
    out = capsys.readouterr().out

    assert "Removed MIMRY's hooks from" in out
    assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "dark"}


def test_uninstall_deletes_a_settings_file_and_folder_mimry_created(repo, capsys):
    installer.install_skill(
        "claude-code", project=True, root=repo, always_on=True, hooks=True, mcp=False
    )
    capsys.readouterr()

    installer.uninstall_skill(
        "claude-code", project=True, root=repo, always_on=True, hooks=True, mcp=False
    )
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]

    assert not (repo / ".claude").exists()
    assert not (repo / "CLAUDE.md").exists()
    assert any(line.startswith("Removed") and "settings.json" in line for line in lines)
    assert not any("MIMRY's hooks" in line for line in lines)
    assert "Removed CLAUDE.md" in lines


def test_uninstall_keeps_an_empty_settings_file_the_user_already_had(repo):
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")
    installer.install_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)

    installer.uninstall_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)

    assert json.loads(settings.read_text(encoding="utf-8")) == {}
    assert (repo / ".claude").is_dir()


def test_uninstall_keeps_a_folder_the_user_already_had_even_when_empty(repo):
    (repo / ".claude").mkdir()
    installer.install_skill("claude-code", project=True, root=repo, mcp=False)

    installer.uninstall_skill("claude-code", project=True, root=repo, mcp=False)

    assert (repo / ".claude").is_dir()
    assert not any((repo / ".claude").iterdir())
    assert not _project_skill(repo).exists()


def test_uninstall_keeps_a_created_folder_that_now_holds_the_users_files(repo):
    installer.install_skill("claude-code", project=True, root=repo, mcp=False)
    (repo / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")

    installer.uninstall_skill("claude-code", project=True, root=repo, mcp=False)

    assert (repo / ".claude" / "settings.local.json").is_file()


def test_an_install_without_a_record_still_uninstalls(repo):
    # No state file means nothing is known about created folders: the
    # skill folder goes, but no folder above it is removed.
    installer.install_skill("claude-code", project=True, root=repo, mcp=False)
    (_project_skill(repo) / ".mimry_state.json").unlink()

    installer.uninstall_skill("claude-code", project=True, root=repo, mcp=False)

    assert not _project_skill(repo).exists()
    assert (repo / ".claude").is_dir()


def test_a_later_hooks_uninstall_deletes_a_file_the_plain_one_kept(repo, capsys):
    installer.install_skill(
        "claude-code", project=True, root=repo, always_on=True, hooks=True, mcp=False
    )
    installer.uninstall_skill("claude-code", project=True, root=repo, mcp=False)
    capsys.readouterr()

    installer.uninstall_skill(
        "claude-code", project=True, root=repo, always_on=True, hooks=True, mcp=False
    )
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]

    assert not (repo / ".claude").exists()
    assert not (repo / "CLAUDE.md").exists()
    assert "Removed .claude/settings.json" in lines
    assert "Removed CLAUDE.md" in lines


def test_later_hooks_uninstall_keeps_a_settings_file_with_a_user_key(repo):
    installer.install_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)
    installer.uninstall_skill("claude-code", project=True, root=repo, mcp=False)
    settings = repo / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    settings.write_text(json.dumps({**data, "theme": "dark"}), encoding="utf-8")

    installer.uninstall_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)

    assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "dark"}


def test_a_plain_uninstall_with_nothing_to_keep_leaves_no_record(repo):
    installer.install_skill("claude-code", project=True, root=repo, mcp=False)

    installer.uninstall_skill("claude-code", project=True, root=repo, mcp=False)

    assert not (repo / ".claude").exists()


# The record: relative paths only, and it survives a move


def test_the_record_holds_no_absolute_path(repo, tmp_path):
    installer.install_skill(
        "claude-code", project=True, root=repo, always_on=True, hooks=True, mcp=False
    )

    text = (_project_skill(repo) / ".mimry_state.json").read_text(encoding="utf-8")
    created = json.loads(text)["created"]

    assert created
    assert not any(installer._is_absolute_text(entry) for entry in created)
    assert str(tmp_path) not in text
    assert tmp_path.as_posix() not in text


def test_the_global_record_holds_no_absolute_path(user):
    installer.install_skill("claude-code", project=False, root=user, mcp=False)

    text = (_global_skill(user) / ".mimry_state.json").read_text(encoding="utf-8")

    assert str(user) not in text
    assert user.as_posix() not in text
    assert all(not installer._is_absolute_text(entry) for entry in json.loads(text)["created"])


def test_an_absolute_entry_in_the_record_is_ignored_not_trusted(repo):
    installer.install_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)
    settings = repo / ".claude" / "settings.json"
    state_path = _project_skill(repo) / ".mimry_state.json"
    state_path.write_text(json.dumps({"created": [settings.as_posix()]}), encoding="utf-8")

    installer.uninstall_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)

    # Not trusted, so the settings file counts as the user's and stays.
    assert json.loads(settings.read_text(encoding="utf-8")) == {}


def test_uninstall_still_deletes_what_mimry_created_after_the_repo_moves(repo, tmp_path):
    installer.install_skill(
        "claude-code", project=True, root=repo, always_on=True, hooks=True, mcp=False
    )
    moved = tmp_path / "renamed-repo"
    shutil.copytree(repo, moved)

    installer.uninstall_skill(
        "claude-code", project=True, root=moved, always_on=True, hooks=True, mcp=False
    )

    assert not (moved / ".claude").exists()
    assert not (moved / "CLAUDE.md").exists()


def test_the_share_hint_lists_the_state_file(repo, capsys):
    installer.install_skill("claude-code", project=True, root=repo, mcp=False)

    hint = [line for line in capsys.readouterr().out.splitlines() if "git add" in line]

    assert len(hint) == 1
    assert ".mimry_version" in hint[0]
    assert ".mimry_state.json" in hint[0]


# #289: --git-ignore removes exactly MIMRY's block


@pytest.fixture
def ignore_file(user, monkeypatch):
    path = user.parent / "git" / "ignore"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(installer, "_global_ignore_path", lambda: path)
    return path


def test_git_ignore_flag_removes_only_mimry_block(ignore_file, capsys):
    ignore_file.write_text("node_modules/\n" + GLOBAL_LINES + "build/\n", encoding="utf-8")

    assert main(["uninstall", "--platform", "claude-code", "--git-ignore"]) == 0

    assert ignore_file.read_text(encoding="utf-8") == "node_modules/\nbuild/\n"
    assert "Removed MIMRY from the global git ignore" in capsys.readouterr().out


def test_without_the_flag_the_block_stays_and_a_line_says_how(ignore_file, user, capsys):
    installer.install_skill("claude-code", project=False, root=user)
    text = "node_modules/\n" + GLOBAL_LINES + "build/\n"
    ignore_file.write_text(text, encoding="utf-8")
    capsys.readouterr()

    assert main(["uninstall", "--platform", "claude-code"]) == 0

    assert ignore_file.read_text(encoding="utf-8") == text
    assert "--git-ignore" in capsys.readouterr().out


def test_git_ignore_flag_with_no_skill_prints_only_the_removal(ignore_file, capsys):
    ignore_file.write_text("node_modules/\n" + GLOBAL_LINES, encoding="utf-8")

    assert main(["uninstall", "--platform", "claude-code", "--git-ignore"]) == 0

    out = capsys.readouterr().out
    assert "Nothing to remove" not in out
    assert "Removed MIMRY from the global git ignore" in out
    assert ignore_file.read_text(encoding="utf-8") == "node_modules/\n"


def test_nothing_installed_prints_only_nothing_to_remove(ignore_file, capsys):
    ignore_file.write_text(GLOBAL_LINES, encoding="utf-8")

    assert main(["uninstall", "--platform", "claude-code"]) == 0

    out = capsys.readouterr().out
    assert "Nothing to remove" in out
    assert "global git ignore" not in out


def test_git_ignore_flag_keeps_a_user_mimry_out_line(ignore_file):
    ignore_file.write_text("mimry-out/\n# MIMRY\n.mimry/\n", encoding="utf-8")

    installer._uninstall_global_ignore(remove=True)

    assert ignore_file.read_text(encoding="utf-8") == "mimry-out/\n"


def test_git_ignore_flag_removes_an_older_install_block(ignore_file):
    ignore_file.write_text("node_modules/\n# MIMRY\n.mimry/\nmimry-out/\n", encoding="utf-8")

    installer._uninstall_global_ignore(remove=True)

    assert ignore_file.read_text(encoding="utf-8") == "node_modules/\n"


def test_git_ignore_flag_keeps_the_line_endings_of_the_other_lines(ignore_file):
    ignore_file.write_bytes(b"node_modules/\r\n" + GLOBAL_LINES.replace("\n", "\r\n").encode())

    installer._uninstall_global_ignore(remove=True)

    assert ignore_file.read_bytes() == b"node_modules/\r\n"


def test_git_ignore_flag_leaves_a_file_without_the_block_alone(ignore_file, capsys):
    ignore_file.write_text("node_modules/\n", encoding="utf-8")

    installer._uninstall_global_ignore(remove=True)

    assert ignore_file.read_text(encoding="utf-8") == "node_modules/\n"
    assert "Removed MIMRY from the global git ignore" not in capsys.readouterr().out


def test_repair_hint_after_a_no_mcp_install_keeps_no_mcp(user, capsys):
    installer.install_skill("claude-code", project=False, root=user, mcp=False)
    (_global_skill(user) / "references" / "mcp.md").unlink()
    capsys.readouterr()

    _, code = _status(user, project=False)
    out = capsys.readouterr().out

    assert code == 2
    assert "Run `mimry install --platform claude-code --no-mcp` to repair it." in out


def test_repair_hint_after_a_prompt_hook_install_keeps_the_prompt_hook(repo, capsys):
    installer.install_skill(
        "claude-code", project=True, root=repo, hooks=True, prompt_hook=True, mcp=False
    )
    settings = repo / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    del data["hooks"]["Stop"]
    settings.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()

    _, code = _status(repo, project=True)
    out = capsys.readouterr().out

    assert code == 2
    assert (
        "Run `mimry install --platform claude-code --project --hooks --prompt-hook --no-mcp`"
        " to repair it." in out
    )


# A second plain uninstall still names the hooks left behind


def test_a_second_plain_uninstall_still_names_the_hooks_left(repo, capsys):
    installer.install_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)
    assert main(["uninstall", "--platform", "claude-code", "--project", "--no-mcp"]) == 0
    capsys.readouterr()

    assert main(["uninstall", "--platform", "claude-code", "--project", "--no-mcp"]) == 0
    out = capsys.readouterr().out

    assert "Nothing to remove" in out
    assert "Kept the hooks in .claude/settings.json. Add --hooks to remove it too." in out


# Hooks are per project: a user-scope install says how to get them


def test_user_scope_claude_code_install_says_hooks_are_per_project(user, capsys):
    installer.install_skill("claude-code", project=False, root=user, mcp=False)

    hint = [line.strip() for line in capsys.readouterr().out.splitlines() if "per project" in line]

    assert hint == [
        "Hooks are per project: run mimry install --platform claude-code --project --hooks"
        " in a repo"
    ]


def test_user_scope_dry_run_says_hooks_are_per_project(user, capsys):
    installer.install_skill("claude-code", project=False, root=user, dry_run=True, mcp=False)

    assert "Hooks are per project" in capsys.readouterr().out
    assert not _global_skill(user).exists()


def test_project_install_with_hooks_does_not_say_hooks_are_per_project(repo, capsys):
    installer.install_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)

    assert "per project" not in capsys.readouterr().out


def test_platform_without_hooks_does_not_say_hooks_are_per_project(user, capsys):
    installer.install_skill("cursor", project=False, root=user, mcp=False)

    assert "per project" not in capsys.readouterr().out
