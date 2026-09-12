#!/bin/sh
# ==============================================================================
# stop.sh - Agent Backend Shutdown & Process Cleaner
#
# Robust, POSIX-compliant shutdown script for agent-backend:
# 1. Inspects .run/*.pid files.
# 2. For each PID, sends SIGTERM, polls for up to 5 seconds, sends SIGKILL
#    if the process is still running, and deletes the PID file.
# 3. Invokes docker compose stop to cleanly pause the Qdrant container.
# 4. Reports cleanly when all processes and containers are stopped.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$SCRIPT_DIR/.run"
cd "$SCRIPT_DIR"

echo "=================================================================="
echo "           🛑 Stopping Agent Backend Infrastructure               "
echo "=================================================================="

# 1. Stop background processes tracked in .run/*.pid
pid_files_found=0

if [ -d "$RUN_DIR" ]; then
  for pid_file in "$RUN_DIR"/*.pid; do
    # POSIX sh retains pattern if no files match
    if [ ! -f "$pid_file" ]; then
      continue
    fi

    pid_files_found=$((pid_files_found + 1))
    service_name="$(basename "$pid_file" .pid)"
    pid="$(cat "$pid_file" 2>/dev/null | tr -d '[:space:]')"

    case "$pid" in
      ''|*[!0-9]*)
        echo "⚠️  [Warning] Invalid or non-numeric PID in $pid_file ('$pid'). Removing PID file."
        rm -f "$pid_file"
        continue
        ;;
    esac

    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $service_name (PID: $pid) with SIGTERM..."
      kill -TERM "$pid" 2>/dev/null || true

      # Propagate SIGTERM to child processes if any
      if command -v pgrep >/dev/null 2>&1; then
        child_pids=$(pgrep -P "$pid" 2>/dev/null || true)
        for cpid in $child_pids; do
          kill -TERM "$cpid" 2>/dev/null || true
        done
      fi

      # Wait up to 5 seconds for clean exit
      waited=0
      while [ "$waited" -lt 5 ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        sleep 1
        waited=$((waited + 1))
      done

      # Send SIGKILL if process is still active after 5s
      if kill -0 "$pid" 2>/dev/null; then
        echo "Process $service_name (PID: $pid) still active after 5s; sending SIGKILL..."
        kill -KILL "$pid" 2>/dev/null || true

        if command -v pgrep >/dev/null 2>&1; then
          child_pids=$(pgrep -P "$pid" 2>/dev/null || true)
          for cpid in $child_pids; do
            kill -KILL "$cpid" 2>/dev/null || true
          done
        fi
        sleep 1
      fi

      if kill -0 "$pid" 2>/dev/null; then
        echo "❌ [Error] Could not terminate $service_name (PID: $pid)." >&2
      else
        echo "✓ Successfully stopped $service_name (PID: $pid)."
      fi
    else
      echo "Process $service_name (PID: $pid) was not running."
    fi

    # Remove the PID file
    rm -f "$pid_file"
  done
fi

if [ "$pid_files_found" -eq 0 ]; then
  echo "No active PID files found in $RUN_DIR."
fi

# 2. Stop Qdrant container cleanly via docker compose stop
if docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
else
  DOCKER_COMPOSE=""
fi

if [ -n "$DOCKER_COMPOSE" ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "Pausing Qdrant container via '$DOCKER_COMPOSE stop'..."
  if $DOCKER_COMPOSE stop; then
    echo "✓ Qdrant container stopped cleanly."
  else
    echo "⚠️  [Warning] Failed to stop Qdrant container cleanly." >&2
  fi
else
  echo "Notice: Docker is not active or compose is unavailable; skipping container stop."
fi

echo "=================================================================="
echo "✓ All processes and containers have been stopped."
echo "=================================================================="
