# Long-Term Implementation Plan: Agent Backend

## 1. System Vision & Architecture

The Agent Backend serves as an enterprise workspace automation bridge. It captures business communication from team chat (Slack Socket Mode), applies structured reasoning via Google Gemini, and records interactions and calendar deadlines across Google Workspace through the Model Context Protocol (MCP).

```
                                +---------------------------+
                                | Slack Platform (Socket)   |
                                +-------------+-------------+
                                              | (WebSocket xapp- / xoxb-)
                                              v
+-----------------------+       +---------------------------+       +------------------------+
| FastAPI Dashboard     |       | slack_bot.py (SocketMode) |       | main.py (CLI Runner)   |
| (dashboard/app.py)    |       +-------------+-------------+       +-----------+------------+
+-----------+-----------+                     |                                 |
            |                                 v                                 |
            |                   +---------------------------+                   |
            |                   | ai_extractor & tracker    |                   |
            |                   | (Gemini 2.5/3.7 Flash)    |                   |
            |                   +-------------+-------------+                   |
            |                                 |                                 |
            +-------------------+-------------+---------------------------------+
                                |
                                v
                +-------------------------------+
                | Service Layer:                |
                | - sheets_service.py (no gspread)|
                | - calendar_service.py         |
                +---------------+---------------+
                                |
                                v
                +-------------------------------+
                | MCP Client Layer:             |
                | - src/mcp_client/client.py    |
                +---------------+---------------+
                                | (stdio / inproc JSON-RPC)
                                v
                +-------------------------------+
                | FastMCP Server Layer:         |
                | - src/mcp_server/server.py    |
                |   (logging strictly to stderr)|
                +---------------+---------------+
                                |
                                v
                +-------------------------------+
                | Google Workspace Service:     |
                | - src/services/google_services|
                +---------------+---------------+
                                | (Google APIs: v4 Sheets, v3 Calendar)
                                v
                +-------------------------------+
                | Google Cloud Workspace APIs   |
                +-------------------------------+
```

---

## 2. Component Ownership & System Boundaries

| Component | File Location | Responsibility Boundary |
|-----------|---------------|-------------------------|
| **CLI Runner** | `main.py` | Argument parsing, verification workflows (`--test-slack`, `--test-mcp`, `--listen-slack`). |
| **Slack Ingestion** | `slack_bot.py` | Socket Mode listener, event filtering, user name resolution, permalink generation, emoji reactions. |
| **AI Extraction Core** | `ai_extractor.py` | Gemini Flash structured extraction (`category`, `notes`, `action_items`, `deadline`) and summary synthesis. |
| **Conversation Tracker**| `conversation_tracker.py` | Gemini embedding clustering, topic labeling, semantic session grouping. |
| **Time Range Parser** | `time_utils.py` | Natural language time range parsing and summary trigger regex matching. |
| **Sheets Service** | `sheets_service.py` | Thin wrapper dispatching sheet read/write calls to MCP tools (`ensure_tab`, `append_rows`, `get_records`). Zero `gspread` dependency. |
| **Calendar Service** | `calendar_service.py` | Thin wrapper dispatching calendar event creations to MCP tool (`calendar_create_event`). |
| **MCP Client Bridge** | `src/mcp_client/client.py`| Connection management for `inproc` and `stdio` transports; synchronous execution bridge (`call_mcp_tool_sync`). |
| **FastMCP Server** | `src/mcp_server/server.py`| Registers 5 core MCP tools, strictly routes logs to `sys.stderr`. |
| **Google Workspace Svc**| `src/services/google_services.py` | Low-level Google API client (Sheets v4, Calendar v3) authenticated via Service Account. |
| **Web Dashboard** | `dashboard/app.py` | FastAPI application rendering historical interaction records via Jinja2 templates. |

---

## 3. Phased Implementation Roadmap

