#!/bin/sh
# ==============================================================================
# run.sh - Agent Backend Startup & Process Orchestrator
#
# Robust, POSIX-compliant service launcher for agent-backend:
# 1. Checks Docker daemon is operational.
# 2. Starts Qdrant vector database via docker compose up -d.
# 3. Polls http://localhost:6333/healthz until Qdrant is healthy (30s timeout).
# 4. Ensures .run/ directory exists.
# 5. Starts FastAPI Dashboard in background (uv run uvicorn dashboard.app:app --port 8000),
#    storing PID in .run/dashboard.pid.
# 6. Checks .env for SLACK_BOT_TOKEN and SLACK_APP_TOKEN; if present, starts Slack Bot
#    in background (uv run python main.py --listen-slack), storing PID in .run/slack_bot.pid.
# 7. Displays active process IDs and clickable service URLs.
# 8. Supports background run mode (default or -b/--background) or foreground wait mode (-f/--foreground).
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$SCRIPT_DIR/.run"
cd "$SCRIPT_DIR"

# Parse CLI options
MODE="background"
for arg in "$@"; do
  case "$arg" in
    -f|--foreground|foreground|-w|--wait|wait)
      MODE="foreground"
      ;;
    -b|--background|background|-d|--detach|detach)
      MODE="background"
      ;;
    -h|--help|help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Starts agent-backend infrastructure: Qdrant, Dashboard, and Slack Bot."
      echo ""
      echo "Options:"
      echo "  -b, --background   Start services in background and exit (default)"
      echo "  -f, --foreground   Start services and wait in foreground (Ctrl+C stops all services)"
      echo "  -h, --help         Show this help message and exit"
      exit 0
      ;;
    *)
      echo "Error: Unknown argument '$arg'" >&2
      echo "Usage: $0 [-b|--background] [-f|--foreground] [-h|--help]" >&2
      exit 1
      ;;
  esac
done

echo "=================================================================="
echo "           🚀 Starting Agent Backend Infrastructure               "
echo "=================================================================="

# 1. Check Docker is installed and running
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ [Error] Docker CLI is not installed or not in PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "❌ [Error] Docker daemon is not running. Please start Docker Desktop or the Docker daemon." >&2
  exit 1
fi
echo "✓ Docker daemon is active."

# Determine docker compose syntax
if docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
else
  echo "❌ [Error] Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

# 2. Start Qdrant via docker compose up -d
echo "Starting Qdrant vector database via '$DOCKER_COMPOSE up -d'..."
if ! $DOCKER_COMPOSE up -d; then
  echo "❌ [Error] Failed to launch Qdrant container with '$DOCKER_COMPOSE up -d'." >&2
  exit 1
fi

# 3. Poll http://localhost:6333/healthz until Qdrant is healthy (up to 30s timeout)
HEALTH_URL="http://localhost:6333/healthz"
TIMEOUT=30
ELAPSED=0
HEALTHY=0

echo "Polling Qdrant health check at $HEALTH_URL (timeout: ${TIMEOUT}s)..."
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
  if curl -s -f "$HEALTH_URL" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 1
  ELAPSED=$((ELAPSED + 1))
done

if [ "$HEALTHY" -ne 1 ]; then
  echo "❌ [Error] Qdrant did not become healthy within ${TIMEOUT} seconds." >&2
  exit 1
fi
echo "✓ Qdrant health check passed (${ELAPSED}s)."

# 4. Create .run/ directory if missing
mkdir -p "$RUN_DIR"

# Verify Astral uv is installed
if ! command -v uv >/dev/null 2>&1; then
  echo "❌ [Error] 'uv' command not found. Astral uv is required to run the services." >&2
  exit 1
fi

# 5. Start FastAPI Dashboard in background
DASHBOARD_PID_FILE="$RUN_DIR/dashboard.pid"
dashboard_pid=""

if [ -f "$DASHBOARD_PID_FILE" ]; then
  existing_pid="$(cat "$DASHBOARD_PID_FILE" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "FastAPI Dashboard is already running (PID: $existing_pid)."
    dashboard_pid="$existing_pid"
  else
    rm -f "$DASHBOARD_PID_FILE"
  fi
fi

if [ -z "$dashboard_pid" ]; then
  echo "Starting FastAPI Dashboard on port 8000..."
  uv run uvicorn dashboard.app:app --port 8000 > "$RUN_DIR/dashboard.log" 2>&1 &
  dashboard_pid=$!
  echo "$dashboard_pid" > "$DASHBOARD_PID_FILE"

  sleep 1
  if ! kill -0 "$dashboard_pid" 2>/dev/null; then
    echo "❌ [Error] FastAPI Dashboard failed to start. Logs:" >&2
    tail -n 20 "$RUN_DIR/dashboard.log" >&2
    exit 1
  fi
  echo "✓ FastAPI Dashboard started (PID: $dashboard_pid)."
