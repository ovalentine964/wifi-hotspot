# WiFi Hotspot API

Flask API server for a WiFi hotspot business. Manages M-Pesa payments, MAC-based access control, and Nokia router integration.

## Architecture

```
Phone SMS Monitor  ──POST /api/confirm──►  Flask API  ◄──POST /api/router/whitelist──  Scheduler (30s)
        │                                       │
        │                                       ├── PostgreSQL (users, sessions, payments)
        │                                       │
        │                                       └── Nokia G-2425G-A Router (MAC whitelist)
        │
User Portal  ──POST /api/register──►  Flask API
```

## Quick Start

### 1. Install dependencies

```bash
cd wifi-hotspot-api
pip install -r requirements.txt
```

### 2. Set environment variables

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=wifi_hotspot
export DB_USER=postgres
export DB_PASS=yourpassword
export API_KEY=your-secret-api-key
export ADMIN_USER=admin
export ADMIN_PASS=secure-password
export ROUTER_HOST=192.168.1.1
export ROUTER_USER=admin
export ROUTER_PASS=router-password
```

### 3. Create PostgreSQL database

```sql
CREATE DATABASE wifi_hotspot;
```

Tables are created automatically on first run.

### 4. Run

```bash
python app.py
# or for production:
gunicorn app:create_app() --bind 0.0.0.0:5000 --workers 2 --threads 4
```

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/` | None | Portal page |
| GET | `/api/plans` | None | List available plans |
| POST | `/api/register` | API Key | Register for a plan |
| POST | `/api/confirm` | API Key | Confirm M-Pesa payment |
| GET | `/api/authorized` | API Key | List authorized MACs |
| GET | `/api/status/:mac` | API Key | Check MAC status |
| POST | `/api/revoke` | Admin | Revoke user access |
| GET | `/api/admin/stats` | Admin | Dashboard stats |
| POST | `/api/admin/vip` | Admin | Add VIP user |
| GET | `/api/admin/vip` | Admin | List VIP users |
| POST | `/api/admin/vip/remove` | Admin | Remove VIP |
| POST | `/api/router/whitelist` | API Key | Sync whitelist to router |

## Authentication

- **API Key**: `X-API-Key` header (for SMS monitor ↔ cloud communication)
- **Admin**: HTTP Basic Auth (username/password from settings table)

## Default Plans

| Plan | Price (KSh) | Duration |
|------|-------------|----------|
| 1 Hour | 20 | 1 hour |
| 3 Hours | 50 | 3 hours |
| 1 Day | 100 | 24 hours |
| 1 Week | 400 | 7 days |

## Payment Flow

1. User connects to WiFi, sees captive portal
2. Selects plan, enters phone number
3. `POST /api/register` creates pending payment
4. User receives M-Pesa prompt on phone
5. SMS monitor detects confirmation SMS
6. `POST /api/confirm` with phone + amount + mpesa_code
7. Server creates/extends session, returns MAC
8. Router sync (every 30s) adds MAC to whitelist
9. User has internet access

## Scheduler

Runs every 30 seconds (configurable via `EXPIRE_INTERVAL`):
- Expires sessions past their `expires_at` time
- Syncs authorized MAC list to Nokia router

## File Structure

```
wifi-hotspot-api/
├── app.py              # Main Flask app + entry point
├── config.py           # Configuration from env vars
├── database.py         # DB connection, migrations, seeding
├── models.py           # SQLAlchemy ORM models
├── routes/
│   ├── portal.py       # Portal page routes
│   ├── api.py          # Core API endpoints
│   ├── admin.py        # Admin panel routes
│   └── router.py       # Router control routes
├── services/
│   ├── payment.py      # Payment processing logic
│   ├── router_ctrl.py  # Nokia router MAC filter control
│   └── scheduler.py    # Auto-expire + router sync
├── templates/
│   ├── portal.html     # User-facing captive portal
│   └── admin.html      # Admin dashboard
├── static/
│   └── style.css       # Styles
├── requirements.txt
├── Procfile
└── README.md
```
