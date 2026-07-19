#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-timers.sh
# Installs systemd timers for:
#   - Full weekly pipeline every Sunday at 03:00 (analytics + report)
#   - Daily SQLite backup
#
# Ingest-only and crawl are deliberately NOT installed as systemd timers -
# they're scheduled by the app's own in-app auto-scheduler thread instead
# (web_viewer.py's _auto_scheduler_loop(), interval configurable at
# Admin → Pipeline, backed by pipeline_config.ingest_interval_hours /
# crawl_interval_hours). Both mechanisms used to run in parallel
# (jobmarket-ingest.timer/jobmarket-crawl.timer were installed here
# alongside the in-app scheduler added later) - each paced itself off
# "N hours since the last run of that mode, regardless of who triggered
# it," so the two independent clocks drifted in and out of phase and
# periodically double-fired within minutes of each other. Confirmed live
# 2026-07-19 via pipeline_runs history (ingest-only firing every ~6h
# instead of the intended 12h, with near-simultaneous pairs where the
# second run re-fetched an already-deduped batch). Fixed by keeping only
# the in-app scheduler (the one editable without VPS access) and adding a
# cross-process file lock so gunicorn's multiple worker processes can't
# race each other into double-launching it either - see
# web_viewer.py::_run_ingest_crawl_scheduler_tick.
#
# jobmarket-ingest.service and jobmarket-crawl.service are still
# installed (not the timers) so `systemctl start jobmarket-ingest.service`
# remains available as an on-demand manual trigger.
#
# Usage:
#   sudo bash /opt/jobmarket/app/deploy/install-timers.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "Installing Job Market Intelligence systemd timers..."

for f in \
    jobmarket-ingest.service \
    jobmarket-crawl.service \
    jobmarket-weekly.service  jobmarket-weekly.timer \
    jobmarket-backup.service  jobmarket-backup.timer; do
    cp "$DEPLOY_DIR/$f" "$SYSTEMD_DIR/$f"
    echo "  Copied $f"
done

systemctl daemon-reload

systemctl enable --now jobmarket-weekly.timer
systemctl enable --now jobmarket-backup.timer

echo ""
echo "✅  Timers installed and running."
echo ""
echo "Schedule:"
echo "  Ingest-only  — via the app's in-app auto-scheduler (Admin → Pipeline), not a systemd timer"
echo "  Crawl        — via the app's in-app auto-scheduler (Admin → Pipeline), not a systemd timer"
echo "  Full weekly  — Sunday 03:00"
echo "  DB backup    — daily"
echo ""
echo "Useful commands:"
echo "  systemctl list-timers jobmarket-*"
echo "  journalctl -u jobmarket-weekly.service -f"
echo "  systemctl start jobmarket-ingest.service   # trigger ingest now (manual, one-off)"
echo "  systemctl start jobmarket-crawl.service    # trigger crawl now (manual, one-off)"
echo ""
systemctl list-timers jobmarket-* --no-pager 2>/dev/null || true
