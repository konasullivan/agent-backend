"""
Very lightweight natural-language time range parsing for summary
requests. Good enough for a demo — swap for a real parser (e.g.
dateparser) later if people start asking for odder ranges.
"""
import re
from datetime import datetime, timedelta, timezone


SUMMARY_COMMAND = "!summarize"
SUMMARY_REGEX = re.compile(
    r"^(?:!summarize\b:?|(?:<@[^>]+>|@\S+?)\s*:?\s*summarize\b:?)",
    re.IGNORECASE,
)


def is_summary_request(text: str) -> bool:
    """Return True if text matches !summarize or @bot summarize / <@...> mention triggers."""
    if not text:
        return False
    return bool(SUMMARY_REGEX.match(text.strip()))


def extract_range_text(text: str) -> str:
    """Extract temporal range string after the summarize command or mention trigger."""
    if not text:
        return ""
    stripped = text.strip()
    match = SUMMARY_REGEX.match(stripped)
    if match:
        return stripped[match.end():].lstrip(":").strip()
    return stripped


def parse_time_range(text: str | None, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Returns (start, end, label) as timezone-aware UTC datetimes."""
    text_l = (text or "").lower()
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if "yesterday" in text_l:
        start = today_start - timedelta(days=1)
        end = today_start
        label = "yesterday"
        return start, end, label

    days_match = re.search(r"(?:(?:last|past)\s+)?(\d+)\s*days?", text_l)
    if days_match:
        num_days = int(days_match.group(1))
        if num_days > 0:
            start = today_start - timedelta(days=num_days)
            end = now
            if num_days == 7:
                label = "the past week"
            elif num_days == 30:
                label = "the past month"
            elif num_days == 1:
                label = "the past day"
            else:
                label = f"the past {num_days} days"
            return start, end, label

    if "month" in text_l:
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
