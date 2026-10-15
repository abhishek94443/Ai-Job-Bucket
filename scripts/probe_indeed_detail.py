import asyncio
import json
from src.mcp_client import OpeningsMCPClient

async def probe():
    client = OpeningsMCPClient()
    print("Calling indeed_get_job_detail...")
    res = await client.call_tool("indeed_get_job_detail", {
        "job_key": "673bfe59ee7c004d",
        "country": "India"
    }, timeout_seconds=40.0)
    print("Indeed detail result type:", type(res))
    if isinstance(res, dict):
        print("Keys:", res.keys())
        print("Preview:", json.dumps(res, indent=2)[:600])

if __name__ == "__main__":
    asyncio.run(probe())
