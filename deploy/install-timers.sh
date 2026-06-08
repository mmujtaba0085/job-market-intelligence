#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-timers.sh
# Installs systemd timers for automatic ingestion (every 12 h) and
# daily SQLite backups.  Run once on the VPS as root.
#
# Usage:
#   sudo bash /opt/jobmarket/deploy/install-timers.sh
#
# To change the interval afterwards:
#   sudo systemctl edit jobmarket-ingest.timer
#   sudo systemctl restart jobmarket-ingest.timer
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "Installing Job Market Intelligence systemd timers..."

# Copy unit files
for f in jobmarket-ingest.service jobmarket-ingest.timer \
          jobmarket-backup.service jobmarket-backup.timer; do
    cp "$DEPLOY_DIR/$f" "$SYSTEMD_DIR/$f"
    echo "  Copied $f"
done

# Reload and enable
systemctl daemon-reload

systemctl enable --now jobmarket-ingest.timer
systemctl enable --now jobmarket-backup.timer

echo ""
echo "✅  Timers installed and running."
echo ""
echo "Useful commands:"
echo "  systemctl list-timers jobmarket-*          # see next run times"
echo "  journalctl -u jobmarket-ingest.service -f  # watch live ingest logs"
echo "  systemctl start jobmarket-ingest.service   # trigger a run right now"
echo ""

# Show next fire times
systemctl list-timers jobmarket-* --no-pager 2>/dev/null || true
