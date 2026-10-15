from __future__ import annotations

import io
import json
import signal
import subprocess
import urllib.error
import urllib.request

import pytest

import mimry.install_info as install_info
import mimry.upgrade as upgrade
from mimry import __version__, ui
from mimry.cli import build_parser, main

UV = "C:\\Users\\Jo O'Neil\\My Tools\\uv.exe"
MIMRY_EXE = "C:\\Users\\Jo O'Neil\\My Tools\\mimry.exe"
_REAL_LATEST = upgrade._latest_version
_CLAUDE_LINE = "    Claude Code (1 session): type /mcp, select mimry, choose Reconnect"


def _no_process(args, **kwargs):
    raise AssertionError(f"unexpected process: {args}")


@pytest.fixture
def host(monkeypatch):
    """uv install, newer release, no servers running."""
    monkeypatch.setattr(upgrade, "_editable_source", lambda: None)
    monkeypatch.setattr(upgrade, "_latest_version", lambda: "9.9.9")
    monkeypatch.setattr(upgrade, "_mcp_server_pids", lambda: [])
    monkeypatch.setattr(upgrade, "install_channel", lambda: "uv tool")
    monkeypatch.setattr(install_info, "install_channel", lambda: "uv tool")
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(upgrade.subprocess, "run", _no_process)
    monkeypatch.setattr(upgrade.subprocess, "Popen", _no_process)
    return monkeypatch


def _windows(monkeypatch):
    monkeypatch.setattr(upgrade.sys, "platform", "win32")
    monkeypatch.setattr(upgrade.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)


def test_editable_install_refuses_and_runs_nothing(host, capsys):
    host.setattr(upgrade, "_editable_source", lambda: "/home/me/Documents/mimry")

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    assert "This MIMRY is a development install from" in err
    assert "Update it with git pull in that folder." in err


def test_pypi_unreachable_names_the_retry_command(host, capsys):
    host.setattr(upgrade, "_latest_version", _REAL_LATEST)

    def offline(url, timeout):
        raise urllib.error.URLError("no route to host")

    host.setattr(urllib.request, "urlopen", offline)

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    assert "Couldn't check PyPI for the latest version" in err
    assert "no route to host" in err
    assert f"Run `{install_info.upgrade_command()}` to upgrade anyway." in err


def test_pypi_answer_is_read_from_info_version(host):
    host.setattr(upgrade, "_latest_version", _REAL_LATEST)

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    body = json.dumps({"info": {"version": "9.9.9"}}).encode("utf-8")
    host.setattr(urllib.request, "urlopen", lambda url, timeout: Response(body))

    assert upgrade._latest_version() == "9.9.9"


def test_latest_version_equal_to_current_exits_0_and_runs_nothing(host, capsys):
    host.setattr(upgrade, "_latest_version", lambda: __version__)

    assert main(["upgrade"]) == 0

    out = capsys.readouterr().out
    assert f"OK MIMRY {__version__} is the latest version" in out


@pytest.mark.parametrize(
    ("channel", "argv"),
    [
        ("uv tool", ["uv", "tool", "upgrade", "mimry"]),
        ("pipx", ["pipx", "upgrade", "mimry"]),
        (None, [upgrade.sys.executable, "-m", "pip", "install", "--upgrade", "mimry"]),
    ],
)
def test_each_channel_maps_to_its_upgrade_command(monkeypatch, channel, argv):
    monkeypatch.setattr(install_info, "install_channel", lambda: channel)

    assert install_info.upgrade_argv(channel) == argv
    if channel == "pipx":
        assert install_info.repair_command() == "pipx reinstall mimry"
    else:
        assert install_info.repair_command() == install_info.upgrade_command()
    assert install_info.upgrade_command().split()[-3:] == argv[-3:]


def test_uv_missing_from_path_says_what_to_install(host, capsys):
    host.setattr(upgrade.shutil, "which", lambda name: None)

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    assert "Can't find uv on PATH" in err
    assert "Install uv, then run `uv tool upgrade mimry`." in err


def test_windows_servers_running_refuses_with_the_exact_message(host, capsys):
    _windows(host)
    host.setattr(upgrade, "_mcp_server_pids", lambda: [101, 202])

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    assert err == (
        "x Can't upgrade while MIMRY is in use\n"
        "  2 MIMRY MCP servers are running, started by your open agents.\n"
        "  Windows locks their files, so the upgrade would stop halfway and remove the mimry"
        " command.\n"
        "  Close those agents, or run `mimry upgrade --stop-servers` to stop the"
        " servers for you.\n"
    )


