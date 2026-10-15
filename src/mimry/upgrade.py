"""`mimry upgrade`: upgrade MIMRY through its install channel.

On Windows a running `mimry.exe` or `mimry-mcp.exe` locks its own files,
so the installer cannot replace them while this command, or an MCP
server, still runs. The upgrade therefore runs in a helper process that
starts after this command has exited.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import urllib.request
from importlib import metadata

from . import __version__, ui
from .doctor import _display, _mcp_server_pids
from .install_info import (
    command_text,
    direct_url,
    editable_folder,
    install_channel,
    upgrade_argv,
    upgrade_command,
)

_PYPI_URL = "https://pypi.org/pypi/mimry/json"
_IN_USE_MARKERS = ("os error 32", "being used by another process", "Access is denied")
_IN_USE_REASON = "A MIMRY file is in use by another program."
_TAIL_LINES = 5
_IN_USE_PATTERN = "|".join(map(re.escape, _IN_USE_MARKERS))
_MAX_WRAPPER_HOPS = 5
_WINDOWS_PROCESSES = (
    "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name"
    " | ConvertTo-Csv -NoTypeInformation"
)
_AGENT_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "code": "VS Code",
    "code - insiders": "VS Code",
}
_AGENT_GUIDE = (
    ("Claude Code", "type /mcp, select mimry, choose Reconnect"),
    ("Codex", "exit Codex and start it again"),
    ("VS Code", 'run "MCP: List Servers", select mimry, choose Restart Server'),
    ("Cursor", "turn mimry off and on in Cursor Settings > MCP"),
    ("Other agents", "restart them"),
)
_LOST_HEADER = "  Your agents lost their MIMRY connection. To reconnect:"


def _still_header(new: str) -> str:
    return f"  Your open agents still use {__version__}. To switch them to {new}:"


def _latest_version() -> str:
    with urllib.request.urlopen(_PYPI_URL, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["info"]["version"])


def _editable_source() -> str | None:
    try:
        dist = metadata.distribution("mimry")
    except metadata.PackageNotFoundError:
        return None
    return editable_folder(direct_url(dist))


def _mimry_argv(*args: str) -> list[str]:
    """Run the mimry on PATH, which after an upgrade is the new one."""
    exe = shutil.which("mimry")
    return [exe, *args] if exe else [sys.executable, "-m", "mimry.cli", *args]


def _version_argv() -> list[str]:
    return _mimry_argv("--version")


def _refresh_argv() -> list[str]:
    return _mimry_argv("install", "--refresh")


def _refresh_failed_line() -> str:
    return ui.headline(
        "warn",
        "Could not refresh the installed MIMRY skills. Run `mimry install --refresh` by hand.",
    )


def _refresh_after_upgrade() -> None:
    """Refresh the installed skills with the new MIMRY.

    This process still has the old templates in memory, so the refresh
    runs as a new process. Its output is indented under the result line.
    """
    try:
        result = subprocess.run(
            _refresh_argv(),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        print(_refresh_failed_line())
        return
    if result.returncode != 0:
        print(_refresh_failed_line())
        return
    for line in result.stdout.splitlines():
        if line.strip():
            print(f"  {line}")


def _installed_version() -> str | None:
    """The version the installed `mimry` reports, or None."""
    try:
        result = subprocess.run(
            _version_argv(),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    words = result.stdout.split()
    return words[-1] if result.returncode == 0 and words else None


def _stopped_line(count: int) -> str:
    return f"Stopped {ui.count(count, 'MIMRY MCP server')}."


def _reinstall_command() -> str:
    """The command that repairs a MIMRY with missing executables."""
    channel = install_channel()
    if channel == "uv tool":
        return "uv tool install --force mimry"
    if channel == "pipx":
        return "pipx reinstall mimry"
    return command_text([sys.executable, "-m", "pip", "install", "--force-reinstall", "mimry"])


def _failure_details(output: str) -> list[str]:
    """A plain reason for a file lock, else the last output lines."""
    lowered = output.lower()
    if any(marker.lower() in lowered for marker in _IN_USE_MARKERS):
        return [_IN_USE_REASON]
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-_TAIL_LINES:]


def _report_failure(latest: str, output: str) -> None:
    gone = shutil.which("mimry") is None
    headline = f"The upgrade to {latest} failed"
    if gone:
        headline += ", and the mimry command was removed"
    last = (
        f"Run `{_reinstall_command()}` to repair it."
        if gone
        else "Run `mimry upgrade` to try again."
    )
    ui.fail(headline, *_failure_details(output), last)


# --- Which agent started each server ---


def _bare_name(name: str) -> str:
    """A process name, lower case, without .exe."""
    name = name.strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def _process_table() -> dict[int, tuple[int, str]] | None:
    """pid -> (parent pid, name), or None if it cannot be read."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", _WINDOWS_PROCESSES],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return None
            table = {}
            for row in csv.reader(result.stdout.splitlines()):
                if len(row) >= 3 and row[0].isdigit() and row[1].isdigit():
                    table[int(row[0])] = (int(row[1]), row[2])
            return table
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    table = {}
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2].rsplit("/", 1)[-1])
    return table


