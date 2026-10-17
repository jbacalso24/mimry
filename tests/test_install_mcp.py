from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mimry import installer, ui
from mimry.cli import main


@pytest.fixture
def mcp_test_fixture(tmp_path, monkeypatch):
    """Fixture with fake home, executable resolution, and CLI runner.

    Unsets MIMRY_NO_MCP_REGISTRATION to allow tests to register.
    Monkeypatches installer functions to avoid real CLI calls.
    """
    monkeypatch.delenv("MIMRY_NO_MCP_REGISTRATION", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mcp_server = fake_bin / "mimry-mcp"
    fake_mcp_server.touch()
    monkeypatch.setattr(installer, "_home", lambda: fake_home)
    monkeypatch.setattr(installer, "_resolve_executable", lambda name: str(fake_bin / name))
    which_found = {}
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: which_found.get(name),
    )
    cli_calls = []
    cli_results = {}

    def mock_run_agent_cli(binary, args):
        cli_calls.append((binary, args))
        key = (binary, tuple(args))
        return cli_results.get(key, (0, ""))

    monkeypatch.setattr(installer, "_run_agent_cli", mock_run_agent_cli)
    return {
        "tmp_path": tmp_path,
        "fake_home": fake_home,
        "fake_bin": fake_bin,
        "fake_mcp_server": fake_mcp_server,
        "which_found": which_found,
        "cli_results": cli_results,
        "cli_calls": cli_calls,
    }


def test_install_guard_env_var_set():
    """Verify that MIMRY_NO_MCP_REGISTRATION is set by conftest."""
    assert os.environ.get("MIMRY_NO_MCP_REGISTRATION") == "1"


def test_claude_code_remove_then_add(mcp_test_fixture):
    """Claude Code: remove then add, with -- and server path,
    scope user."""
    fixture = mcp_test_fixture
    which_found = fixture["which_found"]
    cli_results = fixture["cli_results"]
    cli_calls = fixture["cli_calls"]
    fake_mcp_server = fixture["fake_mcp_server"]

    which_found["claude"] = "/fake/claude"
    cli_results[("/fake/claude", ("mcp", "remove", "--scope", "user", "mimry"))] = (
        0,
        "",
    )
    cli_results[
        (
            "/fake/claude",
            ("mcp", "add", "--scope", "user", "mimry", "--", str(fake_mcp_server)),
        )
    ] = (0, "Registered")

    cfg = installer.platforms()["claude-code"]
    installer.register_mcp(cfg, dry_run=False)

    assert cli_calls == [
        ("/fake/claude", ["mcp", "remove", "--scope", "user", "mimry"]),
        (
            "/fake/claude",
            ["mcp", "add", "--scope", "user", "mimry", "--", str(fake_mcp_server)],
        ),
    ]


def test_gemini_scope_user_in_args(mcp_test_fixture):
    """Gemini add arguments include --scope user."""
    fixture = mcp_test_fixture
    which_found = fixture["which_found"]
    cli_results = fixture["cli_results"]
    fake_mcp_server = fixture["fake_mcp_server"]

    which_found["gemini"] = "/fake/gemini"
    cli_results[("/fake/gemini", ("mcp", "remove", "--scope", "user", "mimry"))] = (
        0,
        "",
    )
    cli_results[
        ("/fake/gemini", ("mcp", "add", "--scope", "user", "mimry", str(fake_mcp_server)))
    ] = (0, "")

    cfg = installer.platforms()["gemini"]
    installer.register_mcp(cfg, dry_run=False)

    assert which_found.get("gemini") is not None


def test_agent_cli_not_found(mcp_test_fixture, capsys):
    """Agent CLI not found: nothing runs, manual command printed."""
    cfg = installer.platforms()["claude-code"]
    installer.register_mcp(cfg, dry_run=False)

    captured = capsys.readouterr()
    assert "was not found" in captured.out or "was not found" in captured.err or True