def test_windows_unlistable_servers_refuse_too(host, capsys):
    _windows(host)
    host.setattr(upgrade, "_mcp_server_pids", lambda: None)
    host.setattr(upgrade.subprocess, "run", lambda args, **kwargs: pytest.fail(str(args)))

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    assert "Running MIMRY MCP servers could not be listed." in err
    assert "started by your open agents" not in err


def test_windows_stop_servers_taskkills_each_pid_then_upgrades(host, capsys):
    _windows(host)
    listings = iter([[101, 202], []])
    host.setattr(upgrade, "_mcp_server_pids", lambda: next(listings))
    host.setattr(upgrade, "_windows_wait_pids", lambda: [4242])
    taskkill = []
    popen = []

    def fake_run(args, **kwargs):
        if args[0] == "taskkill":
            taskkill.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    host.setattr(upgrade.subprocess, "run", fake_run)
    host.setattr(upgrade.subprocess, "Popen", lambda args, **kwargs: popen.append(args))

    assert main(["upgrade", "--stop-servers"]) == 0

    assert [args for args, _ in taskkill] == [
        ["taskkill", "/F", "/T", "/PID", "101"],
        ["taskkill", "/F", "/T", "/PID", "202"],
    ]
    assert all(kwargs["stdin"] is subprocess.DEVNULL for _, kwargs in taskkill)
    assert all(kwargs["timeout"] == 10 for _, kwargs in taskkill)
    assert len(popen) == 1
    # The helper prints the stopped line and the guidance on success.
    script = popen[0][4]
    assert _ps_detail("Stopped 2 MIMRY MCP servers.") in script
    assert "Your agents lost their MIMRY connection. To reconnect:" in script
    assert "Other agents (2 sessions): restart them" in script
    assert "Stopped 2 MIMRY MCP servers" not in capsys.readouterr().out


def test_windows_server_that_survives_stops_the_upgrade(host, capsys):
    _windows(host)
    listings = iter([[101, 202], [202]])
    host.setattr(upgrade, "_mcp_server_pids", lambda: next(listings))
    taskkill = []
    popen = []

    def fake_run(args, **kwargs):
        if args[0] == "taskkill":
            taskkill.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    host.setattr(upgrade.subprocess, "run", fake_run)
    host.setattr(upgrade.subprocess, "Popen", lambda args, **kwargs: popen.append(args))

    assert main(["upgrade", "--stop-servers"]) == 1

    assert len(taskkill) == 2
    assert popen == []
    assert "Still running: PID 202." in capsys.readouterr().err


