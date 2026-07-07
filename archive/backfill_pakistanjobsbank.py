"""
One-off runner to fast-track the Pakistan Jobs Bank windowed backfill.

Normal operation lets PakistanJobsBankCollector advance a few hundred dates
per `--mode ingest-only` cron run. This script just calls the collector (and
the normalize/dedupe/store pipeline) in a loop, immediately, until its state
file reports backfill_complete — useful right after deploying the collector
so the ~9-month window is populated without waiting on the daily cron.

Only runs this one collector/market — does not touch any other source.

Usage:
    python scripts/backfill_pakistanjobsbank.py
"""

import json

from config.markets import TARGET_MARKETS
from src.collectors.pakistanjobsbank_collector import PakistanJobsBankCollector
from src.deduplicator import deduplicate_and_store
from src.normalizer import normalize_batch

MARKET = next(m for m in TARGET_MARKETS if m["market_id"] == "pakistan_jobs_all")
MAX_ITERATIONS = 10  # safety cap — each call already advances up to 200 dates

collector = PakistanJobsBankCollector()

for i in range(1, MAX_ITERATIONS + 1):
    print(f"\n=== Iteration {i} ===")
    jobs = collector.collect(MARKET)
    normalized = normalize_batch(jobs, MARKET["market_id"])
    results, summary = deduplicate_and_store(normalized)
    print(
        f"Fetched {len(jobs)} raw -> {len(normalized)} normalized "
        f"-> inserted {summary.inserted}, deduped {summary.url_dups + summary.canonical_dups}"
    )

    state = collector._load_state()
    print("State:", json.dumps(state, indent=2))

    if state.get("backfill_complete"):
        print("\nBackfill window complete.")
        break
else:
    print(f"\nStopped after {MAX_ITERATIONS} iterations without completing — check state file.")
