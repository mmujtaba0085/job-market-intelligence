#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-timers.sh
# Installs systemd timers for:
#   - Ingest-only run every 12 hours (collect + normalize + dedupe)
#   - Findwork crawler run every 4 hours, capped at 30 minutes
#   - Full weekly pipeline every Sunday at 03:00 (analytics + report)
#   - Daily SQLite backup
#
# Usage:
#   sudo bash /opt/jobmarket/app/deploy/install-timers.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "Installing Job Market Intelligence systemd timers..."

for f in \
    jobmarket-ingest.service jobmarket-ingest.timer \
    jobmarket-crawl.service   jobmarket-crawl.timer \
    jobmarket-weekly.service  jobmarket-weekly.timer \
    jobmarket-backup.service  jobmarket-backup.timer; do
    cp "$DEPLOY_DIR/$f" "$SYSTEMD_DIR/$f"
    echo "  Copied $f"
done

systemctl daemon-reload

systemctl enable --now jobmarket-ingest.timer
systemctl enable --now jobmarket-crawl.timer
systemctl enable --now jobmarket-weekly.timer
systemctl enable --now jobmarket-backup.timer

echo ""
echo "✅  Timers installed and running."
echo ""
echo "Schedule:"
echo "  Ingest-only  — every 12 h"
echo "  Crawl        — every 4 h, max 30 min per run"
echo "  Full weekly  — Sunday 03:00"
echo "  DB backup    — daily"
echo ""
echo "Useful commands:"
echo "  systemctl list-timers jobmarket-*"
echo "  journalctl -u jobmarket-ingest.service -f"
echo "  journalctl -u jobmarket-crawl.service -f"
echo "  journalctl -u jobmarket-weekly.service -f"
echo "  systemctl start jobmarket-ingest.service   # trigger ingest now"
echo "  systemctl start jobmarket-crawl.service    # trigger crawl now"
echo ""
systemctl list-timers jobmarket-* --no-pager 2>/dev/null || true