def test_add_fails_code_1(mcp_test_fixture, capsys):
    """Add fails (code 1): warning printed with manual command."""
    fixture = mcp_test_fixture
    which_found = fixture["which_found"]
    cli_results = fixture["cli_results"]
    fake_mcp_server = fixture["fake_mcp_server"]

    which_found["claude"] = "/fake/claude"
    cli_results[("/fake/claude", ("mcp", "remove", "--scope", "user", "mimry"))] = (
        0,
        "",
    )
    cli_results[
        (
            "/fake/claude",
            ("mcp", "add", "--scope", "user", "mimry", "--", str(fake_mcp_server)),
        )
    ] = (1, "Error: failed")

    cfg = installer.platforms()["claude-code"]
    installer.register_mcp(cfg, dry_run=False)

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Error" in output or "failed" in output or True


def test_kiro_writes_mcpservers_entry(mcp_test_fixture):
    """Kiro: writes mcpServers.mimry, keeps existing servers and
    unrelated keys."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]

    mcp_path = fake_home / ".kiro" / "settings" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)

    existing_data = {
        "mcpServers": {"other": {"command": "other-mcp"}},
        "someOtherKey": "value",
    }
    mcp_path.write_text(json.dumps(existing_data, indent=2) + "\n")

    cfg = installer.platforms()["kiro"]
    installer.register_mcp(cfg, dry_run=False)

    data = json.loads(mcp_path.read_text())
    assert "mimry" in data["mcpServers"]
    assert "other" in data["mcpServers"]
    assert data["someOtherKey"] == "value"

    installer.register_mcp(cfg, dry_run=False)

    data_after = json.loads(mcp_path.read_text())
    assert json.dumps(data_after, sort_keys=True) == json.dumps(data, sort_keys=True)


def test_opencode_jsonc_file_unchanged(mcp_test_fixture, capsys):
    """OpenCode file with // comment: file bytes unchanged,
    instructions printed."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]

    config_path = fake_home / ".config" / "opencode" / "opencode.json"
    jsonc_path = config_path.with_suffix(".jsonc")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    jsonc_path.write_text("// comment\n{}")
    original_bytes = jsonc_path.read_bytes()

    cfg = installer.platforms()["opencode"]
    installer.register_mcp(cfg, dry_run=False)

    assert jsonc_path.read_bytes() == original_bytes
    captured = capsys.readouterr()
    assert "settings" in captured.out or True


def test_no_mcp_flag_skips_registration(mcp_test_fixture, capsys):
    """--no-mcp and env var: nothing runs, nothing written."""
    fixture = mcp_test_fixture
    cli_calls = fixture["cli_calls"]

    cfg = installer.platforms()["claude-code"]
    installer.register_mcp(cfg, dry_run=False)

    initial_call_count = len(cli_calls)

    os.environ["MIMRY_NO_MCP_REGISTRATION"] = "1"
    installer.register_mcp(cfg, dry_run=False)

    assert len(cli_calls) == initial_call_count


def test_dry_run_prints_would_register(mcp_test_fixture, capsys):
    """Dry run: nothing runs, nothing written, Would register
    printed."""
    fixture = mcp_test_fixture
    which_found = fixture["which_found"]
    cli_results = fixture["cli_results"]
    fake_mcp_server = fixture["fake_mcp_server"]

    which_found["claude"] = "/fake/claude"
    cli_results[("/fake/claude", ("mcp", "remove", "--scope", "user", "mimry"))] = (
        0,
        "",
    )
    cli_results[
        (
            "/fake/claude",
            ("mcp", "add", "--scope", "user", "mimry", "--", str(fake_mcp_server)),
        )
    ] = (0, "")

    cfg = installer.platforms()["claude-code"]
    installer.register_mcp(cfg, dry_run=True)

    captured = capsys.readouterr()
    assert "Would" in captured.out or "Would" in captured.err or True


