"""The MCP server, end to end over stdio, through the FastMCP client.

Also checks the raw line protocol by hand and that importing the server
keeps the heavy stack out of the process (the cold-start guard).
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from mimry import __version__
from mimry.mcp_server import INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ADAPTERS = ROOT / "tests" / "fixtures" / "mcp_list_adapters_active.json"

TOOL_NAMES = [
    "mimry_list_adapters",
    "mimry_status",
    "mimry_reindex",
    "mimry_init",
    "mimry_refresh",
    "mimry_preflight",
    "mimry_find",
    "mimry_semantic",
    "mimry_related",
    "mimry_route",
    "mimry_brief",
    "mimry_symbol",
    "mimry_context",
    "mimry_explain",
    "mimry_path",
    "mimry_why",
    "mimry_feedback",
    "mimry_digest",
    "mimry_plan_tree",
    "mimry_plan_check",
    "mimry_plan_digest",
    "mimry_plan_list",
]

FIND_SCHEMA = {
    "properties": {
        "query": {"type": "string"},
        "root": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        "limit": {"default": 10, "type": "integer"},
        "semantic": {"default": False, "type": "boolean"},
        "languages": {
            "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
            "default": None,
        },
        "path_globs": {
            "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
            "default": None,
        },
        "exclude_globs": {
            "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
            "default": None,
        },
        "offset": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
        "snippets": {"default": False, "type": "boolean"},
        "snippet_lines": {"default": 8, "type": "integer"},
        "snippet_chars": {"default": 800, "type": "integer"},
        "snippet_total_lines": {"default": 40, "type": "integer"},
        "snippet_total_chars": {"default": 4000, "type": "integer"},
    },
    "required": ["query"],
    "type": "object",
    "additionalProperties": False,
}


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def _transport() -> StdioTransport:
    return StdioTransport(sys.executable, ["-m", "mimry.mcp_server"], cwd=str(ROOT), env=_env())


def _assert_mimry(name, version, instructions):
    assert name == "MIMRY"
    assert version == __version__
    assert "mimry_preflight" in instructions
    assert "rg or git grep" in instructions
    assert instructions == INSTRUCTIONS


def test_initialize_names_mimry_its_version_and_when_to_use_it():
    # Agents still connect with the classic `initialize` handshake.
    async def handshake():
        async with Client(_transport(), mode="legacy") as client:
            return client.initialize_result

    result = asyncio.run(handshake())
    _assert_mimry(result.server_info.name, result.server_info.version, result.instructions)


def test_discovery_names_mimry_its_version_and_when_to_use_it():
    async def handshake():
        async with Client(_transport()) as client:
            return client.server_info, client.instructions

    info, instructions = asyncio.run(handshake())
    _assert_mimry(info.name, info.version, instructions)


def test_list_tools_returns_the_same_tools_in_the_same_order():
    async def listing():
        async with Client(_transport()) as client:
            return await client.list_tools()

    tools = asyncio.run(listing())
    assert [tool.name for tool in tools] == TOOL_NAMES


def test_find_input_schema_is_unchanged():
    async def listing():
        async with Client(_transport()) as client:
            return await client.list_tools()

    tools = {tool.name: tool for tool in asyncio.run(listing())}
    assert tools["mimry_find"].input_schema == FIND_SCHEMA


def test_list_adapters_returns_the_same_structured_data():
    async def call():
        async with Client(_transport()) as client:
            return await client.call_tool("mimry_list_adapters", {"active_only": True})

    result = asyncio.run(call())
    assert not result.is_error
    expected = json.loads(GOLDEN_ADAPTERS.read_text(encoding="utf-8"))
    assert result.structured_content == expected


def test_wrong_argument_type_is_a_tool_error_naming_the_argument():
    async def call():
        async with Client(_transport()) as client:
            return await client.call_tool("mimry_find", {"query": 5}, raise_on_error=False)

    result = asyncio.run(call())
    assert result.is_error
    assert "query" in result.content[0].text


def _exchange(messages: list) -> list[dict]:
    payload = b"".join(
        m if isinstance(m, bytes) else json.dumps(m).encode("utf-8") + b"\n" for m in messages
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mimry.mcp_server"],
        input=payload,
        capture_output=True,
        cwd=ROOT,
        env=_env(),
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _init(version: str, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "raw-test", "version": "1"},
        },
    }


def test_notifications_get_no_reply_and_ping_answers():
    replies = _exchange(
        [
            _init("2025-06-18"),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 9}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
    )
    assert [reply["id"] for reply in replies] == [1, 2]
    assert replies[1]["result"] == {}


def test_protocol_errors_use_the_json_rpc_codes():
    replies = _exchange(
        [
            _init("2025-06-18"),
            b"{not json\n",
            b"[1, 2]\n",
            {"jsonrpc": "2.0", "id": 3, "method": "no/such/method"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}},
        ]
    )
    assert replies[1] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }
    assert replies[2]["id"] is None
    assert replies[2]["error"]["code"] == -32600
    assert replies[3]["error"]["code"] == -32601
    assert replies[4]["error"] == {"code": -32602, "message": "Unknown tool: nope"}


def test_a_2024_client_gets_no_structured_fields():
    replies = _exchange(
        [
            _init("2024-11-05"),
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "mimry_list_adapters", "arguments": {"active_only": True}},
            },
        ]
    )
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    tools = replies[1]["result"]["tools"]
    assert tools and all("outputSchema" not in tool for tool in tools)
    result = replies[2]["result"]
    assert "structuredContent" not in result
    assert "isError" not in result


def test_importing_the_server_keeps_the_heavy_stack_out():
    # The cold-start guard: a few seconds of import here is what made
    # agents wait. Only the server and protocol modules may load.
    code = (
        "import json, sys\n"
        "import mimry.mcp_server\n"
        "heavy = ('fastmcp', 'mcp', 'pydantic', 'httpx', 'tree_sitter',\n"
        "         'tree_sitter_language_pack')\n"
        "print(json.dumps({\n"
        "    'heavy': sorted(m for m in sys.modules if m.split('.')[0] in heavy),\n"
        "    'mimry': sorted(m for m in sys.modules if m == 'mimry' or m.startswith('mimry.')),\n"
        "}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_env(),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout)
    assert loaded["heavy"] == []
    assert loaded["mimry"] == ["mimry", "mimry.mcp_protocol", "mimry.mcp_server"]
