# M-Pesa SMS Monitor for Termux

A lightweight SMS monitoring system that reads M-Pesa payment confirmations from your Android phone and posts parsed data to a cloud API.

## Architecture

```
[Android SMS] → [Termux:API] → [sms_monitor.py] → [Cloud API]
                                    ↓
                              [Queue (offline)]
```

## Prerequisites

1. **Termux** (from F-Droid, NOT Play Store)
2. **Termux:API** app (from F-Droid)
3. **Python 3** and **requests** library

## Quick Setup

```bash
# Install dependencies
pkg update && pkg install python termux-api
pip install requests

# Grant SMS permission
termux-permission-request android.permission.READ_SMS

# Edit config
nano config.json
# Set your: api_url, api_key, owner_phone

# Start the monitor
bash start_monitor.sh

# Check status
bash status.sh

# Watch live logs
tail -f sms_monitor.log

# Stop
bash stop_monitor.sh
```

## Configuration

Edit `config.json`:

| Field | Description |
|-------|-------------|
| `api_url` | Your Oracle Cloud API endpoint |
| `api_key` | API authentication key |
| `poll_interval` | Seconds between SMS polls (default: 5) |
| `owner_phone` | Your M-Pesa registered phone number |
| `retry_interval` | Seconds between API retry attempts (default: 30) |
| `max_retries` | Max retry attempts per payment (default: 5) |

## M-Pesa SMS Formats Supported

- **Pochi la Biashara** (received payments)
- **Direct send** (person-to-person)
- **Paybill/Till** (merchant payments)
- **Buy Goods** (goods purchases)

## Features

- **No root required** — uses Termux:API
- **Duplicate detection** — tracks last processed SMS ID
- **Offline queue** — messages queued when API is unreachable
- **Auto-retry** — exponential backoff on failures
- **Health monitoring** — heartbeat file, status JSON
- **Graceful shutdown** — handles SIGTERM/SIGINT
- **Persistent state** — survives phone restarts

## File Structure

| File | Purpose |
|------|---------|
| `sms_monitor.py` | Main monitoring loop |
| `mpesa_parser.py` | M-Pesa SMS parsing logic |
| `api_client.py` | Cloud API communication + queue |
| `config.json` | Configuration |
| `start_monitor.sh` | Start monitor in background |
| `stop_monitor.sh` | Graceful stop |
| `status.sh` | Check running status |

## Logs & Monitoring

- **Logs**: `sms_monitor.log` (configurable)
- **Status**: `sms_status.json` — JSON with current state
- **Heartbeat**: `sms_heartbeat.txt` — timestamp updated every 60s
- **Queue**: `sms_queue.json` — pending offline messages
- **State**: `sms_state.json` — last processed SMS ID

## Auto-start on Boot (Optional)

Use Termux:Boot to start on device boot:

1. Install **Termux:Boot** from F-Droid
2. Create `~/.termux/boot/start-mpesa-monitor.sh`:
   ```bash
   #!/data/data/com.termux/files/usr/bin/bash
   termux-wake-lock
   cd ~/sms-monitor
   bash start_monitor.sh
   ```
3. Make executable: `chmod +x ~/.termux/boot/start-mpesa-monitor.sh`

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `termux-sms-list not found` | Install Termux:API app + `pkg install termux-api` |
| Permission denied | `termux-permission-request android.permission.READ_SMS` |
| No SMS read | Check Android Settings > Apps > Termux:API > Permissions |
| API connection failed | Check `config.json` api_url and api_key |
| Monitor won't start | Check `sms_monitor.log` for errors |
