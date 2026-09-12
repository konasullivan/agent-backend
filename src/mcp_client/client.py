"""MCP Client helper providing in-process and stdio transport connection managers
and safe synchronous execution bridge.
"""

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any, Literal

from mcp import Client
from mcp.client.stdio import StdioServerParameters

logger = logging.getLogger(__name__)

# Dedicated thread pool executor for safe synchronous tool execution
_sync_executor: ThreadPoolExecutor | None = None
_sync_executor_lock = threading.Lock()


def _get_sync_executor() -> ThreadPoolExecutor:
    """Lazy thread-safe initialization of the synchronous MCP bridge ThreadPoolExecutor."""
    global _sync_executor
    if _sync_executor is None:
        with _sync_executor_lock:
            if _sync_executor is None:
                _sync_executor = ThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="mcp-sync-bridge",
                )
    return _sync_executor


@asynccontextmanager
async def get_mcp_client(
    transport: Literal["inproc", "stdio"] = "inproc",
) -> AsyncIterator[Client]:
    """Provide an initialized MCP Client context manager.

    Args:
        transport: 'inproc' connects directly in-memory to mcp_server;
                   'stdio' spawns a subprocess executing src.mcp_server.server.

    Yields:
        Initialized and connected mcp.Client instance.
    """
    if transport == "inproc":
        logger.debug("Initializing in-process MCP Client...")
        from src.mcp_server.server import mcp_server

        async with Client(mcp_server) as client:
            yield client
    elif transport == "stdio":
        logger.debug("Spawning stdio MCP Server subprocess...")
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
        }
        # Ensure project root is present on PYTHONPATH so subprocess can locate src
        project_root = str(Path(__file__).resolve().parent.parent.parent)
        if "PYTHONPATH" in env:
            env["PYTHONPATH"] = f"{project_root}:{env['PYTHONPATH']}"
        else:
            env["PYTHONPATH"] = project_root

        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "src.mcp_server.server"],
            env=env,
        )
        async with Client(server_params) as client:
            yield client
    else:
        raise ValueError(
            f"Unsupported transport '{transport}'. Expected 'inproc' or 'stdio'."
        )


async def call_mcp_tool(
    client: Client,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a tool on the MCP client and parse the result.

    Args:
        client: Connected MCP Client instance.
        name: Name of tool to execute.
        arguments: Dictionary of arguments.

    Returns:
        Parsed dictionary output or structured result.

    Raises:
        RuntimeError: If tool call resulted in an error or is_error is True.
    """
    result = await client.call_tool(name=name, arguments=arguments)
    is_err = getattr(result, "is_error", getattr(result, "isError", False))

    # Parse content blocks
    raw_texts: list[str] = []
    for block in getattr(result, "content", []):
        if hasattr(block, "text"):
            raw_texts.append(block.text)

    joined_text = "\n".join(raw_texts)

    if is_err:
        error_msg = joined_text if joined_text else str(getattr(result, "content", "Unknown error"))
        raise RuntimeError(
            f"MCP tool '{name}' failed with error: {error_msg}"
        )

    # If structured_content is present, prefer it
    if hasattr(result, "structured_content") and result.structured_content:
        return result.structured_content

    # Attempt to parse json from text
    if joined_text:
        try:
            return json.loads(joined_text)
        except json.JSONDecodeError:
            return {"raw": joined_text}

    return {"status": "success"}


async def _execute_tool_async(
    name: str,
    arguments: dict[str, Any],
    transport: Literal["inproc", "stdio"] = "inproc",
) -> dict[str, Any]:
    """Internal coroutine to connect, execute tool, and return parsed result."""
    async with get_mcp_client(transport=transport) as client:
        return await call_mcp_tool(client=client, name=name, arguments=arguments)


def call_mcp_tool_sync(
    name: str,
    arguments: dict[str, Any],
    transport: Literal["inproc", "stdio"] = "inproc",
) -> dict[str, Any]:
    """Synchronous execution bridge for calling MCP tools without event loop conflicts.

    Can be safely called from:
    - Synchronous services (`sheets_service.py`, `calendar_service.py`)
    - Web framework threads (FastAPI / Uvicorn sync route worker threads)
    - Asynchronous event loops (pytest-asyncio tests, coroutines) without raising
      `RuntimeError: asyncio.run() cannot be called from a running event loop`.

    Args:
        name: Tool name to execute.
        arguments: Dictionary of arguments.
        transport: Transport mode ('inproc' or 'stdio'). Defaults to 'inproc'.

    Returns:
        Parsed dictionary output.

    Raises:
        RuntimeError: If tool call fails.
        ValueError: If transport is unsupported.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # An event loop is active on the current thread.
        # Offload execution to a separate worker thread to avoid event loop collision.
        executor = _get_sync_executor()
        future = executor.submit(
            asyncio.run, _execute_tool_async(name, arguments, transport)
        )
        return future.result()
    else:
        # No active event loop on current thread; safe to run directly.
        return asyncio.run(_execute_tool_async(name, arguments, transport))
