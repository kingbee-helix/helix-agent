#!/usr/bin/env bash
# Helix stop script

PID_FILE="$HOME/.helix/helix.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        # Wait for process to fully exit and release ports
        for i in $(seq 1 10); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
        done
        rm -f "$PID_FILE"
        echo "Helix stopped (PID $PID)"
    else
        rm -f "$PID_FILE"
        echo "Helix was not running (stale PID file cleaned up)"
    fi
else
    echo "No PID file found — Helix may not be running"
fi