### Milestone M1: Packaging, Security & Setup (COMPLETED)
*Goal: Establish robust packaging, credential isolation, and clean repository state.*
- [x] Configure `.gitignore` blocking `gen-lang-client-*.json`, `.env`, `.venv`, and cache directories.
- [x] Package project with Astral `uv` via `pyproject.toml` with console scripts `main` and `agent-backend`.
- [x] Update `requirements.txt` mirroring `pyproject.toml` dependencies.
- [x] Copy and verify Google Service Account key `gen-lang-client-0552805033-e228d6204d68.json`.
- [x] Update `.env.example` and `.env` with Slack, Gemini, and Google Workspace keys.

### Milestone M2: FastMCP Layer & Service Decoupling (COMPLETED)
*Goal: Decouple Google Sheets and Calendar operations behind FastMCP server and client layers.*
- [x] Port `GoogleWorkspaceService` (`src/services/google_services.py`) with Sheets v4 (`ensure_tab`, `append_rows`, `get_records`) and Calendar v3 (`create_event`, `list_events`).
- [x] Implement FastMCP server (`src/mcp_server/server.py`) exposing 5 workspace tools with strict `sys.stderr` logging.
- [x] Implement MCP client (`src/mcp_client/client.py`) supporting `inproc` and `stdio` transports and thread-safe synchronous bridge `call_mcp_tool_sync`.
- [x] Refactor `sheets_service.py` and `calendar_service.py` to dispatch operations via MCP tools with 0 imports of `gspread`.

### Milestone M3: Slack Bot & Socket Mode Ingestion (COMPLETED)
*Goal: Replace Discord bot with Slack Socket Mode listener and end-to-end ingestion pipeline.*
- [x] Decommission and delete `discord_bot.py` from repository index.
- [x] Implement `slack_bot.py` using `slack-bolt` `SocketModeHandler`.
- [x] Implement 7-step message ingestion flow:
  1. Structured metadata extraction (`ai_extractor.extract`).
  2. Conversation grouping via `thread_ts` or semantic clustering (`conversation_tracker`).
  3. Author subteam mapping (`config.get_subteam`).
  4. Slack permalink generation with synthetic fallback.
  5. 11-column record row append to Google Sheets via MCP `sheets_append_rows`.
  6. Google Calendar event creation via MCP `calendar_create_event` when a deadline is present.
  7. Message acknowledgment reaction (`👀`).
- [x] Implement summary request processing for `!summarize [range]` and `@bot summarize [range]`.

### Milestone M4: Dashboard Continuity & CLI Verification (COMPLETED)
*Goal: Ensure UI continuity and implement automated CLI operational commands.*
- [x] Verify `dashboard/app.py` displays records from Google Sheets via MCP-backed `sheets_service.get_all_records()`.
- [x] Implement CLI command `main.py --test-slack` verifying Slack authentication and bot identity.
- [x] Implement CLI command `main.py --listen-slack` running live Socket Mode listener.
- [x] Implement CLI command `main.py --test-mcp` executing 4-stage verification against Sheets and Calendar over `inproc` and `stdio`.

### Milestone M5: Final Test Pass, Hardening & Documentation (COMPLETED)
*Goal: Maintain 100% test pass rate and produce comprehensive architectural documentation.*
- [x] Full test suite porting and verification across 294 tests with 100% pass rate.
- [x] Port `tests/test_live_mcp.py` for live Google Sheets and Calendar API verification.
- [x] Merge 15 FastMCP and workspace adversarial tests into `tests/test_adversarial.py`.
- [x] Implement unit tests for `conversation_tracker.py` testing cosine similarity, timeout purging, and threshold clustering.
- [x] Author comprehensive project memory in `docs/MEMORY.md`.
- [x] Author long-term architecture and implementation plan in `docs/LONG_TERM_PLAN.md`.
- [x] Update `CHANGELOG.md` in Inverted Log format documenting all migrations and removals.

