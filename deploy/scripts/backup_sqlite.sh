#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB_PATH="${DB_PATH:-/opt/jobmarket/data/jobs.sqlite}"
BACKUP_DIR="${BACKUP_DIR:-/opt/jobmarket/data/sqlite_backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$BACKUP_DIR/jobs_$TIMESTAMP.sqlite.gz"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB_PATH" ]]; then
  echo "ERROR: DB not found at $DB_PATH" >&2
  exit 1
fi

TMP_COPY="$BACKUP_DIR/.jobs_$TIMESTAMP.sqlite"
sqlite3 "$DB_PATH" ".backup '$TMP_COPY'"
gzip -c "$TMP_COPY" > "$OUT_FILE"
rm -f "$TMP_COPY"

echo "Backup created: $OUT_FILE"
