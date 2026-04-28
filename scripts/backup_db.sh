#!/usr/bin/env bash
# Daily Postgres backup. Designed to be invoked by cron.
#
# Behaviour:
#   1. Dumps DB to $BACKUP_DIR/silicon-latest.sql.gz (single file, overwritten
#      atomically each day). No rotation, no accumulating storage.
#   2. If BACKUP_RCLONE_REMOTE is set, mirrors $BACKUP_DIR to that remote.
#      Using `rclone sync` so the remote also has just the one current file.
#
# Setup on the server:
#   sudo mkdir -p /var/backups/silicon
#   sudo chown ubuntu:ubuntu /var/backups/silicon
#   crontab -e
#   # Add: 30 2 * * * /home/ubuntu/silicon-crm/scripts/backup_db.sh >> /var/log/silicon-backup.log 2>&1

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/silicon}"

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
OUTFILE="$BACKUP_DIR/silicon-latest.sql.gz"
TMPFILE="$OUTFILE.tmp"

echo "[$(date -Iseconds)] Backing up $DB_NAME → $OUTFILE"

# Dump to a temp file first, then atomically rename. Means an in-progress
# backup never replaces the previous good one — if pg_dump fails, the old
# silicon-latest.sql.gz is still intact and restorable.
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

# Clean up any old date-stamped backups from earlier versions of this script.
# `|| true` so that a missing-files exit status doesn't trip set -e/pipefail.
( ls -1 "$BACKUP_DIR"/silicon-2[0-9][0-9][0-9]-*.sql.gz 2>/dev/null || true ) | xargs -r rm -v || true

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
