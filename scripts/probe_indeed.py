import asyncio
import json
from src.mcp_client import OpeningsMCPClient

async def probe():
    client = OpeningsMCPClient()
    print("Calling indeed_search_jobs...")
    res = await client.call_tool("indeed_search_jobs", {
        "keyword": "Java Backend Developer",
        "location": "Bengaluru",
        "country": "India"
    }, timeout_seconds=40.0)
    print("Indeed search result type:", type(res))
    if isinstance(res, dict):
        print("Dict keys:", res.keys())
        print("Preview:", json.dumps(res, indent=2)[:500])
    elif isinstance(res, list):
        print("List length:", len(res))
        if res:
            print("First item:", json.dumps(res[0], indent=2)[:500])
    else:
        print("Raw:", str(res)[:500])

if __name__ == "__main__":
    asyncio.run(probe())
