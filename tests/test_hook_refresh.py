"""The background refresh hook: `mimry hook-refresh` and the installer
entries that run it."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry import installer, lifecycle
from mimry.cli import main
from mimry.constants import MCP_QUERY_NAME
from mimry.freshness import index_freshness
from mimry.hook import cmd_hook_refresh
from mimry.storage import load_pointer

EDITED = Path("src") / "auth" / "session.py"
SEARCH_MATCHER = {
    "claude-code": f"^(?:Bash|PowerShell|Glob|Grep|Read|{MCP_QUERY_NAME})$",
    "codex": "Bash",
}
EDIT_MATCHER = {
    "claude-code": "Edit|Write|MultiEdit|NotebookEdit",
    "codex": "apply_patch|Edit|Write",
}
REFRESH_EVENTS = ("SessionStart", "PostToolUse")
SESSION_START_MATCHERS = {
    "claude-code": ["startup|resume", "compact"],
    "codex": ["startup|resume"],
}


def _indexed_repo(tmp_path, monkeypatch) -> Path:
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    repo = copy_fixture(tmp_path)
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    return repo


def _edit(repo: Path) -> None:
    target = repo / EDITED
    target.write_text(
        target.read_text(encoding="utf-8") + "\ndef refresh_marker():\n    return True\n",
        encoding="utf-8",
    )


def _freshness(repo: Path) -> str:
    return index_freshness(repo, load_pointer(repo))["state"]


def _feed_stdin(monkeypatch, raw: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))


def _run_hook(monkeypatch, root: Path) -> int:
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit"}
    _feed_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))
    return cmd_hook_refresh(SimpleNamespace(root=str(root)))


def _settings(repo: Path, platform: str) -> dict:
    return json.loads((repo / installer.platforms()[platform].hook_path).read_text("utf-8"))


def _mimry_entries(settings: dict, event: str) -> list[dict]:
    return [e for e in settings.get("hooks", {}).get(event, []) if installer._is_mimry_hook(e)]


def test_refresh_reindexes_a_stale_index_and_leaves_no_markers(tmp_path, monkeypatch, capsys):
    repo = _indexed_repo(tmp_path, monkeypatch)
    _edit(repo)
    assert _freshness(repo) == "stale"

    assert _run_hook(monkeypatch, repo) == 0

    assert _freshness(repo) == "current"
    assert not (repo / ".mimry" / "refresh.lock").exists()
    assert not (repo / ".mimry" / "refresh.pending").exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("raw", [b"", b"not json", b'{"tool_name": 3}'])
def test_refresh_does_not_depend_on_the_payload(tmp_path, monkeypatch, raw):
    repo = _indexed_repo(tmp_path, monkeypatch)
    _edit(repo)
    _feed_stdin(monkeypatch, raw)

    assert cmd_hook_refresh(SimpleNamespace(root=str(repo))) == 0

    assert _freshness(repo) == "current"


def test_refresh_does_nothing_without_a_mimry_pointer(tmp_path, monkeypatch):
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "app.py").write_text("print('hi')\n", encoding="utf-8")

    assert _run_hook(monkeypatch, folder) == 0

    assert not (folder / ".mimry").exists()


def test_fresh_lock_returns_at_once_and_leaves_the_pending_marker(tmp_path, monkeypatch):
    repo = _indexed_repo(tmp_path, monkeypatch)
    _edit(repo)
    lock = repo / ".mimry" / "refresh.lock"
    lock.touch()
    calls: list[Path] = []
    monkeypatch.setattr(lifecycle, "refresh_if_stale", lambda root: calls.append(root))

    assert _run_hook(monkeypatch, repo) == 0

    # The hook swallows exceptions, so record calls rather than assert.
    assert calls == []
    assert (repo / ".mimry" / "refresh.pending").exists()
    assert lock.exists(), "the holder's lock is not ours to remove"


def test_stale_lock_is_taken_over_and_removed(tmp_path, monkeypatch):
    repo = _indexed_repo(tmp_path, monkeypatch)
    _edit(repo)
    lock = repo / ".mimry" / "refresh.lock"
    lock.touch()
    old = time.time() - 20 * 60
    os.utime(lock, (old, old))

    assert _run_hook(monkeypatch, repo) == 0

    assert _freshness(repo) == "current"
    assert not lock.exists()
    assert not (repo / ".mimry" / "refresh.pending").exists()


def test_a_failing_refresh_prints_nothing_and_still_releases_the_lock(
    tmp_path, monkeypatch, capsys
):
    repo = _indexed_repo(tmp_path, monkeypatch)

    def explode(root):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(lifecycle, "refresh_if_stale", explode)

    assert _run_hook(monkeypatch, repo) == 0

    assert capsys.readouterr().out == ""
    assert not (repo / ".mimry" / "refresh.lock").exists()


@pytest.mark.parametrize("platform", ["claude-code", "codex"])
def test_install_writes_three_mimry_hooks_and_reinstall_does_not_duplicate(tmp_path, platform):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    install = ("install", "--project", "--platform", platform, "--hooks")
    assert run_cli(repo, cache, *install).returncode == 0

    settings = _settings(repo, platform)
    assert [e["matcher"] for e in _mimry_entries(settings, "PreToolUse")] == [
        SEARCH_MATCHER[platform]
    ]
    assert [e["matcher"] for e in _mimry_entries(settings, "SessionStart")] == (
        SESSION_START_MATCHERS[platform]
    )
    assert [e["matcher"] for e in _mimry_entries(settings, "PostToolUse")] == [
        EDIT_MATCHER[platform]
    ]
    for event in REFRESH_EVENTS:
        handler = _mimry_entries(settings, event)[0]["hooks"][0]
        assert handler["async"] is True
        assert handler["command"].endswith(" hook-refresh")

    assert run_cli(repo, cache, *install).returncode == 0
    settings = _settings(repo, platform)
    total = sum(
        len(_mimry_entries(settings, event))
        for event in ("PreToolUse", "SessionStart", "PostToolUse")
    )
    assert total == len(SESSION_START_MATCHERS[platform]) + 2


@pytest.mark.parametrize("platform", ["claude-code", "codex"])
def test_user_hooks_survive_install_and_uninstall(tmp_path, platform):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    user_entry = {"matcher": "Write", "hooks": [{"type": "command", "command": "echo user-hook"}]}
    events = ("PreToolUse", "SessionStart", "PostToolUse")
    path = repo / installer.platforms()[platform].hook_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {event: [user_entry] for event in events}}))

    assert (
        run_cli(repo, cache, "install", "--project", "--platform", platform, "--hooks").returncode
        == 0
    )
    settings = _settings(repo, platform)
    mimry_groups = {
        "PreToolUse": 1,
        "SessionStart": len(SESSION_START_MATCHERS[platform]),
        "PostToolUse": 1,
    }
    for event in events:
        assert settings["hooks"][event][0] == user_entry
        assert len(settings["hooks"][event]) == 1 + mimry_groups[event]

    res = run_cli(repo, cache, "uninstall", "--project", "--platform", platform, "--hooks")
    assert res.returncode == 0, res.stderr
    assert _settings(repo, platform) == {"hooks": {event: [user_entry] for event in events}}


@pytest.mark.parametrize("platform", ["claude-code", "codex"])
def test_uninstall_of_a_fresh_install_leaves_no_mimry_entry_or_empty_event_list(
    tmp_path, platform
):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    install = ("--project", "--platform", platform, "--hooks")
    assert run_cli(repo, cache, "install", *install).returncode == 0

    assert run_cli(repo, cache, "uninstall", *install).returncode == 0

    # The install created this file, so uninstall deletes it once
    # MIMRY's entries are gone (#289). A file the user had would stay,
    # see tests/test_install_record.py.
    assert not (repo / installer.platforms()[platform].hook_path).exists()


def test_status_needs_repair_until_all_three_hooks_are_installed(tmp_path):
    repo = copy_fixture(tmp_path)
    path = repo / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    reminder_only = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Glob|Grep",
                    "hooks": [{"type": "command", "command": "mimry hook-check"}],
                }
            ]
        }
    }
    path.write_text(json.dumps(reminder_only))
    assert installer.install_status("claude-code", project=True, root=repo)["hooks"] is False

    installer.install_skill("claude-code", project=True, root=repo, hooks=True, mcp=False)

    assert installer.install_status("claude-code", project=True, root=repo)["hooks"] is True


def test_hook_refresh_command_returns_zero_outside_a_mimry_folder(tmp_path, monkeypatch):
    folder = tmp_path / "plain"
    folder.mkdir()
    _feed_stdin(monkeypatch, b"{}")

    assert main(["--root", str(folder), "hook-refresh"]) == 0

    assert not (folder / ".mimry").exists()


def test_uninstall_without_hooks_flag_mentions_an_old_reminder_and_keeps_the_file(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    install = run_cli(repo, cache, "install", "--project", "--platform", "claude-code")
    assert install.returncode == 0, install.stderr
    path = repo / ".claude" / "settings.json"
    reminder_only = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Glob|Grep",
                    "hooks": [{"type": "command", "command": "mimry hook-check"}],
                }
            ]
        }
    }
    path.write_text(json.dumps(reminder_only))
    before = path.read_bytes()

    res = run_cli(repo, cache, "uninstall", "--project", "--platform", "claude-code")

    assert res.returncode == 0, res.stderr
    assert "--hooks" in res.stdout
    assert path.read_bytes() == before


def test_install_replaces_a_non_list_event_value_with_the_mimry_entry(tmp_path):
    repo = copy_fixture(tmp_path)
    cfg = installer.platforms()["claude-code"]
    path = repo / cfg.hook_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {"SessionStart": 5, "PostToolUse": {"x": 1}}}))

    installer._install_hooks(repo, cfg)

    settings = _settings(repo, "claude-code")
    assert [e["matcher"] for e in settings["hooks"]["SessionStart"]] == (
        SESSION_START_MATCHERS["claude-code"]
    )
    assert [e["matcher"] for e in settings["hooks"]["PostToolUse"]] == [
        EDIT_MATCHER["claude-code"]
    ]


def test_remove_skips_a_non_list_event_value(tmp_path):
    repo = copy_fixture(tmp_path)
    cfg = installer.platforms()["claude-code"]
    path = repo / cfg.hook_path
    path.parent.mkdir(parents=True, exist_ok=True)
    reminder = {"matcher": "Bash", "hooks": [{"type": "command", "command": "mimry hook-check"}]}
    path.write_text(
        json.dumps({"hooks": {"SessionStart": 5, "PostToolUse": "text", "PreToolUse": [reminder]}})
    )

    assert installer._remove_hooks(repo, cfg) is not None

    settings = json.loads(path.read_text(encoding="utf-8"))
    assert settings == {"hooks": {"SessionStart": 5, "PostToolUse": "text"}}
