# Changelog

## Current Focus
Milestone M8: Local Timezone Alignment & Conversational Event Pruning (Completed)

## Feature & Architecture Log

### 2026-09-12 (Milestone M8: Local Timezone Alignment & Conversational Event Pruning)
+++ Added: Timezone localization to `America/New_York` across `config.py`, `calendar_service.py` (`_normalize_date_range`), and `ai_extractor.py`, resolving the UTC offset shift where 7:00 PM EDT events were incorrectly scheduled at 3:00 PM.
+++ Added: FastMCP `calendar_delete_event` tool in `src/mcp_server/server.py` and `GoogleWorkspaceService.delete_event` in `src/services/google_services.py` with graceful 404/410 handling.
+++ Added: Automated incomplete and superseded event pruning in `calendar_service.py` (`prune_superseded_events`) and `slack_bot.py` (`_active_events` tracking and deletion upon follow-up message enrichment like adding a venue).
+++ Added: Google Calendar event ID extraction helper `extract_event_id_from_link` in `calendar_service.py` decoding base64 `eid` parameters.
+++ Added: Comprehensive unit tests in `tests/test_calendar_service.py`, `tests/test_mcp_server.py`, and `tests/test_slack_bot.py`, expanding the test suite to 322 passing tests.

### 2026-09-12 (Milestone M7: 24h Context Ingestion & Event Link Backpropagation)
+++ Added: Preceding 24-hour channel message history context retrieval in `slack_bot.py` (`_get_recent_channel_context`), allowing unthreaded conversational continuity (e.g. meal rescheduling) without forcing users into Slack threads.
+++ Added: Extended Gemini extraction in `ai_extractor.py` to ingest `context_messages`, resolving relative dates/times against UTC timestamps into ISO 8601 datetimes (`YYYY-MM-DDTHH:MM:SS`) and extracting meeting locations.
+++ Added: Calendar event link backpropagation: upon event creation, `slack_bot.py` calls `sheets_service.update_record_link` using `sheets_update_range` MCP tool to update Column K with the direct Google Calendar URL (`https://www.google.com/calendar/event?eid=...`).
+++ Added: Dashboard UI badge rendering for Google Calendar links displaying `Open Event 📅` with `.link-event` styling, distinguishing them from Slack message permalinks.
+++ Added: Orphan process sweep in `stop.sh` terminating untracked background Python instances (`main.py --listen-slack`, `uvicorn`).
+++ Added: `location` parameter support in `calendar_service.py` and FastMCP `calendar_create_event` tool.

### 2026-09-12 (Milestone M6: Tab Isolation & Dashboard Upgrade)
+++ Added: Dedicated `ChatRecords` worksheet tab created via one-off migration script `scripts/migrate_to_chat_records.py` with 11 canonical headers, preserving legacy `Sheet1` intact and migrating historical rows with aligned columns.
+++ Added: Configured `GOOGLE_SHEET_TAB_NAME=ChatRecords` across `config.py`, `sheets_service.py`, `.env`, and `.env.example`.
+++ Added: Real-time Slack user name resolution in `slack_bot.py` via Slack API `users.info` utilizing active `users:read` OAuth scope (resolving `U0C0YD1F8TG` to `Eland Chan`), with fallback user ID subteam resolution in `config.get_subteam`.
+++ Added: Upgraded FastAPI Dashboard template (`dashboard/templates/index.html`) with 9 visible columns including Conversation Topic, Category pills, tabular date filter (`format_date`), and strict URL scheme validation (`http://`, `https://`, `slack://`) ensuring broken links are never rendered for non-URL values.
+++ Added: Docker offline resilience in `run.sh` enabling clean startup with in-memory vector tracking fallback when Docker is inactive.
--- Removed: Hardcoded fallback to `Sheet1` in `sheets_service.get_sheet_tab_name()`. Reason for removal: Pointed default worksheet tab to `ChatRecords` to prevent column shifting against legacy 7-column header schemas.

### 2026-09-12
+++ Added: Standalone Qdrant Vector Database via Docker Compose (`docker-compose.yml`) exposing REST API (6333) and native visual dashboard (`http://localhost:6333/dashboard`) with persistent storage mapped to `./data/qdrant_storage`.
+++ Added: Vector persistence engine in `conversation_tracker.py` using `qdrant-client` to index conversation embeddings with cosine similarity and sliding time window filters, featuring resilient automatic fallback to in-memory dictionary if Qdrant is unavailable.
+++ Added: Operational lifecycle orchestration scripts `run.sh` and `stop.sh` with PID management in `.run/`, container healthcheck polling, background process launching, graceful SIGTERM/SIGKILL signal handling, and clean shutdown.
+++ Added: Comprehensive vector tracker test suite in `tests/test_qdrant_tracker.py` covering collection creation, vector similarity threshold matching, time-based purging, and fault injection fallback, bringing the test suite to 315 passing tests.