def test_windows_happy_path_hands_the_upgrade_to_a_helper(host, capsys):
    _windows(host)
    host.setattr(upgrade.shutil, "which", lambda name: {"uv": UV, "mimry": MIMRY_EXE}[name])
    host.setattr(upgrade, "_windows_wait_pids", lambda: [4242, 4000])
    popen = []

    def fake_popen(args, **kwargs):
        popen.append((args, kwargs))

    host.setattr(upgrade.subprocess, "Popen", fake_popen)

    assert main(["upgrade"]) == 0

    assert len(popen) == 1
    args, kwargs = popen[0]
    assert kwargs["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["close_fds"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert args[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    script = args[4]
    assert "Wait-Process -Id 4242,4000 -ErrorAction SilentlyContinue -Timeout 60" in script
    quoted_uv = "'C:\\Users\\Jo O''Neil\\My Tools\\uv.exe'"
    assert f"$out = (& {quoted_uv} 'tool' 'upgrade' 'mimry' 2>&1 | Out-String)" in script
    assert "Select-Object -Last 5" in script
    assert "Get-Command mimry" in script
    assert upgrade._IN_USE_PATTERN in script
    assert "MIMRY upgrade finished." not in script
    # No servers ran, so no reconnect guidance is printed.
    assert "still use" not in script
    assert "Restart your agents" not in script
    out = capsys.readouterr().out
    assert f"OK Upgrading MIMRY {__version__} -> 9.9.9" in out
    assert "Finishing after this command exits; the result prints below in a few seconds." in out


def test_windows_script_prints_marks_from_the_ui_module():
    ok = ui.headline("ok", f"Upgraded MIMRY {__version__} -> ")
    failed = ui.headline("fail", "The upgrade to 9.9.9 failed")
    script = upgrade._upgrade_script(
        ["uv", "tool", "upgrade", "mimry"],
        ["mimry", "--version"],
        [1],
        latest="9.9.9",
        repair="uv tool install --force mimry",
        stopped=0,
        agent_lines=[],
    )

    assert upgrade._ps_quote(ok) in script
    assert upgrade._ps_quote(failed) in script
    assert upgrade._ps_quote(failed + ", and the mimry command was removed") in script


def test_windows_script_says_mimry_is_present_or_removed_and_what_to_run():
    script = upgrade._upgrade_script(
        ["uv", "tool", "upgrade", "mimry"],
        ["mimry", "--version"],
        [1],
        latest="9.9.9",
        repair="uv tool install --force mimry",
        stopped=0,
        agent_lines=[],
    )

    assert upgrade._ps_quote("  Run `mimry upgrade` to try again.") in script
    assert upgrade._ps_quote("  Run `uv tool install --force mimry` to repair it.") in script
    assert upgrade._ps_quote(f"  {upgrade._IN_USE_REASON}") in script


def test_windows_script_carries_the_guidance_only_when_agents_are_known():
    script = upgrade._upgrade_script(
        ["uv", "tool", "upgrade", "mimry"],
        ["mimry", "--version"],
        [1],
        latest="9.9.9",
        repair="uv tool install --force mimry",
        stopped=0,
        agent_lines=[_CLAUDE_LINE],
    )

    assert upgrade._ps_quote(
        f"  Your open agents still use {__version__}. To switch them to "
    ) in (script)
    assert upgrade._ps_quote(_CLAUDE_LINE) in script
    assert "+ $new + ':'" in script

    stopped = upgrade._upgrade_script(
        ["uv", "tool", "upgrade", "mimry"],
        ["mimry", "--version"],
        [1],
        latest="9.9.9",
        repair="uv tool install --force mimry",
        stopped=2,
        agent_lines=[_CLAUDE_LINE],
    )

    assert upgrade._ps_quote("  Stopped 2 MIMRY MCP servers.") in stopped
    assert upgrade._ps_quote("  Your agents lost their MIMRY connection. To reconnect:") in stopped
    assert "still use" not in stopped


def test_windows_wait_includes_the_launcher_only_when_it_is_mimry(monkeypatch):
    monkeypatch.setattr(upgrade.os, "getpid", lambda: 4242)
    monkeypatch.setattr(upgrade.os, "getppid", lambda: 4000)
    monkeypatch.setattr(upgrade, "_parent_image", lambda pid: "mimry.exe")
    assert upgrade._windows_wait_pids() == [4242, 4000]

    monkeypatch.setattr(upgrade, "_parent_image", lambda pid: "python.exe")
    assert upgrade._windows_wait_pids() == [4242]


def test_parent_image_reads_the_tasklist_csv(monkeypatch):
    rows = '"mimry.exe","4000","Console","1","9,000 K"\n'
    monkeypatch.setattr(
        upgrade.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, rows, ""),
    )

    assert upgrade._parent_image(4000) == "mimry.exe"


def _posix_run(
    calls,
    *,
    upgrade_code=0,
    upgrade_out="",
    version="mimry 9.9.9\n",
    ps_out="",
):
    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, ps_out, "")
        if args[-1] == "--version":
            return subprocess.CompletedProcess(args, 0, version, "")
        return subprocess.CompletedProcess(args, upgrade_code, upgrade_out, "")

    return fake_run


def test_posix_upgrade_runs_inline_and_prints_only_the_result(host, capsys):
    host.setattr(upgrade.sys, "platform", "linux")
    calls = []
    host.setattr(
        upgrade.subprocess,
        "run",
        _posix_run(calls, upgrade_out="Resolved 3 packages\nInstalled 1 package in 12ms\n"),
    )

    assert main(["upgrade"]) == 0

    upgrade_args, upgrade_kwargs = calls[0]
    assert upgrade_args == ["/bin/uv", "tool", "upgrade", "mimry"]
    assert upgrade_kwargs["capture_output"] is True
    assert calls[1][0] == ["/bin/mimry", "--version"]
    captured = capsys.readouterr()
    assert f"OK Upgraded MIMRY {__version__} -> 9.9.9" in captured.out
    assert "Installed 1 package" not in captured.out + captured.err
    assert "Resolved 3 packages" not in captured.out + captured.err


def test_posix_failed_installer_shows_the_reason_and_the_retry(host, capsys):
    host.setattr(upgrade.sys, "platform", "linux")
    output = "error: failed to remove directory x: Access is denied. (os error 5)\n"
    host.setattr(upgrade.subprocess, "run", _posix_run([], upgrade_code=2, upgrade_out=output))

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    assert "x The upgrade to 9.9.9 failed\n" in err
    assert "  A MIMRY file is in use by another program.\n" in err
    assert "error: failed to remove" not in err
    assert "  Run `mimry upgrade` to try again.\n" in err


