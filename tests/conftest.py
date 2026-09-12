"""Pytest configuration and mock fixtures for offline testing."""

import os
from unittest.mock import MagicMock
import pytest
from google.oauth2 import service_account

# Ensure offline mock keys are present for test execution
os.environ.setdefault("GEMINI_API_KEY", "mock-offline-key")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-mock-bot-token-12345")
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-mock-app-token-12345")

from src.mcp_server.server import set_google_service
from src.services.google_services import GoogleWorkspaceService


@pytest.fixture
def mock_google_credentials(monkeypatch):
    """Fixture providing a mocked Google Service Account credentials object."""
    mock_creds = MagicMock(spec=service_account.Credentials)
    mock_creds.valid = True
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        MagicMock(return_value=mock_creds),
    )
    return mock_creds


@pytest.fixture
def mock_sheets_client():
    """Fixture providing a mocked Google Sheets API Resource client."""
    client = MagicMock()

    # spreadsheets().get()
    mock_get = MagicMock()
    mock_get.execute.return_value = {
        "properties": {"title": "Sample Output for Agent Backend"},
        "sheets": [
            {"properties": {"title": "Sheet1", "sheetId": 0}},
        ],
    }
    client.spreadsheets.return_value.get.return_value = mock_get

    # spreadsheets().batchUpdate()
    mock_batch = MagicMock()
    mock_batch.execute.return_value = {
        "replies": [
            {
                "addSheet": {
                    "properties": {"title": "TestRecords", "sheetId": 123456}
                }
            }
        ]
    }
    client.spreadsheets.return_value.batchUpdate.return_value = mock_batch

    # spreadsheets().values().update()
    mock_update = MagicMock()
    mock_update.execute.return_value = {
        "updatedRange": "'TestRecords'!A1:H1",
        "updatedRows": 1,
    }
    client.spreadsheets.return_value.values.return_value.update.return_value = (
        mock_update
    )

    # spreadsheets().values().append()
    mock_append = MagicMock()
    mock_append.execute.return_value = {
        "updates": {
            "updatedRows": 2,
            "updatedRange": "'TestRecords'!A2:H3",
        }
    }
    client.spreadsheets.return_value.values.return_value.append.return_value = (
        mock_append
    )

    # spreadsheets().values().get()
    mock_values_get = MagicMock()
    mock_values_get.execute.return_value = {
        "range": "'TestRecords'!A1:Z",
        "values": [
            ["Message", "Category", "Staff Member", "Deadline"],
            ["Hello world", "Outreach", "Alice", "2026-09-15"],
            ["Second row", "Trading", "Bob", ""],
        ],
    }
    client.spreadsheets.return_value.values.return_value.get.return_value = (
        mock_values_get
    )

    return client


@pytest.fixture
def mock_calendar_client():
    """Fixture providing a mocked Google Calendar API Resource client."""
    client = MagicMock()

    # events().insert()
    mock_insert = MagicMock()
    mock_insert.execute.return_value = {
        "id": "mock_event_id_12345",
        "htmlLink": "https://www.google.com/calendar/event?eid=mock_12345",
        "summary": "Integration Test Event",
        "start": {"dateTime": "2026-09-11T12:00:00Z"},
        "end": {"dateTime": "2026-09-11T13:00:00Z"},
    }
    client.events.return_value.insert.return_value = mock_insert

    # events().list()
    mock_list = MagicMock()
    mock_list.execute.return_value = {
        "items": [
            {
                "id": "evt_1",
                "summary": "Existing Event 1",
                "start": {"dateTime": "2026-09-11T10:00:00Z"},
                "end": {"dateTime": "2026-09-11T11:00:00Z"},
                "htmlLink": "https://cal.google.com/1",
            }
        ]
    }
    client.events.return_value.list.return_value = mock_list

    return client


@pytest.fixture
def mock_workspace_service(mock_google_credentials, mock_sheets_client, mock_calendar_client, monkeypatch):
    """Fixture providing a GoogleWorkspaceService with mocked API clients."""
    service = GoogleWorkspaceService(credentials_path="test_credentials.json")
    monkeypatch.setattr(service, "_build_sheets_client", lambda: mock_sheets_client)
    monkeypatch.setattr(service, "_build_calendar_client", lambda: mock_calendar_client)
    monkeypatch.setattr(service, "_get_credentials", lambda: mock_google_credentials)

    # Inject into MCP server module
    set_google_service(service)
    yield service
    set_google_service(None)
