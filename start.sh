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

# Check critical dependency versions before launching
WS_VERSION=$("$PYTHON" -c "import websockets; print(websockets.__version__)" 2>/dev/null)
if [ -z "$WS_VERSION" ]; then
    echo "ERROR: websockets not installed. Run: pip install -r requirements.txt inside the venv."
    exit 1
fi
WS_MAJOR=$(echo "$WS_VERSION" | cut -d. -f1)
if [ "$WS_MAJOR" -lt 16 ]; then
    echo "ERROR: websockets $WS_VERSION is too old (need >=16.0)."
    echo "Fix: cd helix-agent && source .venv/bin/activate && pip install --upgrade websockets"
    exit 1
fi

nohup env PYTHONUNBUFFERED=1 "$PYTHON" "$HELIX_DIR/main.py" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Helix started (PID $(cat $PID_FILE))"
