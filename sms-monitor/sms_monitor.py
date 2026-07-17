#!/usr/bin/env python3
"""
M-Pesa SMS Monitor for Termux.
Continuously polls for new M-Pesa SMS messages and posts parsed data to cloud API.

Requires:
  - Termux:API app installed (`pkg install termux-api`)
  - SMS read permission granted (`termux-permission`)
  - Python requests library (`pip install requests`)
"""

import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from mpesa_parser import parse_mpesa_sms, is_valid_mpesa_sms
from api_client import ApiClient, MessageQueue

# --- Logging Setup ---

def setup_logging(log_file: str) -> logging.Logger:
    """Configure logging to both file and stderr with rotation."""
    logger = logging.getLogger("sms_monitor")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Rotating file handler (1MB per file, keep 5 backups)
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError as e:
        print(f"Warning: Cannot open log file {log_file}: {e}", file=sys.stderr)

    # Stderr handler
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# --- Config ---

def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """Load configuration from JSON file."""
    with open(config_path, "r") as f:
        cfg = json.load(f)

    # Validate required fields
    required = ["api_url", "api_key", "poll_interval", "owner_phone"]
    for key in required:
        if key not in cfg:
            raise ValueError(f"Missing required config key: {key}")

    return cfg


# --- Termux SMS Reading ---

