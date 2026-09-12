"""MCP Client package for agent-backend."""

from src.mcp_client.client import (
    call_mcp_tool,
    call_mcp_tool_sync,
    get_mcp_client,
)

__all__ = ["call_mcp_tool", "call_mcp_tool_sync", "get_mcp_client"]
