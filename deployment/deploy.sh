#!/usr/bin/env bash
###############################################################################
# WiFi Hotspot Business — Full Deployment Script
# Target: Oracle Cloud Always Free ARM (2 OCPUs, 12 GB RAM, 200 GB)
# Region: af-johannesburg-1
###############################################################################
set -euo pipefail
IFS=$'\n\t'

# ─── Constants ───────────────────────────────────────────────────────────────
APP_NAME="wifi-hotspot"
APP_USER="hotspot"
APP_DIR="/opt/${APP_NAME}"
VENV_DIR="${APP_DIR}/venv"
DB_NAME="wifi_hotspot"
DB_USER="hotspot_user"
DB_PASS="$(openssl rand -hex 16)"
SECRET_KEY="$(openssl rand -hex 32)"
API_KEY="$(openssl rand -hex 24)"
LOG_FILE="/var/log/${APP_NAME}-deploy.log"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

# ─── Helpers ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

log()  { echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $*" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" | tee -a "$LOG_FILE"; exit 1; }

rollback() {
    warn "Rolling back…"
    systemctl stop "${APP_NAME}-api" "${APP_NAME}-scheduler" 2>/dev/null || true
    systemctl disable "${APP_NAME}-api" "${APP_NAME}-scheduler" 2>/dev/null || true
    rm -f "/etc/systemd/system/${APP_NAME}-"*".service"
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS ${DB_NAME};" 2>/dev/null || true
    sudo -u postgres psql -c "DROP USER IF EXISTS ${DB_USER};" 2>/dev/null || true
    rm -rf "$APP_DIR"
    userdel -r "$APP_USER" 2>/dev/null || true
    systemctl daemon-reload
    err "Deployment failed — rolled back."
}

trap rollback ERR

# ─── Pre-flight checks ──────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || err "Must run as root."
[[ -f "${DEPLOY_DIR}/setup_db.sql" ]] || err "setup_db.sql not found in ${DEPLOY_DIR}"

log "=== WiFi Hotspot Deployment — Starting ==="
log "APP_DIR=${APP_DIR}  DB_USER=${DB_USER}"

###############################################################################
# 1. SYSTEM PACKAGES
###############################################################################
log "── [1/7] System packages ──"
apt-get update -y
apt-get upgrade -y
apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    postgresql postgresql-contrib \
    nginx \
    dnsmasq \
    certbot python3-certbot-nginx \
    fail2ban \
    iptables-persistent \
    curl wget git htop ufw \
    build-essential libpq-dev

# Python 3.11+ check
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
log "Python version: ${PY_VER}"

###############################################################################
# 2. FIREWALL
###############################################################################
log "── [2/7] Firewall (iptables) ──"
# Flush existing rules
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X

# Default policies
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# Loopback
iptables -A INPUT -i lo -j ACCEPT

# Established connections
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# SSH
iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# HTTP / HTTPS
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT

# DNS (captive portal)
iptables -A INPUT -p tcp --dport 53 -j ACCEPT
iptables -A INPUT -p udp --dport 53 -j ACCEPT

# ICMP (ping)
iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT

# Rate-limit SSH (anti brute-force)
iptables -A INPUT -p tcp --dport 22 -m recent --set --name SSH
iptables -A INPUT -p tcp --dport 22 -m recent --update --seconds 60 --hitcount 4 --name SSH -j DROP

# Log dropped
iptables -A INPUT -m limit --limit 5/min -j LOG --log-prefix "iptables-dropped: " --log-level 4

# Save rules
netfilter-persistent save

###############################################################################
# 3. FAIL2BAN
###############################################################################
log "── [3/7] Fail2ban ──"
cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5
backend  = systemd

[sshd]
enabled = true
port    = ssh
filter  = sshd
logpath = /var/log/auth.log
maxretry = 3
EOF
systemctl enable fail2ban
systemctl restart fail2ban

###############################################################################
# 4. POSTGRESQL
###############################################################################
log "── [4/7] PostgreSQL ──"
systemctl enable postgresql
systemctl start postgresql

# Create user & database
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"

# Run schema + seed
log "Running schema migrations…"
sudo -u postgres psql -d "${DB_NAME}" -f "${DEPLOY_DIR}/setup_db.sql"

# Lock down: localhost only
PG_HBA=$(sudo -u postgres psql -t -c "SHOW hba_file;" | xargs)
cp "$PG_HBA" "${PG_HBA}.bak"
cat > "$PG_HBA" <<EOF
# TYPE  DATABASE        USER            ADDRESS         METHOD
local   all             postgres                        peer
local   ${DB_NAME}      ${DB_USER}                      md5
host    ${DB_NAME}      ${DB_USER}      127.0.0.1/32    md5
host    ${DB_NAME}      ${DB_USER}      ::1/128         md5
EOF
systemctl restart postgresql

###############################################################################
# 5. APPLICATION
###############################################################################
log "── [5/7] Flask application ──"

# Create system user
id "$APP_USER" &>/dev/null || useradd -r -m -d "/home/${APP_USER}" -s /bin/bash "$APP_USER"

# App directory
mkdir -p "$APP_DIR"
cp -r "${DEPLOY_DIR}/../wifi-hotspot-api/"* "$APP_DIR/" 2>/dev/null || true  # copy app source only
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"

# Virtual environment
sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
sudo -u "$APP_USER" "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel
[[ -f "${APP_DIR}/requirements.txt" ]] && \
    sudo -u "$APP_USER" "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"

