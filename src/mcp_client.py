import asyncio
import json
import logging
import os
import platform
import shutil
import stat
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

def resolve_openings_mcp_path() -> str:
    """
    Dynamically resolve the openings-mcp executable path:
    1. OPENINGS_MCP_PATH environment variable override.
    2. In-tree multi-platform precompiled binaries in mcp_servers/openings-mcp/bin/:
       - Windows: openings-mcp-windows-amd64.exe
       - Linux: openings-mcp-linux-amd64
       - macOS (arm64): openings-mcp-darwin-arm64
       - macOS (amd64): openings-mcp-darwin-amd64
    3. System PATH executable ('openings-mcp' or 'openings-mcp.exe').
    4. Local Go user bin directory (~/go/bin).
    """
    env_override = os.getenv("OPENINGS_MCP_PATH")
    if env_override and env_override.strip():
        val = env_override.strip()
        candidate = Path(val)
        if candidate.is_file():
            return str(candidate.resolve())
        which_path = shutil.which(val)
        if which_path:
            return which_path

    project_root = Path(__file__).resolve().parent.parent
    bin_dir = project_root / "mcp_servers" / "openings-mcp" / "bin"
    system_os = platform.system().lower()
    machine = platform.machine().lower()

    binary_name = None
    if system_os == "windows":
        binary_name = "openings-mcp-windows-amd64.exe"
    elif system_os == "linux":
        binary_name = "openings-mcp-linux-amd64"
    elif system_os == "darwin":
        if machine in ("arm64", "aarch64"):
            binary_name = "openings-mcp-darwin-arm64"
        else:
            binary_name = "openings-mcp-darwin-amd64"

    if binary_name:
        candidate = bin_dir / binary_name
        if candidate.is_file():
            if system_os != "windows":
                try:
                    mode = candidate.stat().st_mode
                    candidate.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                except Exception as exc:
                    logger.warning(f"Could not set execute bit on {candidate}: {exc}")
            return str(candidate.resolve())

    system_bin = shutil.which("openings-mcp") or shutil.which("openings-mcp.exe")
    if system_bin:
        return system_bin

    user_go_bin = Path.home() / "go" / "bin" / ("openings-mcp.exe" if system_os == "windows" else "openings-mcp")
    if user_go_bin.is_file():
        return str(user_go_bin.resolve())

    return "openings-mcp"

class OpeningsMCPClient:
    """
    Client for interacting with Openings MCP via standard stdio JSON-RPC transport.
    """
    def __init__(self, mcp_path: Optional[str] = None):
        self.mcp_path = mcp_path or resolve_openings_mcp_path()

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout_seconds: float = 30.0) -> Any:
        server_params = StdioServerParameters(
            command=self.mcp_path,
            args=[],
            env=None
        )
        async def _invoke():
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    if not result.content:
                        return None
                    
                    combined_text = []
                    for content_block in result.content:
                        if hasattr(content_block, "text"):
                            combined_text.append(content_block.text)
                    
                    raw_str = "\n".join(combined_text).strip()
                    if not raw_str:
                        return None
                    
                    try:
                        return json.loads(raw_str)
                    except json.JSONDecodeError:
                        return raw_str

        try:
            return await asyncio.wait_for(_invoke(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.error(f"Timeout ({timeout_seconds}s) while calling MCP tool {tool_name}")
            raise TimeoutError(f"Call to {tool_name} timed out after {timeout_seconds} seconds")
        except Exception as e:
            logger.error(f"Error calling MCP tool {tool_name}: {e}")
            raise
