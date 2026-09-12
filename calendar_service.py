"""
Creates a Google Calendar event when the AI extractor finds a deadline
in a message. Dispatches event creation via Model Context Protocol (MCP).
"""
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

import config
from src.mcp_client.client import call_mcp_tool_sync

logger = logging.getLogger(__name__)


def get_calendar_id() -> str:
    """Resolve target calendar ID from config or environment."""
    cal_id = getattr(config, "GOOGLE_CALENDAR_ID", None) or os.getenv("GOOGLE_CALENDAR_ID")
    if not cal_id:
        raise ValueError(
            "GOOGLE_CALENDAR_ID is not configured in config.py or environment variables."
        )
    return cal_id


def _normalize_date_range(date_str: str) -> tuple[str, str]:
    """Convert an incoming date or datetime string into ISO start_iso and end_iso strings.

    Supports:
    - Pure date strings: '2026-09-15' -> 09:00:00Z to 10:00:00Z
    - ISO datetime strings: '2026-09-15T14:00:00' -> 14:00:00Z to 15:00:00Z
    - Formatted date strings: '09/15/2026' -> 09:00:00Z to 10:00:00Z
    """
    s = str(date_str).strip()
    if not s:
        raise ValueError("Date string is empty")

    # Try standard ISO format
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # If pure date (length 10 like 'YYYY-MM-DD' and no 'T'), default to business hours (9:00 AM UTC)
        if len(s) == 10 and "T" not in s:
            start_dt = dt.replace(hour=9, minute=0, second=0)
        else:
            start_dt = dt
        end_dt = start_dt + timedelta(hours=1)
        return start_dt.isoformat(), end_dt.isoformat()
    except ValueError:
        pass

    # Try localized date formats
    for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(s, fmt).replace(
                tzinfo=timezone.utc, hour=9, minute=0, second=0
            )
            return dt.isoformat(), (dt + timedelta(hours=1)).isoformat()
        except ValueError:
            continue

    raise ValueError(f"Unable to parse date string into ISO timestamp: {date_str}")


def create_event(summary: str, description: str, date_str: str) -> str | None:
    """date_str is an ISO date like '2026-09-15'. Returns the event's
    htmlLink on success, None on failure.

    Dispatches to MCP tool 'calendar_create_event'.
    """
    if not date_str:
        return None

    try:
        start_iso, end_iso = _normalize_date_range(date_str)
        calendar_id = get_calendar_id()

        result: dict[str, Any] = call_mcp_tool_sync(
            "calendar_create_event",
            {
                "calendar_id": calendar_id,
                "summary": summary[:120],
                "description": description,
                "start_iso": start_iso,
                "end_iso": end_iso,
            },
        )

        # Accommodate both camelCase and snake_case return contracts
        html_link = result.get("html_link") or result.get("htmlLink")
        return str(html_link) if html_link else None

    except Exception as e:  # noqa: BLE001 - demo-level error handling
        logger.error("[calendar_service] failed to create event via MCP: %s", e)
        return None
