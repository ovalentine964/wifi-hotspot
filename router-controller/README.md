# Nokia G-2425G-A MAC Filter Controller

Automated WiFi hotspot MAC filter management for Nokia G-2425G-A (Airtel fiber ONT).

## Architecture

```
┌─────────────────┐    HTTP (encrypted)    ┌──────────────────┐
│  Oracle Cloud    │◄──────────────────────►│  Nokia Router    │
│  Whitelist API   │    poll every 30s      │  192.168.1.1     │
└────────┬─────────┘                        └──────────────────┘
         │
         ▼
┌─────────────────┐   AES-128-CBC + RSA    ┌──────────────────┐
│  Whitelist Mgr   │───────────────────────►│  Router Ctrl     │
│  (sync service)  │                        │  (login/filter)  │
└─────────────────┘                         └──────────────────┘
```

## Encryption

The router uses a dual-layer encryption scheme for all POST requests:

1. **AES-128-CBC** encrypts the payload with a random key+IV (PKCS7 padding)
2. **RSA-1024** encrypts the AES key+IV with the router's embedded public key
3. POST body: `encrypted=1&ct=<base64url(ciphertext)>&ck=<base64url(encrypted_key)>`

## Files

| File | Purpose |
|------|---------|
| `config.py` | Router & cloud API configuration |
| `nokia_crypto.py` | AES+RSA encryption layer |
| `nokia_parse.py` | HTML/JS parser for router responses |
| `router_ctrl.py` | Router controller (login, MAC filter, devices) |
| `whitelist_manager.py` | Whitelist sync service (cloud ↔ router) |

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Edit config
vi config.py  # Set router password, cloud API URL/key
```

## Usage

### Router Controller (library)

```python
import asyncio
from router_ctrl import RouterController

async def main():
    async with RouterController() as rc:
        await rc.login()

        # Read connected devices
        devices = await rc.get_connected_devices()
        for d in devices:
            print(f"{d.get('mac')} — {d.get('hostname')} ({d.get('ip')})")

        # Block a MAC
        await rc.block_mac("AA:BB:CC:DD:EE:FF")

        # Unblock a MAC
        await rc.unblock_mac("AA:BB:CC:DD:EE:FF")

        # Get status
        status = await rc.status()
        print(status)

asyncio.run(main())
```

### Whitelist Manager (service)

```bash
# Run as foreground service
python whitelist_manager.py

# Or with systemd
sudo cp whitelist-manager.service /etc/systemd/system/
sudo systemctl enable --now whitelist-manager
```

## Cloud API Contract

The whitelist manager expects your Oracle Cloud API to expose:

```
GET /api/v1/authorized_macs
Authorization: Bearer <api_key>

Response:
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
```

- `expires_at` in ISO 8601 (UTC). Null/missing = never expires.
- VIP MACs are always whitelisted regardless of expiry.

## Key Implementation Notes

- **Login success = HTTP 299** (not 200)
- **Write operations take 10-45 seconds** — the router re-applies WLAN config
- **Read timeouts on writes are expected** and treated as success
- **Sessions are IP-bound** — maintain the same source IP across requests
- **CSRF token must be refreshed** before each write operation
- **RSA public key** is embedded in the login page JavaScript
- **Parental Control page** is used only to read connected devices (it doesn't block on this firmware)

## Error Handling

| Error | Behavior |
|-------|----------|
| Session expired | Auto re-login on next request |
| Write timeout | Treated as success (router is slow) |
| CSRF expired | Fresh token scraped automatically |
| Network error | Exponential backoff (5s, 10s, 20s) |
| Cloud API down | Skip cycle, retry next poll |
| Concurrent writes | Serialized via asyncio.Lock |

## License

Internal use — WiFi hotspot business automation.
