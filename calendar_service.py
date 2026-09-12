"""
Creates a Google Calendar event when the AI extractor finds a deadline
in a message. Dispatches event creation via Model Context Protocol (MCP).
"""
import base64
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any
from zoneinfo import ZoneInfo

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


def get_timezone_str() -> str:
    """Resolve configured IANA timezone string."""
    return getattr(config, "TIMEZONE", None) or os.getenv("TIMEZONE", "America/New_York")


def get_timezone() -> ZoneInfo:
    """Resolve ZoneInfo object for configured timezone."""
    try:
        return ZoneInfo(get_timezone_str())
    except Exception:
        return ZoneInfo("America/New_York")


def _normalize_date_range(date_str: str) -> tuple[str, str]:
    """Convert an incoming date or datetime string into ISO start_iso and end_iso strings.

    Localizes naive datetimes to the configured local timezone (e.g. 'America/New_York' -> -04:00 EDT)
    to prevent UTC timezone shifting when scheduled on user calendars.

    Supports:
    - Pure date strings: '2026-09-15' -> 09:00:00 local to 10:00:00 local
    - ISO datetime strings: '2026-09-15T19:00:00' -> 19:00:00 local to 20:00:00 local
    - Formatted date strings: '09/15/2026' -> 09:00:00 local to 10:00:00 local
    """
    s = str(date_str).strip()
    if not s:
        raise ValueError("Date string is empty")

    tz = get_timezone()

    # Try standard ISO format
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        # If pure date (length 10 like 'YYYY-MM-DD' and no 'T'), default to business hours (9:00 AM local)
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
                tzinfo=tz, hour=9, minute=0, second=0
            )
            return dt.isoformat(), (dt + timedelta(hours=1)).isoformat()
        except ValueError:
            continue

    raise ValueError(f"Unable to parse date string into ISO timestamp: {date_str}")


def extract_event_id_from_link(link: str) -> str | None:
    """Extract Google Calendar event ID from an event htmlLink URL (eid parameter)."""
    if not link or "eid=" not in link:
        return None
    try:
        eid_part = link.split("eid=")[1].split("&")[0].split(" ")[0]
        missing_padding = len(eid_part) % 4
        if missing_padding:
            eid_part += "=" * (4 - missing_padding)
        decoded = base64.urlsafe_b64decode(eid_part).decode("utf-8", errors="ignore")
        event_id = decoded.split(" ")[0].strip()
        return event_id if event_id else None
    except Exception as exc:
        logger.debug("Failed to extract event ID from link '%s': %s", link, exc)
        return None


def delete_event(
    event_id: str,
    calendar_id: str | None = None,
) -> bool:
    """Delete a Google Calendar event by ID via MCP tool 'calendar_delete_event'.

    Returns True on success or if the event was already deleted, False on failure.
    """
    if not event_id:
        return False
    try:
        cal_id = calendar_id or get_calendar_id()
        result: dict[str, Any] = call_mcp_tool_sync(
            "calendar_delete_event",
            {
                "calendar_id": cal_id,
                "event_id": event_id,
            },
        )
        return bool(result.get("status") == "success" and result.get("deleted"))
    except Exception as e:
        logger.error("[calendar_service] failed to delete event %s via MCP: %s", event_id, e)
        return False


def prune_superseded_events(
    target_date_str: str,
    new_location: str = "",
    calendar_id: str | None = None,
) -> list[str]:
    """Scan existing upcoming events for older, incomplete events on the same day and delete them.

    For example, if an existing event has no location and a new event specifies 'Texas Roadhouse',
    the prior incomplete event is removed to avoid duplicates.
    """
    deleted_ids: list[str] = []
    try:
        start_iso, _ = _normalize_date_range(target_date_str)
        target_date = start_iso[:10]  # YYYY-MM-DD
        cal_id = calendar_id or get_calendar_id()

        events_res: dict[str, Any] = call_mcp_tool_sync(
            "calendar_list_events",
            {
                "calendar_id": cal_id,
                "max_results": 20,
            },
        )
        for ev in events_res.get("events", []):
            ev_id = ev.get("id")
            ev_start = str(ev.get("start") or "")
            ev_loc = str(ev.get("location") or "").strip()
            # If on the same calendar day
            if ev_start.startswith(target_date) and ev_id:
                # If existing event lacks location and new event provides one, or is marked incomplete
                if (new_location and not ev_loc) or ("planned" in str(ev.get("summary", "")).lower()):
                    if delete_event(ev_id, calendar_id=cal_id):
                        logger.info(
                            "Pruned superseded/incomplete event %s on %s (had loc '%s', new loc '%s')",
                            ev_id,
                            target_date,
                            ev_loc,
                            new_location,
                        )
                        deleted_ids.append(ev_id)
    except Exception as exc:
        logger.debug("[calendar_service] prune_superseded_events non-fatal error: %s", exc)

    return deleted_ids


def create_event(
    summary: str,
    description: str,
    date_str: str,
    location: str = "",
) -> str | None:
    """date_str is an ISO date or datetime (e.g. '2026-09-15' or '2026-09-15T20:00:00').
    Returns the event's htmlLink on success, None on failure.

    Dispatches to MCP tool 'calendar_create_event'.
    """
    if not date_str:
        return None

    try:
        start_iso, end_iso = _normalize_date_range(date_str)
        calendar_id = get_calendar_id()
        tz_str = get_timezone_str()

        result: dict[str, Any] = call_mcp_tool_sync(
            "calendar_create_event",
            {
                "calendar_id": calendar_id,
                "summary": summary[:120],
                "description": description,
                "start_iso": start_iso,
                "end_iso": end_iso,
                "location": location,
                "time_zone": tz_str,
            },
        )

        # Accommodate both camelCase and snake_case return contracts
        html_link = result.get("html_link") or result.get("htmlLink")
        return str(html_link) if html_link else None

    except Exception as e:  # noqa: BLE001 - demo-level error handling
        logger.error("[calendar_service] failed to create event via MCP: %s", e)
        return None