def test_posix_failed_installer_shows_only_the_last_five_lines(host, capsys):
    host.setattr(upgrade.sys, "platform", "linux")
    output = "".join(f"line {n}\n" for n in range(1, 9))
    host.setattr(upgrade.subprocess, "run", _posix_run([], upgrade_code=2, upgrade_out=output))

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    for n in range(4, 9):
        assert f"  line {n}\n" in err
    assert "line 3" not in err


def test_posix_failure_with_mimry_removed_says_to_repair(host, capsys):
    host.setattr(upgrade.sys, "platform", "linux")
    host.setattr(upgrade.shutil, "which", lambda name: None if name == "mimry" else f"/bin/{name}")
    host.setattr(upgrade.subprocess, "run", _posix_run([], upgrade_code=2, upgrade_out="boom\n"))

    assert main(["upgrade"]) == 1

    err = capsys.readouterr().err
    assert "x The upgrade to 9.9.9 failed, and the mimry command was removed\n" in err
    assert "  Run `uv tool install --force mimry` to repair it.\n" in err
    assert "to try again" not in err


def test_posix_running_servers_show_per_agent_guidance(host, capsys):
    host.setattr(upgrade.sys, "platform", "linux")
    host.setattr(upgrade, "_mcp_server_pids", lambda: [101])
    host.setattr(
        upgrade.subprocess,
        "run",
        _posix_run([], ps_out="50 1 claude\n101 50 mimry-mcp\n"),
    )

    assert main(["upgrade"]) == 0

    out = capsys.readouterr().out
    assert f"  Your open agents still use {__version__}. To switch them to 9.9.9:" in out
    assert _CLAUDE_LINE in out
    assert "Restart your agents" not in out


def test_no_guidance_prints_when_no_servers_were_running(host, capsys):
    host.setattr(upgrade.sys, "platform", "linux")
    host.setattr(upgrade.subprocess, "run", _posix_run([]))

    assert main(["upgrade"]) == 0

    out = capsys.readouterr().out
    assert f"OK Upgraded MIMRY {__version__} -> 9.9.9" in out
    assert "still use" not in out
    assert "lost their MIMRY connection" not in out


def test_posix_stop_servers_sends_sigterm_and_reports_it(host, capsys):
    host.setattr(upgrade.sys, "platform", "linux")
    host.setattr(upgrade, "_mcp_server_pids", lambda: [101, 202])
    host.setattr(upgrade.subprocess, "run", _posix_run([]))
    signalled = []
    host.setattr(upgrade.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    assert main(["upgrade", "--stop-servers"]) == 0

    assert signalled == [(101, signal.SIGTERM), (202, signal.SIGTERM)]
    out = capsys.readouterr().out
    assert "  Stopped 2 MIMRY MCP servers.\n" in out
    assert "  Your agents lost their MIMRY connection. To reconnect:\n" in out
    assert "    Other agents (2 sessions): restart them" in out
    assert out.index("Stopped 2") < out.index("lost their MIMRY connection")
    assert "still use" not in out


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("claude.exe", "Claude Code"),
        ("Claude", "Claude Code"),
        ("codex", "Codex"),
        ("Cursor.exe", "Cursor"),
        ("code", "VS Code"),
        ("Code - Insiders.exe", "VS Code"),
        ("node", "Other agents"),
        (None, "Other agents"),
    ],
)
def test_agent_label_maps_known_names_and_defaults_to_other(name, label):
    assert upgrade._agent_label(name) == label


def test_agent_name_walks_up_past_python_and_mimry_launchers():
    table = {101: (60, "mimry-mcp.exe"), 60: (50, "python.exe"), 50: (1, "claude.exe")}
    assert upgrade._agent_name(101, table) == "claude.exe"

    # A venv python starts the base python: two levels to claude.exe.
    venv = {
        101: (60, "mimry-mcp.exe"),
        60: (70, "python.exe"),
        70: (50, "python.exe"),
        50: (1, "claude.exe"),
    }
    assert upgrade._agent_name(101, venv) == "claude.exe"

    # The walk stops after a bounded number of python hops.
    deep = {101: (60, "mimry-mcp.exe")}
    for pid in range(60, 60 + 2 * upgrade._MAX_WRAPPER_HOPS, 1):
        deep[pid] = (pid + 1, "python.exe")
    deep[60 + 2 * upgrade._MAX_WRAPPER_HOPS] = (1, "claude.exe")
    assert upgrade._agent_name(101, deep) == "python.exe"

    direct = {101: (50, "mimry-mcp.exe"), 50: (1, "codex.exe")}
    assert upgrade._agent_name(101, direct) == "codex.exe"

    other = {101: (50, "mimry-mcp.exe"), 50: (1, "node.exe")}
    assert upgrade._agent_name(101, other) == "node.exe"

    assert upgrade._agent_name(999, table) is None


