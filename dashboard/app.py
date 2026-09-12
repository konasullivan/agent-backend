from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sys
sys.path.append(str(Path(__file__).resolve().parent.parent))
from sheets_service import get_all_records  # noqa: E402

from datetime import datetime

app = FastAPI(title="Interaction Log Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def format_date(value: str) -> str:
    """Format date string cleanly if possible, fallback to raw."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    val = value.strip()
    if not val:
        return ""
    # Try ISO parsing (e.g. 2026-09-12T09:07:30Z or 2026-09-12T09:07:30-04:00)
    try:
        iso_val = val.replace("Z", "+00:00")
        if "T" in iso_val:
            dt = datetime.fromisoformat(iso_val)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    # Standard format attempts
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            if fmt == "%Y-%m-%d":
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return val


templates.env.filters["format_date"] = format_date


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    records = get_all_records()
    records = list(reversed(records))  # most recent first
    return templates.TemplateResponse(
        request=request, name="index.html", context={"records": records}
    )


@app.get("/api/records")
def api_records():
    return get_all_records()
