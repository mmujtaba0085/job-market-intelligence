"""
Seed the database with every job currently listed on 10Pearls' careers
board in one run, instead of waiting for that market's next scheduled
ingest pass.

There's no historical crawl to do here (unlike Pakistan Jobs Bank's
date-archive backfill) — the 10Pearls board only ever exposes whatever is
*currently* open, so TenPearlsCollector already fetches "everything
available" on every single run. This script just runs that one market's
ingestion immediately and prints a summary. Safe to re-run any time
(dedup keys prevent duplicate inserts, same as the regular pipeline).

Usage:
    python scripts/backfill_tenpearls.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.markets import TARGET_MARKETS
from src.analytics.diversity_rank import recompute_diversity_ranks
from src.orchestrator import run_ingestion
from src.run_manager import RunContext
from src.storage.db import run_migrations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MARKET_ID = "tenpearls_jobs"


def main() -> None:
    market = next((m for m in TARGET_MARKETS if m["market_id"] == MARKET_ID), None)
    if market is None:
        print(f"Market '{MARKET_ID}' not found in config/markets.py — nothing to run.")
        sys.exit(1)

    print("Running database migrations...")
    run_migrations()

    week_str = date.today().isoformat()
    run = RunContext(market_id=MARKET_ID, week=week_str)

    print(f"Backfilling '{MARKET_ID}' from 10Pearls' careers board...")
    run_ingestion(market, run)

    print("Recomputing diversity ranks...")
    try:
        recompute_diversity_ranks()
    except Exception:
        logger.exception("[backfill_tenpearls] diversity rank recompute failed; leaving ranks stale until next run")

    print()
    print("Done.")
    print(f"  Sources attempted: {run.sources_attempted}")
    print(f"  Jobs fetched:      {run.jobs_fetched}")
    print(f"  Jobs inserted:     {run.jobs_inserted}")
    print(f"  Jobs deduped:      {run.jobs_deduped}")
    print(f"  Skills extracted:  {run.skills_extracted}")
    if run.errors_count:
        print(f"  Errors:            {run.errors_count}")
        for sample in run.error_samples:
            print(f"    - {sample}")


if __name__ == "__main__":
    main()
