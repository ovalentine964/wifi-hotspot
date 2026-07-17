"""Nokia G-2425G-A router MAC filter control service.

Uses the router's HTTP admin API to manage the MAC address whitelist.
Falls back gracefully when router is unreachable — never removes MACs on failure.
"""
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

# Router API endpoints (Nokia G-2425G-A)
BASE_URL = f"http://{Config.ROUTER_HOST}"
LOGIN_URL = f"{BASE_URL}/login"
MAC_FILTER_URL = f"{BASE_URL}/cgi-bin/macfilter"

session = requests.Session()

# Last-known-good authorized MAC list — preserved when router is unreachable
_last_known_good: set = set()


def _login() -> bool:
    """Authenticate with the router."""
    try:
        resp = session.post(
            LOGIN_URL,
            data={"username": Config.ROUTER_USER, "password": Config.ROUTER_PASS},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Router login failed: {e}")
        return False


def get_current_whitelist() -> list:
    """Fetch the current MAC whitelist from the router."""
    try:
        if not _login():
            logger.warning("Router unreachable — returning last-known-good whitelist")
            return list(_last_known_good)
        resp = session.get(MAC_FILTER_URL, timeout=10)
        macs = []
        if resp.status_code == 200:
            for line in resp.text.split("\n"):
                line = line.strip()
                if ":" in line and len(line) == 17:
                    macs.append(line.upper())
        return macs
    except Exception as e:
        logger.error(f"Failed to get whitelist: {e}")
        return list(_last_known_good)


def apply_whitelist(authorized_macs: list) -> dict:
    """Apply a MAC whitelist to the router.

    Safety rules:
    - If router is unreachable, SKIP removal — never block active users.
    - Only add new MACs when router is reachable.
    - Preserve last-known-good authorized MAC list on failure.
    """
    global _last_known_good

    desired = set(m.upper() for m in authorized_macs)

    try:
        if not _login():
            logger.warning("Router unreachable — skipping whitelist sync. Preserving last-known-good list.")
            return {
                "success": True,
                "skipped": True,
                "reason": "Router unreachable",
                "preserved_count": len(_last_known_good),
            }

        current = set(m.upper() for m in get_current_whitelist())

        to_add = desired - current
        # NEVER remove MACs — only add. Removal happens via session expiry + next successful sync.
        # to_remove is intentionally not computed here.

        added = 0

        for mac in to_add:
            try:
                resp = session.post(
                    MAC_FILTER_URL,
                    data={"action": "add", "mac": mac},
                    timeout=10,
                )
                if resp.status_code == 200:
                    added += 1
                    logger.info(f"Added MAC to router: {mac}")
            except Exception as e:
                logger.error(f"Failed to add MAC {mac}: {e}")

        # Update last-known-good on success
        _last_known_good = desired.copy()

        return {
            "success": True,
            "added": added,
            "removed": 0,  # Never remove — safety first
            "current_count": len(desired),
        }
    except Exception as e:
        logger.exception("Error applying whitelist — preserving last-known-good")
        return {"success": False, "error": str(e), "preserved_count": len(_last_known_good)}


def force_remove_mac(mac: str) -> dict:
    """Remove a specific MAC from the router whitelist.

    Only called explicitly (e.g., VIP removal). Logs warning if router unreachable.
    """
    mac = mac.upper()
    try:
        if not _login():
            logger.warning(f"Router unreachable — cannot remove MAC {mac}. Will retry on next sync.")
            return {"success": False, "error": "Router unreachable", "retry_later": True}

        resp = session.post(
            MAC_FILTER_URL,
            data={"action": "delete", "mac": mac},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"Force-removed MAC from router: {mac}")
            return {"success": True, "removed": mac}
        return {"success": False, "error": f"Router returned status {resp.status_code}"}
    except Exception as e:
        logger.error(f"Failed to remove MAC {mac}: {e}")
        return {"success": False, "error": str(e), "retry_later": True}