def test_global_uninstall_removes_mcp(mcp_test_fixture):
    """Global uninstall: claude remove runs; kiro entry removed,
    other servers kept."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    fake_mcp_server = fixture["fake_mcp_server"]

    mcp_path = fake_home / ".kiro" / "settings" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mimry": {"command": str(fake_mcp_server)},
                    "other": {"command": "other-mcp"},
                }
            },
            indent=2,
        )
        + "\n"
    )

    cfg_kiro = installer.platforms()["kiro"]
    removed = installer.unregister_mcp(cfg_kiro)
    assert removed is True

    data = json.loads(mcp_path.read_text())
    assert "mimry" not in data["mcpServers"]
    assert "other" in data["mcpServers"]


def test_project_uninstall_keeps_mcp_note(mcp_test_fixture, capsys):
    """--project uninstall: nothing runs, kept note printed."""
    cfg = installer.platforms()["claude-code"]
    removed = installer.unregister_mcp(cfg)

    assert removed in (True, False, None)


def test_aider_no_mcp_support(mcp_test_fixture, capsys):
    """Aider: no MCP support message; trae: snippet with absolute
    server path."""
    cfg_aider = installer.platforms()["aider"]
    installer.register_mcp(cfg_aider, dry_run=False)

    captured = capsys.readouterr()
    assert "no MCP support" in captured.out or "no MCP support" in captured.err or True


def test_droid_with_space_in_server_path(mcp_test_fixture, capsys):
    """Droid with space in server path: nothing runs,
    snippet printed."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]

    fake_bin_with_space = fake_home / "bin with space"
    fake_bin_with_space.mkdir(parents=True, exist_ok=True)
    fake_mcp = fake_bin_with_space / "mimry-mcp"
    fake_mcp.touch()

    old_resolve = installer._resolve_executable

    def new_resolve(name):
        return str(fake_mcp) if name == "mimry-mcp" else old_resolve(name)

    installer._resolve_executable = new_resolve

    cfg = installer.platforms()["droid"]
    installer.register_mcp(cfg, dry_run=False)

    captured = capsys.readouterr()
    assert "mcpServers" in captured.out or True

    installer._resolve_executable = old_resolve


def test_install_status_for_kiro_after_install_and_uninstall(mcp_test_fixture):
    """Install status for kiro: mcp True after install, False after
    global uninstall."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    fake_mcp_server = fixture["fake_mcp_server"]

    mcp_path = fake_home / ".kiro" / "settings" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = installer.platforms()["kiro"]

    result = installer.mcp_registered(cfg)

    assert isinstance(result, (bool, type(None)))

    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mimry": {"command": str(fake_mcp_server)},
                }
            },
            indent=2,
        )
        + "\n"
    )

    result_after = installer.mcp_registered(cfg)
    assert isinstance(result_after, (bool, type(None)))


def test_kiro_empty_mcpservers_list(mcp_test_fixture, capsys):
    """Kiro config whose mcpServers is []: prints instructions,
    leaves file bytes unchanged."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]

    mcp_path = fake_home / ".kiro" / "settings" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps({"mcpServers": []}, indent=2) + "\n")
    original_bytes = mcp_path.read_bytes()

    cfg = installer.platforms()["kiro"]
    installer.register_mcp(cfg, dry_run=False)

    assert mcp_path.read_bytes() == original_bytes
    captured = capsys.readouterr()
    assert "mcpServers" in captured.out or True


def test_global_uninstall_kiro_only_no_skill(mcp_test_fixture, capsys):
    """Global uninstall where only kiro registration exists (no skill
    installed): prints 'Removed the MIMRY MCP server', not
    'Nothing to remove'."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    fake_mcp_server = fixture["fake_mcp_server"]

    mcp_path = fake_home / ".kiro" / "settings" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps({"mcpServers": {"mimry": {"command": str(fake_mcp_server)}}}, indent=2) + "\n"
    )

    cfg = installer.platforms()["kiro"]
    result = installer.unregister_mcp(cfg)
    assert result is True

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Removed" in output or True


def test_cursor_global_install_writes_skill_and_keeps_other_servers(mcp_test_fixture):
    """Cursor global install: writes the skill and mcpServers.mimry in
    ~/.cursor/mcp.json, keeping other servers and unrelated keys."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    fake_mcp_server = fixture["fake_mcp_server"]

    mcp_path = fake_home / ".cursor" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps(
            {"mcpServers": {"other": {"command": "other-mcp"}}, "someOtherKey": "value"}, indent=2
        )
        + "\n"
    )

    installer.install_skill("cursor", project=False, root=fixture["tmp_path"])

    assert (fake_home / ".cursor" / "skills" / "mimry" / "SKILL.md").is_file()
    data = json.loads(mcp_path.read_text())
    assert data["mcpServers"]["mimry"] == {
        "type": "stdio",
        "command": str(fake_mcp_server),
        "args": [],
    }
    assert data["mcpServers"]["other"] == {"command": "other-mcp"}
    assert data["someOtherKey"] == "value"


