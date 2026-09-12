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


def test_normalize_date_range_localizes_to_configured_timezone():
    """Verify naive ISO datetimes are localized to America/New_York (-04:00 in EDT)."""
    start_iso, end_iso = calendar_service._normalize_date_range("2026-09-29T19:00:00")
    assert "2026-09-29T19:00:00-04:00" in start_iso
    assert "2026-09-29T20:00:00-04:00" in end_iso


def test_extract_event_id_from_link():
    """Verify Google Calendar event IDs can be extracted from base64 eid parameters."""
    link = "https://www.google.com/calendar/event?eid=bGozYTdiY21tY292ZmhwY3B2am1lMXBjYWcgYW16bS5tYW5hQG0"
    event_id = calendar_service.extract_event_id_from_link(link)
    assert event_id == "lj3a7bcmmcovfhpcpvjme1pcag"

    assert calendar_service.extract_event_id_from_link("") is None
    assert calendar_service.extract_event_id_from_link("https://slack.com/archives/C123/p123") is None


@patch("calendar_service.call_mcp_tool_sync")
def test_delete_event_dispatches_mcp_tool(mock_call_mcp):
    """Verify delete_event invokes calendar_delete_event MCP tool."""
    mock_call_mcp.return_value = {"status": "success", "deleted": True}

    ok = calendar_service.delete_event("event_to_delete_123")
    assert ok is True
    assert mock_call_mcp.call_count == 1
    tool_name, tool_args = mock_call_mcp.call_args[0]
    assert tool_name == "calendar_delete_event"
    assert tool_args["event_id"] == "event_to_delete_123"


@patch("calendar_service.call_mcp_tool_sync")
def test_prune_superseded_events(mock_call_mcp):
    """Verify prune_superseded_events identifies and deletes incomplete events on the same day."""
    # First call: calendar_list_events returns two events on 2026-09-29
    # One has no location ("Dinner planned for September 29th at 7:00 PM.")
    # Second call: calendar_delete_event deletes it
    mock_call_mcp.side_effect = [
        {
            "status": "success",
            "events": [
                {
                    "id": "old_incomplete_event_1",
                    "start": "2026-09-29T15:00:00-04:00",
                    "summary": "Dinner planned for September 29th at 7:00 PM.",
                    "location": "",
                },
                {
                    "id": "other_day_event",
                    "start": "2026-09-30T10:00:00-04:00",
                    "summary": "Team Sync",
                    "location": "HQ",
                },
            ],
        },
        {"status": "success", "deleted": True},
    ]

    pruned = calendar_service.prune_superseded_events(
        target_date_str="2026-09-29T19:00:00",
        new_location="Texas Roadhouse",
    )
    assert "old_incomplete_event_1" in pruned
    assert len(pruned) == 1

