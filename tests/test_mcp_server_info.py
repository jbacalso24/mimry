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

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from mimry import __version__
from mimry.mcp_server import INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ADAPTERS = ROOT / "tests" / "fixtures" / "mcp_list_adapters_active.json"

CORE_TOOL_NAMES = [
    "mimry_status",
    "mimry_preflight",
    "mimry_find",
    "mimry_related",
    "mimry_symbol",
    "mimry_path",
    "mimry_impact",
    "mimry_why",
    "mimry_feedback",
]

ALL_TOOL_NAMES = [
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
    "mimry_impact",
    "mimry_why",
    "mimry_history",
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


def _env(tools: str | None = None) -> dict[str, str]:
    # An operator's MIMRY_MCP_TOOLS must not change these tests.
    env = {key: value for key, value in os.environ.items() if key != "MIMRY_MCP_TOOLS"}
    env["PYTHONPATH"] = str(ROOT / "src")
    if tools is not None:
        env["MIMRY_MCP_TOOLS"] = tools
    return env


def _transport(tools: str | None = None) -> StdioTransport:
    return StdioTransport(
        sys.executable, ["-m", "mimry.mcp_server"], cwd=str(ROOT), env=_env(tools)
    )


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


def test_list_tools_lists_the_core_tools_by_default():
    async def listing():
        async with Client(_transport()) as client:
            return await client.list_tools()

    tools = asyncio.run(listing())
    assert [tool.name for tool in tools] == CORE_TOOL_NAMES


def test_list_tools_with_all_returns_every_tool_in_the_same_order():
    async def listing():
        async with Client(_transport("all")) as client:
            return await client.list_tools()

    tools = asyncio.run(listing())
    assert [tool.name for tool in tools] == ALL_TOOL_NAMES


def test_find_input_schema_is_unchanged():
    async def listing():
        async with Client(_transport("all")) as client:
            return await client.list_tools()

    tools = {tool.name: tool for tool in asyncio.run(listing())}
    assert tools["mimry_find"].input_schema == FIND_SCHEMA


def test_list_adapters_returns_the_same_structured_data():
    async def call():
        async with Client(_transport("all")) as client:
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


def _exchange_with_stderr(messages: list, tools: str | None = None) -> tuple[list[dict], str]:
    payload = b"".join(
        m if isinstance(m, bytes) else json.dumps(m).encode("utf-8") + b"\n" for m in messages
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mimry.mcp_server"],
        input=payload,
        capture_output=True,
        cwd=ROOT,
        env=_env(tools),
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return replies, proc.stderr.decode("utf-8", "replace")


def _exchange(messages: list, tools: str | None = None) -> list[dict]:
    return _exchange_with_stderr(messages, tools)[0]


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
        ],
        tools="all",
    )
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    tools = replies[1]["result"]["tools"]
    assert tools and all("outputSchema" not in tool for tool in tools)
    result = replies[2]["result"]
    assert "structuredContent" not in result
    assert "isError" not in result


def _tools_listed(replies: list[dict]) -> list[str]:
    return [tool["name"] for tool in replies[1]["result"]["tools"]]


def test_a_tool_that_is_not_listed_by_default_is_an_unknown_tool():
    replies = _exchange(
        [
            _init("2025-06-18"),
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "mimry_list_adapters", "arguments": {}},
            },
        ]
    )
    assert replies[1]["error"] == {"code": -32602, "message": "Unknown tool: mimry_list_adapters"}


def test_mimry_history_is_an_unknown_tool_unless_mimry_mcp_tools_names_it():
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "mimry_history", "arguments": {"query": "share extension"}},
    }
    replies = _exchange([_init("2025-06-18"), call])
    assert replies[1]["error"] == {"code": -32602, "message": "Unknown tool: mimry_history"}

    listed = _exchange(
        [_init("2025-06-18"), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
        tools="mimry_history",
    )
    assert _tools_listed(listed) == ["mimry_history"]


def test_mimry_mcp_tools_lists_exactly_the_named_tools():
    replies = _exchange(
        [_init("2025-06-18"), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
        tools="mimry_find, mimry_path",
    )
    # Registration order, whatever order the names were given in.
    assert _tools_listed(replies) == ["mimry_find", "mimry_path"]


def test_an_unknown_name_in_mimry_mcp_tools_lists_the_core_tools_and_says_so():
    replies, stderr = _exchange_with_stderr(
        [_init("2025-06-18"), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
        tools="mimry_find, mimry_nope",
    )
    assert _tools_listed(replies) == CORE_TOOL_NAMES
    lines = [line for line in stderr.splitlines() if line.strip()]
    assert len(lines) == 1, stderr
    assert "mimry_nope" in lines[0]
    assert "mimry_list_adapters" in lines[0]


@pytest.mark.parametrize("keyword", ["all", "ALL", "All", " all "])
def test_mimry_mcp_tools_all_is_case_insensitive(keyword):
    replies = _exchange(
        [_init("2025-06-18"), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
        tools=keyword,
    )
    assert _tools_listed(replies) == ALL_TOOL_NAMES


@pytest.mark.parametrize("keyword", ["core", "CORE", "Core"])
def test_mimry_mcp_tools_core_is_case_insensitive(keyword):
    replies = _exchange(
        [_init("2025-06-18"), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
        tools=keyword,
    )
    assert _tools_listed(replies) == CORE_TOOL_NAMES


def test_the_default_tool_listing_is_well_under_the_size_of_the_full_one():
    # Pins the cut without tiktoken: characters stand in for tokens.
    # The default listing grew past half when mimry_impact joined it
    # (#283), so the bound is 60%: still a clear cut from the full one.
    def listing(tools: str | None) -> str:
        replies = _exchange(
            [_init("2025-06-18"), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
            tools=tools,
        )
        return json.dumps(replies[1]["result"])

    assert len(listing(None)) < len(listing("all")) * 0.6


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
