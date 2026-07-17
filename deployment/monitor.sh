#!/usr/bin/env bash
###############################################################################
# WiFi Hotspot — Health Monitor
# Checks all services, connectivity, and resource usage
###############################################################################
set -euo pipefail

APP_NAME="wifi-hotspot"
APP_DIR="/opt/${APP_NAME}"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

# ─── Helpers ─────────────────────────────────────────────────────────────────
CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNED=0

check_pass() { echo -e "  ${GREEN}✓${NC} $*"; ((CHECKS_PASSED++)); }
check_fail() { echo -e "  ${RED}✗${NC} $*"; ((CHECKS_FAILED++)); }
check_warn() { echo -e "  ${YELLOW}⚠${NC} $*"; ((CHECKS_WARNED++)); }

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo -e "${CYAN}  WiFi Hotspot — Health Check${NC}"
echo -e "${CYAN}  $(date '+%Y-%m-%d %H:%M:%S %Z')${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"

###############################################################################
# 1. SYSTEMD SERVICES
###############################################################################
echo ""
echo -e "${CYAN}── Services ──${NC}"

for svc in "${APP_NAME}-api" "${APP_NAME}-scheduler" dnsmasq nginx postgresql; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        check_pass "$svc is running"
    else
        check_fail "$svc is NOT running"
    fi
done

###############################################################################
# 2. API HEALTH
###############################################################################
echo ""
echo -e "${CYAN}── API Health ──${NC}"

# Check if gunicorn is listening
if ss -tlnp | grep -q ':8000'; then
    check_pass "Gunicorn listening on :8000"
else
    check_fail "Gunicorn NOT listening on :8000"
fi

# HTTP health check
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 http://127.0.0.1:8000/health 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "200" ]]; then
    check_pass "API health endpoint returns 200"
elif [[ "$HTTP_CODE" == "000" ]]; then
    check_fail "API health endpoint unreachable"
else
    check_warn "API health endpoint returns HTTP $HTTP_CODE"
fi

###############################################################################
# 3. DATABASE
###############################################################################
echo ""
echo -e "${CYAN}── Database ──${NC}"

if sudo -u postgres psql -d wifi_hotspot -c "SELECT 1;" &>/dev/null; then
    check_pass "PostgreSQL connection OK"
else
    check_fail "PostgreSQL connection FAILED"
fi

# Active sessions count
if [[ -f "${APP_DIR}/.env" ]]; then
    DB_URL=$(grep '^DATABASE_URL=' "${APP_DIR}/.env" | cut -d= -f2-)
    if [[ -n "$DB_URL" ]]; then
        SESSION_COUNT=$(sudo -u postgres psql -d wifi_hotspot -t -c \
            "SELECT COUNT(*) FROM sessions WHERE is_active = TRUE AND expires_at > NOW();" 2>/dev/null | xargs || echo "?")
        check_pass "Active sessions: ${SESSION_COUNT}"
    fi
fi

###############################################################################
# 4. DNS (dnsmasq)
###############################################################################
echo ""
echo -e "${CYAN}── DNS Server ──${NC}"

if ss -ulnp | grep -q ':53'; then
    check_pass "dnsmasq listening on UDP :53"
else
    check_fail "dnsmasq NOT listening on UDP :53"
fi

# Test DNS resolution
DNS_RESULT=$(dig +short @127.0.0.1 example.com 2>/dev/null || echo "")
if [[ -n "$DNS_RESULT" ]]; then
    check_pass "DNS resolution working → ${DNS_RESULT}"
else
    check_warn "DNS resolution returned empty"
fi

###############################################################################
# 5. NGINX
###############################################################################
echo ""
echo -e "${CYAN}── Nginx ──${NC}"

if ss -tlnp | grep -q ':80'; then
    check_pass "Nginx listening on :80"
else
    check_fail "Nginx NOT listening on :80"
fi

if ss -tlnp | grep -q ':443'; then
    check_pass "Nginx listening on :443"
else
    check_warn "Nginx NOT listening on :443 (SSL not configured?)"
fi

NGINX_ERRORS=$(tail -5 /var/log/nginx/error.log 2>/dev/null | grep -c "error" || echo "0")
if [[ "$NGINX_ERRORS" -gt 0 ]]; then
    check_warn "Nginx has ${NGINX_ERRORS} recent errors in error.log"
fi

###############################################################################
# 6. ROUTER CONNECTIVITY
###############################################################################
echo ""
echo -e "${CYAN}── Router ──${NC}"

if [[ -f "${APP_DIR}/.env" ]]; then
    ROUTER_IP=$(grep '^ROUTER_IP=' "${APP_DIR}/.env" | cut -d= -f2)
    if ping -c 1 -W 3 "$ROUTER_IP" &>/dev/null; then
        check_pass "Router ${ROUTER_IP} is reachable"
    else
        check_fail "Router ${ROUTER_IP} is unreachable"
    fi
else
    check_warn ".env file not found — can't check router"
fi

