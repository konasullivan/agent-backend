"""Live integration tests for Google Workspace and FastMCP server."""

from pathlib import Path
import pytest

import config
from main import run_mcp_verification
from src.services.google_services import GoogleWorkspaceService

_key_path = (
    Path(config.GOOGLE_SERVICE_ACCOUNT_FILE)
    if config.GOOGLE_SERVICE_ACCOUNT_FILE
    else Path("nonexistent")
)


@pytest.mark.skipif(
    not _key_path.exists(),
    reason="Service account key file not found; skipping live test",
)
def test_live_google_workspace_connectivity():
    """Verify live credentials and metadata retrieval from Google Sheets and Calendar."""
    service = GoogleWorkspaceService()

    # Verify Sheets API connectivity
    spreadsheet_meta = (
        service.sheets_service.spreadsheets()
        .get(spreadsheetId=config.GOOGLE_SHEET_ID)
        .execute()
    )
    assert "properties" in spreadsheet_meta
    title = spreadsheet_meta["properties"].get("title")
    assert title is not None
    assert len(title) > 0

    # Verify Calendar API connectivity
    calendar_meta = (
        service.calendar_service.calendars()
        .get(calendarId=config.GOOGLE_CALENDAR_ID)
        .execute()
    )
    assert "summary" in calendar_meta
    assert calendar_meta.get("summary") == config.GOOGLE_CALENDAR_ID


@pytest.mark.skipif(
    not _key_path.exists(),
    reason="Service account key file not found; skipping live test",
)
@pytest.mark.asyncio
async def test_live_inproc_verification_pipeline():
    """Verify the full in-process verification pipeline against live Google APIs."""
    success = await run_mcp_verification(
        transport="inproc",
        spreadsheet_id=config.GOOGLE_SHEET_ID,
        calendar_id=config.GOOGLE_CALENDAR_ID,
        tab_name="TestRecords",
    )
    assert success is True
