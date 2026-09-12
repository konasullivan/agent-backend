"""FastMCP / MCPServer definition and Google Workspace tools for agent-backend.

Under mcp==2.2.0, FastMCP was renamed to MCPServer.
All logging is directed strictly to sys.stderr to ensure stdio JSON-RPC framing remains uncorrupted.
Eager package import is avoided to eliminate RuntimeWarning when run via `python -m src.mcp_server.server`.
"""

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from src.services.google_services import GoogleWorkspaceService

# Configure logging strictly to sys.stderr so stdout JSON-RPC packets remain uncorrupted
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger(__name__)

# FastMCP alias for MCPServer
FastMCP = MCPServer

# Server instance
mcp_server = MCPServer("agent-backend-mcp")

# Default service instance, can be set/mocked for testing
_google_service: GoogleWorkspaceService | None = None


def get_google_service() -> GoogleWorkspaceService:
    """Retrieve or lazily initialize the GoogleWorkspaceService instance."""
    global _google_service
    if _google_service is None:
        _google_service = GoogleWorkspaceService()
    return _google_service


def set_google_service(service: GoogleWorkspaceService | None) -> None:
    """Explicitly set or reset the GoogleWorkspaceService instance (used for testing)."""
    global _google_service
    _google_service = service


# -----------------------------------------------------------------------------
# FastMCP Tools Registration (5 Core Tools)
# -----------------------------------------------------------------------------


@mcp_server.tool()
def sheets_ensure_tab(
    spreadsheet_id: str,
    sheet_name: str,
    headers: list[str],
) -> dict[str, Any]:
    """Ensure a worksheet tab exists in the specified Google Spreadsheet.

    If the tab does not exist, it is created and row 1 is populated with headers.
    If the tab already exists, existing data is preserved (idempotent).

    Args:
        spreadsheet_id: Google Spreadsheet ID.
        sheet_name: Worksheet tab name to ensure.
        headers: List of column header names to write if the tab is newly created.

    Returns:
        Dict containing status, spreadsheet_id, sheet_name, sheet_id, and created flag.
    """
    logger.info(
        "Executing sheets_ensure_tab: spreadsheet=%s, sheet_name=%s",
        spreadsheet_id,
        sheet_name,
    )
    svc = get_google_service()
    return svc.ensure_tab(
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        headers=headers,
    )


@mcp_server.tool()
def sheets_append_rows(
    spreadsheet_id: str,
    sheet_name: str,
    rows: list[list[Any]],
) -> dict[str, Any]:
    """Append rows of data to the specified worksheet tab in Google Sheets.

    Args:
        spreadsheet_id: Google Spreadsheet ID.
        sheet_name: Worksheet tab name to append data to.
        rows: 2D list of row values to append.

    Returns:
        Dict containing status, spreadsheet_id, sheet_name, rows_appended, and updated_range.
    """
    logger.info(
        "Executing sheets_append_rows: spreadsheet=%s, sheet_name=%s, row_count=%d",
        spreadsheet_id,
        sheet_name,
        len(rows),
    )
    svc = get_google_service()
    return svc.append_rows(
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        rows=rows,
    )


@mcp_server.tool()
def sheets_update_range(
    spreadsheet_id: str,
    sheet_name: str,
    range_notation: str,
    values: list[list[Any]],
) -> dict[str, Any]:
    """Update a range of cells in the specified worksheet tab in Google Sheets.

    Args:
        spreadsheet_id: Google Spreadsheet ID.
        sheet_name: Worksheet tab name.
        range_notation: Target cell or range (e.g. 'K17').
        values: 2D list of values to write.

    Returns:
        Dict containing status, spreadsheet_id, sheet_name, updated_range, and updated_cells.
    """
    logger.info(
        "Executing sheets_update_range: spreadsheet=%s, sheet_name=%s, range=%s",
        spreadsheet_id,
        sheet_name,
        range_notation,
    )
    svc = get_google_service()
    return svc.update_range(
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        range_notation=range_notation,
        values=values,
    )


@mcp_server.tool()
def sheets_get_records(
    spreadsheet_id: str,
    sheet_name: str,
) -> dict[str, Any]:
    """Retrieve all records from the specified worksheet tab in Google Sheets.

    Row 1 is treated as column headers and each subsequent row is returned as a
    dictionary mapping header name to cell value, matching gspread's get_all_records()
    behavior.

    Args:
        spreadsheet_id: Google Spreadsheet ID.
        sheet_name: Worksheet tab name to read records from.

    Returns:
        Dict containing status ('success'), spreadsheet_id, sheet_name,
        records (list of dicts keyed by header), and count.
    """
    logger.info(
        "Executing sheets_get_records: spreadsheet=%s, sheet_name=%s",
        spreadsheet_id,
        sheet_name,
    )
    svc = get_google_service()
    return svc.get_records(
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
    )


@mcp_server.tool()
def calendar_create_event(
    calendar_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    location: str = "",
    time_zone: str = "",
) -> dict[str, Any]:
    """Create a new event on the specified Google Calendar.

    Args:
        calendar_id: Target calendar email or ID (e.g. user email or 'primary').
        summary: Event title / headline.
        start_iso: Start datetime in ISO 8601 format (e.g. 2026-09-11T10:00:00-04:00).
        end_iso: End datetime in ISO 8601 format (e.g. 2026-09-11T11:00:00-04:00).
        description: Optional event description or notes.
        location: Optional meeting location or video call link.
        time_zone: Optional IANA timezone string (e.g. 'America/New_York').

    Returns:
        Dict containing status, event_id, html_link, summary, start, and end.
    """
    logger.info(
        "Executing calendar_create_event: calendar=%s, summary=%s",
        calendar_id,
        summary,
    )
    svc = get_google_service()
    return svc.create_event(
        calendar_id=calendar_id,
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        description=description,
        location=location,
        time_zone=time_zone,
    )


@mcp_server.tool()
def calendar_delete_event(
    calendar_id: str,
    event_id: str,
) -> dict[str, Any]:
    """Delete an event from Google Calendar by ID.

    Args:
        calendar_id: Google Calendar ID.
        event_id: Event ID to remove.

    Returns:
        Dict containing status, calendar_id, event_id, and deleted boolean.
    """
    logger.info(
        "Executing calendar_delete_event: calendar=%s, event_id=%s",
        calendar_id,
        event_id,
    )
    svc = get_google_service()
    return svc.delete_event(calendar_id=calendar_id, event_id=event_id)


@mcp_server.tool()
def calendar_list_events(
    calendar_id: str,
    max_results: int = 10,
) -> dict[str, Any]:
    """List upcoming events from the specified Google Calendar.

    Args:
        calendar_id: Target calendar email or ID.
        max_results: Maximum number of upcoming events to return (default: 10).

    Returns:
        Dict containing status, calendar_id, count, and list of upcoming events.
    """
    logger.info(
        "Executing calendar_list_events: calendar=%s, max_results=%d",
        calendar_id,
        max_results,
    )
    svc = get_google_service()
    return svc.list_events(
        calendar_id=calendar_id,
        max_results=max_results,
    )


def run_stdio() -> None:
    """Run the MCP server over stdio transport."""
    logger.info("Starting agent-backend FastMCP server on stdio transport...")
    mcp_server.run("stdio")


if __name__ == "__main__":
    run_stdio()
