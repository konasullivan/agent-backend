"""
Thin wrapper around Google Sheets, dispatched via Model Context Protocol (MCP).
Swap this module for a real ERP REST client later -- as long as
append_record()/get_all_records() keep the same signatures, nothing
else in the app needs to change.
"""
from datetime import datetime
import logging
import os
from typing import Any

import config
from src.mcp_client.client import call_mcp_tool_sync

logger = logging.getLogger(__name__)

_tab_ensured: bool = False


def get_sheet_tab_name() -> str:
    """Resolve target sheet tab name from config or environment, defaulting to 'ChatRecords'."""
    return getattr(
        config,
        "GOOGLE_SHEET_TAB_NAME",
        os.getenv("GOOGLE_SHEET_TAB_NAME", "ChatRecords"),
    )


def get_spreadsheet_id() -> str:
    """Resolve target spreadsheet ID from config or environment."""
    sheet_id = getattr(config, "GOOGLE_SHEET_ID", None) or os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError(
            "GOOGLE_SHEET_ID is not configured in config.py or environment variables."
        )
    return sheet_id


def ensure_tab_exists() -> None:
    """Ensure that target worksheet tab exists with configured headers.

    Idempotent: calls sheets_ensure_tab via MCP on first invocation per process,
    or re-attempts if a previous attempt failed.
    """
    global _tab_ensured
    if _tab_ensured:
        return
    try:
        call_mcp_tool_sync(
            "sheets_ensure_tab",
            {
                "spreadsheet_id": get_spreadsheet_id(),
                "sheet_name": get_sheet_tab_name(),
                "headers": config.SHEET_HEADERS,
            },
        )
        _tab_ensured = True
    except Exception as exc:
        logger.warning(
            "sheets_ensure_tab check failed or skipped: %s (proceeding with operation)",
            exc,
        )


def append_record(record: dict) -> str:
    """Append a business chat record dictionary to Google Sheets via FastMCP.

    record keys should match config.SHEET_HEADERS.
    Dispatches to MCP tool 'sheets_append_rows'.
    Returns updated_range (e.g. "'ChatRecords'!A17:K17").
    """
    ensure_tab_exists()
    row = [record.get(col, "") for col in config.SHEET_HEADERS]
    res = call_mcp_tool_sync(
        "sheets_append_rows",
        {
            "spreadsheet_id": get_spreadsheet_id(),
            "sheet_name": get_sheet_tab_name(),
            "rows": [row],
        },
    )
    return str(res.get("updated_range") or "") if isinstance(res, dict) else ""


def update_record_link(updated_range: str, link: str) -> None:
    """Update the 'Link' column (Column K) of a previously appended row."""
    if not updated_range or not link:
        return
    import re
    # Match row numbers from range like 'ChatRecords'!A17:K17 or A17:K17
    match = re.search(r"(\d+)(?::[A-Za-z]+(\d+))?$", updated_range)
    if not match:
        return
    row_num = match.group(1)
    target_cell = f"K{row_num}"

    try:
        call_mcp_tool_sync(
            "sheets_update_range",
            {
                "spreadsheet_id": get_spreadsheet_id(),
                "sheet_name": get_sheet_tab_name(),
                "range_notation": target_cell,
                "values": [[link]],
            },
        )
    except Exception as exc:
        logger.warning("Failed to update record link via MCP: %s", exc)


def get_all_records() -> list[dict]:
    """Retrieve all records from the target Google Sheet tab via FastMCP.

    Returns a list of dicts keyed by header names matching config.SHEET_HEADERS.
    Guarantees 100% backward compatibility with dashboard/app.py and index.html:
    - Dispatches to MCP tool 'sheets_get_records'.
    - Normalizes header names (mapping legacy column 'A' to 'Message').
    - Ensures all keys from config.SHEET_HEADERS exist with default string values.
    """
    result = call_mcp_tool_sync(
        "sheets_get_records",
        {
            "spreadsheet_id": get_spreadsheet_id(),
            "sheet_name": get_sheet_tab_name(),
        },
    )

    raw_records: list[dict[str, Any]] = result.get("records", [])
    normalized_records: list[dict[str, Any]] = []

    for r in raw_records:
        # Pre-populate all expected headers with empty strings
        normalized: dict[str, Any] = {col: "" for col in config.SHEET_HEADERS}
        normalized.update(r)

        # Handle legacy sheet header where Column A was named 'A' instead of 'Message'
        if not normalized.get("Message") and r.get("A"):
            normalized["Message"] = r["A"]

        normalized_records.append(normalized)

    return normalized_records


def _parse_record_datetime(val: Any) -> datetime | None:
    """Parse various datetime string formats into a comparable datetime object."""
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None

    # Try standard ISO format
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass

    # Try common formats (e.g. '9/9/2026 8:32:11', '9/9/2026')
    for fmt in (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    return None


def get_records_in_range(start: datetime, end: datetime) -> list[dict]:
    """Filters logged rows whose Date falls within [start, end].

    Harmonizes timezone awareness between start and parsed dates to avoid TypeError.
    """
    filtered = []
    start_is_aware = start.tzinfo is not None

    for r in get_all_records():
        date_val = r.get("Date")
        if not date_val:
            continue
        dt = _parse_record_datetime(date_val)
        if dt is None:
            continue

        # Harmonize timezone awareness between start and dt
        if start_is_aware and dt.tzinfo is None:
            dt = dt.replace(tzinfo=start.tzinfo)
        elif not start_is_aware and dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)

        if start <= dt <= end:
            filtered.append(r)

    return filtered
