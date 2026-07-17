#!/data/data/com.termux/files/usr/bin/bash
# stop_monitor.sh - Gracefully stop the SMS monitor
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${SCRIPT_DIR}/.monitor.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Monitor may not be running."
    # Try to find by process name
    PID=$(pgrep -f "sms_monitor.py" 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "Found running process (PID $PID). Stopping..."
        kill "$PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        echo "Stopped."
    else
        echo "No running monitor found."
    fi
    exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "Sending SIGTERM to monitor (PID $PID)..."
    kill "$PID"

    # Wait up to 10 seconds for graceful shutdown
    for i in $(seq 1 10); do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "Monitor stopped gracefully."
            rm -f "$PID_FILE"
            exit 0
        fi
        sleep 1
    done

    echo "Graceful shutdown timed out. Sending SIGKILL..."
    kill -9 "$PID" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "Monitor killed."
else
    echo "Process $PID not running. Cleaning up PID file."
    rm -f "$PID_FILE"
fi
