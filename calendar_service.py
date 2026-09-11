"""
Creates a Google Calendar event when the AI extractor finds a deadline
in a message. Uses the same service account as sheets_service.py —
just remember to also share the calendar with that service account's
email (Calendar settings -> "Share with specific people").
"""
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]

_service = None


def _get_service():
    global _service
    if _service is None:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        _service = build("calendar", "v3", credentials=creds)
    return _service


def create_event(summary: str, description: str, date_str: str) -> str | None:
    """date_str is an ISO date like '2026-09-15'. Returns the event's
    htmlLink on success, None on failure."""
    service = _get_service()
    event = {
        "summary": summary[:120],
        "description": description,
        "start": {"date": date_str},
        "end": {"date": date_str},
    }
    try:
        created = (
            service.events()
            .insert(calendarId=config.GOOGLE_CALENDAR_ID, body=event)
            .execute()
        )
        return created.get("htmlLink")
    except Exception as e:  # noqa: BLE001 - demo-level error handling
        print(f"[calendar_service] failed to create event: {e}")
        return None
