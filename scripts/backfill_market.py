"""
Seed the database with every job currently available for one market, in
one run, instead of waiting for its next scheduled ingest pass. Generic
version of backfill_tenpearls.py's pattern, for any "current state" board
(no historical crawl to do - the source only ever exposes what's open
right now, so a normal ingest run already fetches "everything available").

Safe to re-run any time (dedup keys prevent duplicate inserts, same as
the regular pipeline).

Usage:
    python scripts/backfill_market.py <market_id>
    python scripts/backfill_market.py pakistan_company_boards
"""

from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market_id", help="market_id from config/markets.py to seed immediately")
    args = parser.parse_args()

    market = next((m for m in TARGET_MARKETS if m["market_id"] == args.market_id), None)
    if market is None:
        print(f"Market '{args.market_id}' not found in config/markets.py — nothing to run.")
        sys.exit(1)

    print("Running database migrations...")
    run_migrations()

    week_str = date.today().isoformat()
    run = RunContext(market_id=args.market_id, week=week_str)

    print(f"Seeding '{args.market_id}'...")
    run_ingestion(market, run)

    print("Recomputing diversity ranks...")
    try:
        recompute_diversity_ranks()
    except Exception:
        logger.exception("[backfill_market] diversity rank recompute failed; leaving ranks stale until next run")

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
