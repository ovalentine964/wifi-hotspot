#!/usr/bin/env bash
###############################################################################
# WiFi Hotspot — Database Backup Script
# Backs up PostgreSQL, rotates old backups, optional Oracle Object Storage upload
###############################################################################
set -euo pipefail

APP_NAME="wifi-hotspot"
DB_NAME="wifi_hotspot"
DB_USER="hotspot_user"
BACKUP_DIR="/opt/${APP_NAME}/backups"
RETENTION_DAYS=7
LOG_FILE="/var/log/${APP_NAME}-backup.log"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql.gz"
# NOTE: Using plain SQL dump + gzip (NOT pg_dump -Fc which already compresses)

# Oracle Object Storage (optional — set these to enable upload)
OCI_BUCKET=""
OCI_NAMESPACE=""
OCI_REGION=""

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# ─── Create backup directory ────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# ─── Backup ──────────────────────────────────────────────────────────────────
log "Starting backup of ${DB_NAME}…"

# Use plain SQL dump (pg_dump without -Fc) then gzip for compression
# pg_dump -Fc already compresses internally, so piping to gzip is redundant
sudo -u postgres pg_dump --no-owner --no-privileges "$DB_NAME" | gzip > "$BACKUP_FILE"

BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
log "Backup complete: ${BACKUP_FILE} (${BACKUP_SIZE})"

# ─── Verify backup ──────────────────────────────────────────────────────────
if [[ ! -s "$BACKUP_FILE" ]]; then
    log "ERROR: Backup file is empty!"
    exit 1
fi

# Quick integrity check — verify gzip is valid
if gzip -t "$BACKUP_FILE" 2>/dev/null; then
    log "Backup integrity check: PASSED"
else
    log "ERROR: Backup integrity check FAILED"
    exit 1
fi

# ─── Rotate old backups ─────────────────────────────────────────────────────
log "Rotating backups older than ${RETENTION_DAYS} days…"
DELETED=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
log "Deleted ${DELETED} old backup(s)"

# Show remaining backups
log "Current backups:"
ls -lh "${BACKUP_DIR}/${DB_NAME}_"*.sql.gz 2>/dev/null | while read -r line; do
    log "  ${line}"
done

# ─── Optional: Upload to Oracle Object Storage ──────────────────────────────
if [[ -n "$OCI_BUCKET" && -n "$OCI_NAMESPACE" ]]; then
    if command -v oci &>/dev/null; then
        log "Uploading to Oracle Object Storage: ${OCI_BUCKET}…"
        oci os object put \
            --bucket-name "$OCI_BUCKET" \
            --namespace "$OCI_NAMESPACE" \
            --region "$OCI_REGION" \
            --file "$BACKUP_FILE" \
            --name "backups/$(basename "$BACKUP_FILE")" \
            --no-multipart && \
            log "Upload complete" || \
            log "WARNING: Upload failed"
    else
        log "WARNING: oci CLI not installed — skipping upload"
    fi
else
    log "Object Storage not configured — skipping upload"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
TOTAL_BACKUPS=$(ls -1 "${BACKUP_DIR}/${DB_NAME}_"*.sql.gz 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
log "Backup summary: ${TOTAL_BACKUPS} backup(s), ${TOTAL_SIZE} total"
log "Done."