### 2026-09-11
+++ Added: Comprehensive project memory in `docs/MEMORY.md` capturing core architecture, FastMCP dual transport (`inproc` and `stdio`), Slack Socket Mode, service decoupling, and critical engineering lessons learned (git index untracking vs disk deletion, strict stderr logging for MCP JSON-RPC, token prefix validation, timezone awareness harmonization, empty row append protection, and thread pool sync execution).
+++ Added: Long-term architectural roadmap and system specifications in `docs/LONG_TERM_PLAN.md` detailing system vision, ASCII component architecture, component ownership, completed milestones (M1–M5), future enhancement tracks, and strict schema contracts.
+++ Added: Test suite porting and expansion achieving 100% pass rate across 294 tests (`tests/test_live_mcp.py` for live Google Sheets/Calendar API connectivity, 15 merged FastMCP/workspace adversarial tests in `tests/test_adversarial.py`, and `tests/test_conversation_tracker.py` unit tests for cosine similarity, timeout purging, and threshold clustering).
+++ Added: Interactive and automated CLI operational commands in `main.py` supporting `--test-slack` (authentication and bot identity verification), `--listen-slack` (live Socket Mode message streaming), and `--test-mcp` (4-stage verification of tool discovery, tab creation, row appending, and calendar event scheduling across `inproc` and `stdio` transports).
+++ Added: Dashboard UI continuity in `dashboard/app.py` and `dashboard/templates/index.html` backed by MCP `sheets_service.get_all_records()`, featuring newest-first reverse chronological ordering, legacy column normalization, and full Jinja2 XSS escaping.
+++ Added: Slack Socket Mode ingestion engine in `slack_bot.py` utilizing `slack-bolt` and `SocketModeHandler` for bidirectional streaming over WebSockets without public ingress tunnels or webhooks.
+++ Added: 7-step user message ingestion pipeline in `slack_bot.py`: Gemini structured extraction (`ai_extractor`), conversation session grouping (`conversation_tracker`), subteam resolution, message permalink generation, 11-column row appending via FastMCP `sheets_append_rows`, calendar event creation via FastMCP `calendar_create_event`, and receipt emoji reaction (`👀`).
+++ Added: Natural language summary query handler in `slack_bot.py` and `time_utils.py` supporting `!summarize [range]` and `@bot summarize [range]` triggers, temporal range parsing, MCP record retrieval, and Gemini summary posting.
+++ Added: FastMCP server implementation in `src/mcp_server/server.py` using `mcp.server.mcpserver.MCPServer`, registering 5 workspace tools (`sheets_ensure_tab`, `sheets_append_rows`, `sheets_get_records`, `calendar_create_event`, `calendar_list_events`) with strict `sys.stderr` log routing.
+++ Added: FastMCP client helper in `src/mcp_client/client.py` supporting both in-process (`inproc`) and subprocess (`stdio`) transports, equipped with a thread-pool synchronous bridge (`call_mcp_tool_sync`) to prevent event loop collisions.
+++ Added: Decoupled service layer in `sheets_service.py` and `calendar_service.py` delegating all operations through MCP tool calls with zero direct `gspread` or `googleapiclient` imports.
+++ Added: Google Workspace low-level client `GoogleWorkspaceService` in `src/services/google_services.py` supporting Sheets API v4 and Calendar API v3 with service account authentication.
+++ Added: Project packaging configuration with Astral `uv` in `pyproject.toml` and synchronized `requirements.txt`, exposing CLI console scripts `main` and `agent-backend`.
+++ Added: Git security configuration in `.gitignore` excluding `.env`, `.venv`, `.agents/`, test caches, and service account key files (`gen-lang-client-*.json`).
--- Removed: `discord_bot.py`. Reason for removal: Decommissioned Discord ingestion pipeline and removed `discord.py` dependency to migrate organizational chat ingestion to Slack Socket Mode (`slack-bolt`), eliminating external webhook exposure and unifying workspace communication.
--- Removed: Direct `gspread` dependency from project requirements. Reason for removal: Replaced direct spreadsheet API manipulation with decoupled FastMCP JSON-RPC tools (`sheets_ensure_tab`, `sheets_append_rows`, `sheets_get_records`) to isolate Google Workspace logic and support pluggable downstream ERP backends.
--- Removed: Unstaged `.DS_Store` and `.env` files from git repository tracking. Reason for removal: Enforced strict credential isolation and OS metadata exclusion to prevent inadvertent secret leakage.
