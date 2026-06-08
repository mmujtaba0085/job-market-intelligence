#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JOBMARKET_HOME="${JOBMARKET_HOME:-/opt/jobmarket}"
DB_PATH="${DB_PATH:-$JOBMARKET_HOME/data/jobs.sqlite}"
AUTH_DB_PATH="${AUTH_DB_PATH:-$JOBMARKET_HOME/data/auth.sqlite}"
BACKUP_DIR="${BACKUP_DIR:-$JOBMARKET_HOME/data/sqlite_backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

backup_db() {
  local source_path="$1"
  local label="$2"
  local required="$3"
  local out_file="$BACKUP_DIR/${label}_$TIMESTAMP.sqlite.gz"
  local tmp_copy="$BACKUP_DIR/.${label}_$TIMESTAMP.sqlite"

  if [[ ! -f "$source_path" ]]; then
    if [[ "$required" == "yes" ]]; then
      echo "ERROR: DB not found at $source_path" >&2
      exit 1
    fi
    echo "Skipping absent optional DB: $source_path"
    return
  fi

  sqlite3 "$source_path" ".backup '$tmp_copy'"
  gzip -c "$tmp_copy" > "$out_file"
  rm -f "$tmp_copy"
  echo "Backup created: $out_file"
}

backup_db "$DB_PATH" "jobs" "yes"
backup_db "$AUTH_DB_PATH" "auth" "no"
