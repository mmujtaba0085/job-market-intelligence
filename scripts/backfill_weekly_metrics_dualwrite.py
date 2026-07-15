"""
scripts/backfill_weekly_metrics_dualwrite.py
────────────────────────────────────────────────
One-time reconciliation for the weekly_metrics rotation-clobber bug fixed in
src/storage/db.py::upsert_weekly_metric(). Before that fix, each weekly run
wrote via get_connection() only - whichever physical file the pointer
called "Serving" at that moment - so a row from one weekly run could exist
on serving_a.sqlite while a row from a later run (after rotation flipped
the pointer) exists only on serving_b.sqlite. Any such row was also at
constant risk of being silently destroyed outright by a later ingest-only
rotation's _refresh_demoted_file() (confirmed happening in production:
weekly_metrics was found completely empty on the live Serving file days
after the weekly timer had run successfully).

The code fix (upsert_weekly_metric() now writing to both files) only
prevents this from recurring on FUTURE weekly writes. This script is the
one-time fix for whatever rows already exist BEFORE that fix - it reads
every weekly_metrics row from both serving_a.sqlite and serving_b.sqlite,
takes the union keyed by (market_id, week_start_date, skill_name) - the
same natural key upsert_weekly_metric()'s own ON CONFLICT clause uses -
and re-applies every row through upsert_weekly_metric() itself, so its
now-fixed dual-write logic lands the full union on both files.

If a key exists on both sides already (shouldn't normally happen pre-fix,
since a single-file write could only ever land on one side per run, but
handled defensively anyway), the row read from Serving wins - arbitrary but
deterministic, and no worse than the divergent state already on disk.

Safe to re-run: re-applying the same union twice is a no-op the second time
(upsert_weekly_metric() is itself idempotent per its ON CONFLICT clause).

Usage:

    docker exec jobmarket-web python scripts/backfill_weekly_metrics_dualwrite.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import db
from src.storage.db import get_connection, get_free_connection, run_migrations, upsert_weekly_metric
from src.storage.models import WeeklyMetric

_COLUMNS = (
    "market_id, week_start_date, week_number, skill_name, category, "
    "frequency, growth_percentage, absolute_delta, mover_score, "
    "emerging_flag, declining_flag"
)


def _read_all(conn) -> dict[tuple, WeeklyMetric]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM weekly_metrics").fetchall()
    result: dict[tuple, WeeklyMetric] = {}
    for r in rows:
        key = (r["market_id"], r["week_start_date"], r["skill_name"])
        result[key] = WeeklyMetric(
            market_id=r["market_id"],
            week_start_date=date.fromisoformat(r["week_start_date"]),
            week_number=r["week_number"],
            skill_name=r["skill_name"],
            category=r["category"],
            frequency=r["frequency"],
            growth_percentage=r["growth_percentage"],
            absolute_delta=r["absolute_delta"],
            mover_score=r["mover_score"],
            emerging_flag=bool(r["emerging_flag"]),
            declining_flag=bool(r["declining_flag"]),
        )
    return result


def main() -> None:
    print("=" * 70)
    print("BACKFILL: RECONCILE weekly_metrics ACROSS BOTH SERVING-SLOT FILES")
    print("One-time fix for the weekly_metrics rotation-clobber bug -")
    print("see this script's module docstring for details. Safe to re-run.")
    print("=" * 70)

    run_migrations()  # idempotent - guarantees weekly_metrics exists

    pointer = db._read_pointer()
    serving_name = db.serving_db_path().name
    free_name = db._free_path().name
    print(f"\nCurrent Serving pointer: '{pointer}' -> {serving_name}  (Free: {free_name})")

    serving_conn = get_connection()
    try:
        serving_rows = _read_all(serving_conn)
    finally:
        serving_conn.close()

    free_conn = get_free_connection()
    try:
        free_rows = _read_all(free_conn)
    finally:
        free_conn.close()

    print(f"\nServing ({serving_name}): {len(serving_rows)} row(s)")
    print(f"Free ({free_name}):    {len(free_rows)} row(s)")

    union: dict[tuple, WeeklyMetric] = dict(free_rows)
    union.update(serving_rows)  # Serving wins on key collision

    only_on_free = set(free_rows) - set(serving_rows)
    print(f"Rows only on Free (missing from Serving before this run): {len(only_on_free)}")
    print(f"Union to reconcile onto both files: {len(union)} row(s)")

    if not union:
        print("\nNothing to reconcile.")
        return

    for metric in union.values():
        upsert_weekly_metric(metric)  # now writes to both files - see db.py

    print("\n" + "=" * 70)
    print(f"Done - {len(union)} weekly_metrics row(s) now present on both")
    print("serving_a.sqlite and serving_b.sqlite.")
    print("=" * 70)


if __name__ == "__main__":
    main()
