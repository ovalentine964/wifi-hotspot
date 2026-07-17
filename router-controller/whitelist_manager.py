"""
Whitelist Manager — syncs authorized MACs from cloud API with router MAC filter.

Flow (every poll_interval seconds):
  1. Fetch authorized MACs from Oracle Cloud API
  2. Read current router MAC filter
  3. Unblock MACs that are authorized but currently blocked
  4. Block MACs that are expired (not in authorized list) but currently allowed
  5. VIP users are always whitelisted regardless of cloud status
  6. Log all changes

Improvements:
  - Cached fallback: if API fails 3x consecutively, use last-known-good list
  - Failed write retry queue: retries up to 3 times on next cycles
  - Never removes MACs when API is unreachable (fail-safe)
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import ROUTER_CONFIG, LOG_LEVEL, LOG_FORMAT
from router_ctrl import RouterController

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_API_CONSECUTIVE_FAILURES = 3  # Switch to cached list after this many failures
MAX_WRITE_RETRIES = 3             # Retry failed router writes up to this many times
CACHE_FILE = "mac_cache.json"     # File to persist last-known-good MAC list

# ---------------------------------------------------------------------------
# MAC Cache — persists last-known-good authorized MAC list to disk
# ---------------------------------------------------------------------------

class MacCache:
    """File-based cache for the last-known-good authorized MAC list."""

    def __init__(self, cache_file: str):
        self.cache_file = cache_file
        self._cached_authorized: set[str] = set()
        self._cached_vip: set[str] = set()
        self._cached_at: str = ""
        self._load()

    def _load(self):
        try:
            with open(self.cache_file, "r") as f:
                data = json.load(f)
                self._cached_authorized = set(data.get("authorized", []))
                self._cached_vip = set(data.get("vip", []))
                self._cached_at = data.get("cached_at", "")
                logger.info("Loaded MAC cache: %d authorized, %d VIP (cached at %s)",
                            len(self._cached_authorized), len(self._cached_vip), self._cached_at)
        except (FileNotFoundError, json.JSONDecodeError):
            self._cached_authorized = set()
            self._cached_vip = set()
            self._cached_at = ""

    def save(self, authorized: set[str], vip: set[str]):
        """Persist the current authorized MAC list to disk."""
        self._cached_authorized = authorized
        self._cached_vip = vip
        self._cached_at = datetime.now(timezone.utc).isoformat()
        try:
            os.makedirs(os.path.dirname(self.cache_file) if os.path.dirname(self.cache_file) else ".", exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump({
                    "authorized": sorted(authorized),
                    "vip": sorted(vip),
                    "cached_at": self._cached_at,
                }, f, indent=2)
            logger.info("Saved MAC cache: %d authorized, %d VIP", len(authorized), len(vip))
        except OSError as e:
            logger.error("Failed to save MAC cache: %s", e)

    def get_authorized(self) -> set[str]:
        return self._cached_authorized

    def get_vip(self) -> set[str]:
        return self._cached_vip

    def is_empty(self) -> bool:
        return len(self._cached_authorized) == 0 and len(self._cached_vip) == 0

# ---------------------------------------------------------------------------
# Failed Write Queue — retries router writes that failed
# ---------------------------------------------------------------------------

class FailedWriteQueue:
    """
    Queue for router write operations that failed.
    Retries up to MAX_WRITE_RETRIES times, then drops with a log.
    """

    def __init__(self):
        # List of dicts: {"mac": str, "action": "block"|"unblock", "retries": int}
        self._queue: list[dict] = []

    def add(self, mac: str, action: str):
        """Add a failed operation to the retry queue."""
        # Don't add duplicates
        for entry in self._queue:
            if entry["mac"] == mac and entry["action"] == action:
                logger.debug("Write %s %s already in retry queue", action, mac)
                return
        self._queue.append({"mac": mac, "action": action, "retries": 0})
        logger.info("Added %s %s to write retry queue (queue size: %d)",
                     action, mac, len(self._queue))

    def get_pending(self) -> list[dict]:
        """Return list of pending retries (not yet exceeded max retries)."""
        return [e for e in self._queue if e["retries"] < MAX_WRITE_RETRIES]

    def mark_attempted(self, mac: str, action: str, success: bool):
        """Record a retry attempt. Remove if successful or max retries exceeded."""
        for entry in list(self._queue):
            if entry["mac"] == mac and entry["action"] == action:
                if success:
                    logger.info("✓ Retry succeeded: %s %s", action, mac)
                    self._queue.remove(entry)
                else:
                    entry["retries"] += 1
                    if entry["retries"] >= MAX_WRITE_RETRIES:
                        logger.error("✗ Dropping from retry queue after %d attempts: %s %s",
                                     entry["retries"], action, mac)
                        self._queue.remove(entry)
                    else:
                        logger.warning("Retry failed: %s %s (attempt %d/%d)",
                                       action, mac, entry["retries"], MAX_WRITE_RETRIES)
                return

    def size(self) -> int:
        return len(self._queue)

# ---------------------------------------------------------------------------
# Cloud API client
# ---------------------------------------------------------------------------

class CloudAPI:
    """
    Client for the Oracle Cloud API that manages authorized MACs.

    Expected API responses:
      GET /api/v1/authorized_macs
      Headers: Authorization: Bearer <api_key>
      Response JSON:
      {
        "authorized_macs": [
          {"mac": "AA:BB:CC:DD:EE:FF", "expires_at": "2026-07-17T23:59:59Z", "user": "john"},
          ...
        ],
        "vip_macs": [
          {"mac": "11:22:33:44:55:66", "user": "owner"},
          ...
        ]
      }
    """

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self):
        await self._client.aclose()

    async def get_authorized_macs(self) -> Optional[dict]:
        """
        Fetch authorized MACs and VIP list from cloud.

        Returns dict on success:
          authorized: set of MAC strings (uppercase, colon-separated)
          vip: set of VIP MAC strings
          raw: full response dict

        Returns None on failure.
        """
        url = f"{self.base_url}/api/authorized"
        headers = {"X-API-Key": self.api_key}

        for attempt in range(1, 4):
            try:
                resp = await self._client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                # Flask /api/authorized returns {"macs": [...], "count": N}
                authorized = set()
                for mac in data.get("macs", []):
                    mac = mac.upper().replace("-", ":")
                    if mac:
                        authorized.add(mac)

                # VIP MACs are included in the authorized list from Flask
                vip = set()

                return {"authorized": authorized, "vip": vip, "raw": data}

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPStatusError) as e:
                logger.warning("Cloud API fetch failed (attempt %d/3): %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(5 * attempt)
                else:
                    logger.error("Cloud API unreachable after 3 attempts")
                    return None  # Return None instead of empty set on failure

    @staticmethod
    def _is_expired(expires_at: Optional[str]) -> bool:
        if not expires_at:
            return False  # No expiry = never expires
        try:
            # Parse ISO 8601
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            return exp < datetime.now(timezone.utc)
        except (ValueError, TypeError):
            return False


# ---------------------------------------------------------------------------
# Whitelist Manager
# ---------------------------------------------------------------------------

class WhitelistManager:
    """
    Periodic sync service.

    - Polls cloud API for authorized MACs
    - Compares with router MAC filter
    - Blocks/unblocks as needed
    - VIP MACs are always whitelisted
    - Falls back to cached list if API is unreachable
    - Retries failed router writes on next cycle
    """

    def __init__(
        self,
        cloud_api_url: str | None = None,
        cloud_api_key: str | None = None,
        poll_interval: int | None = None,
        vip_override: set[str] | None = None,
        cache_file: str | None = None,
    ):
        self.cloud_api_url = cloud_api_url or ROUTER_CONFIG["cloud_api_url"]
        self.cloud_api_key = cloud_api_key or ROUTER_CONFIG["cloud_api_key"]
        self.poll_interval = poll_interval or ROUTER_CONFIG["poll_interval"]
        # Extra VIP MACs to always whitelist (hardcoded overrides)
        self.vip_override: set[str] = {m.upper() for m in (vip_override or set())}

        self._running = False
        self._cloud: Optional[CloudAPI] = None

        # API failure tracking for fallback
        self._api_consecutive_failures = 0

        # Cached MAC list
        _cache_path = cache_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), CACHE_FILE
        )
        self._cache = MacCache(_cache_path)

        # Failed write retry queue
        self._retry_queue = FailedWriteQueue()

    async def start(self):
        """Run the sync loop until stopped."""
        self._running = True
        self._cloud = CloudAPI(self.cloud_api_url, self.cloud_api_key)

        logger.info("WhitelistManager starting — poll every %ds", self.poll_interval)
        logger.info("Cache file: %s", self._cache.cache_file)

        async with RouterController() as rc:
            # Initial login
            if not await rc.login():
                logger.error("Initial router login failed — will retry in loop")

            while self._running:
                try:
                    await self._sync_cycle(rc)
                except Exception as e:
                    logger.exception("Sync cycle error: %s", e)
                await asyncio.sleep(self.poll_interval)

        if self._cloud:
            await self._cloud.close()
        logger.info("WhitelistManager stopped")

    def stop(self):
        self._running = False

    async def _sync_cycle(self, rc: RouterController):
        """One poll-compare-apply cycle."""
        t0 = time.monotonic()

        # 1. Fetch cloud state (may return None on failure)
        cloud = await self._cloud.get_authorized_macs()

        if cloud is not None:
            # API succeeded — reset failure counter and update cache
            self._api_consecutive_failures = 0
            authorized = cloud["authorized"]
            vip = cloud["vip"] | self.vip_override
            all_whitelisted = authorized | vip

            # Update cache with fresh data
            self._cache.save(authorized, cloud["vip"])
            logger.info("API reachable: %d authorized MACs, %d VIP",
                         len(authorized), len(vip))
        else:
            # API failed — increment failure counter
            self._api_consecutive_failures += 1
            logger.warning("API failure %d/%d consecutive",
                           self._api_consecutive_failures, MAX_API_CONSECUTIVE_FAILURES)

            if self._api_consecutive_failures >= MAX_API_CONSECUTIVE_FAILURES and not self._cache.is_empty():
                # Use cached list after repeated failures
                cached_auth = self._cache.get_authorized()
                cached_vip = self._cache.get_vip() | self.vip_override
                all_whitelisted = cached_auth | cached_vip
                logger.warning(
                    "API unreachable for %d cycles — using CACHED list: "
                    "%d authorized, %d VIP (cached at %s)",
                    self._api_consecutive_failures, len(cached_auth),
                    len(cached_vip), self._cache._cached_at
                )
            else:
                # Not enough failures yet, or no cache available — skip this cycle
                logger.info("Skipping sync cycle (API failure %d/%d, cache empty=%s)",
                            self._api_consecutive_failures,
                            MAX_API_CONSECUTIVE_FAILURES, self._cache.is_empty())
                return

        # 2. Read router state
        try:
            current_blocked = await rc.get_blocked_macs()
        except Exception as e:
            logger.error("Failed to read router MAC filter: %s", e)
            # Force re-login next cycle
            rc._logged_in = False
            return

        # 3. Process retry queue first
        await self._process_retry_queue(rc)

        # 4. Determine actions
        to_unblock = set()  # authorized but blocked
        to_block = set()    # expired/unknown but currently not blocked

        for mac in all_whitelisted:
            if mac in current_blocked:
                to_unblock.add(mac)

        # Only block MACs that are in the filter list and not authorized
        # If API is unreachable (using cache), NEVER block new MACs — only allow
        is_api_unreachable = self._api_consecutive_failures >= MAX_API_CONSECUTIVE_FAILURES

        if not is_api_unreachable:
            # API is reachable — safe to block expired MACs
            current_filter = await rc.get_mac_filter()
            for entry in current_filter:
                mac = entry.get("mac", entry.get("Mac", "")).upper()
                action = entry.get("action", entry.get("Action", 0))
                if not mac:
                    continue
                # If currently allowed (action==0) and NOT in whitelist → block it
                if action == 0 and mac not in all_whitelisted:
                    to_block.add(mac)
        else:
            logger.warning("API unreachable — SKIPPING block operations (fail-safe)")

        if not to_unblock and not to_block:
            elapsed = time.monotonic() - t0
            logger.debug("No changes needed (cycle took %.1fs)", elapsed)
            return

        logger.info("Changes: %d to unblock, %d to block", len(to_unblock), len(to_block))

        # 5. Apply changes
        for mac in to_unblock:
            logger.info("Decision: UNBLOCK authorized MAC %s", mac)
            ok = await rc.unblock_mac(mac)
            if not ok:
                logger.error("Failed to unblock %s — adding to retry queue", mac)
                self._retry_queue.add(mac, "unblock")
            else:
                logger.info("✓ Unblocked %s", mac)

        for mac in to_block:
            logger.info("Decision: BLOCK expired/unknown MAC %s", mac)
            ok = await rc.block_mac(mac)
            if not ok:
                logger.error("Failed to block %s — adding to retry queue", mac)
                self._retry_queue.add(mac, "block")
            else:
                logger.info("✓ Blocked %s", mac)

        elapsed = time.monotonic() - t0
        logger.info("Sync cycle complete in %.1fs — unblocked=%s, blocked=%s, "
                     "retry_queue=%d, api_failures=%d",
                     elapsed, to_unblock, to_block,
                     self._retry_queue.size(), self._api_consecutive_failures)

    async def _process_retry_queue(self, rc: RouterController):
        """Retry previously failed write operations."""
        pending = self._retry_queue.get_pending()
        if not pending:
            return

        logger.info("Processing %d pending write retries", len(pending))

        for entry in pending:
            mac = entry["mac"]
            action = entry["action"]

            if action == "block":
                ok = await rc.block_mac(mac)
            elif action == "unblock":
                ok = await rc.unblock_mac(mac)
            else:
                logger.error("Unknown retry action: %s for %s", action, mac)
                self._retry_queue.mark_attempted(mac, action, success=True)
                continue

            self._retry_queue.mark_attempted(mac, action, success=ok)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
    )

    mgr = WhitelistManager()

    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        logger.info("Received signal %s — shutting down", sig)
        mgr.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(mgr.start())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