### Future Enhancements (Post-M5 Roadmap)
- **Persistent Conversation Memory**: Migrate `_active_conversations` in `conversation_tracker.py` from in-memory dictionary to SQLite / PostgreSQL or a vector database (pgvector / Chroma) to survive application restarts.
- **Multi-Channel & Multi-Workspace Routing**: Expand Slack bot configuration to dynamically monitor multiple channels with channel-specific Sheets and Calendar routing.
- **Bidirectional Event Sync**: Implement webhooks or polling to sync changes made directly in Google Calendar back into Slack status or announcements.
- **Enterprise ERP MCP Connectors**: Implement pluggable MCP tool connectors for enterprise ERP backends (Odoo, SAP S/4HANA, NetSuite) fulfilling the same record schema contract.
- **Interactive Slack Modals & Block Kit UI**: Add interactive Slack modal dialogs for staff to confirm, edit, or reclassify AI-extracted records prior to final sheet insertion.

---

## 4. Schema Contracts & Interface Specifications

### 4.1 FastMCP Server Tool Contract
- `sheets_ensure_tab(spreadsheet_id: str, sheet_name: str, headers: list[str]) -> dict[str, Any]`
  - Ensures tab exists; if newly created, row 1 is populated with `headers`.
  - Returns `{"status": "success", "spreadsheet_id": str, "sheet_name": str, "sheet_id": int, "created": bool, "headers_written": list[str]}`.
- `sheets_append_rows(spreadsheet_id: str, sheet_name: str, rows: list[list[Any]]) -> dict[str, Any]`
  - Appends 2D list of values. Empty list short-circuits safely.
  - Returns `{"status": "success", "spreadsheet_id": str, "sheet_name": str, "rows_appended": int, "updated_range": str}`.
- `sheets_get_records(spreadsheet_id: str, sheet_name: str) -> dict[str, Any]`
  - Reads tab rows and maps them to dictionaries keyed by row 1 headers.
  - Returns `{"status": "success", "spreadsheet_id": str, "sheet_name": str, "records": list[dict[str, Any]], "count": int}`.
- `calendar_create_event(calendar_id: str, summary: str, start_iso: str, end_iso: str, description: str = "", location: str = "") -> dict[str, Any]`
  - Validates ISO 8601 datetimes and inserts calendar event.
  - Returns `{"status": "success", "event_id": str, "html_link": str, "summary": str, "start": str, "end": str}`.
- `calendar_list_events(calendar_id: str, max_results: int = 10) -> dict[str, Any]`
  - Lists upcoming events ordered by start time.
  - Returns `{"status": "success", "calendar_id": str, "count": int, "events": list[dict[str, Any]]}`.

### 4.2 Google Sheets Record Schema (11 Columns)
```json
{
  "Message": "We need to finalize the quarterly audit with Maria by next Friday",
  "Category": "Research Project",
  "Conversation ID": "thread_1715000000.000100",
  "Conversation Topic": "Quarterly Audit",
  "Staff Member": "Cory Emil",
  "Subteam": "Outreach",
  "Date": "2026-09-11T20:15:00+00:00",
  "Notes": "Finalize quarterly audit",
  "Action Items": "Review audit draft; sync with Maria",
  "Deadline": "2026-09-18",
  "Link": "https://slack.com/archives/C01234567/p1715000000000100"
}
```

### 4.3 AI Extraction Schema (`ai_extractor.py`)
```json
{
  "category": "Trading Project",
  "notes": "Execute hedge rebalance",
  "action_items": ["Verify risk limits", "Submit order batch"],
  "deadline": "2026-09-15"
}
```

---

## 5. Technology Stack

- **Language Runtime**: Python 3.11+
- **Package & Virtualenv Manager**: Astral `uv`
- **Protocol Layer**: Model Context Protocol (`mcp>=1.2.0`, `FastMCP` / `MCPServer`)
- **Chat Platform**: Slack Socket Mode (`slack-bolt>=1.20.0`, `slack-sdk>=3.30.0`)
- **Intelligence Engine**: Google GenAI SDK (`google-genai>=0.2.0`, Gemini 2.5/3.7 Flash)
- **Workspace APIs**: Google Sheets API v4, Google Calendar API v3 (`google-api-python-client>=2.140.0`, `google-auth>=2.30.0`)
- **Web & Dashboard**: FastAPI (`fastapi>=0.111.0`, `uvicorn>=0.30.0`, `jinja2>=3.1.0`)
- **Testing Framework**: `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`
