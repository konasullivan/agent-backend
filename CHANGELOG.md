# Changelog

## Current Focus
Milestone M5: Final Test Pass, Hardening & Documentation (Completed)

## Feature & Architecture Log

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
