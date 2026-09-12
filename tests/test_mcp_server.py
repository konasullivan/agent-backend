"""Unit tests for FastMCP / MCPServer and tool executions."""

import pytest
from mcp import Client

from src.mcp_client.client import call_mcp_tool_sync
from src.mcp_server.server import mcp_server


@pytest.mark.asyncio
async def test_tool_registration():
    """Verify that all expected 5 tools are properly registered on MCPServer."""
    async with Client(mcp_server) as client:
        tools_resp = await client.list_tools()
        tool_names = {t.name for t in tools_resp.tools}

        expected = {
            "sheets_ensure_tab",
            "sheets_append_rows",
            "sheets_get_records",
            "sheets_update_range",
            "calendar_create_event",
            "calendar_delete_event",
            "calendar_list_events",
        }
        assert expected.issubset(tool_names)


@pytest.mark.asyncio
async def test_inproc_sheets_ensure_tab(mock_workspace_service):
    """Verify in-process tool execution of sheets_ensure_tab."""
    async with Client(mcp_server) as client:
        res = await client.call_tool(
            "sheets_ensure_tab",
            arguments={
                "spreadsheet_id": "test_sheet_id",
                "sheet_name": "TestRecords",
                "headers": ["ColA", "ColB"],
            },
        )
        assert not res.is_error
        assert len(res.content) > 0


@pytest.mark.asyncio
async def test_inproc_sheets_append_rows(mock_workspace_service):
    """Verify in-process tool execution of sheets_append_rows."""
    async with Client(mcp_server) as client:
        res = await client.call_tool(
            "sheets_append_rows",
            arguments={
                "spreadsheet_id": "test_sheet_id",
                "sheet_name": "TestRecords",
                "rows": [["val1", "val2"]],
            },
        )
        assert not res.is_error
        assert len(res.content) > 0


@pytest.mark.asyncio
async def test_inproc_sheets_get_records(mock_workspace_service):
    """Verify in-process tool execution of sheets_get_records."""
    async with Client(mcp_server) as client:
        res = await client.call_tool(
            "sheets_get_records",
            arguments={
                "spreadsheet_id": "test_sheet_id",
                "sheet_name": "TestRecords",
            },
        )
        assert not res.is_error
        assert len(res.content) > 0


@pytest.mark.asyncio
async def test_inproc_calendar_create_event(mock_workspace_service):
    """Verify in-process tool execution of calendar_create_event."""
    async with Client(mcp_server) as client:
        res = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Integration Test Event",
                "start_iso": "2026-09-11T15:00:00Z",
                "end_iso": "2026-09-11T16:00:00Z",
            },
        )
        assert not res.is_error
        assert len(res.content) > 0


@pytest.mark.asyncio
async def test_inproc_calendar_list_events(mock_workspace_service):
    """Verify in-process tool execution of calendar_list_events."""
    async with Client(mcp_server) as client:
        res = await client.call_tool(
            "calendar_list_events",
            arguments={
                "calendar_id": "test@example.com",
                "max_results": 5,
            },
        )
        assert not res.is_error
        assert len(res.content) > 0


@pytest.mark.asyncio
async def test_inproc_calendar_delete_event(mock_workspace_service):
    """Verify in-process tool execution of calendar_delete_event."""
    async with Client(mcp_server) as client:
        res = await client.call_tool(
            "calendar_delete_event",
            arguments={
                "calendar_id": "test@example.com",
                "event_id": "event_123",
            },
        )
        assert not res.is_error
        assert len(res.content) > 0



@pytest.mark.asyncio
async def test_inproc_calendar_create_event_invalid_timing(mock_workspace_service):
    """Verify tool call reports error when end time is before start time."""
    async with Client(mcp_server) as client:
        res = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Invalid Event",
                "start_iso": "2026-09-11T16:00:00Z",
                "end_iso": "2026-09-11T15:00:00Z",
            },
        )
        assert res.is_error


def test_call_mcp_tool_sync_outside_event_loop(mock_workspace_service):
    """Verify call_mcp_tool_sync functions normally outside any event loop."""
    res = call_mcp_tool_sync(
        "sheets_get_records",
        arguments={
            "spreadsheet_id": "test_sheet_id",
            "sheet_name": "TestRecords",
        },
    )
    assert res.get("status") == "success"
    assert "records" in res


@pytest.mark.asyncio
async def test_call_mcp_tool_sync_inside_running_event_loop(mock_workspace_service):
    """Verify call_mcp_tool_sync bridges to ThreadPoolExecutor when called inside an active loop."""
    res = call_mcp_tool_sync(
        "sheets_get_records",
        arguments={
            "spreadsheet_id": "test_sheet_id",
            "sheet_name": "TestRecords",
        },
    )
    assert res.get("status") == "success"
    assert "records" in res
