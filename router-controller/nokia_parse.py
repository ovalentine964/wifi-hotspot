"""
HTML / JavaScript parser for Nokia G-2425G-A router responses.

Parses:
  - Login page: nonce, CSRF token, RSA public key (modulus + exponent)
  - macfilter.cgi: wlan_mac_filter JavaScript variable
  - parental_control.cgi: device_cfg JavaScript variable
"""

import re
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_login_page(html: str) -> dict:
    """
    Extract authentication parameters from the login page.

    Returns dict with keys:
      nonce: str
      token: CSRF token
      modulus: RSA modulus hex string
      exponent: RSA exponent hex string
    """
    result = {}

    # Nonce — typically in a hidden input or JS variable
    m = re.search(r'name="nonce"\s+value="([^"]+)"', html)
    if not m:
        m = re.search(r"nonce\s*[:=]\s*['\"]([^'\"]+)", html)
    if m:
        result["nonce"] = m.group(1)
    else:
        raise ValueError("Cannot find nonce in login page")

    # CSRF token
    m = re.search(r'name="token"\s+value="([^"]+)"', html)
    if not m:
        m = re.search(r"token\s*[:=]\s*['\"]([^'\"]+)", html)
    if m:
        result["token"] = m.group(1)
    else:
        raise ValueError("Cannot find CSRF token in login page")

    # RSA public key — modulus and exponent, often as JS variables
    # Common patterns: var modulus = "xxxx"; var exponent = "xxxx";
    m = re.search(r'(?:modulus|rsa_n)\s*[:=]\s*["\']([0-9a-fA-F]+)', html)
    if m:
        result["modulus"] = m.group(1)

    m = re.search(r'(?:exponent|rsa_e)\s*[:=]\s*["\']([0-9a-fA-F]+)', html)
    if m:
        result["exponent"] = m.group(1)

    if "modulus" not in result or "exponent" not in result:
        # Try alternate: single "pubkey" variable with modulus exponent separated
        m = re.search(r'pubkey\s*[:=]\s*["\']([0-9a-fA-F]+)', html)
        if m:
            # Some firmware puts modulus only; exponent is usually 10001
            result["modulus"] = m.group(1)
            result.setdefault("exponent", "10001")
        else:
            raise ValueError("Cannot find RSA public key in login page")

    logger.info("Parsed login page: nonce=%s…, token=%s…", result["nonce"][:8], result["token"][:8])
    return result


def parse_js_var(html: str, var_name: str) -> Any:
    """
    Extract a JavaScript variable assignment from HTML.

    Supports patterns like:
      var wlan_mac_filter = [ ... ];
      var device_cfg = { ... };
    Returns parsed Python object (list/dict).
    """
    # Match: var varname = <value>;
    pattern = rf"var\s+{re.escape(var_name)}\s*=\s*"
    m = re.search(pattern, html)
    if not m:
        raise ValueError(f"JavaScript variable '{var_name}' not found in page")

    start = m.end()
    # Find matching bracket or semicolon
    if start >= len(html):
        raise ValueError(f"Variable '{var_name}' has no value")

    # If starts with [ or { find matching closer
    if html[start] in "[{":
        opener = html[start]
        closer = "]" if opener == "[" else "}"
        depth = 0
        end = start
        for i in range(start, len(html)):
            if html[i] == opener:
                depth += 1
            elif html[i] == closer:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        js_value = html[start:end]
    else:
        # Simple value until semicolon
        end = html.find(";", start)
        if end == -1:
            end = len(html)
        js_value = html[start:end]

    # Try parsing as JSON (most router JS is JSON-compatible)
    try:
        return json.loads(js_value)
    except json.JSONDecodeError:
        # Fix common JS quirks: single quotes, trailing commas, unquoted keys
        fixed = js_value.replace("'", '"')
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # trailing commas
        fixed = re.sub(r"(\w+)\s*:", r'"\1":', fixed)  # unquoted keys
        # Avoid double-quoting already quoted keys
        fixed = fixed.replace('""', '"')
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            logger.error("Failed to parse JS variable %s: %s", var_name, js_value[:200])
            raise ValueError(f"Cannot parse JS variable '{var_name}' as JSON")


def parse_mac_filter(html: str) -> list[dict]:
    """
    Parse wlan_mac_filter from macfilter.cgi.

    Returns list of dicts, each with keys like:
      mac, hostname, ssid_index, action (1=deny, 0=allow), etc.
    """
    data = parse_js_var(html, "wlan_mac_filter")
    if isinstance(data, list):
        return data
    return []


def parse_device_list(html: str) -> list[dict]:
    """
    Parse device_cfg from parental_control.cgi.

    Returns list of dicts with keys: mac, ip, hostname, etc.
    """
    data = parse_js_var(html, "device_cfg")
    if isinstance(data, list):
        return data
    return []


def parse_csrf_token(html: str) -> str:
    """
    Extract a fresh CSRF token from any router page.
    """
    m = re.search(r'name="token"\s+value="([^"]+)"', html)
    if not m:
        m = re.search(r"token\s*[:=]\s*['\"]([^'\"]+)", html)
    if not m:
        raise ValueError("Cannot find CSRF token")
    return m.group(1)
