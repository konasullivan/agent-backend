from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sys
sys.path.append(str(Path(__file__).resolve().parent.parent))
from sheets_service import get_all_records  # noqa: E402

app = FastAPI(title="Interaction Log Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