def _agent_name(pid: int, table: dict[int, tuple[int, str]]) -> str | None:
    """The name of the process that started a server.

    Python and MIMRY launchers in between (a venv python launches the
    base python, so there can be several) are skipped, at most
    _MAX_WRAPPER_HOPS steps up.
    """
    entry = table.get(pid)
    if entry is None:
        return None
    parent = entry[0]
    for _ in range(_MAX_WRAPPER_HOPS):
        info = table.get(parent)
        if info is None or not _is_wrapper(info[1]):
            break
        parent = info[0]
    info = table.get(parent)
    return info[1] if info else None


def _is_wrapper(name: str) -> bool:
    bare = _bare_name(name)
    return bare in ("python", "python3", "pythonw") or bare.startswith("mimry")


def _agent_label(name: str | None) -> str:
    if name is None:
        return "Other agents"
    return _AGENT_NAMES.get(_bare_name(name), "Other agents")


def _agent_sessions(pids: list[int]) -> dict[str, int]:
    """Running servers per agent. Failures count as Other agents."""
    table = _process_table() or {}
    counts: dict[str, int] = {}
    for pid in pids:
        label = _agent_label(_agent_name(pid, table))
        counts[label] = counts.get(label, 0) + 1
    return counts


def _agent_lines(counts: dict[str, int]) -> list[str]:
    """The reconnect guidance, one line per agent found."""
    return [
        f"    {label} ({ui.count(counts[label], 'session')}): {action}"
        for label, action in _AGENT_GUIDE
        if counts.get(label)
    ]


# --- Windows helper ---


def _ps_quote(text: str) -> str:
    """A PowerShell single-quoted literal; a quote inside is doubled."""
    return "'" + text.replace("'", "''") + "'"


def _ps_line(text: str) -> str:
    return f"Write-Output ({_ps_quote(text)})"


