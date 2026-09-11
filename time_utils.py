"""
Very lightweight natural-language time range parsing for summary
requests. Good enough for a demo — swap for a real parser (e.g.
dateparser) later if people start asking for odder ranges.
"""
from datetime import datetime, timedelta, timezone


SUMMARY_COMMAND = "!summarize"


def is_summary_request(text: str) -> bool:
    return text.strip().lower().startswith(SUMMARY_COMMAND)


def parse_time_range(text: str, now: datetime | None = None):
    """Returns (start, end, label) as timezone-aware UTC datetimes."""
    text_l = text.lower()
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if "yesterday" in text_l:
        start = today_start - timedelta(days=1)
        end = today_start
        label = "yesterday"
    elif "month" in text_l:
        start = today_start - timedelta(days=30)
        end = now
        label = "the past month"
    elif "week" in text_l:
        start = today_start - timedelta(days=7)
        end = now
        label = "the past week"
    else:
        start = today_start
        end = now
        label = "today"

    return start, end, label
