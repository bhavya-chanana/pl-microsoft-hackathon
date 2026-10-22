"""MCP server exposing match-state tools to agents (BRD §8.2).

Mounted into the API app at /mcp (streamable HTTP), so local agents, Foundry
agents and any MCP client can use the same tools.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from matchlens.tools.match_tools import ALL_TOOLS

mcp = FastMCP(
    "matchlens-match-state",
    instructions=(
        "Live state of the current football match. Use these tools for every fact you state. "
        "Never invent players, events or numbers; null means unavailable."
    ),
    stateless_http=True,
    json_response=True,
)
mcp.settings.streamable_http_path = "/"

for tool in ALL_TOOLS:
    mcp.add_tool(tool, name="verify_claims" if tool.__name__ == "verify" else tool.__name__)
