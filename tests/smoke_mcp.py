"""Exercise the real MCP stdio handshake without a running Ableton instance."""
import asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "ableton_mcp.server"], env={"ABLETON_MCP_TOKEN": ""})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"get_project", "get_devices", "set_mixer"}
            result = await session.call_tool("get_project", {})
            assert result.isError, "Missing bridge token must produce an MCP tool error"
    print("MCP handshake, tool discovery, and missing-token error passed")


asyncio.run(main())
