from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from mimry import __version__, installer
from mimry.plugin import plugin_files

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "mimry"
REGENERATE = "uv run python scripts/build_plugin.py"
PIN = f"mimry=={__version__}"


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _plugin_hooks() -> dict:
    return _load(PLUGIN / "hooks" / "hooks.json")["hooks"]


def test_committed_plugin_files_match_the_generator():
    for rel, content in plugin_files().items():
        path = ROOT / rel
        assert path.is_file(), f"{rel} is missing; run `{REGENERATE}`"
        assert path.read_bytes().decode("utf-8") == content, (
            f"{rel} is out of date; run `{REGENERATE}`"
        )


def test_no_plugin_file_is_missing_or_extra():
    expected = set(plugin_files())
    committed = {
        path.relative_to(ROOT).as_posix()
        for base in (PLUGIN, ROOT / ".claude-plugin")
        for path in base.rglob("*")
        if path.is_file()
    }
    assert committed == expected, (
        f"missing: {sorted(expected - committed)}; extra: {sorted(committed - expected)}; "
        f"run `{REGENERATE}`"
    )


def test_versions_and_descriptions_come_from_the_package():
    project = _project()
    assert project["version"] == __version__
    manifest = _load(PLUGIN / ".claude-plugin" / "plugin.json")
    assert manifest["version"] == __version__
    assert manifest["description"] == project["description"]
    marketplace = _load(ROOT / ".claude-plugin" / "marketplace.json")
    assert marketplace["plugins"][0]["description"] == project["description"]

    mcp = _load(PLUGIN / ".mcp.json")["mcpServers"]["mimry"]
    assert mcp["args"] == ["--from", PIN, "mimry-mcp"]
    for groups in _plugin_hooks().values():
        for group in groups:
            for handler in group["hooks"]:
                assert handler["args"][:2] == ["--from", PIN], handler


def test_plugin_hooks_match_the_installed_claude_code_hooks():
    installed = installer._mimry_hook_entries(installer.platforms()["claude-code"])
    hooks = _plugin_hooks()
    assert set(hooks) == set(installed)
    for event, entry in installed.items():
        (group,) = hooks[event]
        assert group["matcher"] == entry["matcher"], event
        plugin_handler = group["hooks"][0]
        installed_handler = entry["hooks"][0]
        # The installed command ends in the subcommand, and the plugin
        # passes it as its last argument.
        assert plugin_handler["args"][-1] == installed_handler["command"].split()[-1], event
        assert plugin_handler.get("async") == installed_handler.get("async"), event


@pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude CLI")
@pytest.mark.parametrize("target", [".", "plugins/mimry"])
def test_claude_plugin_validate_passes(target):
    result = subprocess.run(
        ["claude", "plugin", "validate", "--strict", target],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("plugin_root", "expected"),
    [
        pytest.param(
            "/plugins/mimry",
            'MIMRY can answer this: call the mimry_find tool with query "auth session" to rank'
            " the files and the lines to read first. For an exact identifier or string,"
            " `rg` is fine.",
            id="plugin",
        ),
        pytest.param(
            None,
            'MIMRY can answer this: run `mimry find "auth session"` to rank the files and the'
            " lines to read first. For an exact identifier or string, `rg` is fine.",
            id="cli",
        ),
    ],
)
def test_hook_nudge_names_the_mcp_tool_only_in_plugin_mode(tmp_path, plugin_root, expected):
    repo = tmp_path / "repo"
    (repo / ".mimry").mkdir(parents=True)
    (repo / ".mimry" / "pointer.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    if plugin_root:
        env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    payload = {"tool_input": {"command": 'rg "auth|session"'}}
    res = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), "hook-check"],
        cwd=repo,
        env=env,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"] == expected


def test_skill_for_the_plugin_is_not_an_installable_platform():
    assert "claude-code-plugin" not in installer.platforms()
    body = installer.skill_body("claude-code-plugin")
    assert "MIMRY Claude Code plugin" in body
    assert f"`uvx --from {PIN} mimry <command>`" in body


@pytest.fixture
def claude_config(tmp_path, monkeypatch):
    """A Claude config dir and a fake home, so installs stay in tmp."""
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(installer, "_home", lambda: tmp_path / "home")
    return config


def _write_listing(config: Path, text: str) -> None:
    (config / "plugins").mkdir(parents=True, exist_ok=True)
    (config / "plugins" / "installed_plugins.json").write_text(text, encoding="utf-8")


def _install_args(root: Path, *, platform: str = "claude-code", project: bool = False):
    return argparse.Namespace(
        platform=platform,
        project=project,
        root=str(root),
        status=False,
        list_platforms=False,
        verbose=False,
        dry_run=False,
        always_on=False,
        hooks=False,
        no_mcp=False,
    )


@pytest.mark.parametrize("platform", ["claude-code", "claude"])
def test_user_install_refuses_when_the_plugin_is_installed(
    tmp_path, claude_config, capsys, platform
):
    _write_listing(claude_config, json.dumps({"version": 2, "plugins": {"mimry@mimry": []}}))
    code = installer.cmd_install(_install_args(tmp_path, platform=platform))
    out = capsys.readouterr().out
    assert code == 1
    assert "! The MIMRY Claude Code plugin is installed" in out
    assert "Installing both runs every hook twice. Uninstall one of them." in out
    assert not (claude_config / "skills" / "mimry" / "SKILL.md").exists()


@pytest.mark.parametrize(
    "listing",
    [
        pytest.param('{"version": 2, "plugins": {"other@mimry": []}}', id="other-plugin"),
        pytest.param('{"version": 2, "plugins": {"mimry-dev@x": []}}', id="prefix-only"),
        pytest.param('{"version": 2, "plugins": ["mimry@mimry"]}', id="list-not-dict"),
        pytest.param('{"version": 2, "plugins": {"mimry@mimry"', id="broken-json"),
    ],
)
def test_user_install_proceeds_when_the_plugin_is_not_installed(tmp_path, claude_config, listing):
    _write_listing(claude_config, listing)
    assert installer.cmd_install(_install_args(tmp_path)) == 0
    assert (claude_config / "skills" / "mimry" / "SKILL.md").exists()


def test_missing_listing_means_not_installed(tmp_path, claude_config):
    assert installer.cmd_install(_install_args(tmp_path)) == 0
    assert (claude_config / "skills" / "mimry" / "SKILL.md").exists()


def test_project_install_is_not_blocked_by_the_plugin(tmp_path, claude_config):
    _write_listing(claude_config, json.dumps({"version": 2, "plugins": {"mimry@mimry": []}}))
    assert installer.cmd_install(_install_args(tmp_path, project=True)) == 0
    assert (tmp_path / ".claude" / "skills" / "mimry" / "SKILL.md").exists()


def test_other_platforms_are_not_blocked_by_the_plugin(tmp_path, claude_config):
    _write_listing(claude_config, json.dumps({"version": 2, "plugins": {"mimry@mimry": []}}))
    assert installer.cmd_install(_install_args(tmp_path, platform="cursor")) == 0


def test_plugin_detection_defaults_to_the_claude_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(installer, "_home", lambda: home)
    _write_listing(home / ".claude", json.dumps({"plugins": {"mimry@mimry": []}}))
    assert installer._plugin_installed() is True
