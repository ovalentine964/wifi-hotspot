"""
Cloud API Client for M-Pesa SMS Monitor.
Handles posting parsed payment data to Oracle Cloud API with retry logic.
"""

import json
import time
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger("sms_monitor.api")


class ApiClient:
    """Handles communication with the cloud API endpoint."""

    def __init__(self, api_url: str, api_key: str, max_retries: int = 5, retry_interval: int = 30, connect_timeout: int = 10):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.connect_timeout = connect_timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "User-Agent": "MPesa-SMS-Monitor/1.0",
        })

    def post_payment(self, payment: Dict[str, Any]) -> bool:
        """Post a single payment to the API.
        
        Returns True on success, False on failure.
        """
        url = f"{self.api_url}/api/confirm"
        payload = {
            "phone": payment.get("counterparty_phone", ""),
            "amount": payment["amount"],
            "mpesa_code": payment["tx_code"],
            "raw_sms": payment.get("raw_sms", ""),
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"Posting payment {payment['tx_code']} (attempt {attempt}/{self.max_retries})")
                resp = self._session.post(url, json=payload, timeout=self.connect_timeout)

                if resp.status_code in (200, 201):
                    logger.info(f"✓ Payment {payment['tx_code']} posted successfully (HTTP {resp.status_code})")
                    return True
                elif resp.status_code == 409:
                    # Duplicate - already posted, treat as success
                    logger.info(f"Payment {payment['tx_code']} already exists (409), skipping")
                    return True
                elif resp.status_code == 429:
                    # Rate limited
                    retry_after = int(resp.headers.get("Retry-After", self.retry_interval))
                    logger.warning(f"Rate limited (429), waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue
                elif resp.status_code >= 500:
                    # Server error - retry
                    logger.warning(f"Server error {resp.status_code}, retrying in {self.retry_interval}s")
                    time.sleep(self.retry_interval)
                    continue
                else:
                    # Client error (400, 401, 403, etc.) - don't retry
                    logger.error(f"Client error {resp.status_code}: {resp.text[:500]}")
                    return False

            except requests.exceptions.ConnectionError:
                logger.warning(f"Connection error on attempt {attempt}, retrying in {self.retry_interval}s")
                time.sleep(self.retry_interval)
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on attempt {attempt}, retrying in {self.retry_interval}s")
                time.sleep(self.retry_interval)
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error: {e}")
                time.sleep(self.retry_interval)

        logger.error(f"Failed to post payment {payment['tx_code']} after {self.max_retries} attempts")
        return False

    def check_health(self) -> bool:
        """Check if the API is reachable."""
        try:
            url = f"{self.api_url}/api/v1/health"
            resp = self._session.get(url, timeout=self.connect_timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def close(self):
        """Close the HTTP session."""
        self._session.close()


class MessageQueue:
    """Simple file-based queue for offline messages."""

    MAX_QUEUE_SIZE = 1000  # Maximum messages to hold in queue

    def __init__(self, queue_file: str, max_size: int = MAX_QUEUE_SIZE):
        self.queue_file = queue_file
        self.max_size = max_size
        self._queue: list = []
        self._load()

    def _load(self):
        try:
            with open(self.queue_file, "r") as f:
                self._queue = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._queue = []

    def _save(self):
        try:
            with open(self.queue_file, "w") as f:
                json.dump(self._queue, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save queue: {e}")

    def enqueue(self, payment: Dict[str, Any]):
        """Add a payment to the queue. Drops oldest if at capacity."""
        if len(self._queue) >= self.max_size:
            dropped = self._queue.pop(0)
            logger.warning(
                f"Queue full ({self.max_size}), dropping oldest: "
                f"{dropped.get('tx_code', 'unknown')}"
            )
        self._queue.append(payment)
        self._save()
        logger.info(f"Queued payment {payment['tx_code']} (queue size: {len(self._queue)})")

    def dequeue_all(self) -> list:
        """Remove and return all queued payments."""
        items = self._queue.copy()
        self._queue.clear()
        self._save()
        return items

    def size(self) -> int:
        return len(self._queue)

    def clear(self):
        self._queue.clear()
        self._save()