def _parent_image(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for row in csv.reader(result.stdout.splitlines()):
        if row and row[0].lower().endswith(".exe"):
            return row[0]
    return None


def _windows_wait_pids() -> list[int]:
    """This process, plus its parent if that parent is a mimry*.exe."""
    pids = [os.getpid()]
    parent = os.getppid()
    image = (_parent_image(parent) or "").lower()
    if image.startswith("mimry") and image.endswith(".exe"):
        pids.append(parent)
    return pids


def _upgrade_script(
    argv: list[str],
    version_argv: list[str],
    pids: list[int],
    *,
    latest: str,
    repair: str,
    stopped: int,
    agent_lines: list[str],
    refresh_argv: list[str],
) -> str:
    """PowerShell: wait for the locks, upgrade, print the result.

    The installer's output is captured. Only MIMRY's own result lines
    are printed, with the same look as the POSIX path.
    """
    wait = f"Wait-Process -Id {','.join(map(str, pids))} -ErrorAction SilentlyContinue -Timeout 60"
    upgrade = "$out = (& " + " ".join(_ps_quote(part) for part in argv) + " 2>&1 | Out-String)"
    code = "$code = $LASTEXITCODE"
    version = (
        "$new = ''; if ($code -eq 0) { $new = ((& "
        + " ".join(_ps_quote(part) for part in version_argv)
        + " 2>&1 | Out-String).Trim() -split '\\s+')[-1] }"
    )
    tail = (
        "$lines = @($out -split '\\r?\\n' | Where-Object { $_.Trim() -ne '' }"
        f" | Select-Object -Last {_TAIL_LINES})"
    )
    gone = "$gone = $null -eq (Get-Command mimry -ErrorAction SilentlyContinue)"
    flagged = f"$inuse = $out -match '{_IN_USE_PATTERN}'"

    # Marks come from ui.py: coloured check and cross on a UTF-8
    # terminal, plain OK and x otherwise.
    ok_mark = _ps_quote(ui.headline("ok", f"Upgraded MIMRY {__version__} -> "))
    success_lines = [f"Write-Output ({ok_mark} + $new)"]
    # The new mimry refreshes the skills, only after a good upgrade.
    refresh_failed = _ps_quote(_refresh_failed_line())
    success_lines.extend(
        [
            "$refresh = (& "
            + " ".join(_ps_quote(part) for part in refresh_argv)
            + " 2>&1 | Out-String); $refreshCode = $LASTEXITCODE",
            "if ($refreshCode -eq 0) { $refresh -split '\\r?\\n'"
            " | Where-Object { $_.Trim() -ne '' }"
            " | ForEach-Object { Write-Output ('  ' + $_) } }"
            f" else {{ Write-Output {refresh_failed} }}",
        ]
    )
    if stopped:
        success_lines.append(_ps_line("  " + _stopped_line(stopped)))
    if agent_lines:
        if stopped:
            success_lines.append(_ps_line(_LOST_HEADER))
        else:
            still = f"  Your open agents still use {__version__}. To switch them to "
            success_lines.append(f"Write-Output ({_ps_quote(still)} + $new + ':')")
        success_lines.extend(_ps_line(line) for line in agent_lines)
    success = "; ".join([*success_lines, "exit 0"])

    failed = ui.headline("fail", f"The upgrade to {latest} failed")
    headline_gone = _ps_quote(f"{failed}, and the mimry command was removed")
    headline_present = _ps_quote(failed)
    last_gone = _ps_quote(f"  Run `{repair}` to repair it.")
    last_present = _ps_quote("  Run `mimry upgrade` to try again.")
    failure = "; ".join(
        [
            f"if ($gone) {{ Write-Output {headline_gone} }}"
            f" else {{ Write-Output {headline_present} }}",
            "if ($inuse) { Write-Output "
            + _ps_quote(f"  {_IN_USE_REASON}")
            + " } else { foreach ($line in $lines) { Write-Output ('  ' + $line) } }",
            f"if ($gone) {{ Write-Output {last_gone} }} else {{ Write-Output {last_present} }}",
            "exit 1",
        ]
    )
    return "; ".join(
        [
            wait,
            upgrade,
            code,
            version,
            tail,
            gone,
            flagged,
            f"if ($code -eq 0 -and $new) {{ {success} }} else {{ {failure} }}",
        ]
    )


def _start_windows_helper(
    argv: list[str],
    *,
    latest: str,
    repair: str,
    stopped: int,
    agent_lines: list[str],
) -> None:
    script = _upgrade_script(
        argv,
        _version_argv(),
        _windows_wait_pids(),
        latest=latest,
        repair=repair,
        stopped=stopped,
        agent_lines=agent_lines,
        refresh_argv=_refresh_argv(),
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        # A new process group on the same console: output stays visible.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        stdin=subprocess.DEVNULL,
    )


# --- Stopping servers ---


def _refuse_in_use(pids: list[int] | None, command: str) -> int:
    if pids is None:
        listed = "Running MIMRY MCP servers could not be listed."
    else:
        verb = "is" if len(pids) == 1 else "are"
        count = ui.count(len(pids), "MIMRY MCP server")
        listed = f"{count} {verb} running, started by your open agents."
    ui.fail(
        "Can't upgrade while MIMRY is in use",
        listed,
        "Windows locks their files, so the upgrade would stop halfway and remove the mimry"
        " command.",
        "Close those agents, or run `mimry upgrade --stop-servers` to stop the servers for you.",
    )
    return 1


def _stop_windows_servers(pids: list[int]) -> bool:
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass  # the list below decides whether it stopped
    remaining = _mcp_server_pids()
    if remaining is None or remaining:
        still = "Running MIMRY MCP servers could not be listed."
        if remaining:
            still = f"Still running: PID {', '.join(map(str, remaining))}."
        ui.fail(
            "Couldn't stop the running MIMRY MCP servers",
            still,
            "Nothing was upgraded. Close the agents that use MIMRY, then run"
            " `mimry upgrade` again.",
        )
        return False
    return True


def _stop_posix_servers(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass  # already gone


# --- The command ---


def _upgrade(stop_servers: bool) -> int:
    folder = _editable_source()
    if folder:
        ui.fail(
            f"This MIMRY is a development install from {_display(folder)}",
            "Update it with git pull in that folder.",
        )
        return 1
    command = upgrade_command()
    try:
        latest = _latest_version()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ui.fail(
            "Couldn't check PyPI for the latest version",
            str(exc),
            f"Run `{command}` to upgrade anyway.",
        )
        return 1
    if latest == __version__:
        ui.ok(f"MIMRY {__version__} is the latest version")
        return 0

    channel = install_channel()
    argv = upgrade_argv(channel)
    if channel in ("uv tool", "pipx"):
        resolved = shutil.which(argv[0])
        if resolved is None:
            ui.fail(
                f"Can't find {argv[0]} on PATH",
                f"Install {argv[0]}, then run `{command}`.",
            )
            return 1
        argv[0] = resolved

    if sys.platform == "win32":
        pids = _mcp_server_pids()
        if pids is None or pids:
            if not stop_servers or pids is None:
                return _refuse_in_use(pids, command)
            # Sessions are read before the servers are stopped.
            agent_lines = _agent_lines(_agent_sessions(pids))
            if not _stop_windows_servers(pids):
                return 1
            stopped = len(pids)
        else:
            agent_lines, stopped = [], 0
        _start_windows_helper(
            argv,
            latest=latest,
            repair=_reinstall_command(),
            stopped=stopped,
            agent_lines=agent_lines,
        )
        ui.ok(f"Upgrading MIMRY {__version__} -> {latest}")
        ui.detail("Finishing after this command exits; the result prints below in a few seconds.")
        return 0

    running = _mcp_server_pids() or []
    agent_lines = _agent_lines(_agent_sessions(running)) if running else []
    stopped = 0
    if stop_servers and running:
        _stop_posix_servers(running)
        stopped = len(running)
    result = subprocess.run(
        argv,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        _report_failure(latest, f"{result.stdout}\n{result.stderr}")
        return 1
    new = _installed_version()
    if new is None:
        ui.fail(
            "The upgrade finished, but `mimry --version` failed",
            "Run `mimry --version` to check the installed version.",
        )
        return 1
    ui.ok(f"Upgraded MIMRY {__version__} -> {new}")
    _refresh_after_upgrade()
    if stopped:
        ui.detail(_stopped_line(stopped))
    if agent_lines:
        print(_LOST_HEADER if stopped else _still_header(new))
        for line in agent_lines:
            print(line)
    return 0


def cmd_upgrade(args) -> int:
    try:
        return _upgrade(args.stop_servers)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        ui.fail("The upgrade could not run", str(exc), f"Run `{upgrade_command()}` to retry.")
        return 1