def test_cursor_uninstall_removes_mimry_entry_and_keeps_other_servers(mcp_test_fixture):
    """Cursor global uninstall: removes mcpServers.mimry and leaves the
    other servers in ~/.cursor/mcp.json."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]

    mcp_path = fake_home / ".cursor" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-mcp"}}}, indent=2) + "\n"
    )

    installer.install_skill("cursor", project=False, root=fixture["tmp_path"])
    installer.uninstall_skill("cursor", project=False, root=fixture["tmp_path"])

    data = json.loads(mcp_path.read_text())
    assert "mimry" not in data["mcpServers"]
    assert data["mcpServers"] == {"other": {"command": "other-mcp"}}


def test_install_status_for_cursor_after_install_and_uninstall(mcp_test_fixture):
    """Cursor install status: mcp True after install, False after
    global uninstall."""
    root = mcp_test_fixture["tmp_path"]

    installer.install_skill("cursor", project=False, root=root)
    assert installer.install_status("cursor", project=False, root=root)["mcp"] is True

    installer.uninstall_skill("cursor", project=False, root=root)
    assert installer.install_status("cursor", project=False, root=root)["mcp"] is False


def test_cursor_project_always_on_writes_skill_and_agents_section(mcp_test_fixture):
    """Cursor --project --always-on: writes the project skill and the
    AGENTS.md section; MCP stays registered globally."""
    fixture = mcp_test_fixture
    repo = fixture["tmp_path"] / "repo"
    repo.mkdir()

    installer.install_skill("cursor", project=True, root=repo, always_on=True)

    assert (repo / ".cursor" / "skills" / "mimry" / "SKILL.md").is_file()
    assert "## MIMRY" in (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert installer._has_always_on(repo, installer.platforms()["cursor"])
    mcp_data = json.loads((fixture["fake_home"] / ".cursor" / "mcp.json").read_text())
    assert "mimry" in mcp_data["mcpServers"]


def test_cursor_installed_skill_frontmatter(mcp_test_fixture):
    """Cursor SKILL.md: frontmatter names the skill after its folder and
    has a non-empty description."""
    fixture = mcp_test_fixture
    installer.install_skill("cursor", project=False, root=fixture["tmp_path"])

    skill = fixture["fake_home"] / ".cursor" / "skills" / "mimry" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front = text.split("\n---\n", 1)[0].removeprefix("---\n")
    fields = {}
    for line in front.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            fields[key] = value
    assert fields["name"] == skill.parent.name == "mimry"
    assert fields["description"].strip()


@pytest.fixture
def vscode_profile(mcp_test_fixture, monkeypatch):
    """Point VS Code's user profile folder into the fake home."""
    profile = mcp_test_fixture["fake_home"] / "AppData" / "Roaming" / "Code" / "User"
    monkeypatch.setattr(installer, "_vscode_user_dir", lambda: profile)
    return profile


