import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.mcp_client import resolve_openings_mcp_path

OPENINGS_MCP_PATH = resolve_openings_mcp_path()

async def test_mcp_connection():
    server_params = StdioServerParameters(
        command=OPENINGS_MCP_PATH,
        args=[],
        env=None
    )
    print(f"Connecting to {OPENINGS_MCP_PATH} via stdio...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print(f"Success! Found {len(tool_names)} tools.")
            has_linkedin = "linkedin_search_jobs" in tool_names
            has_indeed = "indeed_search_jobs" in tool_names
            print(f"linkedin_search_jobs present: {has_linkedin}")
            print(f"indeed_search_jobs present: {has_indeed}")
            assert has_linkedin and has_indeed, "Missing expected tools"

if __name__ == "__main__":
    asyncio.run(test_mcp_connection())
