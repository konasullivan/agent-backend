# Project Memory & Learned Context: Agent Backend

## Core Architecture & Technical Choices
- **Decoupled Model Context Protocol (MCP) Layer**:
  - The backend decouples all external workspace writes and reads through the Model Context Protocol (`mcp`).
  - Google Workspace operations (Google Sheets API v4 and Google Calendar API v3) are encapsulated behind an MCP server (`src/mcp_server/server.py`).
  - Application services (`sheets_service.py`, `calendar_service.py`) and the web dashboard (`dashboard/app.py`) interact with Google Workspace exclusively via MCP tool calls (`sheets_ensure_tab`, `sheets_append_rows`, `sheets_get_records`, `calendar_create_event`, `calendar_list_events`), completely eliminating direct dependencies on legacy libraries like `gspread`.
  - This architecture prevents vendor lock-in and isolates external API changes from business logic.

- **FastMCP Server & Logging Hygiene**:
  - The server utilizes `mcp.server.mcpserver.MCPServer` (compatible with `mcp>=1.2.0` and `mcp==2.2.0`).
  - **CRITICAL**: In stdio transport mode, stdout is strictly reserved for JSON-RPC framing (e.g. `Content-Length: ...`). Any raw text printed to stdout corrupts JSON-RPC packet framing and crashes the client. All logging handlers in `src/mcp_server/server.py` and `slack_bot.py` are explicitly forced to stream to `sys.stderr`.

- **Dual-Transport MCP Client Bridge**:
  - The MCP client (`src/mcp_client/client.py`) supports two transport modes:
    1. `inproc` (default): Connects directly in-memory to the `mcp_server` object via `Client(mcp_server)`. Zero process overhead, ideal for fast internal unit tests and direct application execution.
    2. `stdio`: Spawns a dedicated Python subprocess executing `python -m src.mcp_server.server` via `StdioServerParameters`. Mirrors production cross-service IPC.
  - **Synchronous Execution Bridge (`call_mcp_tool_sync`)**:
    - Designed to enable synchronous callers (FastAPI endpoints, `sheets_service.py`, `calendar_service.py`) to invoke async MCP client tools without event loop collisions.
    - If called when an event loop is already running (e.g., inside `pytest-asyncio` or async web framework tasks), it submits execution to a dedicated background `ThreadPoolExecutor` worker thread (`mcp-sync-bridge`) using `asyncio.run()`, completely avoiding `RuntimeError: This event loop is already running`.
    - If no loop is active, it safely invokes `asyncio.run()` directly.

- **Slack Socket Mode Ingestion**:
  - Implemented using `slack-bolt` (`SocketModeHandler`).
  - Connects out to Slack over a secure WebSocket using `SLACK_APP_TOKEN` (`xapp-...`), eliminating the need for public webhooks, open ingress ports, or reverse proxy tunnels (ngrok/Cloudflare).
  - Listens to channel messages, filters bot noise and subtypes, applies Gemini structured extraction, groups conversation sessions, maps subteams, generates permalinks, appends rows to Google Sheets via MCP, schedules deadlines to Google Calendar via MCP, and adds a `👀` emoji reaction.
  - Supports on-demand natural language summary requests triggered by `!summarize [range]` or `@bot summarize [range]`.

- **Package & Dependency Management**:
  - Managed via Astral `uv` (`pyproject.toml`, `uv.lock`).
  - Exposes console entrypoints `main` and `agent-backend` mapping to `main:cli`.
  - Maintains `requirements.txt` strictly in sync with `pyproject.toml` for partner and container deployment compatibility.

- **Qdrant Vector Database & Semantic Persistence**:
  - Standalone Qdrant container managed via `docker-compose.yml` (`qdrant/qdrant:latest`).
  - Stores conversation topic embeddings with cosine similarity distance metric in collection `conversations`.
  - Native Web UI available out of the box at `http://localhost:6333/dashboard` for live vector inspection.
  - Resilient design: `conversation_tracker.py` connects with timeout protection and automatically falls back to in-memory cosine clustering if Qdrant or Docker is offline.

- **Process Lifecycle & PID Management (`run.sh` & `stop.sh`)**:
  - `run.sh` launches Docker Qdrant, polls `http://localhost:6333/healthz` until healthy, and starts background FastAPI dashboard and Slack bot processes.
  - Active process IDs are written to `.run/dashboard.pid` and `.run/slack_bot.pid` (`.run/` is gitignored).
  - `stop.sh` reads active PIDs, sends `SIGTERM` (with 5-second `SIGKILL` escalation), purges PID files, and cleanly pauses the Qdrant container with `docker compose stop`.

---

## Target Resources & Configuration Reference
- **GCP Authentication**:
  - Google Cloud Service Account loaded via `gen-lang-client-0552805033-e228d6204d68.json` (configured via `GOOGLE_SERVICE_ACCOUNT_FILE`).
  - Scopes:
    - `https://www.googleapis.com/auth/spreadsheets`
    - `https://www.googleapis.com/auth/calendar`
- **Target Resources**:
  - Google Spreadsheet ID: `1cr6lVb9l3IhmEN8s8baIyuUxYw-mqLa-gxVptTS0cBE`
  - Target Calendar ID: `amzm.mana@gmail.com`
  - Verification Sheet Tab: `TestRecords`
  - Production Interaction Log Tab: `Sheet1` (or configured `GOOGLE_SHEET_TAB_NAME`)
- **Slack Tokens**:
  - `SLACK_BOT_TOKEN`: Bot User OAuth Token starting with `xoxb-`.
  - `SLACK_APP_TOKEN`: App-Level Token starting with `xapp-` (with `connections:write` scope).
  - `SLACK_CHANNEL_ID`: Channel ID monitored for live message ingestion.
