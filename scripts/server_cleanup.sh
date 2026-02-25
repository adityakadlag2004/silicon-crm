#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Server Maintenance Script for silicon-crm
# Run on server: bash scripts/server_cleanup.sh
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/silicon-crm}"
VENV="${PROJECT_DIR}/venv_new/bin/activate"

echo "=== Silicon CRM — Server Cleanup ==="
echo "Project: ${PROJECT_DIR}"
echo ""

# 1. Remove Python bytecode cache
echo "[1/6] Removing __pycache__ directories…"
find "$PROJECT_DIR" -path '*/venv_new' -prune -o -name '__pycache__' -type d -print -exec rm -rf {} + 2>/dev/null || true
find "$PROJECT_DIR" -path '*/venv_new' -prune -o -name '*.pyc' -delete 2>/dev/null || true
echo "  Done."

# 2. Clean old log files (keep last 3 days)
echo "[2/6] Cleaning old log files…"
find "$PROJECT_DIR/logs" -name '*.log.*' -mtime +3 -delete 2>/dev/null || true
echo "  Done."

# 3. Run Django data cleanup
echo "[3/6] Running Django cleanup_data…"
source "$VENV"
cd "$PROJECT_DIR"
python manage.py cleanup_data --days 90
echo "  Done."

# 4. Clear expired sessions
echo "[4/6] Clearing expired Django sessions…"
python manage.py clearsessions
echo "  Done."

# 5. VACUUM the PostgreSQL database
echo "[5/6] Running PostgreSQL VACUUM ANALYZE…"
DB_NAME=$(python -c "import os; exec(open('.env').read().replace('=','=\"',1).rstrip()+'\"' if False else ''); [print(l.split('=',1)[1].strip()) for l in open('.env') if l.startswith('DB_NAME')]" 2>/dev/null || echo "crmdb")
sudo -u postgres psql -d "${DB_NAME:-crmdb}" -c "VACUUM ANALYZE;" 2>/dev/null || echo "  (Skipped — run manually: sudo -u postgres psql -d crmdb -c 'VACUUM ANALYZE;')"
echo "  Done."

# 6. Summary
echo ""
echo "[6/6] Disk usage summary:"
du -sh "$PROJECT_DIR"/{static,media,logs,.git,clients} 2>/dev/null || true
echo ""
df -h / | tail -1
echo ""
echo "=== Cleanup Complete ==="
