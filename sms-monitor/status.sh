#!/data/data/com.termux/files/usr/bin/bash
# status.sh - Check SMS monitor status
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${SCRIPT_DIR}/.monitor.pid"
STATUS_FILE="${SCRIPT_DIR}/sms_status.json"
HEARTBEAT_FILE="${SCRIPT_DIR}/sms_heartbeat.txt"
LOG_FILE="${SCRIPT_DIR}/sms_monitor.log"
QUEUE_FILE="${SCRIPT_DIR}/sms_queue.json"
STATE_FILE="${SCRIPT_DIR}/sms_state.json"

echo "=========================================="
echo "  M-Pesa SMS Monitor - Status"
echo "=========================================="

# Process check
RUNNING=false
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        RUNNING=true
        echo "Process: RUNNING (PID $PID)"
    else
        echo "Process: DEAD (stale PID $PID)"
    fi
else
    # Try finding by name
    PID=$(pgrep -f "sms_monitor.py" 2>/dev/null | head -1 || true)
    if [ -n "$PID" ]; then
        RUNNING=true
        echo "Process: RUNNING (PID $PID, no PID file)"
    else
        echo "Process: NOT RUNNING"
    fi
fi

echo ""

# Status file
if [ -f "$STATUS_FILE" ]; then
    echo "--- Status ---"
    cat "$STATUS_FILE" | python3 -m json.tool 2>/dev/null || cat "$STATUS_FILE"
    echo ""
fi

# Heartbeat
if [ -f "$HEARTBEAT_FILE" ]; then
    HEARTBEAT=$(cat "$HEARTBEAT_FILE")
    echo "Last heartbeat: $HEARTBEAT"

    # Check if stale (>2 minutes)
    if command -v python3 &>/dev/null; then
        STALE=$(python3 -c "
from datetime import datetime, timedelta
try:
    hb = datetime.fromisoformat('$HEARTBEAT')
    if datetime.now() - hb > timedelta(minutes=2):
        print('YES')
    else:
        print('NO')
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")
        if [ "$STALE" = "YES" ]; then
            echo "WARNING: Heartbeat is stale (>2 min old)"
        fi
    fi
    echo ""
fi

# Queue size
if [ -f "$QUEUE_FILE" ]; then
    QSIZE=$(python3 -c "import json; print(len(json.load(open('$QUEUE_FILE'))))" 2>/dev/null || echo "?")
    echo "Pending queue: $QSIZE messages"
fi

# State
if [ -f "$STATE_FILE" ]; then
    echo "--- State ---"
    cat "$STATE_FILE" | python3 -m json.tool 2>/dev/null || cat "$STATE_FILE"
    echo ""
fi

# Last 5 log lines
if [ -f "$LOG_FILE" ]; then
    echo "--- Recent Logs ---"
    tail -5 "$LOG_FILE"
fi

echo "=========================================="