- **Gemini API Configuration**:
  - `GEMINI_API_KEY`: Google GenAI API key for structured extraction (`gemini-2.5-flash` / `gemini-3.7-flash`) and semantic clustering (`gemini-embedding-001`).

---

## Architectural Patterns & Service Contracts
- **Google Sheets 11-Column Schema**:
  1. `Message`: Raw chat text.
  2. `Category`: High-level topic (e.g., Fundraiser, Trading Project, Research Project, Outreach, Customer Service, Miscellaneous).
  3. `Conversation ID`: `thread_<thread_ts>` for threaded replies, or semantic clustering hash `convo_<uuid>` for top-level messages.
  4. `Conversation Topic`: AI-generated 2–5 word topic label.
  5. `Staff Member`: Resolved real name or display name of the message author.
  6. `Subteam`: Department mapping resolved from `config.get_subteam(author_name)`.
  7. `Date`: ISO 8601 UTC timestamp string (`YYYY-MM-DDTHH:MM:SS+00:00`).
  8. `Notes`: Concise summary/note (under 8 words).
  9. `Action Items`: Semicolon-delimited concrete follow-up tasks.
  10. `Deadline`: ISO date (`YYYY-MM-DD`) if a deadline exists, otherwise empty.
  11. `Link`: Slack permalink (`chat_getPermalink` or synthetic archive fallback).

- **Conversation Grouping Strategy**:
  - Threaded replies use Slack's explicit `thread_ts`.
  - Top-level channel messages are dynamically clustered using Gemini text embeddings (`gemini-embedding-001`) with a cosine similarity threshold of 0.72 and an inactivity timeout window of 4 hours.

- **Dashboard Continuity & Null Safety**:
  - FastAPI server (`dashboard/app.py`) serves `/` with Jinja2 templates and `/api/records`.
  - Retrieves records via `sheets_service.get_all_records()` which dispatches `sheets_get_records` over MCP.
  - Automatically handles legacy column mappings (e.g. mapping legacy column 'A' to 'Message') and pre-populates missing keys with default empty strings to guarantee zero runtime rendering crashes.

---

## Learned Lessons & Critical Engineering Pitfalls
1. **Git Tracking vs. Disk Deletion (`git rm` vs `rm`)**:
   - Simply deleting a file from disk with `rm` leaves it as an unstaged deletion (` D filename`).
   - For repository decommissions (such as retiring `discord_bot.py`), the file must be removed from the git index (`git rm discord_bot.py` or staging the deletion). Otherwise, git continues tracking the file in the index, leading to ghost files in checkouts or test audit failures.
   - Always verify deletion from the index using `git ls-files --stage <filename>`.

2. **Strict `sys.stderr` Logging for MCP JSON-RPC**:
   - In standard Python logging, `logging.basicConfig()` defaults to `sys.stderr`, but third-party libraries or accidental `print()` statements frequently write to `sys.stdout`.
   - When running an MCP server over `stdio`, any extraneous byte on `stdout` breaks JSON-RPC header framing and causes immediate connection dropouts.
   - Always configure logging with `stream=sys.stderr` and `force=True`, and ensure CLI runners redirect diagnostics to `sys.stderr`.

3. **Slack Token Validation & Pre-Flight Verification**:
   - Bot token (`SLACK_BOT_TOKEN`) must have prefix `xoxb-`.
   - App token (`SLACK_APP_TOKEN`) must have prefix `xapp-`.
   - Failing to validate token prefixes early leads to confusing runtime handshake errors inside WebSocket threads. Pre-flight verification with clear error messages in `main.py --test-slack` guarantees rapid diagnostic feedback.

4. **Timezone Awareness Harmonization**:
   - When filtering records by time range (`sheets_service.get_records_in_range`), comparing timezone-aware datetimes with timezone-naive datetimes raises `TypeError: can't compare offset-naive and offset-aware datetimes`.
   - Always inspect `tzinfo` and harmonize awareness (e.g., converting naive timestamps to UTC) before applying range comparisons (`start <= dt <= end`).

5. **Empty Row Append Guard in Google Sheets API**:
   - Submitting an empty list of rows (`rows=[]`) to Google Sheets `spreadsheets().values().append()` results in an immediate HTTP 400 Bad Request error from Google APIs.
   - The service layer (`GoogleWorkspaceService.append_rows`) must short-circuit when `len(rows) == 0` and return `{status: "success", rows_appended: 0}`.

6. **Event Loop Collision Avoidance in FastMCP Client**:
   - Using naive `asyncio.run()` within synchronous functions that get called from inside an already running event loop (e.g., during `pytest-asyncio` test suites or async ASGI middleware) raises `RuntimeError: asyncio.run() cannot be called from a running event loop`.
   - Using a dedicated worker thread pool (`ThreadPoolExecutor`) to bridge synchronous calls into an event loop allows safe calling from both synchronous and asynchronous contexts.

7. **Null Safety & Row Normalization in Dashboard**:
   - When external Google Sheets contain empty cells or missing header columns, naive access raises `KeyError` or causes `TypeError` in templating engines.
   - Normalizing row dictionaries with default empty strings (`record.get(header, "")`) ensures robust table rendering across any ragged sheet structure.

---

## Security & Guardrails
- **Credential Protection**:
  - `gen-lang-client-*.json` and `.env` are strictly excluded in `.gitignore`.
  - Never commit service account private keys or Slack app tokens to version control.
- **Least Privilege Access**:
  - Google Service Account is granted access strictly to the target spreadsheet and calendar ID.
  - Slack bot scopes are constrained to `chat:write`, `channels:history`, `reactions:write`, `users:read`.
