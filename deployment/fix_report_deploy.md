# Deployment Fix Report

**Date:** 2026-07-17  
**Scope:** All files in `deployment/` directory

---

## Summary of Changes

| # | Severity | Issue | File | Fix Applied |
|---|----------|-------|------|-------------|
| 1 | 🔴 CRITICAL | Database schema mismatch (UUID-based vs integer-based) | `setup_db.sql` | Complete rewrite to match `models.py` |
| 2 | 🟡 MEDIUM | Apple CNA returns "Success" → iOS skips portal | `nginx.conf` | Changed `/hotspot-detect.html` from 200 to 302 redirect |
| 3 | 🟡 MEDIUM | Catch-all DNS breaks server's own resolution | `dnsmasq.conf` | Removed broken `server=` lines, added resolv.conf guidance |
| 4 | 🟢 LOW | Double compression in backup (pg_dump -Fc + gzip) | `backup.sh` | Changed to plain SQL dump + gzip |
| 5 | 🟢 LOW | No log rotation config | `logrotate-wifi-hotspot` | New file: daily rotation, 7 days, compressed |
| 6 | 🟢 LOW | Monitor missing <10% disk warning | `monitor.sh` | Added critical disk space check |
| 7 | 🟢 LOW | No deployment guide for fallback platform | `README.md` | Added Render + Neon.tech guide |
| 8 | — | Deploy script missing logrotate install | `deploy.sh` | Added logrotate config installation step |

---

## Detailed Changes

### 1. `setup_db.sql` — CRITICAL Rewrite

**Problem:** Schema used UUIDs, vouchers, sms_log, router_sync_log, audit_log tables. Flask app uses simple integer-based schema with `plans`, `users`, `payments`, `sessions`, `settings`.

**Before:**
- UUID primary keys with `uuid-ossp` extension
- Voucher-based session activation
- 8 plans (1hr through 30 days)
- Complex views joining sessions → vouchers → plans
- No `users` or `settings` tables

**After:**
- `SERIAL` integer primary keys (matches `models.py`)
- 5 tables: `plans`, `users`, `payments`, `sessions`, `settings`
- 4 plans: 1hr=20, 3hr=50, 1day=100, 1week=400 (KES)
- Default admin credentials in `settings` table (bcrypt hashed)
- Clean indexes on all foreign keys and lookup columns
- Views updated to match new schema

### 2. `nginx.conf` — Apple CNA Fix

**Problem:** iOS Captive Network Assistant (CNA) requests `/hotspot-detect.html` and receives `200 "Success"` — this tells iOS the network has internet access, so the portal is never shown.

**Fix:** Changed `/hotspot-detect.html` and `/library/test/success.html` from `return 200` to `return 302 http://$host/`. Also changed Windows `/ncsi.txt` and `/connecttest.txt` from 200 to 302 for consistency.

**Impact:** iOS devices will now see the captive portal when connecting to WiFi.

### 3. `dnsmasq.conf` — DNS Resolution Fix

**Problem:** Two issues:
1. `address=/#/{{SERVER_IP}}` catch-all redirects ALL DNS queries to server IP, which is correct for captive portal but...
2. `server=/127.0.0.1/8.8.8.8` and `server=/localhost/8.8.8.8` are invalid dnsmasq syntax and don't actually help
3. If `/etc/resolv.conf` points to `127.0.0.1` (common with systemd-resolved), the server's own DNS breaks

**Fix:**
- Removed broken `server=/127.0.0.1/` and `server=/localhost/` lines
- Added clear documentation that `/etc/resolv.conf` must use `8.8.8.8` (not `127.0.0.1`)
- Kept `address=/#/{{SERVER_IP}}` (essential for captive portal)
- Kept upstream `server=8.8.8.8` and `server=1.1.1.1`
- Note: `deploy.sh` already handles disabling systemd-resolved and setting resolv.conf

### 4. `backup.sh` — Double Compression Fix

**Problem:** `pg_dump -Fc` produces a custom-format archive that is already compressed internally. Piping it to `gzip` adds no benefit and makes the file harder to work with (double-compressed).

**Fix:** Changed to `pg_dump --no-owner --no-privileges` (plain SQL) piped to `gzip`. This gives:
- Standard SQL format (restorable with any PostgreSQL version)
- Single layer of gzip compression
- Smaller total file size

### 5. `logrotate-wifi-hotspot` — New File

**Problem:** No log rotation configured. Logs grow unbounded.

**Fix:** Created `/etc/logrotate.d/wifi-hotspot` with:
- Daily rotation
- 7 days retention
- Compression enabled
- Covers: `/var/log/wifi-hotspot/*.log`, `/var/log/dnsmasq.log`
- Post-rotation signal to dnsmasq to reopen log file

### 6. `monitor.sh` — Disk Space Warning

**Problem:** Only warned at 70% and 90% usage. No alert when disk is nearly full (< 10% free).

**Fix:** Added check for < 10% free disk space with CRITICAL severity.

### 7. `README.md` — Comprehensive Deployment Guide

**Problem:** Only covered Oracle Cloud deployment. No guidance for fallback platforms.

**Fix:** Complete rewrite with:
- **Option A: Oracle Cloud** — Full step-by-step (account creation, instance setup, security lists, upload, configure, deploy, verify)
- **Option B: Render + Neon.tech** — Fallback guide (Neon database setup, Render web service, environment variables, limitations)
- **First-run VIP setup** instructions
- **Admin credentials** documentation
- **Router configuration** for Nokia G-2425G-A
- **Troubleshooting** for iOS CNA, Android, DNS resolution issues

### 8. `deploy.sh` — Logrotate Installation

**Fix:** Added step to copy `logrotate-wifi-hotspot` to `/etc/logrotate.d/` during deployment.

---

## Files Modified

| File | Action |
|------|--------|
| `setup_db.sql` | **Rewritten** — new schema matching Flask app |
| `nginx.conf` | **Edited** — CNA detection URLs changed to 302 |
| `dnsmasq.conf` | **Rewritten** — removed broken lines, added docs |
| `backup.sh` | **Edited** — fixed double compression |
| `monitor.sh` | **Edited** — added <10% disk check |
| `deploy.sh` | **Edited** — added logrotate install |
| `README.md` | **Rewritten** — complete deployment guide |
| `logrotate-wifi-hotspot` | **New file** — log rotation config |

## Files Unchanged

| File | Reason |
|------|--------|
| `.env.template` | Already correct |
| `gunicorn.conf.py` | Already correct |
| `wifi-hotspot-api.service` | Already correct |
| `wifi-hotspot-scheduler.service` | Already correct |

---

## Verification Checklist

After deploying, verify:

- [ ] `psql -d wifi_hotspot -c "\dt"` shows 5 tables (plans, users, payments, sessions, settings)
- [ ] `psql -d wifi_hotspot -c "SELECT * FROM plans;"` shows 4 plans
- [ ] `curl -v http://<ip>/hotspot-detect.html` returns 302 (not 200)
- [ ] `cat /etc/resolv.conf` shows `nameserver 8.8.8.8` (not 127.0.0.1)
- [ ] `ls /etc/logrotate.d/wifi-hotspot` exists
- [ ] `bash monitor.sh` passes all checks
- [ ] iOS device shows captive portal when connecting to WiFi
- [ ] Android device shows captive portal when connecting to WiFi
