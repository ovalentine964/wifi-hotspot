#!/data/data/com.termux/files/usr/bin/bash
# start_monitor.sh - Start M-Pesa SMS Monitor in background
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/sms_monitor.log"
PID_FILE="${SCRIPT_DIR}/.monitor.pid"
PYTHON="python3"

echo "=========================================="
echo "  M-Pesa SMS Monitor - Starting"
echo "=========================================="

# 1. Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Monitor already running (PID $OLD_PID)"
        echo "Use stop_monitor.sh to stop it first."
        exit 1
    else
        echo "Removing stale PID file"
        rm -f "$PID_FILE"
    fi
fi

# 2. Check Termux:API is installed
if ! command -v termux-sms-list &>/dev/null; then
    echo "ERROR: termux-sms-list not found."
    echo "Install Termux:API:"
    echo "  1. Install 'Termux:API' app from F-Droid"
    echo "  2. Run: pkg install termux-api"
    exit 1
fi

# 3. Check Python is available
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: Python3 not found. Install with: pkg install python"
    exit 1
fi

# 4. Check requests library
if ! "$PYTHON" -c "import requests" 2>/dev/null; then
    echo "Installing requests library..."
    pip install requests
fi

# 5. Check SMS permission
echo "Checking SMS permissions..."
SMS_OUTPUT=$(termux-sms-list -l 1 2>&1 || true)
if echo "$SMS_OUTPUT" | grep -qi "permission"; then
    echo "SMS permission not granted. Requesting..."
    termux-permission-request android.permission.READ_SMS
    sleep 3
    # Re-check
    SMS_OUTPUT=$(termux-sms-list -l 1 2>&1 || true)
    if echo "$SMS_OUTPUT" | grep -qi "permission"; then
        echo "ERROR: SMS permission still not granted."
        echo "Grant manually: Settings > Apps > Termux:API > Permissions > SMS"
        exit 1
    fi
fi
echo "SMS permissions OK"

# 6. Start the monitor
echo "Starting SMS monitor..."
cd "$SCRIPT_DIR"

nohup "$PYTHON" -u sms_monitor.py >> "$LOG_FILE" 2>&1 &
MONITOR_PID=$!
echo "$MONITOR_PID" > "$PID_FILE"

echo "Monitor started (PID $MONITOR_PID)"
echo "Log file: $LOG_FILE"
echo ""
echo "Commands:"
echo "  tail -f $LOG_FILE    # Watch live logs"
echo "  bash status.sh       # Check status"
echo "  bash stop_monitor.sh # Stop monitor"
echo "=========================================="
