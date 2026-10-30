"""The opt-in prompt hook, and the Stop hook every install gets.

Every test runs with a scratch user profile, so the real ~/.claude is
never read or written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mimry.installer as installer
from mimry.cli import main
from mimry.plugin import plugin_files

NO_MCP = ["--no-mcp", "--no-global-gitignore"]


@pytest.fixture
def home(tmp_path, monkeypatch):
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
    monkeypatch.setattr(installer, "_project_repo_root", lambda start: project)
    return project


def _base(repo: Path) -> list[str]:
    return ["--root", str(repo)]


def _settings(repo: Path) -> dict:
    return json.loads((repo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))


def _commands(settings: dict, event: str) -> list[str]:
    return [
        handler["command"]
        for group in settings.get("hooks", {}).get(event, [])
        for handler in group["hooks"]
    ]


def _status(repo: Path) -> int:
    return installer.install_status_with_code("claude-code", project=True, root=repo)[1]


def _install(repo: Path, *flags: str) -> None:
    assert main([*_base(repo), "install", "--platform", "claude-code", "--project", *flags]) == 0


def test_prompt_hook_is_added_only_with_its_flag(repo, capsys):
    _install(repo, "--hooks", *NO_MCP)
    assert "UserPromptSubmit" not in _settings(repo)["hooks"]

    _install(repo, "--hooks", "--prompt-hook", *NO_MCP)
    [command] = _commands(_settings(repo), "UserPromptSubmit")
    assert command.split()[-1].endswith("hook-prompt")
    group = _settings(repo)["hooks"]["UserPromptSubmit"][0]
    assert "matcher" not in group
    assert "async" not in group["hooks"][0]

    _install(repo, "--hooks", *NO_MCP)
    assert "UserPromptSubmit" not in _settings(repo)["hooks"]


def test_prompt_hook_needs_hooks_and_claude_code(repo, home, capsys):
    with pytest.raises(SystemExit):
        _install(repo, "--prompt-hook", *NO_MCP)
    assert not (repo / ".claude" / "settings.local.json").exists()

    with pytest.raises(SystemExit):
        main(
            [
                *_base(repo),
                "install",
                "--platform",
                "codex",
                "--project",
                "--hooks",
                "--prompt-hook",
                *NO_MCP,
            ]
        )
    assert not (repo / ".codex" / "hooks.json").exists()


def test_install_without_the_prompt_hook_is_healthy(repo, capsys):
    _install(repo, "--hooks", *NO_MCP)
    capsys.readouterr()

    assert _status(repo) == 0
    assert "needs repair" not in capsys.readouterr().out


def test_status_shows_the_prompt_hook_once_added(repo, capsys):
    _install(repo, "--hooks", "--prompt-hook", *NO_MCP)
    capsys.readouterr()

    assert _status(repo) == 0
    assert "Prompt hook" in capsys.readouterr().out


def test_uninstall_hooks_removes_the_prompt_hook(repo):
    _install(repo, "--hooks", "--prompt-hook", *NO_MCP)

    assert (
        main(
            [
                *_base(repo),
                "uninstall",
                "--platform",
                "claude-code",
                "--project",
                "--hooks",
                "--no-mcp",
            ]
        )
        == 0
    )
    settings = repo / ".claude" / "settings.local.json"
    # The file goes when MIMRY created it and nothing else is left.
    assert not settings.exists() or "hook-prompt" not in settings.read_text(encoding="utf-8")


def test_refresh_keeps_the_prompt_hook(repo, capsys):
    _install(repo, "--hooks", "--prompt-hook", *NO_MCP)
    settings = _settings(repo)
    del settings["hooks"]["Stop"]
    (repo / ".claude" / "settings.local.json").write_text(json.dumps(settings), encoding="utf-8")

    assert main([*_base(repo), "install", "--refresh"]) == 0

    after = _settings(repo)
    assert _commands(after, "UserPromptSubmit")
    assert _commands(after, "Stop")


def test_stop_hook_is_required_until_refresh_adds_it(repo, capsys):
    _install(repo, "--hooks", *NO_MCP)
    settings = _settings(repo)
    del settings["hooks"]["Stop"]
    (repo / ".claude" / "settings.local.json").write_text(json.dumps(settings), encoding="utf-8")
    capsys.readouterr()

    assert _status(repo) == 2
    assert "needs repair" in capsys.readouterr().out

    assert main([*_base(repo), "install", "--refresh"]) == 0
    assert _status(repo) == 0


def test_stop_entry_is_async_and_runs_the_flush_command(repo):
    _install(repo, "--hooks", *NO_MCP)

    [group] = _settings(repo)["hooks"]["Stop"]
    assert "matcher" not in group
    [handler] = group["hooks"]
    assert handler["command"].split()[-1].endswith("hook-stop")
    assert handler["async"] is True


def test_plugin_hooks_have_stop_and_no_prompt_hook():
    hooks = json.loads(plugin_files()["plugins/mimry/hooks/hooks.json"])["hooks"]

    assert "Stop" in hooks
    assert "UserPromptSubmit" not in hooks
    assert hooks["Stop"][0]["hooks"][0]["args"][-1] == "hook-stop"