###############################################################################
# 7. LAST ROUTER SYNC
###############################################################################
echo ""
echo -e "${CYAN}── Router Sync ──${NC}"

LAST_SYNC=$(sudo -u postgres psql -d wifi_hotspot -t -c \
    "SELECT TO_CHAR(MAX(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM router_sync_log;" 2>/dev/null | xargs || echo "")
if [[ -n "$LAST_SYNC" && "$LAST_SYNC" != "" ]]; then
    check_pass "Last router sync: ${LAST_SYNC}"
else
    check_warn "No router sync records found"
fi

###############################################################################
# 8. SMS MONITOR
###############################################################################
echo ""
echo -e "${CYAN}── SMS Monitor ──${NC}"

LAST_SMS=$(sudo -u postgres psql -d wifi_hotspot -t -c \
    "SELECT TO_CHAR(MAX(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM sms_log;" 2>/dev/null | xargs || echo "")
if [[ -n "$LAST_SMS" && "$LAST_SMS" != "" ]]; then
    check_pass "Last SMS received: ${LAST_SMS}"
else
    check_warn "No SMS records found"
fi

UNPROCESSED_SMS=$(sudo -u postgres psql -d wifi_hotspot -t -c \
    "SELECT COUNT(*) FROM sms_log WHERE processed = FALSE AND direction = 'in';" 2>/dev/null | xargs || echo "?")
if [[ "$UNPROCESSED_SMS" -gt 0 ]]; then
    check_warn "Unprocessed incoming SMS: ${UNPROCESSED_SMS}"
fi

###############################################################################
# 9. RESOURCES
###############################################################################
echo ""
echo -e "${CYAN}── System Resources ──${NC}"

# Disk
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | tr -d '%')
if [[ "$DISK_USAGE" -lt 70 ]]; then
    check_pass "Disk usage: ${DISK_USAGE}%"
elif [[ "$DISK_USAGE" -lt 90 ]]; then
    check_warn "Disk usage: ${DISK_USAGE}% (getting high)"
else
    check_fail "Disk usage: ${DISK_USAGE}% (CRITICAL)"
fi
# <10% free space warning
DISK_FREE_PCT=$((100 - DISK_USAGE))
if [[ "$DISK_FREE_PCT" -lt 10 ]]; then
    check_fail "Only ${DISK_FREE_PCT}% disk free — CRITICAL: disk nearly full!"
fi

# Memory
MEM_TOTAL=$(free -m | awk '/Mem:/ {print $2}')
MEM_USED=$(free -m | awk '/Mem:/ {print $3}')
MEM_PCT=$((MEM_USED * 100 / MEM_TOTAL))
if [[ "$MEM_PCT" -lt 70 ]]; then
    check_pass "Memory: ${MEM_USED}MB / ${MEM_TOTAL}MB (${MEM_PCT}%)"
elif [[ "$MEM_PCT" -lt 90 ]]; then
    check_warn "Memory: ${MEM_USED}MB / ${MEM_TOTAL}MB (${MEM_PCT}%)"
else
    check_fail "Memory: ${MEM_USED}MB / ${MEM_TOTAL}MB (${MEM_PCT}%) — LOW"
fi

# CPU load
LOAD=$(cat /proc/loadavg | awk '{print $1}')
CPUS=$(nproc)
check_pass "CPU load: ${LOAD} (${CPUS} cores)"

# Uptime
UPTIME=$(uptime -p 2>/dev/null || uptime)
check_pass "Uptime: ${UPTIME}"

###############################################################################
# 10. SSL CERTIFICATE
###############################################################################
echo ""
echo -e "${CYAN}── SSL ──${NC}"

if [[ -f /etc/letsencrypt/live/*/fullchain.pem ]]; then
    CERT_EXPIRY=$(openssl x509 -enddate -noout -in /etc/letsencrypt/live/*/fullchain.pem 2>/dev/null | cut -d= -f2)
    if [[ -n "$CERT_EXPIRY" ]]; then
        DAYS_LEFT=$(( ( $(date -d "$CERT_EXPIRY" +%s) - $(date +%s) ) / 86400 ))
        if [[ "$DAYS_LEFT" -gt 14 ]]; then
            check_pass "SSL cert expires in ${DAYS_LEFT} days"
        elif [[ "$DAYS_LEFT" -gt 0 ]]; then
            check_warn "SSL cert expires in ${DAYS_LEFT} days — renew soon!"
        else
            check_fail "SSL cert EXPIRED"
        fi
    fi
else
    check_warn "No Let's Encrypt certificate found (using self-signed or no SSL)"
fi

###############################################################################
# SUMMARY
###############################################################################
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo -e "  ${GREEN}Passed: ${CHECKS_PASSED}${NC}  ${YELLOW}Warnings: ${CHECKS_WARNED}${NC}  ${RED}Failed: ${CHECKS_FAILED}${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo ""

if [[ "$CHECKS_FAILED" -gt 0 ]]; then
    exit 1
fi
exit 0
