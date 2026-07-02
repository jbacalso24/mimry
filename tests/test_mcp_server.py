from __future__ import annotations

from pathlib import Path

from mimry import mcp_server
from mimry.mcp_server import mimry_find, mimry_status


def test_mcp_server_imports_and_registers_tools():
    assert mcp_server.mcp.name == "MIMRY"
    # FastMCP stores tools internally, but import success and callable decorated
    # functions are the minimum guard for packaging/console-script regressions.
    assert callable(mimry_status)
    assert callable(mimry_find)


def test_mcp_status_uninitialized_root(tmp_path: Path):
    payload = mimry_status(str(tmp_path))
    assert payload["initialized"] is False
    assert payload["root"] == str(tmp_path.resolve())
