"""FastMCP server package for agent-backend.

To eliminate RuntimeWarning during stdio execution (e.g. via `python -m src.mcp_server.server`),
we avoid eager top-level imports of `server.py` and instead provide lazy attribute access.
"""

from typing import Any

__all__ = ["FastMCP", "mcp_server", "run_stdio"]


def __getattr__(name: str) -> Any:
    if name in ("mcp_server", "run_stdio", "FastMCP"):
        from src.mcp_server.server import FastMCP, mcp_server, run_stdio

        mapping = {
            "mcp_server": mcp_server,
            "run_stdio": run_stdio,
            "FastMCP": FastMCP,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
