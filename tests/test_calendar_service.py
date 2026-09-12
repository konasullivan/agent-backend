"""Unit and regression tests for MCP-backed calendar_service.py."""

from unittest.mock import patch
import pytest

import config
import calendar_service


def test_zero_googleapiclient_imports():
    """Verify calendar_service does not import googleapiclient or Credentials."""
    assert not hasattr(calendar_service, "build")
    assert not hasattr(calendar_service, "Credentials")
    assert not hasattr(calendar_service, "SCOPES")
    assert not hasattr(calendar_service, "_service")


@patch("calendar_service.call_mcp_tool_sync")
def test_create_event_dispatches_mcp_tool(mock_call_mcp):
    """Verify create_event converts date and calls calendar_create_event tool."""
    mock_call_mcp.return_value = {
        "status": "success",
        "html_link": "https://calendar.google.com/event?eid=123",
        "event_id": "event_123",
    }

    link = calendar_service.create_event(
        summary="Sprint Planning Review",
        description="Discuss roadmap",
        date_str="2026-09-15",
    )

    assert link == "https://calendar.google.com/event?eid=123"
    assert mock_call_mcp.call_count == 1

    tool_name, tool_args = mock_call_mcp.call_args[0]
    assert tool_name == "calendar_create_event"
    assert tool_args["calendar_id"] == config.GOOGLE_CALENDAR_ID
    assert tool_args["summary"] == "Sprint Planning Review"
    assert tool_args["start_iso"].startswith("2026-09-15T")
    assert tool_args["end_iso"].startswith("2026-09-15T")


@patch("calendar_service.call_mcp_tool_sync")
def test_create_event_empty_date_returns_none(mock_call_mcp):
    """Verify empty date returns None without calling MCP tool."""
    result = calendar_service.create_event("Summary", "Desc", "")
    assert result is None
    assert mock_call_mcp.call_count == 0


@patch("calendar_service.call_mcp_tool_sync")
def test_create_event_handles_mcp_failure(mock_call_mcp):
    """Verify create_event traps exceptions and returns None (no crash)."""
    mock_call_mcp.side_effect = RuntimeError("MCP daemon timeout")

    result = calendar_service.create_event("Summary", "Desc", "2026-09-15")
    assert result is None
