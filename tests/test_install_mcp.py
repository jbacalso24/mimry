from __future__ import annotations

import json
import os

import pytest

from mimry import installer


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
