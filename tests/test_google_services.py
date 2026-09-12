"""Unit tests for GoogleWorkspaceService in agent-backend."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.services.google_services import GoogleWorkspaceService


def test_credentials_missing_raises_error():
    """Verify FileNotFoundError is raised when credentials key does not exist."""
    service = GoogleWorkspaceService(
        credentials_path=Path("/non/existent/path/creds.json")
    )
    with pytest.raises(FileNotFoundError, match="Google Service Account key file not found"):
        service._get_credentials()


def test_ensure_tab_creates_when_missing(mock_workspace_service, mock_sheets_client):
    """Verify ensure_tab calls batchUpdate (addSheet) and updates headers when tab is missing."""
    headers = ["Col1", "Col2", "Col3"]
    result = mock_workspace_service.ensure_tab(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        headers=headers,
    )

    assert result["status"] == "success"
    assert result["created"] is True
    assert result["sheet_name"] == "TestRecords"
    assert result["sheet_id"] == 123456
    assert result["headers_written"] == headers

    mock_sheets_client.spreadsheets().batchUpdate.assert_called_once()
    mock_sheets_client.spreadsheets().values().update.assert_called_once()


def test_ensure_tab_idempotent_when_exists(mock_workspace_service, mock_sheets_client):
    """Verify ensure_tab skips addSheet and header update if tab already exists."""
    mock_sheets_client.spreadsheets().get().execute.return_value = {
        "sheets": [
            {"properties": {"title": "Sheet1", "sheetId": 0}},
            {"properties": {"title": "TestRecords", "sheetId": 777}},
        ]
    }

    result = mock_workspace_service.ensure_tab(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        headers=["Col1", "Col2"],
    )

    assert result["status"] == "success"
    assert result["created"] is False
    assert result["sheet_id"] == 777
    assert result["headers_written"] == []

    mock_sheets_client.spreadsheets().batchUpdate.assert_not_called()
    mock_sheets_client.spreadsheets().values().update.assert_not_called()


def test_ensure_tab_without_headers(mock_workspace_service, mock_sheets_client):
    """Verify ensure_tab creates tab but does not write values when headers is empty or None."""
    result = mock_workspace_service.ensure_tab(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        headers=None,
    )

    assert result["status"] == "success"
    assert result["created"] is True
    assert result["headers_written"] == []

    mock_sheets_client.spreadsheets().batchUpdate.assert_called_once()
    mock_sheets_client.spreadsheets().values().update.assert_not_called()


def test_append_rows_success(mock_workspace_service, mock_sheets_client):
    """Verify append_rows sends sanitized rows and returns updated count."""
    rows = [
        ["2026-09-10", "TASK", "Title 1", "Details", "User", "2026-09-11", "HIGH", "Quote"],
        ["2026-09-10", "EVENT", "Title 2", "Details", "User", "2026-09-12", "LOW", "Quote"],
    ]
    result = mock_workspace_service.append_rows(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        rows=rows,
    )

    assert result["status"] == "success"
    assert result["rows_appended"] == 2
    assert result["updated_range"] == "'TestRecords'!A2:H3"
    mock_sheets_client.spreadsheets().values().append.assert_called_once()


def test_append_rows_empty_short_circuits(mock_workspace_service, mock_sheets_client):
    """Verify append_rows short-circuits on empty list without calling API."""
    result = mock_workspace_service.append_rows(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        rows=[],
    )

    assert result["status"] == "success"
    assert result["rows_appended"] == 0
    mock_sheets_client.spreadsheets().values().append.assert_not_called()


def test_get_records_success(mock_workspace_service, mock_sheets_client):
    """Verify get_records retrieves rows and correctly maps to dictionaries using row 1 headers."""
    mock_get = MagicMock()
    mock_get.execute.return_value = {
        "range": "'TestRecords'!A1:Z",
        "values": [
            ["Message", "Category", "Staff Member", "Deadline"],
            ["Hello world", "Outreach", "Alice", "2026-09-15"],
            ["Second row", "Trading", "Bob", ""],
        ],
    }
    mock_sheets_client.spreadsheets().values().get.return_value = mock_get

    result = mock_workspace_service.get_records(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
    )

    assert result["status"] == "success"
    assert result["count"] == 2
    assert len(result["records"]) == 2
    assert result["records"][0] == {
        "Message": "Hello world",
        "Category": "Outreach",
        "Staff Member": "Alice",
        "Deadline": "2026-09-15",
    }
    assert result["records"][1] == {
        "Message": "Second row",
        "Category": "Trading",
        "Staff Member": "Bob",
        "Deadline": "",
    }
    mock_sheets_client.spreadsheets().values().get.assert_called_once_with(
        spreadsheetId="test_sheet_id",
        range="'TestRecords'!A1:Z",
    )


def test_get_records_empty_sheet(mock_workspace_service, mock_sheets_client):
    """Verify get_records returns 0 records when sheet has no values."""
    mock_get = MagicMock()
    mock_get.execute.return_value = {"values": []}
    mock_sheets_client.spreadsheets().values().get.return_value = mock_get

    result = mock_workspace_service.get_records(
        spreadsheet_id="test_sheet_id",
        sheet_name="EmptyTab",
    )

    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["records"] == []


def test_get_records_headers_only(mock_workspace_service, mock_sheets_client):
    """Verify get_records returns 0 records when sheet only contains row 1 headers."""
    mock_get = MagicMock()
    mock_get.execute.return_value = {
        "values": [["Message", "Category", "Date"]],
    }
    mock_sheets_client.spreadsheets().values().get.return_value = mock_get

    result = mock_workspace_service.get_records(
        spreadsheet_id="test_sheet_id",
        sheet_name="HeadersOnly",
    )

    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["records"] == []


def test_get_records_ragged_rows(mock_workspace_service, mock_sheets_client):
    """Verify get_records pads missing trailing columns with empty strings."""
    mock_get = MagicMock()
    mock_get.execute.return_value = {
        "values": [
            ["Col1", "Col2", "Col3"],
            ["val1"],  # only 1 column
        ],
    }
    mock_sheets_client.spreadsheets().values().get.return_value = mock_get

    result = mock_workspace_service.get_records(
        spreadsheet_id="test_sheet_id",
        sheet_name="RaggedTab",
    )

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["records"][0] == {"Col1": "val1", "Col2": "", "Col3": ""}


def test_create_event_success(mock_workspace_service, mock_calendar_client):
    """Verify create_event inserts event with ISO datetimes."""
    result = mock_workspace_service.create_event(
        calendar_id="test@example.com",
        summary="Test Event",
        start_iso="2026-09-11T12:00:00Z",
        end_iso="2026-09-11T13:00:00Z",
        description="Test Description",
        location="Virtual",
    )

    assert result["status"] == "success"
    assert result["event_id"] == "mock_event_id_12345"
    assert result["summary"] == "Integration Test Event"
    mock_calendar_client.events().insert.assert_called_once()


def test_create_event_invalid_end_before_start(mock_workspace_service):
    """Verify create_event raises ValueError when end_iso is before start_iso."""
    with pytest.raises(ValueError, match="must be greater than or equal to start_iso"):
        mock_workspace_service.create_event(
            calendar_id="test@example.com",
            summary="Invalid Timing",
            start_iso="2026-09-11T14:00:00Z",
            end_iso="2026-09-11T13:00:00Z",
        )


def test_create_event_invalid_iso_format(mock_workspace_service):
    """Verify create_event raises ValueError on invalid ISO timestamp strings."""
    with pytest.raises(ValueError, match="Invalid ISO 8601 datetime format"):
        mock_workspace_service.create_event(
            calendar_id="test@example.com",
            summary="Bad ISO",
            start_iso="not-a-timestamp",
            end_iso="2026-09-11T13:00:00Z",
        )


def test_list_events(mock_workspace_service, mock_calendar_client):
    """Verify list_events returns parsed upcoming events."""
    result = mock_workspace_service.list_events(calendar_id="test@example.com", max_results=5)
    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt_1"
    mock_calendar_client.events().list.assert_called_once()
