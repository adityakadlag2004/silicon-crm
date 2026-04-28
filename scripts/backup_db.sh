#!/usr/bin/env bash
# Daily Postgres backup. Designed to be invoked by cron.
#
# Behaviour:
#   1. Dumps DB to /var/backups/silicon/silicon-YYYY-MM-DD.sql.gz
#   2. Keeps last KEEP_LOCAL daily backups locally (default 14)
#   3. If GOOGLE_DRIVE_BACKUP_FOLDER_ID is set, uploads via the same
#      service-account JSON key used by the Drive folder feature.
#
# Setup on the server:
#   sudo mkdir -p /var/backups/silicon
#   sudo chown ubuntu:ubuntu /var/backups/silicon
#   crontab -e
#   # Add: 30 2 * * * /home/ubuntu/silicon-crm/scripts/backup_db.sh >> /var/log/silicon-backup.log 2>&1

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/silicon}"
KEEP_LOCAL="${KEEP_LOCAL:-14}"
TIMESTAMP="$(date +%Y-%m-%d)"

# Load only DB_* and GOOGLE_* env vars from .env (avoids shell-parsing the whole file,
# which breaks if SECRET_KEY contains special characters like parens).
if [ -f "$PROJECT_ROOT/.env" ]; then
  while IFS='=' read -r key value; do
    # Skip comments and blank lines
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    # Only export the keys we actually need
    if [[ "$key" =~ ^(DB_NAME|DB_USER|DB_PASSWORD|DB_HOST|DB_PORT|GOOGLE_DRIVE_BACKUP_FOLDER_ID|GOOGLE_APPLICATION_CREDENTIALS)$ ]]; then
      export "$key=$value"
    fi
  done < "$PROJECT_ROOT/.env"
fi

: "${DB_NAME:?DB_NAME not set}"
: "${DB_USER:?DB_USER not set}"

mkdir -p "$BACKUP_DIR"
OUTFILE="$BACKUP_DIR/silicon-${TIMESTAMP}.sql.gz"

echo "[$(date -Iseconds)] Backing up $DB_NAME → $OUTFILE"
PGPASSWORD="${DB_PASSWORD:-}" pg_dump \
  -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" \
  -U "$DB_USER" "$DB_NAME" \
  | gzip -9 > "$OUTFILE"

# Verify the dump is non-trivial
SIZE=$(stat -c%s "$OUTFILE" 2>/dev/null || stat -f%z "$OUTFILE")
if [ "$SIZE" -lt 1024 ]; then
  echo "[$(date -Iseconds)] ERROR: backup is suspiciously small ($SIZE bytes)" >&2
  exit 1
fi
echo "[$(date -Iseconds)] OK ($SIZE bytes)"

# Rotate — keep last KEEP_LOCAL files
ls -1t "$BACKUP_DIR"/silicon-*.sql.gz 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -v

# Optional: upload to Google Drive if configured
if [ -n "${GOOGLE_DRIVE_BACKUP_FOLDER_ID:-}" ] && [ -f "${GOOGLE_APPLICATION_CREDENTIALS:-/dev/null}" ]; then
  echo "[$(date -Iseconds)] Uploading to Drive folder $GOOGLE_DRIVE_BACKUP_FOLDER_ID"
  "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/upload_backup_to_drive.py" "$OUTFILE"
fi

echo "[$(date -Iseconds)] Done."
