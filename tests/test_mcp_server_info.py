import asyncio

from fastmcp import Client

from mimry import __version__
from mimry.mcp_server import mcp


def test_initialize_names_mimry_its_version_and_when_to_use_it():
    async def handshake():
        async with Client(mcp) as client:
            return client.initialize_result

    result = asyncio.run(handshake())

    assert result.serverInfo.name == "MIMRY"
    assert result.serverInfo.version == __version__
    assert "mimry_preflight" in result.instructions
    assert "rg or git grep" in result.instructions
