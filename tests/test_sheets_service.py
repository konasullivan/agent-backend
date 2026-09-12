"""Unit and regression tests for MCP-backed sheets_service.py."""

import sys
from datetime import datetime, timezone
from unittest.mock import patch
import pytest

import config
import sheets_service


def test_zero_gspread_imports():
    """Verify sheets_service does not import gspread or google.oauth2.service_account."""
    assert "gspread" not in sys.modules or "gspread" not in dir(sheets_service)
    assert not hasattr(sheets_service, "Credentials")
    assert not hasattr(sheets_service, "SCOPES")
    assert not hasattr(sheets_service, "_gc")


@patch("sheets_service.call_mcp_tool_sync")
def test_append_record_dispatches_mcp_tools(mock_call_mcp):
    """Verify append_record calls sheets_ensure_tab and sheets_append_rows."""
    sheets_service._tab_ensured = False
    mock_call_mcp.return_value = {"status": "success"}

    sample_record = {
        "Message": "Testing MCP append",
        "Category": "Outreach",
        "Conversation ID": "conv-123",
        "Conversation Topic": "General",
        "Staff Member": "Alice",
        "Subteam": "Core",
        "Date": "2026-09-11T12:00:00",
        "Notes": "Action needed",
        "Action Items": "Follow up",
        "Deadline": "2026-09-15",
        "Link": "https://slack.com/archives/123",
    }

    sheets_service.append_record(sample_record)

    # Should call sheets_ensure_tab first, then sheets_append_rows
    assert mock_call_mcp.call_count == 2
    first_call, second_call = mock_call_mcp.call_args_list

    assert first_call[0][0] == "sheets_ensure_tab"
    assert first_call[0][1]["headers"] == config.SHEET_HEADERS

    assert second_call[0][0] == "sheets_append_rows"
    expected_row = [sample_record[h] for h in config.SHEET_HEADERS]
    assert second_call[0][1]["rows"] == [expected_row]


@patch("sheets_service.call_mcp_tool_sync")
def test_get_all_records_backward_compatibility(mock_call_mcp):
    """Verify get_all_records normalizes legacy column A and guarantees all SHEET_HEADERS keys."""
    mock_call_mcp.return_value = {
        "status": "success",
        "records": [
            {
                "A": "Legacy Message text",
                "Category": "Fundraiser",
                "Staff Member": "Maria",
                "Subteam": "Open Source",
                "Date": "9/9/2026 8:32:11",
                "Notes": "Remind Maria",
                "Link": "https://slack.com/archives/abc",
            }
        ],
    }

    records = sheets_service.get_all_records()
    assert len(records) == 1
    r = records[0]

    # Verify column 'A' was mapped to 'Message'
    assert r["Message"] == "Legacy Message text"
    # Verify all expected columns in config.SHEET_HEADERS exist without KeyError
    for header in config.SHEET_HEADERS:
        assert header in r
    assert r["Deadline"] == ""


@patch("sheets_service.call_mcp_tool_sync")
def test_get_records_in_range_filtering(mock_call_mcp):
    """Verify filtering by datetime range with both ISO and non-ISO date strings."""
    mock_call_mcp.return_value = {
        "status": "success",
        "records": [
            {"Date": "2026-09-08T10:00:00Z", "Message": "Too early"},
            {"Date": "9/9/2026 8:32:11", "Message": "In range (slash date)"},
            {"Date": "2026-09-10T12:00:00Z", "Message": "In range (ISO)"},
            {"Date": "2026-09-15T10:00:00Z", "Message": "Too late"},
            {"Date": "invalid-date", "Message": "Skipped"},
        ],
    }

    start = datetime(2026, 9, 9, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 11, 0, 0, 0, tzinfo=timezone.utc)

    results = sheets_service.get_records_in_range(start, end)
    messages = [r["Message"] for r in results]
    assert messages == ["In range (slash date)", "In range (ISO)"]


@patch("sheets_service.call_mcp_tool_sync")
def test_dashboard_app_compatibility(mock_call_mcp):
    """Verify FastAPI dashboard works seamlessly with refactored sheets_service."""
    from fastapi.testclient import TestClient

    mock_call_mcp.return_value = {
        "status": "success",
        "records": [
            {
                "Message": "Welcome to agent backend",
                "Category": "Outreach",
                "Staff Member": "Bob",
                "Subteam": "Community",
                "Date": "2026-09-11 12:00:00",
                "Notes": "All good",
                "Deadline": "2026-09-12",
                "Link": "https://example.com",
            }
        ],
    }

    import dashboard.app as dashboard_app

    client = TestClient(dashboard_app.app)

    # Test JSON API endpoint (calls get_all_records())
    res_api = client.get("/api/records")
    assert res_api.status_code == 200
    data = res_api.json()
    assert len(data) == 1
    assert data[0]["Message"] == "Welcome to agent backend"
    assert data[0]["Staff Member"] == "Bob"

    # Test HTML dashboard rendering
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "Welcome to agent backend" in res_html.text
    assert "Business Chat Record" in res_html.text
