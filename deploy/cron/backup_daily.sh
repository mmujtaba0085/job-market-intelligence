#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/deploy/scripts/backup_sqlite.sh"

# Keep last 14 daily backups
JOBMARKET_HOME="${JOBMARKET_HOME:-/opt/jobmarket}"
find "$JOBMARKET_HOME/data/sqlite_backups" -type f -name "*.sqlite.gz" -mtime +14 -delete