def read_sms_from_termux() -> List[Dict[str, Any]]:
    """Read SMS messages using termux-sms-list.
    
    Returns list of dicts with keys: _id, number, body, date, read
    """
    try:
        result = subprocess.run(
            ["termux-sms-list", "-l", "50"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            raise RuntimeError(f"termux-sms-list failed: {result.stderr.strip()}")

        messages = json.loads(result.stdout)
        if not isinstance(messages, list):
            return []
        return messages

    except FileNotFoundError:
        raise RuntimeError("termux-sms-list not found. Install Termux:API: pkg install termux-api")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse SMS JSON: {e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("termux-sms-list timed out (15s)")


# --- State Management ---

class MonitorState:
    """Persist monitor state to survive restarts."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.last_sms_id: int = 0
        self.total_processed: int = 0
        self.last_processed_timestamp: float = 0.0  # Unix timestamp of last processed SMS
        self._load()

    def _load(self):
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
                self.last_sms_id = data.get("last_sms_id", 0)
                self.total_processed = data.get("total_processed", 0)
                self.last_processed_timestamp = data.get("last_processed_timestamp", 0.0)
        except (FileNotFoundError, json.JSONDecodeError):
            self.last_sms_id = 0
            self.total_processed = 0
            self.last_processed_timestamp = 0.0

    def save(self):
        """Persist state to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump({
                    "last_sms_id": self.last_sms_id,
                    "total_processed": self.total_processed,
                    "last_processed_timestamp": self.last_processed_timestamp,
                    "saved_at": datetime.now().isoformat(),
                }, f, indent=2)
        except OSError as e:
            logging.getLogger("sms_monitor").error(f"Failed to save state: {e}")


# --- Status / Heartbeat ---

def update_status(status_file: str, status: Dict[str, Any]):
    """Write current status to a JSON file for external monitoring."""
    try:
        with open(status_file, "w") as f:
            json.dump(status, f, indent=2)
    except OSError:
        pass


def write_heartbeat(heartbeat_file: str):
    """Write current timestamp as heartbeat."""
    try:
        with open(heartbeat_file, "w") as f:
            f.write(datetime.now().isoformat())
    except OSError:
        pass


# --- SMS Filtering ---

def filter_new_mpesa_sms(
    messages: List[Dict[str, Any]],
    last_id: int,
    owner_phone: str,
    last_timestamp: float = 0.0,
) -> List[Dict[str, Any]]:
    """Filter SMS list for new M-Pesa messages.

    Uses dual filtering to handle Android SMS ID resets:
    1. Primary: _id > last_id (normal operation)
    2. Fallback: SMS date > last_timestamp (handles ID reset after phone restart)

    A message must pass BOTH filters when both are active, ensuring
    no re-processing of old messages even if IDs reset.
    """
    new_messages = []
    for msg in messages:
        msg_id = msg.get("_id", 0)
        sms_date = msg.get("date", 0)  # Unix timestamp in milliseconds from Android

        # Convert Android ms timestamp to seconds for comparison
        sms_timestamp = sms_date / 1000.0 if sms_date else 0.0

        # Primary filter: ID must be newer
        if msg_id <= last_id:
            continue

        # Fallback filter: if we have a stored timestamp, SMS must also be newer
        # This protects against Android reindexing (ID reset) after phone restart
        if last_timestamp > 0 and sms_timestamp > 0 and sms_timestamp < last_timestamp:
            continue

        body = msg.get("body", "")
        if not is_valid_mpesa_sms(body):
            continue

        new_messages.append(msg)

    # Sort by ID ascending (process oldest first)
    new_messages.sort(key=lambda m: m.get("_id", 0))
    return new_messages


# --- Main Monitor ---

class SmsMonitor:
    """Main SMS monitoring loop."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("sms_monitor")
        self.running = True

        self.api = ApiClient(
            api_url=config["api_url"],
            api_key=config["api_key"],
            max_retries=config.get("max_retries", 5),
            retry_interval=config.get("retry_interval", 30),
            connect_timeout=config.get("connect_timeout", 10),
        )
        self.queue = MessageQueue(config.get("queue_file", "sms_queue.json"))
        self.state = MonitorState(config["state_file"])
        self.owner_phone = config["owner_phone"]

        self.poll_interval = config.get("poll_interval", 5)
        self.heartbeat_file = config.get("heartbeat_file", "sms_heartbeat.txt")
        self.status_file = config.get("status_file", "sms_status.json")

        # Track last heartbeat time
        self._last_heartbeat = 0.0
        self._heartbeat_interval = 30  # Write heartbeat every 30 seconds

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def _process_payment(self, payment: Dict[str, Any]) -> bool:
        """Process a single parsed payment: post to API or queue."""
        tx_code = payment["tx_code"]
        amount = payment["amount"]
        direction = payment["direction"]
        phone = payment.get("counterparty_phone", "N/A")

        self.logger.info(
            f"Processing: {tx_code} | Ksh{amount:.2f} {direction} | "
            f"From/To: {phone} ({payment.get('counterparty_name', 'unknown')})"
        )

        # Post to API
        success = self.api.post_payment(payment)

        if not success:
            # Queue for retry
            self.logger.warning(f"API post failed for {tx_code}, queuing for retry")
            self.queue.enqueue(payment)

        return success

    def _flush_queue(self):
        """Attempt to send all queued messages."""
        if self.queue.size() == 0:
            return

        self.logger.info(f"Flushing queue ({self.queue.size()} messages)")
        items = self.queue.dequeue_all()
        requeue = []

        for payment in items:
            if self.api.post_payment(payment):
                self.logger.info(f"✓ Queued payment {payment['tx_code']} sent successfully")
            else:
                requeue.append(payment)

        if requeue:
            for p in requeue:
                self.queue.enqueue(p)
            self.logger.warning(f"Re-queued {len(requeue)} messages")

    def _do_heartbeat(self):
        """Write heartbeat and status periodically."""
        now = time.time()
        if now - self._last_heartbeat < self._heartbeat_interval:
            return

        self._last_heartbeat = now
        write_heartbeat(self.heartbeat_file)
        update_status(self.status_file, {
            "running": True,
            "last_sms_id": self.state.last_sms_id,
            "total_processed": self.state.total_processed,
            "queue_size": self.queue.size(),
            "heartbeat": datetime.now().isoformat(),
            "api_url": self.config["api_url"],
        })

    def _init_run(self):
        """Initialize: on first run, set last_sms_id to current max so we don't process old SMS."""
        if self.state.last_sms_id == 0:
            self.logger.info("First run: fetching current SMS list to set baseline...")
            try:
                messages = read_sms_from_termux()
                if messages:
                    max_id = max(m.get("_id", 0) for m in messages)
                    self.state.last_sms_id = max_id
                    self.state.save()
                    self.logger.info(f"Baseline set: last_sms_id = {max_id}")
                else:
                    self.logger.info("No existing SMS found, starting from 0")
            except Exception as e:
                self.logger.warning(f"Could not set baseline: {e}")

    def run(self):
        """Main monitoring loop."""
        self.logger.info("=" * 60)
        self.logger.info("M-Pesa SMS Monitor starting")
        self.logger.info(f"API URL: {self.config['api_url']}")
        self.logger.info(f"Owner phone: {self.owner_phone}")
        self.logger.info(f"Poll interval: {self.poll_interval}s")
        self.logger.info(f"Last processed SMS ID: {self.state.last_sms_id}")
        self.logger.info("=" * 60)

        self._init_run()

        # Initial queue flush
        self._flush_queue()

        consecutive_errors = 0
        max_consecutive_errors = 10

        while self.running:
            try:
                # Read SMS
                messages = read_sms_from_termux()

                # Filter new M-Pesa messages (with timestamp fallback)
                new_messages = filter_new_mpesa_sms(
                    messages, self.state.last_sms_id, self.owner_phone,
                    last_timestamp=self.state.last_processed_timestamp,
                )

                if new_messages:
                    self.logger.info(f"Found {len(new_messages)} new M-Pesa SMS")

                max_id_seen = self.state.last_sms_id

                for msg in new_messages:
                    if not self.running:
                        break

                    body = msg.get("body", "")
                    msg_id = msg.get("_id", 0)

                    # Parse
                    payment = parse_mpesa_sms(body, self.owner_phone)
                    if not payment:
                        self.logger.warning(f"Failed to parse SMS ID {msg_id}: {body[:80]}...")
                        max_id_seen = max(max_id_seen, msg_id)
                        continue

                    # Enrich with SMS metadata
                    payment["sms_id"] = msg_id
                    payment["sms_number"] = msg.get("number", "")
                    payment["sms_date"] = msg.get("date", "")

                    # Update last processed timestamp from SMS date
                    sms_date_ms = msg.get("date", 0)
                    if sms_date_ms:
                        sms_ts = sms_date_ms / 1000.0
                        if sms_ts > self.state.last_processed_timestamp:
                            self.state.last_processed_timestamp = sms_ts

                    # Process
                    try:
                        self._process_payment(payment)
                    except Exception as e:
                        self.logger.error(f"Error processing payment {payment['tx_code']}: {e}")
                        self.logger.debug(traceback.format_exc())

                    max_id_seen = max(max_id_seen, msg_id)
                    self.state.total_processed += 1

                # Update state
                if max_id_seen > self.state.last_sms_id:
                    self.state.last_sms_id = max_id_seen
                    self.state.save()

                # Flush queued messages
                self._flush_queue()

                # Heartbeat
                self._do_heartbeat()

                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                self.logger.error(f"Poll error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                self.logger.debug(traceback.format_exc())

                if consecutive_errors >= max_consecutive_errors:
                    self.logger.critical(
                        f"Too many consecutive errors ({consecutive_errors}), "
                        f"backing off for {self.poll_interval * 10}s"
                    )
                    time.sleep(self.poll_interval * 10)
                    consecutive_errors = 0

            # Wait before next poll
            time.sleep(self.poll_interval)

        # Cleanup
        self.logger.info("Monitor shutting down")
        self._shutdown()

    def _shutdown(self):
        """Graceful shutdown: save state, close connections."""
        self.state.save()
        self.api.close()
        update_status(self.status_file, {
            "running": False,
            "last_sms_id": self.state.last_sms_id,
            "total_processed": self.state.total_processed,
            "queue_size": self.queue.size(),
            "stopped_at": datetime.now().isoformat(),
        })
        self.logger.info("Shutdown complete")


def main():
    """Entry point."""
    # Determine paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")

    # Load config
    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"FATAL: Failed to load config: {e}", file=sys.stderr)
        sys.exit(1)

    # Setup logging
    log_file = config.get("log_file", os.path.join(script_dir, "sms_monitor.log"))
    logger = setup_logging(log_file)

    # Create and run monitor
    try:
        monitor = SmsMonitor(config)
        monitor.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