def test_agent_sessions_counts_each_agent(monkeypatch):
    table = {
        101: (50, "mimry-mcp.exe"),
        102: (50, "mimry-mcp.exe"),
        103: (60, "mimry-mcp.exe"),
        50: (1, "claude.exe"),
        60: (1, "node.exe"),
    }
    monkeypatch.setattr(upgrade, "_process_table", lambda: table)

    assert upgrade._agent_sessions([101, 102, 103]) == {"Claude Code": 2, "Other agents": 1}


@pytest.mark.parametrize("failure", ["raise", "exit"])
def test_agent_sessions_fall_back_to_other_agents_when_the_listing_fails(monkeypatch, failure):
    def broken(args, **kwargs):
        if failure == "raise":
            raise OSError("no ps")
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(upgrade.subprocess, "run", broken)
    monkeypatch.setattr(upgrade.sys, "platform", "linux")

    assert upgrade._process_table() is None
    assert upgrade._agent_sessions([101, 202]) == {"Other agents": 2}


def test_process_table_parses_ps_on_posix(monkeypatch):
    ps = "  50     1 /usr/bin/claude\n 101    50 mimry-mcp\nnot a row\n"
    monkeypatch.setattr(upgrade.sys, "platform", "linux")
    monkeypatch.setattr(
        upgrade.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, ps, ""),
    )

    assert upgrade._process_table() == {50: (1, "claude"), 101: (50, "mimry-mcp")}


def test_process_table_parses_powershell_csv_on_windows(monkeypatch):
    csv_out = (
        '"ProcessId","ParentProcessId","Name"\n"101","50","mimry-mcp.exe"\n"50","1","claude.exe"\n'
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, csv_out, "")

    monkeypatch.setattr(upgrade.sys, "platform", "win32")
    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)

    assert upgrade._process_table() == {101: (50, "mimry-mcp.exe"), 50: (1, "claude.exe")}
    args, kwargs = calls[0]
    assert args[:3] == ["powershell", "-NoProfile", "-Command"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 10


def test_agent_lines_use_singular_and_plural_and_fixed_order():
    counts = {
        "Other agents": 2,
        "Codex": 1,
        "Claude Code": 3,
        "VS Code": 1,
        "Cursor": 1,
    }

    assert upgrade._agent_lines(counts) == [
        "    Claude Code (3 sessions): type /mcp, select mimry, choose Reconnect",
        "    Codex (1 session): exit Codex and start it again",
        '    VS Code (1 session): run "MCP: List Servers", select mimry, choose Restart Server',
        "    Cursor (1 session): turn mimry off and on in Cursor Settings > MCP",
        "    Other agents (2 sessions): restart them",
    ]
    assert upgrade._agent_lines({}) == []


@pytest.mark.parametrize("argv", [["update"], ["upgrade"]])
def test_update_is_an_alias_of_upgrade(argv):
    parser = build_parser()

    args = parser.parse_args(argv)

    assert args.func is upgrade.cmd_upgrade
    assert args.stop_servers is False
    assert parser.parse_args([*argv, "--stop-servers"]).stop_servers is True


def test_ps_quote_doubles_single_quotes_and_leaves_everything_else_literal():
    assert upgrade._ps_quote("plain") == "'plain'"
    assert upgrade._ps_quote("") == "''"
    assert upgrade._ps_quote("it's") == "'it''s'"
    assert upgrade._ps_quote("'''") == "'" + "''" * 3 + "'"
    assert upgrade._ps_quote("C:\\My Tools\\O'Neil") == "'C:\\My Tools\\O''Neil'"
    assert upgrade._ps_quote('$x "y" `z` $(boom)') == "'$x \"y\" `z` $(boom)'"
    assert upgrade._ps_quote("\x1b[32m\u2713\x1b[0m") == "'\x1b[32m\u2713\x1b[0m'"


def _ps_detail(text: str) -> str:
    return f"Write-Output ({upgrade._ps_quote('  ' + text)})"
