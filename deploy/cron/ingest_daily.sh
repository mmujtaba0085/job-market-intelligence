#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

/usr/bin/docker compose --profile jobs run --rm pipeline python -m src.orchestrator --mode ingest-only
