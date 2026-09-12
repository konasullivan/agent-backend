# AI Interaction Agent & Workspace Automation

An autonomous interaction intelligence backend that ingests group chat messages from **Slack** via Socket Mode, extracts structured business records with **Gemini**, clusters topics using a **Qdrant Vector Database**, and writes downstream updates to **Google Sheets** and **Google Calendar** through a decoupled **Model Context Protocol (MCP)** layer.

---

## System Architecture

```
                               ┌────────────────────────────────┐
                               │       Slack Workspace          │
                               │  (Socket Mode / WebSocket)     │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │   Slack Bot Ingestion Engine   │
                               │        (slack_bot.py)          │
                               └───────┬───────────────┬────────┘
                                       │               │
                     Structured Record │               │ Embeddings & Topics
                     Extraction        ▼               ▼
                       ┌──────────────────┐  ┌──────────────────┐
                       │   Google GenAI   │  │ Qdrant Vector DB │
                       │ (Gemini 2.5/3.7) │  │  (Docker / 6333) │
                       └──────────────────┘  └──────────────────┘
                                       │
                                       ▼ MCP Protocol (JSON-RPC)
                       ┌────────────────────────────────┐
                       │        FastMCP Server          │
                       │   (src/mcp_server/server.py)   │
                       └───────┬────────────────┬───────┘
                               │                │
                               ▼                ▼
                     ┌──────────────────┐ ┌──────────────────┐
                     │  Google Sheets   │ │ Google Calendar  │
                     │  (Record Logs)   │ │ (Auto-Deadlines) │
                     └─────────┬────────┘ └──────────────────┘
                               │
                               ▼
                     ┌──────────────────┐
                     │ FastAPI Dashboard│
                     │ (localhost:8000) │
                     └──────────────────┘
```

---

## Directory Structure

```
agent-backend/
├── run.sh                    # One-command startup (Docker Qdrant + Dashboard + Slack Bot)
├── stop.sh                   # Graceful shutdown script (PID cleanup + container pause)
├── docker-compose.yml        # Standalone Qdrant Vector DB container
├── main.py                   # Unified CLI runner (--test-mcp, --test-slack, --listen-slack)
├── slack_bot.py              # Slack Socket Mode listener & message pipeline
├── conversation_tracker.py   # Semantic clustering via Qdrant (with in-memory fallback)
├── ai_extractor.py           # Gemini structured extraction & summarizer
├── sheets_service.py         # MCP-backed Google Sheets client (zero direct gspread)
├── calendar_service.py       # MCP-backed Google Calendar client
├── time_utils.py             # Natural-language time range parsing
├── config.py                 # Central configuration & subteam mapping
├── dashboard/                # FastAPI web dashboard
│   ├── app.py
│   └── templates/index.html
├── src/
│   ├── mcp_server/           # FastMCP server exposing Workspace tools
│   ├── mcp_client/           # Dual-transport (inproc / stdio) MCP client bridge
│   ├── services/             # GoogleWorkspaceService (Sheets API v4, Calendar v3)
│   ├── ingestion/            # Sessionizer & message stream abstractions
│   └── models/               # Pydantic data schemas
├── tests/                    # Comprehensive test suite (315 passing tests)
├── pyproject.toml            # Astral uv packaging configuration
├── requirements.txt          # Exported dependency lock for deployment
└── .env.example              # Environment variables template
```

---

## Behavior & Core Capabilities

1. **Every Message Logged to Google Sheets**:
   - Ingests user messages and extracts: `Message`, `Category` (AI-classified topic), `Conversation ID`, `Conversation Topic`, `Staff Member`, `Subteam` (from `config.STAFF_SUBTEAM`), `Date`, `Notes`, `Action Items`, `Deadline`, and `Link` (Slack jump-to-message permalink).
   - Writes are executed exclusively via the FastMCP tool `sheets_append_rows`.
2. **Persistent Semantic Topic Clustering**:
   - Embeds message content with Gemini embeddings (`gemini-embedding-001`).
   - Slack threads (`thread_ts`) are grouped automatically. Top-level chatter is matched to active threads via **Qdrant Vector DB** cosine similarity (`threshold = 0.72`, 4-hour window).
   - **Zero-Downtime Fallback**: If Qdrant/Docker is offline, clustering automatically falls back to an in-memory dictionary.
3. **Automated Calendar Deadlines**:
   - When Gemini identifies a deadline in any message, a Google Calendar event is silently scheduled via the FastMCP tool `calendar_create_event`.
4. **On-Demand Chat Summaries**:
   - The bot speaks in Slack only when requested using:
     - `!summarize` (defaults to today's summary + action items)
     - `!summarize yesterday` / `!summarize week` / `!summarize month`
     - Or by mentioning the bot: `@smart_summerizer summarize week`
   - Queries logged records via MCP `sheets_get_records`, formats a concise summary with action owners via Gemini, and replies directly in Slack.
5. **Visual Vector Console & Web Dashboard**:
   - Web Dashboard: `http://localhost:8000` (live table of logged business records).
   - Qdrant Vector Console: `http://localhost:6333/dashboard` (inspect collections, points, and vector payloads).

---

## Quickstart

### 1. Prerequisites
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (Astral Python package manager)
- **Docker** & **Docker Compose** (for Qdrant Vector DB)

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/konasullivan/agent-backend.git
cd agent-backend

# Install dependencies and sync virtual environment
uv sync

# Configure environment variables
cp .env.example .env
```

### 3. Configure `.env`
Fill in your credentials:
```ini
# Slack (Socket Mode)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_CHANNEL_ID=C0123456789

# Gemini API
GEMINI_API_KEY=AIzaSy...

# Google Workspace (Sheets + Calendar)
GOOGLE_SERVICE_ACCOUNT_FILE=gen-lang-client-0552805033-e228d6204d68.json
GOOGLE_SHEET_ID=1cr6lVb9l3IhmEN8s8baIyuUxYw-mqLa-gxVptTS0cBE
GOOGLE_CALENDAR_ID=amzm.mana@gmail.com
GOOGLE_SHEET_TAB_NAME=Sheet1

# Qdrant Vector DB (Optional overrides)
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION=conversations
```

---

## Running the Application

### One-Command Full Stack (Recommended)
Start Qdrant, the FastAPI Dashboard, and the Slack Bot concurrently:
```bash
./run.sh
```
*To run in the foreground with live logs (where `Ctrl+C` stops all services):*
```bash
./run.sh -f
```

### Stopping All Services
Stop background processes cleanly by PID and pause the Qdrant container:
```bash
./stop.sh
```

---

## Individual Component Commands

### Run Slack Bot Only
```bash
uv run python main.py --listen-slack
```

### Run Dashboard Only
```bash
uv run uvicorn dashboard.app:app --reload --port 8000
```

### Verify Slack API Authentication
```bash
uv run python main.py --test-slack
```

### Verify FastMCP & Google Workspace Connectivity
```bash
uv run python main.py --test-mcp
```

### Run Full Test Suite
```bash
uv run pytest
```
*(315 unit & integration tests passing with 100% pass rate).*

---

## Architecture Guarantees & Security

- **Strict Stdio MCP Isolation**: All MCP server diagnostics stream to `sys.stderr` to prevent JSON-RPC framing corruption on `sys.stdout`.
- **Credential Protection**: `.gitignore` explicitly blocks `.env`, service account JSON keys (`gen-lang-client-*.json`), virtual environments (`.venv/`), and runtime state (`.run/`, `data/`).
- **Zero Webhook Exposure**: Slack Socket Mode maintains an outbound-only WebSocket; no public IP, ngrok, or reverse proxy is required.
