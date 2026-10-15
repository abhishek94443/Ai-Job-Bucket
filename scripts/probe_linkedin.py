import asyncio
import json
from src.mcp_client import OpeningsMCPClient

async def probe():
    client = OpeningsMCPClient()
    print("Calling linkedin_search_jobs with limit=2 (start=0)...")
    res = await client.call_tool("linkedin_search_jobs", {
        "keyword": "Java Backend Developer",
        "location": "Bengaluru, Karnataka, India",
        "start": 0
    }, timeout_seconds=40.0)
    print("LinkedIn search result type:", type(res))
    print("Sample keys/preview:")
    if isinstance(res, list):
        print(f"List with {len(res)} items")
        if res:
            print("First item keys:", res[0].keys() if isinstance(res[0], dict) else res[0])
            print("First item preview:", json.dumps(res[0], indent=2)[:300])
    elif isinstance(res, dict):
        print("Dict keys:", res.keys())
        print("Preview:", json.dumps(res, indent=2)[:300])
    else:
        print("Raw:", str(res)[:300])

if __name__ == "__main__":
    asyncio.run(probe())
