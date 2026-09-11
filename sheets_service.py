"""
Thin wrapper around Google Sheets, used as the "ERP" for the demo.
Swap this module for a real ERP REST client later -- as long as
append_record()/get_all_records() keep the same signatures, nothing
else in the app needs to change.
"""
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_gc = None
_sheet = None


def _get_sheet():
    global _gc, _sheet
    if _sheet is None:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        _gc = gspread.authorize(creds)
        _sheet = _gc.open_by_key(config.GOOGLE_SHEET_ID).sheet1

        existing = _sheet.row_values(1)
        if existing != config.SHEET_HEADERS:
            _sheet.update("A1", [config.SHEET_HEADERS])
    return _sheet


def append_record(record: dict) -> None:
    """record keys should match config.SHEET_HEADERS."""
    sheet = _get_sheet()
    row = [record.get(col, "") for col in config.SHEET_HEADERS]
    sheet.append_row(row, value_input_option="USER_ENTERED")


def get_all_records() -> list[dict]:
    sheet = _get_sheet()
    return sheet.get_all_records()  # list of dicts keyed by header row


def get_records_in_range(start: datetime, end: datetime) -> list[dict]:
    """Filters logged rows whose Date falls within [start, end]."""
    filtered = []
    for r in get_all_records():
        date_str = r.get("Date")
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(str(date_str))
        except ValueError:
            continue
        if start <= dt <= end:
            filtered.append(r)
    return filtered
