import asyncio

from fastmcp import Client

from mimry import __version__
from mimry.mcp_server import mcp


def _assert_mimry(name, version, instructions):
    assert name == "MIMRY"
    assert version == __version__
    assert "mimry_preflight" in instructions
    assert "rg or git grep" in instructions


def test_initialize_names_mimry_its_version_and_when_to_use_it():
    # Agents still connect with the classic `initialize` handshake.
    async def handshake():
        async with Client(mcp, mode="legacy") as client:
            return client.initialize_result

    result = asyncio.run(handshake())
    _assert_mimry(result.server_info.name, result.server_info.version, result.instructions)


def test_discovery_names_mimry_its_version_and_when_to_use_it():
    async def handshake():
        async with Client(mcp) as client:
            return client.server_info, client.instructions

    info, instructions = asyncio.run(handshake())
    _assert_mimry(info.name, info.version, instructions)
