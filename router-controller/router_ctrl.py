"""
Nokia G-2425G-A Router Controller.

Handles:
  - Encrypted login (AES-128-CBC + RSA-1024)
  - MAC filter read/write (block/unblock)
  - Connected device list
  - Session management with auto re-login
  - CSRF token refresh per write
  - Write serialization queue
"""

import asyncio
import logging
import re
import time
from typing import Optional

import httpx

from config import ROUTER_CONFIG, MAX_RETRIES, BASE_BACKOFF
from nokia_crypto import encrypt_payload
from nokia_parse import (
    parse_login_page,
    parse_mac_filter,
    parse_device_list,
    parse_csrf_token,
    parse_js_var,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router Controller
# ---------------------------------------------------------------------------

class RouterController:
    """
    Async controller for Nokia G-2425G-A.

    Usage:
        async with RouterController() as rc:
            await rc.login()
            devices = await rc.get_connected_devices()
            await rc.block_mac("AA:BB:CC:DD:EE:FF")
    """

    def __init__(
        self,
        router_ip: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ssid_index: int | None = None,
        write_timeout: float | None = None,
    ):
        self.router_ip = router_ip or ROUTER_CONFIG["router_ip"]
        self.username = username or ROUTER_CONFIG["username"]
        self.password = password or ROUTER_CONFIG["password"]
        self.ssid_index = ssid_index if ssid_index is not None else ROUTER_CONFIG["ssid_index"]
        self.write_timeout = write_timeout or ROUTER_CONFIG["write_timeout"]

        self.base_url = f"http://{self.router_ip}"
        self._client: Optional[httpx.AsyncClient] = None

        # Cached auth state
        self._logged_in = False
        self._token: str | None = None
        self._nonce: str | None = None
        self._modulus: str | None = None
        self._exponent: str | None = None

        # Serialize writes (router can't handle concurrent POSTs)
        self._write_lock = asyncio.Lock()

    # -- context manager --------------------------------------------------

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10, read=30, write=30, pool=10),
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- helpers ----------------------------------------------------------

    def _ensure_client(self):
        if self._client is None:
            raise RuntimeError("RouterController not entered as context manager")

    async def _get(self, path: str, **kwargs) -> httpx.Response:
        """GET with retry on connection errors."""
        self._ensure_client()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._client.get(path, **kwargs)
                return resp
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                if attempt == MAX_RETRIES:
                    raise
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning("GET %s failed (attempt %d/%d): %s — retrying in %ds",
                               path, attempt, MAX_RETRIES, e, wait)
                await asyncio.sleep(wait)

    async def _post(self, path: str, **kwargs) -> httpx.Response:
        """POST with retry on connection errors."""
        self._ensure_client()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._client.post(path, **kwargs)
                return resp
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                if attempt == MAX_RETRIES:
                    raise
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning("POST %s failed (attempt %d/%d): %s — retrying in %ds",
                               path, attempt, MAX_RETRIES, e, wait)
                await asyncio.sleep(wait)

    # -- authentication ---------------------------------------------------

    async def login(self) -> bool:
        """
        Full login flow:
          1. GET /login.cgi?out  — force clean session
          2. GET /login.cgi      — scrape nonce, token, pubkey
          3. POST /login.cgi     — encrypted credentials
        Returns True on success (HTTP 299).
        """
        logger.info("Logging in to router %s as %s", self.router_ip, self.username)

        # 1. Clean session
        await self._get("/login.cgi?out")

        # 2. Scrape login page
        resp = await self._get("/login.cgi")
        page = resp.text
        params = parse_login_page(page)
        self._nonce = params["nonce"]
        self._token = params["token"]
        self._modulus = params["modulus"]
        self._exponent = params.get("exponent", "10001")

        # 3. Build encrypted login payload
        plaintext = f"username={self.username}&password={self.password}&nonce={self._nonce}"
        enc = encrypt_payload(plaintext, self._modulus, self._exponent)
        enc["token"] = self._token

        # 4. POST login
        resp = await self._post("/login.cgi", data=enc)
        # Nokia returns 299 on success
        if resp.status_code == 299:
            self._logged_in = True
            logger.info("Login successful (HTTP %d)", resp.status_code)
            return True

        # Some firmwares return 200 with redirect or content check
        if "success" in resp.text.lower() or resp.status_code in (200, 302):
            self._logged_in = True
            logger.info("Login appears successful (HTTP %d)", resp.status_code)
            return True

        logger.error("Login failed: HTTP %d — body: %s", resp.status_code, resp.text[:300])
        self._logged_in = False
        return False

    async def _ensure_logged_in(self):
        if not self._logged_in:
            await self.login()

    async def _refresh_token(self):
        """Scrape a fresh CSRF token + RSA pubkey from a router page."""
        logger.debug("Refreshing CSRF token")
        resp = await self._get("/login.cgi")
        params = parse_login_page(resp.text)
        self._token = params["token"]
        self._nonce = params["nonce"]
        self._modulus = params["modulus"]
        self._exponent = params.get("exponent", "10001")

    # -- MAC filter -------------------------------------------------------

    async def get_mac_filter(self) -> list[dict]:
        """
        Read current MAC filter list from /macfilter.cgi.
        Returns list of filter entries (each a dict).
        """
        await self._ensure_logged_in()
        resp = await self._get("/macfilter.cgi")
        # Check for session expiry
        if resp.status_code in (302, 401) or "login" in resp.url.path.lower():
            self._logged_in = False
            await self.login()
            resp = await self._get("/macfilter.cgi")

        return parse_mac_filter(resp.text)

    async def get_blocked_macs(self) -> set[str]:
        """Return set of currently blocked MAC addresses (uppercase, colon-separated)."""
        filters = await self.get_mac_filter()
        blocked = set()
        for entry in filters:
            # action == 1 means deny (block)
            if entry.get("action") == 1 or entry.get("Action") == 1:
                mac = entry.get("mac", entry.get("Mac", "")).upper()
                if mac:
                    blocked.add(mac)
        return blocked

    async def get_allowed_macs(self) -> set[str]:
        """Return set of currently allowed (non-blocked) MAC addresses."""
        filters = await self.get_mac_filter()
        allowed = set()
        for entry in filters:
            action = entry.get("action", entry.get("Action", 0))
            if action == 0:
                mac = entry.get("mac", entry.get("Mac", "")).upper()
                if mac:
                    allowed.add(mac)
        return allowed

    async def block_mac(self, mac: str) -> bool:
        """Add a MAC address to the deny filter (block it)."""
        return await self._write_mac_filter(mac, action=1)

    async def unblock_mac(self, mac: str) -> bool:
        """Remove a MAC from the deny filter (allow it)."""
        return await self._write_mac_filter(mac, action=0)

    async def _write_mac_filter(self, mac: str, action: int) -> bool:
        """
        Write MAC filter entry.
        action: 1 = deny (block), 0 = allow (unblock)

        Uses a lock to serialize concurrent writes.
        """
        async with self._write_lock:
            return await self._do_write_mac(mac, action)

    async def _do_write_mac(self, mac: str, action: int, _relogin_attempts: int = 0) -> bool:
        await self._ensure_logged_in()

        mac_norm = mac.upper().replace("-", ":")
        action_label = "BLOCK" if action == 1 else "UNBLOCK"
        logger.info("%s MAC %s", action_label, mac_norm)

        # Fresh CSRF token + pubkey for each write
        await self._refresh_token()

        # Build plaintext payload — field names match router's form
        plaintext = (
            f"mac={mac_norm}"
            f"&ssid_index={self.ssid_index}"
            f"&action={action}"
            f"&token={self._token}"
        )

        enc = encrypt_payload(plaintext, self._modulus, self._exponent)
        enc["token"] = self._token

        # Write operations take 10-45 seconds (router re-applies WLAN)
        try:
            url = "/macfilter.cgi?add_wlan" if action == 1 else "/macfilter.cgi?act=del_wlan"
            resp = await self._post(url, data=enc, timeout=self.write_timeout)

            # Detect session expiry: 401/403 or redirect to login page
            if resp.status_code in (302, 401, 403) or "login" in resp.url.path.lower():
                if _relogin_attempts < 2:
                    logger.warning("%s MAC %s — session expired (HTTP %d), re-logging in (attempt %d/2)",
                                   action_label, mac_norm, resp.status_code, _relogin_attempts + 1)
                    self._logged_in = False
                    await self.login()
                    return await self._do_write_mac(mac, action, _relogin_attempts + 1)
                else:
                    logger.error("%s MAC %s — session expired after 2 re-login attempts, giving up",
                                 action_label, mac_norm)
                    return False

            logger.info("%s MAC %s — HTTP %d", action_label, mac_norm, resp.status_code)
            return True
        except httpx.ReadTimeout:
            # Read timeout on writes is expected — router takes a long time
            logger.info("%s MAC %s — read timeout (expected, assuming success)", action_label, mac_norm)
            return True
        except Exception as e:
            logger.error("%s MAC %s failed: %s", action_label, mac_norm, e)
            return False

    # -- connected devices ------------------------------------------------

    async def get_connected_devices(self) -> list[dict]:
        """
        Read connected device list from /parental_control.cgi.

        Returns list of dicts with keys: mac, ip, hostname, etc.
        Note: parental_control doesn't actually block on this firmware;
        we only use it to read the device list.
        """
        await self._ensure_logged_in()
        resp = await self._get("/parental_control.cgi")

        if resp.status_code in (302, 401) or "login" in resp.url.path.lower():
            self._logged_in = False
            await self.login()
            resp = await self._get("/parental_control.cgi")

        return parse_device_list(resp.text)

    # -- bulk operations --------------------------------------------------

    async def apply_filter(self, allowed_macs: set[str], blocked_macs: set[str]) -> dict:
        """
        Apply desired state: ensure allowed_macs are unblocked,
        blocked_macs are blocked.

        Returns dict with keys: blocked, unblocked, errors (lists of MACs).
        """
        current_blocked = await self.get_blocked_macs()

        results = {"blocked": [], "unblocked": [], "errors": []}

        # Unblock any allowed MAC that is currently blocked
        for mac in allowed_macs:
            if mac in current_blocked:
                ok = await self.unblock_mac(mac)
                if ok:
                    results["unblocked"].append(mac)
                else:
                    results["errors"].append(mac)

        # Block any MAC that should be blocked and isn't
        for mac in blocked_macs:
            if mac not in current_blocked:
                ok = await self.block_mac(mac)
                if ok:
                    results["blocked"].append(mac)
                else:
                    results["errors"].append(mac)

        return results

    # -- convenience ------------------------------------------------------

    async def status(self) -> dict:
        """Return current router status summary."""
        await self._ensure_logged_in()
        blocked = await self.get_blocked_macs()
        devices = await self.get_connected_devices()
        return {
            "logged_in": self._logged_in,
            "blocked_count": len(blocked),
            "blocked_macs": sorted(blocked),
            "connected_devices": len(devices),
            "devices": devices,
        }


# ---------------------------------------------------------------------------
# CLI quick test
# ---------------------------------------------------------------------------

async def _main():
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    async with RouterController() as rc:
        await rc.login()
        status = await rc.status()
        print("Status:", status)


if __name__ == "__main__":
    asyncio.run(_main())
