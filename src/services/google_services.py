"""Google Workspace integration service for Google Sheets and Google Calendar.

Ported from smart_summerizer with full support for:
- Google Sheets API v4 (ensure_tab, append_rows, get_records)
- Google Calendar API v3 (create_event, list_events)
- Service account credential loading from config.GOOGLE_SERVICE_ACCOUNT_FILE
- Resilient relative path resolution and error handling
"""

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build

# Ensure project root is available on sys.path for config import
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import config
except ImportError:
    config = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
]


class GoogleWorkspaceService:
    """Service wrapper for interacting with Google Sheets v4 and Calendar v3 APIs."""

    def __init__(
        self,
        credentials_path: str | Path | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        """Initialize GoogleWorkspaceService.

        Args:
            credentials_path: Path to the Google Service Account JSON key file.
                              If None, resolved from config.GOOGLE_SERVICE_ACCOUNT_FILE
                              or environment variables.
            scopes: OAuth 2.0 scopes for Sheets and Calendar APIs.
                    Defaults to DEFAULT_SCOPES.
        """
        if credentials_path is None:
            creds_candidate = (
                getattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", None)
                or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
                or os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH")
                or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
                or "gen-lang-client-0552805033-e228d6204d68.json"
            )
            credentials_path = creds_candidate

        candidate_path = Path(credentials_path)
        # If relative path and does not exist in current working directory, resolve from PROJECT_ROOT
        if not candidate_path.is_absolute() and not candidate_path.exists():
            root_relative = PROJECT_ROOT / candidate_path
            if root_relative.exists():
                candidate_path = root_relative

        self.credentials_path = candidate_path
        self.scopes = scopes or list(DEFAULT_SCOPES)
        self._credentials: service_account.Credentials | None = None
        self._sheets_client: Resource | None = None
        self._calendar_client: Resource | None = None

    def _get_credentials(self) -> service_account.Credentials:
        """Load and return service account credentials.

        Raises:
            FileNotFoundError: If the service account file does not exist.
        """
        if self._credentials is None:
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f"Google Service Account key file not found at: {self.credentials_path}"
                )
            self._credentials = (
                service_account.Credentials.from_service_account_file(
                    str(self.credentials_path),
                    scopes=self.scopes,
                )
            )
        return self._credentials

    def _build_sheets_client(self) -> Resource:
        """Build Google Sheets API v4 Resource."""
        creds = self._get_credentials()
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    def _build_calendar_client(self) -> Resource:
        """Build Google Calendar API v3 Resource."""
        creds = self._get_credentials()
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    @property
    def sheets_service(self) -> Resource:
        """Lazy-loaded Sheets API client."""
        if self._sheets_client is None:
            self._sheets_client = self._build_sheets_client()
        return self._sheets_client

    @property
    def calendar_service(self) -> Resource:
        """Lazy-loaded Calendar API client."""
        if self._calendar_client is None:
            self._calendar_client = self._build_calendar_client()
        return self._calendar_client

    # -------------------------------------------------------------------------
    # Google Sheets Methods
    # -------------------------------------------------------------------------

    def ensure_tab(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        headers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Ensure a worksheet tab exists in the spreadsheet.

        If the tab is missing, it is created with `addSheet` and optionally
        populated with the provided headers in row 1.
        If the tab already exists, existing data is preserved (idempotent).

        Args:
            spreadsheet_id: Google Spreadsheet ID.
            sheet_name: Worksheet tab title.
            headers: Optional column header strings to write to row 1 if newly created.

        Returns:
            Dict containing status, spreadsheet_id, sheet_name, sheet_id, created flag, headers_written.
        """
        spreadsheet = (
            self.sheets_service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id)
            .execute()
        )
        existing_sheets = spreadsheet.get("sheets", [])

        for sheet in existing_sheets:
            props = sheet.get("properties", {})
            if props.get("title") == sheet_name:
                sheet_id = props.get("sheetId")
                return {
                    "status": "success",
                    "spreadsheet_id": spreadsheet_id,
                    "sheet_name": sheet_name,
                    "sheet_id": sheet_id,
                    "created": False,
                    "headers_written": [],
                }

        # Tab does not exist; create it via batchUpdate
        add_sheet_request = {
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": sheet_name,
                        }
                    }
                }
            ]
        }
        res = (
            self.sheets_service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=add_sheet_request)
            .execute()
        )
        replies = res.get("replies", [])
        new_sheet_id = (
            replies[0]["addSheet"]["properties"]["sheetId"] if replies else None
        )

        headers_written: list[str] = []
        if headers:
            # Populate header row 1
            safe_range = f"'{sheet_name}'!A1"
            header_body = {"values": [headers]}
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=safe_range,
                valueInputOption="USER_ENTERED",
                body=header_body,
            ).execute()
            headers_written = headers

        return {
            "status": "success",
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
            "sheet_id": new_sheet_id,
            "created": True,
            "headers_written": headers_written,
        }

    def append_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        rows: list[list[Any]],
    ) -> dict[str, Any]:
        """Append rows to the specified worksheet tab.

        If rows is empty, short-circuits to avoid Google Sheets API 400 error.
        Sanitizes non-primitive cell types to strings.

        Args:
            spreadsheet_id: Google Spreadsheet ID.
            sheet_name: Worksheet tab title.
            rows: List of rows (each row is a list of cell values).

        Returns:
            Dict containing status, spreadsheet_id, sheet_name, rows_appended count, updated_range.
        """
        if not rows:
            return {
                "status": "success",
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "rows_appended": 0,
                "updated_range": "",
            }

        # Sanitize row data: convert non-primitive types to strings
        sanitized_rows = [
            [str(cell) if cell is not None else "" for cell in row]
            for row in rows
        ]

        safe_range = f"'{sheet_name}'!A1"
        body = {"values": sanitized_rows}

        response = (
            self.sheets_service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=safe_range,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            )
            .execute()
        )

        updates = response.get("updates", {})
        return {
            "status": "success",
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
            "rows_appended": updates.get("updatedRows", len(rows)),
            "updated_range": updates.get("updatedRange", ""),
        }

    def get_records(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> dict[str, Any]:
        """Retrieve all records from the specified worksheet tab.

        Uses spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'!A1:Z").
        Row 1 is treated as column headers. Subsequent rows are mapped to dictionaries
        keyed by header names, matching the behavior of gspread's get_all_records().

        Args:
            spreadsheet_id: Google Spreadsheet ID.
            sheet_name: Worksheet tab title.

        Returns:
            Dict with status ('success'), spreadsheet_id, sheet_name, records (list of dicts), and count.
        """
        range_name = f"'{sheet_name}'!A1:Z"
        logger.info(
            "Fetching records from spreadsheet=%s, sheet_name=%s, range=%s",
            spreadsheet_id,
            sheet_name,
            range_name,
        )

        response = (
            self.sheets_service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )

        values = response.get("values", [])
        if not values:
            logger.info("Sheet '%s' is empty; returning 0 records", sheet_name)
            return {
                "status": "success",
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "records": [],
                "count": 0,
            }

        headers = [str(h) for h in values[0]]
        if len(values) <= 1:
            logger.info("Sheet '%s' has headers only; returning 0 records", sheet_name)
            return {
                "status": "success",
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "records": [],
                "count": 0,
            }

        records: list[dict[str, Any]] = []
        for row in values[1:]:
            record: dict[str, Any] = {}
            for idx, header in enumerate(headers):
                cell_val = row[idx] if idx < len(row) else ""
                record[header] = "" if cell_val is None else cell_val
            records.append(record)

        logger.info(
            "Retrieved %d records from sheet '%s' in spreadsheet=%s",
            len(records),
            sheet_name,
            spreadsheet_id,
        )
        return {
            "status": "success",
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
            "records": records,
            "count": len(records),
        }

    # -------------------------------------------------------------------------
    # Google Calendar Methods
    # -------------------------------------------------------------------------

    def create_event(
        self,
        calendar_id: str,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        """Create an event on the specified Google Calendar.

        Args:
            calendar_id: Target calendar email or ID (e.g. 'primary' or user email).
            summary: Event title / headline.
            start_iso: Event start datetime in ISO 8601 string format.
            end_iso: Event end datetime in ISO 8601 string format.
            description: Optional event description or notes.
            location: Optional event location or link.

        Returns:
            Dict containing status, event_id, html_link, summary, start, and end.

        Raises:
            ValueError: If start_iso or end_iso are not valid ISO 8601 strings,
                        or if end_iso < start_iso.
        """
        try:
            start_dt = datetime.fromisoformat(start_iso)
            end_dt = datetime.fromisoformat(end_iso)
        except ValueError as exc:
            raise ValueError(
                f"Invalid ISO 8601 datetime format: {exc}"
            ) from exc

        if end_dt < start_dt:
            raise ValueError(
                f"end_iso ({end_iso}) must be greater than or equal to start_iso ({start_iso})"
            )

        event_body = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {
                "dateTime": start_iso,
            },
            "end": {
                "dateTime": end_iso,
            },
        }

        created = (
            self.calendar_service.events()
            .insert(calendarId=calendar_id, body=event_body)
            .execute()
        )

        return {
            "status": "success",
            "event_id": created.get("id"),
            "html_link": created.get("htmlLink", ""),
            "summary": created.get("summary", summary),
            "start": created.get("start", {}).get("dateTime", start_iso),
            "end": created.get("end", {}).get("dateTime", end_iso),
        }

    def list_events(
        self,
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
        now_iso = datetime.now(UTC).isoformat()
        events_result = (
            self.calendar_service.events()
            .list(
                calendarId=calendar_id,
                timeMin=now_iso,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        items = events_result.get("items", [])
        return {
            "status": "success",
            "calendar_id": calendar_id,
            "count": len(items),
            "events": [
                {
                    "id": item.get("id"),
                    "summary": item.get("summary"),
                    "start": item.get("start", {}).get("dateTime")
                    or item.get("start", {}).get("date"),
                    "end": item.get("end", {}).get("dateTime")
                    or item.get("end", {}).get("date"),
                    "html_link": item.get("htmlLink"),
                }
                for item in items
            ],
        }
