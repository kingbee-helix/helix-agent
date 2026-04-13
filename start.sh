#!/usr/bin/env bash
# Helix start script — runs in background, logs to ~/.helix/logs/helix.log

HELIX_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$HOME/.helix/logs/helix.log"
PID_FILE="$HOME/.helix/helix.pid"

mkdir -p "$HOME/.helix/logs"

# Don't start if already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Helix already running (PID $PID)"
        exit 0
    fi
fi

cd "$HELIX_DIR"

# Use venv python if available, fall back to system python3 (which will auto-create venv)
if [ -f "$HELIX_DIR/.venv/bin/python" ]; then
    PYTHON="$HELIX_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

nohup env PYTHONUNBUFFERED=1 "$PYTHON" "$HELIX_DIR/main.py" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Helix started (PID $(cat $PID_FILE))"