# .env file
cat > "${APP_DIR}/.env" <<EOF
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASS=${DB_PASS}
SECRET_KEY=${SECRET_KEY}
API_KEY=${API_KEY}
ROUTER_HOST=192.168.1.1
ROUTER_USER=admin
ROUTER_PASS=changeme
ADMIN_USER=admin
ADMIN_PASS=admin123
FLASK_DEBUG=false
EXPIRE_INTERVAL=30
DOMAIN=$(curl -s ifconfig.me 2>/dev/null || echo "your-oracle-cloud-ip")
EOF
chmod 600 "${APP_DIR}/.env"
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"

# Gunicorn config
cp "${DEPLOY_DIR}/gunicorn.conf.py" "${APP_DIR}/gunicorn.conf.py"
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/gunicorn.conf.py"

# Systemd — API service
cat > "/etc/systemd/system/${APP_NAME}-api.service" <<EOF
[Unit]
Description=WiFi Hotspot API (Gunicorn)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/gunicorn -c gunicorn.conf.py wsgi:app
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${APP_NAME}-api

[Install]
WantedBy=multi-user.target
EOF

# Systemd — Scheduler service
cat > "/etc/systemd/system/${APP_NAME}-scheduler.service" <<EOF
[Unit]
Description=WiFi Hotspot Scheduler (auto-expire + router sync)
After=network.target postgresql.service ${APP_NAME}-api.service

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/python scheduler.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${APP_NAME}-scheduler

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${APP_NAME}-api" "${APP_NAME}-scheduler"
systemctl start "${APP_NAME}-api" "${APP_NAME}-scheduler"

###############################################################################
# 6. NGINX
###############################################################################
log "── [6/7] Nginx ──"
cp "${DEPLOY_DIR}/nginx.conf" "/etc/nginx/sites-available/${APP_NAME}"
ln -sf "/etc/nginx/sites-available/${APP_NAME}" "/etc/nginx/sites-enabled/${APP_NAME}"
rm -f /etc/nginx/sites-enabled/default

# Test and reload
nginx -t || err "Nginx config test failed"
systemctl enable nginx
systemctl restart nginx

###############################################################################
# 7. DNSMASQ (Captive Portal)
###############################################################################
log "── [7/7] dnsmasq (captive portal) ──"

# Get server IP
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "127.0.0.1")

# Generate dnsmasq config with actual IP
sed "s/{{SERVER_IP}}/${SERVER_IP}/g" "${DEPLOY_DIR}/dnsmasq.conf" > /etc/dnsmasq.d/${APP_NAME}.conf

# Disable systemd-resolved if it conflicts on port 53
if systemctl is-active --quiet systemd-resolved; then
    systemctl stop systemd-resolved
    systemctl disable systemd-resolved
    rm -f /etc/resolv.conf
    echo "nameserver 8.8.8.8" > /etc/resolv.conf
fi

systemctl enable dnsmasq
systemctl restart dnsmasq

###############################################################################
# LOG ROTATION
###############################################################################
log "── Installing logrotate config ──"
cp "${DEPLOY_DIR}/logrotate-wifi-hotspot" /etc/logrotate.d/wifi-hotspot
chmod 644 /etc/logrotate.d/wifi-hotspot
mkdir -p /var/log/wifi-hotspot
chown root:adm /var/log/wifi-hotspot
log "Logrotate config installed"

###############################################################################
# SSL (Let's Encrypt)
###############################################################################
DOMAIN_OR_IP=$(grep '^DOMAIN=' "${APP_DIR}/.env" | cut -d= -f2)
if [[ "$DOMAIN_OR_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    warn "No domain — skipping Let's Encrypt. Use self-signed cert or set up a domain."
    # Generate self-signed cert as placeholder
    mkdir -p /etc/ssl/private
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "/etc/ssl/private/${APP_NAME}.key" \
        -out "/etc/ssl/certs/${APP_NAME}.crt" \
        -subj "/CN=${DOMAIN_OR_IP}" 2>/dev/null
else
    log "Obtaining SSL certificate for ${DOMAIN_OR_IP}…"
    certbot --nginx -d "$DOMAIN_OR_IP" --non-interactive --agree-tos \
        --email "admin@${DOMAIN_OR_IP}" --redirect || warn "Certbot failed — continuing with self-signed"
    # Auto-renewal cron
    echo "0 3 * * * root certbot renew --quiet --post-hook 'systemctl reload nginx'" \
        > /etc/cron.d/certbot-renew
fi

###############################################################################
# DEPLOYMENT SUMMARY
###############################################################################
log "=========================================="
log "  ✅ DEPLOYMENT COMPLETE"
log "=========================================="
log ""
log "  App directory : ${APP_DIR}"
log "  Database      : ${DB_NAME}"
log "  DB User       : ${DB_USER}"
log "  DB Password   : ${DB_PASS}"
log "  Secret Key    : ${SECRET_KEY}"
log "  API Key       : ${API_KEY}"
log "  Server IP     : ${SERVER_IP}"
log ""
log "  ⚠️  SAVE THESE CREDENTIALS — they won't be shown again!"
log ""
log "  Services:"
log "    systemctl status ${APP_NAME}-api"
log "    systemctl status ${APP_NAME}-scheduler"
log "    systemctl status dnsmasq"
log "    systemctl status nginx"
log ""
log "  Logs:"
log "    journalctl -u ${APP_NAME}-api -f"
log "    journalctl -u ${APP_NAME}-scheduler -f"
log ""
log "  Health check:"
log "    bash ${DEPLOY_DIR}/monitor.sh"
log "=========================================="

# Save credentials securely
CREDS_FILE="/root/.${APP_NAME}-credentials"
cat > "$CREDS_FILE" <<EOF
Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
DB_USER=${DB_USER}
DB_PASS=${DB_PASS}
SECRET_KEY=${SECRET_KEY}
API_KEY=${API_KEY}
SERVER_IP=${SERVER_IP}
EOF
chmod 600 "$CREDS_FILE"
log "Credentials saved to ${CREDS_FILE}"
