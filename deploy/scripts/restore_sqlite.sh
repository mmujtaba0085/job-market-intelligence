#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup-file.sqlite.gz> [db-path]" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_FILE="$1"
DB_PATH="${2:-${DB_PATH:-/opt/jobmarket/data/jobs.sqlite}}"
DATA_DIR="$(dirname "$DB_PATH")"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
PRE_RESTORE_COPY="$DATA_DIR/jobs_pre_restore_$TIMESTAMP.sqlite"

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"

if [[ -f "$DB_PATH" ]]; then
  cp "$DB_PATH" "$PRE_RESTORE_COPY"
  echo "Existing DB backup saved to: $PRE_RESTORE_COPY"
fi

TMP_RESTORE="$DATA_DIR/.jobs_restore_$TIMESTAMP.sqlite"
gunzip -c "$BACKUP_FILE" > "$TMP_RESTORE"
mv "$TMP_RESTORE" "$DB_PATH"

echo "Restore completed: $DB_PATH"
