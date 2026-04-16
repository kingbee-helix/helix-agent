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

# ── Preflight checks ──────────────────────────────────────────────────────────

# Venv check
if [ ! -f "$HELIX_DIR/.venv/bin/python" ]; then
    echo "ERROR: Virtual environment not found."
    echo "Fix: Run the setup wizard first: python3 main.py setup"
    exit 1
fi

PYTHON="$HELIX_DIR/.venv/bin/python"

# Python version check (3.10+)
PYTHON_INFO=$("$PYTHON" -c "import sys; print(sys.version_info.major, sys.version_info.minor)" 2>/dev/null)
PYTHON_MAJOR=$(echo "$PYTHON_INFO" | cut -d' ' -f1)
PYTHON_MINOR=$(echo "$PYTHON_INFO" | cut -d' ' -f2)

if [ -z "$PYTHON_MAJOR" ]; then
    echo "ERROR: Could not determine Python version."
    echo "Fix: Recreate the venv: python3 main.py setup"
    exit 1
fi

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    echo "ERROR: Python $PYTHON_MAJOR.$PYTHON_MINOR is too old. Helix requires Python 3.10+."
    echo "Fix: Install Python 3.10+ from https://python.org then re-run: python3 main.py setup"
    exit 1
fi

# Claude Code CLI check
if ! command -v claude &>/dev/null; then
    echo "ERROR: Claude Code CLI not found."
    echo "Fix: Install Claude Code from https://docs.anthropic.com/claude-code"
    exit 1
fi

# Dependencies check
DEP_ISSUES=$("$PYTHON" -W ignore -c "
import pkg_resources, sys
issues = []
try:
    with open('requirements.txt') as f:
        reqs = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    for req in reqs:
        try:
            pkg_resources.require(req)
        except pkg_resources.DistributionNotFound as e:
            issues.append('  Missing: ' + str(e.req))
        except pkg_resources.VersionConflict as e:
            issues.append('  Wrong version: ' + str(e.req) + ' (have ' + str(e.dist.version) + ')')
except Exception:
    pass
for issue in issues:
    print(issue)
" 2>/dev/null)

if [ -n "$DEP_ISSUES" ]; then
    echo "ERROR: Missing or outdated dependencies:"
    echo "$DEP_ISSUES"
    echo ""
    echo "Fix: source .venv/bin/activate && pip install --upgrade -r requirements.txt"
    exit 1
fi

# ── Launch ──────────────────────────────────────────────────────────────────────
nohup env PYTHONUNBUFFERED=1 "$PYTHON" "$HELIX_DIR/main.py" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Helix started (PID $(cat $PID_FILE))"