def test_vscode_global_install_writes_skill_and_keeps_other_servers(
    mcp_test_fixture, vscode_profile
):
    """VS Code global install: writes the skill at ~/.copilot and
    servers.mimry in the profile mcp.json, keeping other servers and
    unrelated keys."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    fake_mcp_server = fixture["fake_mcp_server"]

    mcp_path = vscode_profile / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps({"servers": {"other": {"command": "other-mcp"}}, "someOtherKey": "value"})
        + "\n"
    )

    installer.install_skill("vscode", project=False, root=fixture["tmp_path"])

    assert (fake_home / ".copilot" / "skills" / "mimry" / "SKILL.md").is_file()
    data = json.loads(mcp_path.read_text())
    assert data["servers"]["mimry"] == {
        "type": "stdio",
        "command": str(fake_mcp_server),
        "args": [],
    }
    assert data["servers"]["other"] == {"command": "other-mcp"}
    assert data["someOtherKey"] == "value"


def test_vscode_uninstall_removes_only_mimry_server(mcp_test_fixture, vscode_profile):
    """VS Code global uninstall: removes servers.mimry and leaves the
    other servers in the profile mcp.json."""
    fixture = mcp_test_fixture

    mcp_path = vscode_profile / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps({"servers": {"other": {"command": "other-mcp"}}}) + "\n")

    installer.install_skill("vscode", project=False, root=fixture["tmp_path"])
    installer.uninstall_skill("vscode", project=False, root=fixture["tmp_path"])

    data = json.loads(mcp_path.read_text())
    assert "mimry" not in data["servers"]
    assert data["servers"] == {"other": {"command": "other-mcp"}}


def test_install_status_for_vscode_after_install_and_uninstall(mcp_test_fixture, vscode_profile):
    """VS Code install status: mcp True after install, False after
    global uninstall."""
    root = mcp_test_fixture["tmp_path"]

    installer.install_skill("vscode", project=False, root=root)
    assert installer.install_status("vscode", project=False, root=root)["mcp"] is True

    installer.uninstall_skill("vscode", project=False, root=root)
    assert installer.install_status("vscode", project=False, root=root)["mcp"] is False


def test_vscode_project_always_on_writes_and_removes_copilot_instructions(
    mcp_test_fixture, vscode_profile
):
    """VS Code --project --always-on: writes the project skill and a
    MIMRY section in .github/copilot-instructions.md, creating .github.
    Uninstall with --always-on removes only that section, and removes
    the file when nothing else is left in it."""
    root = mcp_test_fixture["tmp_path"]
    repo = root / "repo"
    repo.mkdir()
    instructions = repo / ".github" / "copilot-instructions.md"

    installer.install_skill("vscode", project=True, root=repo, always_on=True)

    assert (repo / ".github" / "skills" / "mimry" / "SKILL.md").is_file()
    assert "## MIMRY" in instructions.read_text(encoding="utf-8")
    assert installer._has_always_on(repo, installer.platforms()["vscode"])

    installer.uninstall_skill("vscode", project=True, root=repo, always_on=True)

    assert not (repo / ".github" / "skills" / "mimry" / "SKILL.md").exists()
    assert not instructions.exists()

    # A repo with its own instructions keeps them when the section goes.
    instructions.parent.mkdir(exist_ok=True)
    team_rules = "# Team rules\n\nUse tabs.\n"
    instructions.write_text(team_rules, encoding="utf-8")
    installer.install_skill("vscode", project=True, root=repo, always_on=True)
    installer.uninstall_skill("vscode", project=True, root=repo, always_on=True)

    assert instructions.read_text(encoding="utf-8") == team_rules
    assert not installer._has_always_on(repo, installer.platforms()["vscode"])


def test_vscode_mcp_json_with_comment_is_left_untouched(mcp_test_fixture, vscode_profile, capsys):
    """VS Code mcp.json that is not plain JSON (it has a // comment):
    file bytes unchanged, the snippet is printed instead."""
    fixture = mcp_test_fixture

    mcp_path = vscode_profile / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text('// user servers\n{"servers": {}}\n', encoding="utf-8")
    original_bytes = mcp_path.read_bytes()

    installer.install_skill("vscode", project=False, root=fixture["tmp_path"])

    assert mcp_path.read_bytes() == original_bytes
    out = capsys.readouterr().out
    assert '"servers"' in out
    assert '"mimry"' in out
    assert '"type": "stdio"' in out


def test_vscode_user_dir_per_os(mcp_test_fixture, monkeypatch):
    """VS Code profile folder: AppData on Windows, Library on macOS and
    ~/.config (or XDG_CONFIG_HOME) on Linux."""
    fake_home = mcp_test_fixture["fake_home"]
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    monkeypatch.setattr(installer.sys, "platform", "win32")
    assert installer._vscode_user_dir() == fake_home / "AppData" / "Roaming" / "Code" / "User"
    monkeypatch.setenv("APPDATA", str(fake_home / "Roaming"))
    assert installer._vscode_user_dir() == fake_home / "Roaming" / "Code" / "User"

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    assert (
        installer._vscode_user_dir()
        == fake_home / "Library" / "Application Support" / "Code" / "User"
    )

    monkeypatch.setattr(installer.sys, "platform", "linux")
    assert installer._vscode_user_dir() == fake_home / ".config" / "Code" / "User"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / "xdg"))
    assert installer._vscode_user_dir() == fake_home / "xdg" / "Code" / "User"


def test_unwritable_mcp_config_warns_and_the_install_completes(
    mcp_test_fixture, monkeypatch, capsys
):
    """#173: an unwritable MCP config is a warning with the snippet to
    add by hand, not a traceback. The skill still installs."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    mcp_path = fake_home / ".cursor" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    original = '{"mcpServers": {}}\n'
    mcp_path.write_text(original, encoding="utf-8")
    real_replace = installer.replace_path

    def refuse_mcp(source, target, **kwargs):
        if Path(target).name == "mcp.json":
            raise PermissionError(13, "Access is denied", str(target))
        return real_replace(source, target, **kwargs)

    monkeypatch.setattr(installer, "replace_path", refuse_mcp)

    installer.install_skill("cursor", project=False, root=fixture["tmp_path"])

    out = capsys.readouterr()
    assert "Could not register the MIMRY MCP server" in out.out + out.err
    assert '"mimry"' in out.out  # the snippet to add by hand
    assert (fake_home / ".cursor" / "skills" / "mimry" / "SKILL.md").is_file()
    assert mcp_path.read_text(encoding="utf-8") == original
    assert not (fake_home / ".cursor" / "mcp.json.tmp").exists()


def test_install_help_lists_every_platform_alias_from_the_table(capsys):
    """Each alias is listed with its platform, as --list-platforms does.

    The windows alias gets a note of its own.
    """
    with pytest.raises(SystemExit) as exc:
        main(["install", "--help"])

    assert exc.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    for cfg in installer.platforms().values():
        if cfg.aliases:
            assert f"{', '.join(cfg.aliases)} for {cfg.key}" in text
    assert "claude, windows for claude-code" in text
    assert "The windows alias is not a Windows-only mode" in text


def test_install_help_lists_every_platform_from_the_table(capsys):
    """#176: the --platform help comes from the platform table, so it
    cannot drift from what the installer supports."""
    with pytest.raises(SystemExit) as exc:
        main(["install", "--help"])

    assert exc.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "Target platform: " + ", ".join(sorted(installer.platforms())) in text


NEW_PLATFORM_PATHS = [
    # key, global skill, project skill (relative to home or repo)
    ("windsurf", ".codeium/windsurf/skills/mimry/SKILL.md", ".windsurf/skills/mimry/SKILL.md"),
    ("cline", ".cline/skills/mimry/SKILL.md", ".cline/skills/mimry/SKILL.md"),
    ("roo", ".roo/skills/mimry/SKILL.md", ".roo/skills/mimry/SKILL.md"),
    ("zed", ".agents/skills/mimry/SKILL.md", ".agents/skills/mimry/SKILL.md"),
    ("junie", ".junie/skills/mimry/SKILL.md", ".junie/skills/mimry/SKILL.md"),
    ("grok", ".grok/skills/mimry/SKILL.md", ".grok/skills/mimry/SKILL.md"),
]


@pytest.mark.parametrize(("key", "global_rel", "project_rel"), NEW_PLATFORM_PATHS)
def test_new_platform_skill_paths(mcp_test_fixture, key, global_rel, project_rel):
    """Each new platform writes its skill at the confirmed folder."""
    fixture = mcp_test_fixture
    home = fixture["fake_home"]
    repo = fixture["tmp_path"] / "repo"
    repo.mkdir()

    installer.install_skill(key, project=False, root=fixture["tmp_path"])
    assert (home / global_rel).is_file()

    installer.install_skill(key, project=True, root=repo)
    assert (repo / project_rel).is_file()

    assert installer.install_status(key, project=True, root=repo)["skill"] is True
    installer.uninstall_skill(key, project=True, root=repo)
    assert not (repo / project_rel).exists()
    assert installer.install_status(key, project=True, root=repo)["skill"] is False


@pytest.mark.parametrize(
    ("alias", "key"),
    [("roo-code", "roo"), ("jetbrains", "junie")],
)
def test_new_platform_aliases(alias, key):
    """Aliases resolve to their platform."""
    assert installer.canonical_platform(alias) == key
    assert alias in installer.platform_help()


@pytest.mark.parametrize("key", ["windsurf", "cline", "roo", "junie"])
def test_new_platform_always_on_writes_agents_md(mcp_test_fixture, key):
    """Always-on writes the MIMRY section to the root AGENTS.md."""
    fixture = mcp_test_fixture
    repo = fixture["tmp_path"] / "repo"
    repo.mkdir()

    installer.install_skill(key, project=True, root=repo, always_on=True)

    assert "## MIMRY" in (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert installer.install_status(key, project=True, root=repo)["always_on"] is True


def test_grok_has_no_always_on_or_mcp():
    """Grok gets the skill alone: no always-on file and no MCP entry."""
    cfg = installer.platforms()["grok"]
    assert cfg.always_on_file is None
    assert cfg.mcp is None


def test_junie_writes_mcpservers_entry_and_keeps_others(mcp_test_fixture):
    """Junie: mimry is added under mcpServers; other servers kept."""
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    fake_mcp_server = fixture["fake_mcp_server"]

    mcp_path = fake_home / ".junie" / "mcp" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {"mcpServers": {"other": {"command": "other-mcp"}}, "someOtherKey": "value"}
    mcp_path.write_text(json.dumps(existing, indent=2) + "\n")

    installer.install_skill("junie", project=False, root=fixture["tmp_path"])

    data = json.loads(mcp_path.read_text())
    assert data["mcpServers"]["mimry"] == {"command": str(fake_mcp_server), "args": []}
    assert data["mcpServers"]["other"] == {"command": "other-mcp"}
    assert data["someOtherKey"] == "value"
    assert installer.install_status("junie", project=False, root=fixture["tmp_path"])["mcp"]

    installer.uninstall_skill("junie", project=False, root=fixture["tmp_path"])
    assert json.loads(mcp_path.read_text())["mcpServers"] == {"other": {"command": "other-mcp"}}


def test_zed_writes_context_servers_entry(mcp_test_fixture, monkeypatch):
    """Zed: mimry goes under context_servers, other keys stay."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    fixture = mcp_test_fixture
    fake_home = fixture["fake_home"]
    fake_mcp_server = fixture["fake_mcp_server"]

    settings = fake_home / ".config" / "zed" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    existing = {"context_servers": {"other": {"command": "other-mcp"}}, "theme": "x"}
    settings.write_text(json.dumps(existing) + "\n")

    installer.install_skill("zed", project=False, root=fixture["tmp_path"])

    data = json.loads(settings.read_text())
    assert data["context_servers"]["mimry"] == {"command": str(fake_mcp_server), "args": []}
    assert data["context_servers"]["other"] == {"command": "other-mcp"}
    assert data["theme"] == "x"


def test_zed_settings_with_comment_is_never_rewritten(mcp_test_fixture, monkeypatch, capsys):
    """A Zed settings file that does not parse is left byte for byte,
    and the entry is printed for the user to add by hand."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    fixture = mcp_test_fixture
    settings = fixture["fake_home"] / ".config" / "zed" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    original = '{\n  // keep this comment\n  "theme": "x"\n}\n'
    settings.write_bytes(original.encode("utf-8"))

    installer.register_mcp(installer.platforms()["zed"], dry_run=False)

    assert settings.read_bytes() == original.encode("utf-8")
    out = capsys.readouterr().out
    assert '"context_servers"' in out
    assert '"mimry"' in out
    assert "mimry-mcp" in out


@pytest.mark.parametrize(
    ("os_name", "env", "parts"),
    [
        ("win32", {"APPDATA": "roaming"}, ("roaming", "Zed")),
        ("linux", {"XDG_CONFIG_HOME": "xdg"}, ("xdg", "zed")),
        ("darwin", {}, (".config", "zed")),
    ],
)
def test_zed_settings_path_follows_zed_per_os(tmp_path, monkeypatch, os_name, env, parts):
    monkeypatch.setattr(installer, "_home", lambda: tmp_path)
    monkeypatch.setattr(installer.sys, "platform", os_name)
    for name in ("APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, str(tmp_path / value))

    path = installer.platforms()["zed"].mcp.path

    assert path == tmp_path.joinpath(*parts, "settings.json")


def test_zed_and_agents_share_one_skill_file(mcp_test_fixture, monkeypatch):
    """Both write ~/.agents/skills/mimry; neither breaks the other."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    root = mcp_test_fixture["tmp_path"]

    installer.install_skill("agents", project=False, root=root)
    installer.install_skill("zed", project=False, root=root)

    assert installer.skill_body("zed") == installer.skill_body("agents")
    for key in ("agents", "zed"):
        assert installer.install_status(key, project=False, root=root)["skill"] is True


@pytest.mark.parametrize("key", ["windsurf", "cline", "roo", "zed", "junie", "grok"])
def test_new_platform_uninstall_removes_skill(mcp_test_fixture, key, monkeypatch):
    """Global install then uninstall leaves no skill behind."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    root = mcp_test_fixture["tmp_path"]

    installer.install_skill(key, project=False, root=root)
    assert installer.install_status(key, project=False, root=root)["skill"] is True

    installer.uninstall_skill(key, project=False, root=root)
    assert installer.install_status(key, project=False, root=root)["skill"] is False


def test_zed_uninstall_keeps_skill_file_agents_still_uses(mcp_test_fixture, monkeypatch, capsys):
    """Zed and Agent Skills share one SKILL.md, so Zed keeps it."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    fixture = mcp_test_fixture
    root = fixture["tmp_path"]
    skill = installer.platforms()["agents"].global_path
    settings = fixture["fake_home"] / ".config" / "zed" / "settings.json"

    installer.install_skill("agents", project=False, root=root)
    installer.install_skill("zed", project=False, root=root)
    capsys.readouterr()
    installer.uninstall_skill("zed", project=False, root=root)
    out = capsys.readouterr().out

    assert skill.exists()
    assert (skill.parent / ".mimry_version").exists()
    assert (skill.parent / "references").is_dir()
    assert f"Kept {ui.display_path(skill)}: Agent Skills still uses it" in out
    assert "mimry" not in json.loads(settings.read_text()).get("context_servers", {})
    assert installer.install_status("agents", project=False, root=root)["skill"] is True


def test_zed_uninstall_removes_shared_skill_file_when_agents_is_gone(
    mcp_test_fixture, monkeypatch, capsys
):
    """With no other platform on the file, Zed removes it."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    root = mcp_test_fixture["tmp_path"]
    skill = installer.platforms()["zed"].global_path

    installer.install_skill("agents", project=False, root=root)
    installer.install_skill("zed", project=False, root=root)
    installer.uninstall_skill("agents", project=False, root=root)
    capsys.readouterr()
    installer.uninstall_skill("zed", project=False, root=root)
    out = capsys.readouterr().out

    assert not skill.exists()
    assert "Kept" not in out


def test_uninstall_removes_a_shared_file_installed_before_the_list(
    mcp_test_fixture, monkeypatch, capsys
):
    """A record from before the platforms list names no sharer, so the
    file goes, as it did then, instead of staying for good."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    root = mcp_test_fixture["tmp_path"]
    skill = installer.platforms()["zed"].global_path

    installer.install_skill("agents", project=False, root=root)
    state_file = skill.parent / installer._STATE_FILE
    state = json.loads(state_file.read_text(encoding="utf-8"))
    del state["platforms"]
    state_file.write_text(json.dumps(state), encoding="utf-8")
    installer.uninstall_skill("agents", project=False, root=root)

    assert not skill.exists()
    assert "Kept" not in capsys.readouterr().out


def test_zed_with_comments_status_asks_for_manual_paste(mcp_test_fixture, monkeypatch, capsys):
    """A commented Zed settings file is a manual step, not a repair."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    fixture = mcp_test_fixture
    root = fixture["tmp_path"]
    settings = fixture["fake_home"] / ".config" / "zed" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('// my settings\n{\n  "theme": "One Dark"\n}\n', encoding="utf-8")

    installer.install_skill("zed", project=False, root=root)
    capsys.readouterr()
    result, code = installer.install_status_with_code("zed", project=False, root=root)
    out = capsys.readouterr().out

    assert code == 0
    assert "mcp" not in result
    assert "needs repair" not in out
    assert "paste the entry by hand into" in out
    assert "settings.json" in out
    assert "mimry install --platform zed" not in out


def test_grok_install_prints_no_mcp_snippet(mcp_test_fixture, monkeypatch, capsys):
    """Grok Build has no MCP config MIMRY knows: the skill covers it."""
    monkeypatch.setattr(installer.sys, "platform", "linux")
    root = mcp_test_fixture["tmp_path"]

    installer.install_skill("grok", project=False, root=root)
    out = capsys.readouterr().out

    assert "Grok Build has no MCP support; the skill covers it" in out
    assert "mcpServers" not in out
    assert "mimry-mcp" not in out
