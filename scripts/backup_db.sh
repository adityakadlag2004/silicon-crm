#!/usr/bin/env bash
# Daily Postgres backup. Designed to be invoked by cron.
#
# Behaviour:
#   1. Dumps DB to $BACKUP_DIR/silicon-YYYY-MM-DD.sql.gz
#   2. Keeps the last KEEP_LOCAL files locally (default 7).
#   3. If BACKUP_RCLONE_REMOTE is set, mirrors $BACKUP_DIR to that remote
#      via `rclone sync` so the remote retains the same set of files.
#
# Setup on the server:
#   sudo mkdir -p /var/backups/silicon
#   sudo chown ubuntu:ubuntu /var/backups/silicon
#   crontab -e
#   # Add: 30 2 * * * /home/ubuntu/silicon-crm/scripts/backup_db.sh >> /var/log/silicon-backup.log 2>&1

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/silicon}"
KEEP_LOCAL="${KEEP_LOCAL:-7}"

# Load only DB_* and BACKUP_* env vars from .env (avoids shell-parsing the whole
# file, which breaks if SECRET_KEY contains special characters like parens).
if [ -f "$PROJECT_ROOT/.env" ]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    if [[ "$key" =~ ^(DB_NAME|DB_USER|DB_PASSWORD|DB_HOST|DB_PORT|BACKUP_RCLONE_REMOTE)$ ]]; then
      export "$key=$value"
    fi
  done < "$PROJECT_ROOT/.env"
fi

: "${DB_NAME:?DB_NAME not set}"
: "${DB_USER:?DB_USER not set}"

mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date +%Y-%m-%d)"
OUTFILE="$BACKUP_DIR/silicon-${TIMESTAMP}.sql.gz"
TMPFILE="$OUTFILE.tmp"

echo "[$(date -Iseconds)] Backing up $DB_NAME → $OUTFILE"

# Dump to a temp file first, then atomically rename. If pg_dump fails midway,
# previous date-stamped backups stay intact.
PGPASSWORD="${DB_PASSWORD:-}" pg_dump \
  -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" \
  -U "$DB_USER" "$DB_NAME" \
  | gzip -9 > "$TMPFILE"

# Verify the dump is non-trivial before promoting it
SIZE=$(stat -c%s "$TMPFILE" 2>/dev/null || stat -f%z "$TMPFILE")
if [ "$SIZE" -lt 1024 ]; then
  echo "[$(date -Iseconds)] ERROR: backup is suspiciously small ($SIZE bytes) — keeping previous" >&2
  rm -f "$TMPFILE"
  exit 1
fi

mv -f "$TMPFILE" "$OUTFILE"
echo "[$(date -Iseconds)] OK ($SIZE bytes)"

# Rotate — keep last KEEP_LOCAL date-stamped files; delete older ones.
# `|| true` so a missing-files exit status doesn't trip set -e/pipefail.
( ls -1t "$BACKUP_DIR"/silicon-2[0-9][0-9][0-9]-*.sql.gz 2>/dev/null || true ) \
  | tail -n +$((KEEP_LOCAL + 1)) \
  | xargs -r rm -v || true
# Also clean up any leftover legacy "silicon-latest.sql.gz" from the
# single-file era, so the remote sync doesn't keep it indefinitely.
rm -f "$BACKUP_DIR/silicon-latest.sql.gz"

# Mirror $BACKUP_DIR → remote via rclone if configured.
# Using `sync` (not `copy`) so older files in the remote are deleted to match
# the local retention — keeps Drive storage bounded.
#
# Direct Drive API upload is intentionally NOT used: service accounts have no
# storage quota in personal Gmail Drive, and gcloud-default OAuth has the Drive
# scope blocked. rclone handles its own user-level OAuth cleanly.
#   Setup: rclone config  →  set BACKUP_RCLONE_REMOTE=<name>:<folder> in .env
if [ -n "${BACKUP_RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
  echo "[$(date -Iseconds)] Mirroring $BACKUP_DIR → $BACKUP_RCLONE_REMOTE"
  rclone sync "$BACKUP_DIR" "$BACKUP_RCLONE_REMOTE" --quiet
fi

echo "[$(date -Iseconds)] Done."
