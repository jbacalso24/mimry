from __future__ import annotations

import sys
from pathlib import Path

from mimry.agent_integration import _client_env, _herdr_status, _protocol_smoke


def test_real_mcp_stdio_protocol_round_trip_is_home_isolated(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    monkeypatch.setenv("HOME", str(operator_home))
    source = repo / "src" / "entrypoint.py"
    source.parent.mkdir(parents=True)
    source.write_text("def daily_agent_entrypoint():\n    return 'ready'\n", encoding="utf-8")
    (repo / "README.md").write_text("# MCP integration fixture\n", encoding="utf-8")

    import asyncio

    result = asyncio.run(_protocol_smoke(repo, tmp_path / "cache", [sys.executable, "-m", "mimry.mcp_server"]))
    assert result["state"] == "PASS"
    assert result["find_paths"][0] == "src/entrypoint.py"
    assert not list(operator_home.rglob("*"))


def test_external_client_environment_is_credential_free_and_sandboxed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "privacy-canary-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "privacy-canary-openai")
    monkeypatch.setenv("MIMRY_CACHE_HOME", "/operator/cache")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/operator/claude")
    monkeypatch.setenv("CODEX_HOME", "/operator/codex")

    claude = _client_env(tmp_path, client="claude")
    codex = _client_env(tmp_path, client="codex")

    for env in (claude, codex):
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env
        assert env["MIMRY_CACHE_HOME"].startswith(str(tmp_path))
        assert env["FASTMCP_HOME"].startswith(str(tmp_path))
        assert env["HOME"].startswith(str(tmp_path))
        assert env["APPDATA"].startswith(str(tmp_path))
        assert env["LOCALAPPDATA"].startswith(str(tmp_path))
        assert env["XDG_DATA_HOME"].startswith(str(tmp_path))
        assert not any("privacy-canary" in value for value in env.values())
    assert claude["CLAUDE_CONFIG_DIR"].startswith(str(tmp_path))
    assert "CODEX_HOME" not in claude
    assert codex["CODEX_HOME"].startswith(str(tmp_path))
    assert "CLAUDE_CONFIG_DIR" not in codex


def test_herdr_status_is_neutral_and_never_claims_proof(monkeypatch):
    monkeypatch.setattr("mimry.agent_integration.shutil.which", lambda name: "/opt/herdr" if name == "herdr" else None)
    detected = _herdr_status()
    assert detected["state"] == "UNVERIFIED"
    assert detected["detected"] is True
    assert "pane-only" in detected["blocker"]

    monkeypatch.setattr("mimry.agent_integration.shutil.which", lambda name: None)
    absent = _herdr_status()
    assert absent["state"] == "UNVERIFIED"
    assert absent["detected"] is False
    assert "not detected" in absent["blocker"]
