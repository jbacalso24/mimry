"""Isolated real-client integration smoke for MIMRY's MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REQUIRED_TOOLS = {
    "mimry_context",
    "mimry_find",
    "mimry_init",
    "mimry_preflight",
    "mimry_status",
}
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "SYSTEMROOT")


def _run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _require_ok(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{label} failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def _client_env(sandbox: Path, *, client: str) -> dict[str, str]:
    """Build a fail-closed client environment without operator credentials."""
    env = {name: os.environ[name] for name in _ENV_ALLOWLIST if os.environ.get(name)}
    home = sandbox / f"{client}-home"
    config = sandbox / f"{client}-config"
    cache = sandbox / f"{client}-cache"
    for path in (home, config, cache):
        path.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            # Windows resolves Path.home() from USERPROFILE (then HOMEDRIVE+HOMEPATH)
            # and ignores HOME entirely. Without these the server subprocess raises
            # "Could not determine home directory" at startup and the client just
            # waits out its timeout with no diagnosable error. Point them at the same
            # sandbox home so isolation is unchanged.
            "USERPROFILE": str(home),
            "HOMEDRIVE": home.drive,
            "HOMEPATH": str(home)[len(home.drive) :],
            "XDG_CONFIG_HOME": str(config),
            "XDG_CACHE_HOME": str(cache),
            "FASTMCP_HOME": str(sandbox / f"{client}-fastmcp"),
            "MIMRY_CACHE_HOME": str(sandbox / "mimry-cache"),
            "NO_COLOR": "1",
        }
    )
    if client == "claude":
        env["CLAUDE_CONFIG_DIR"] = str(config)
    elif client == "codex":
        env["CODEX_HOME"] = str(config)
    return env


async def _protocol_smoke(repo: Path, cache: Path, server_command: list[str]) -> dict[str, Any]:
    env = _client_env(cache.parent, client="protocol")
    env["MIMRY_CACHE_HOME"] = str(cache)
    transport = StdioTransport(server_command[0], server_command[1:], cwd=str(repo), env=env)
    async with Client(transport, timeout=30) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}
        missing = sorted(REQUIRED_TOOLS - tool_names)
        if missing:
            raise RuntimeError(f"MCP protocol is missing required tools: {', '.join(missing)}")

        init = (await client.call_tool("mimry_init", {"root": str(repo), "skip_graph": True})).data
        if init.get("returncode") != 0:
            raise RuntimeError(f"mimry_init failed over MCP: {init}")

        preflight = (
            await client.call_tool(
                "mimry_preflight",
                {"query": "locate daily agent entrypoint", "root": str(repo)},
            )
        ).data
        if preflight.get("returncode") != 0 or preflight.get("index_state") != "current":
            raise RuntimeError(f"mimry_preflight failed over MCP: {preflight}")

        found = (
            await client.call_tool(
                "mimry_find",
                {"query": "daily agent entrypoint", "root": str(repo), "limit": 3},
            )
        ).data
        paths = [row.get("path") for row in found.get("results", [])]
        if "src/entrypoint.py" not in paths:
            raise RuntimeError(f"mimry_find did not return the fixture source: {paths}")

        context = (
            await client.call_tool(
                "mimry_context",
                {"query": "daily agent entrypoint", "root": str(repo)},
            )
        ).data
        context_path = Path(context.get("output", ""))
        if not context_path.is_file() or "## Source of Truth Reminder" not in context_path.read_text(encoding="utf-8"):
            raise RuntimeError("mimry_context did not write an evidence-grade context pack")

    return {
        "state": "PASS",
        "transport": "stdio",
        "tools": len(tool_names),
        "required_tools": sorted(REQUIRED_TOOLS),
        "find_paths": paths,
        "context": str(context_path.relative_to(repo)),
    }


def _claude_smoke(sandbox: Path, repo: Path, server_command: list[str]) -> dict[str, Any]:
    binary = shutil.which("claude")
    if not binary:
        return {"state": "UNVERIFIED", "blocker": "claude binary not found"}

    env = _client_env(sandbox, client="claude")
    add = _run([binary, "mcp", "add", "--scope", "user", "mimry-smoke", "--", *server_command], cwd=repo, env=env)
    _require_ok(add, "Claude Code MCP registration")
    get = _run([binary, "mcp", "get", "mimry-smoke"], cwd=repo, env=env)
    output = _require_ok(get, "Claude Code MCP health check")
    if "Connected" not in output or server_command[0] not in output:
        raise RuntimeError(f"Claude Code did not report the isolated MIMRY server connected: {output}")
    return {
        "state": "PASS",
        "version": _require_ok(_run([binary, "--version"], cwd=repo, env=env), "Claude Code version"),
        "health": "Connected",
        "config_home": "temporary isolated sandbox",
    }


def _codex_smoke(sandbox: Path, repo: Path, server_command: list[str]) -> dict[str, Any]:
    binary = shutil.which("codex")
    if not binary:
        return {"state": "UNVERIFIED", "blocker": "codex binary not found"}

    env = _client_env(sandbox, client="codex")
    add = _run([binary, "mcp", "add", "mimry-smoke", "--", *server_command], cwd=repo, env=env)
    _require_ok(add, "Codex MCP registration")
    get = _run([binary, "mcp", "get", "mimry-smoke", "--json"], cwd=repo, env=env)
    payload = json.loads(_require_ok(get, "Codex MCP config readback"))
    transport = payload.get("transport", {})
    if not payload.get("enabled") or transport.get("type") != "stdio" or transport.get("command") != server_command[0]:
        raise RuntimeError(f"Codex MCP registration readback mismatch: {payload}")
    return {
        "state": "PASS",
        "version": _require_ok(_run([binary, "--version"], cwd=repo, env=env), "Codex version"),
        "transport": "stdio",
        "config_readback": "matched",
        "config_home": "temporary isolated sandbox",
    }


def _herdr_status() -> dict[str, Any]:
    binary = shutil.which("herdr")
    blocker = (
        "Herdr runtime detected, but pane-only round-trip proof requires an active isolated pane harness."
        if binary
        else "Herdr runtime not detected; pane-only integration was not simulated."
    )
    return {"state": "UNVERIFIED", "detected": bool(binary), "blocker": blocker}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely smoke MIMRY through real MCP, Claude Code, and Codex clients.")
    parser.add_argument("--json-output", type=Path, help="Optional path for the machine-readable report.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mimry-agent-integration-") as raw_sandbox:
        sandbox = Path(raw_sandbox)
        repo = sandbox / "repo"
        source = repo / "src" / "entrypoint.py"
        source.parent.mkdir(parents=True)
        source.write_text("def daily_agent_entrypoint():\n    return 'ready'\n", encoding="utf-8")
        (repo / "README.md").write_text("# Integration fixture\n", encoding="utf-8")
        server_command = [sys.executable, "-m", "mimry.mcp_server"]

        try:
            report = {
                "schema_version": 1,
                "sandbox": "temporary and deleted after run",
                "mcp_protocol": asyncio.run(_protocol_smoke(repo, sandbox / "cache", server_command)),
                "claude_code": _claude_smoke(sandbox, repo, server_command),
                "codex": _codex_smoke(sandbox, repo, server_command),
                "herdr": _herdr_status(),
            }
        except Exception as exc:
            report = {"schema_version": 1, "state": "FAIL", "error": str(exc)}
            code = 1
        else:
            code = 0

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