fi

# 6. Check .env for Slack tokens and start Slack Bot if present
SLACK_PID_FILE="$RUN_DIR/slack_bot.pid"
slack_bot_pid=""
slack_bot_token=""
slack_app_token=""

if [ -f "$SCRIPT_DIR/.env" ]; then
  slack_bot_token=$(grep -E '^[[:space:]]*SLACK_BOT_TOKEN[[:space:]]*=' "$SCRIPT_DIR/.env" 2>/dev/null | head -n 1 | sed -E 's/^[[:space:]]*SLACK_BOT_TOKEN[[:space:]]*=[[:space:]]*//' | tr -d '\"'\''[:space:]')
  slack_app_token=$(grep -E '^[[:space:]]*SLACK_APP_TOKEN[[:space:]]*=' "$SCRIPT_DIR/.env" 2>/dev/null | head -n 1 | sed -E 's/^[[:space:]]*SLACK_APP_TOKEN[[:space:]]*=[[:space:]]*//' | tr -d '\"'\''[:space:]')
fi

# Fallback to exported environment variables if not found in .env
if [ -z "$slack_bot_token" ] && [ -n "${SLACK_BOT_TOKEN:-}" ]; then
  slack_bot_token="$SLACK_BOT_TOKEN"
fi
if [ -z "$slack_app_token" ] && [ -n "${SLACK_APP_TOKEN:-}" ]; then
  slack_app_token="$SLACK_APP_TOKEN"
fi

if [ -n "$slack_bot_token" ] && [ -n "$slack_app_token" ]; then
  if [ -f "$SLACK_PID_FILE" ]; then
    existing_slack_pid="$(cat "$SLACK_PID_FILE" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$existing_slack_pid" ] && kill -0 "$existing_slack_pid" 2>/dev/null; then
      echo "Slack Bot listener is already running (PID: $existing_slack_pid)."
      slack_bot_pid="$existing_slack_pid"
    else
      rm -f "$SLACK_PID_FILE"
    fi
  fi

  if [ -z "$slack_bot_pid" ]; then
    echo "Starting Slack Bot listener..."
    uv run python main.py --listen-slack > "$RUN_DIR/slack_bot.log" 2>&1 &
    slack_bot_pid=$!
    echo "$slack_bot_pid" > "$SLACK_PID_FILE"

    sleep 1
    if ! kill -0 "$slack_bot_pid" 2>/dev/null; then
      echo "❌ [Error] Slack Bot listener failed to start. Logs:" >&2
      tail -n 20 "$RUN_DIR/slack_bot.log" >&2
      exit 1
    fi
    echo "✓ Slack Bot listener started (PID: $slack_bot_pid)."
  fi
else
  echo "Notice: SLACK_BOT_TOKEN and/or SLACK_APP_TOKEN not configured in .env; skipping Slack Bot."
fi

# 7. Print active PIDs and clickable URLs
echo ""
echo "=================================================================="
echo "                  Active Services Summary                         "
echo "=================================================================="
echo "  Processes:"
echo "    • FastAPI Dashboard PID : $dashboard_pid"
if [ -n "$slack_bot_pid" ]; then
  echo "    • Slack Bot Listener PID: $slack_bot_pid"
else
  echo "    • Slack Bot Listener PID: (inactive - tokens missing in .env)"
fi
echo ""
echo "  Clickable Service Endpoints:"
echo "    • FastAPI Dashboard UI  : http://localhost:8000"
echo "    • Qdrant Web Dashboard  : http://localhost:6333/dashboard"
echo "    • Qdrant Health Check   : http://localhost:6333/healthz"
echo "=================================================================="
echo ""

# 8. Foreground wait or background run mode
if [ "$MODE" = "foreground" ]; then
  echo "Running in foreground mode (Press Ctrl+C to stop all services)..."

  cleanup() {
    echo ""
    echo "Shutdown signal received. Stopping all services..."
    "$SCRIPT_DIR/stop.sh"
    exit 0
  }
  trap cleanup INT TERM HUP

  while true; do
    if [ -n "$dashboard_pid" ] && ! kill -0 "$dashboard_pid" 2>/dev/null; then
      echo "⚠️ [Warning] FastAPI Dashboard process (PID: $dashboard_pid) terminated unexpectedly."
      "$SCRIPT_DIR/stop.sh"
      exit 1
    fi
    if [ -n "$slack_bot_pid" ] && ! kill -0 "$slack_bot_pid" 2>/dev/null; then
      echo "⚠️ [Warning] Slack Bot process (PID: $slack_bot_pid) terminated unexpectedly."
      "$SCRIPT_DIR/stop.sh"
      exit 1
    fi
    sleep 2
  done
else
  echo "✓ Services are running in background mode."
  echo "  Execute './stop.sh' to cleanly stop all processes and containers."
fi
