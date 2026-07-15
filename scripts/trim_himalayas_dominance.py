"""
scripts/trim_himalayas_dominance.py
──────────────────────────────────────
One-time correction: Himalayas was re-enabled at some point after being
disabled (see config/sources.py) and, uncapped, grew to 46.9% of all
active jobs (82.8% of the last-month Active window) across the two global
markets that include it - confirmed against production on 2026-07-16.
config/markets.py now caps future collection (source_overrides:
{"himalayas": {"max_jobs": 50}} on both ai_ml_global and swe_backend_global),
but that only slows future growth - it does nothing for the ~52,000 jobs
already sitting in the active pool. This script is the one-time fix for
that: it hides (listing_status = 'hidden', the same mechanism active_jobs
already excludes on - see src/storage/db.py migration 009 - not a DELETE,
fully reversible) the oldest Himalayas jobs by first_seen_at until
Himalayas's share of active jobs reaches the target percentage.

Oldest-first, not random: Himalayas provides no reliable per-job posting
date (see src/collectors/himalayas_collector.py - posted_date is stamped
with the date *we* first saw the job, not sourced from Himalayas itself),
so first_seen_at is the only age signal available, and older listings are
the most likely to actually be stale/filled by now given nothing else
ever expires a Himalayas job automatically.

The target job count is computed fresh against live data each run, not
hardcoded - solving (himalayas_active - X) / (total_active - X) = target_pct
for X. Safe to re-run: a second run at the same target percentage will
find the ratio already met and hide 0 more (idempotent), and running it
again later at a different --target-pct will only ever hide more, never
un-hide anything (this script only ever sets listing_status = 'hidden',
never reverts it - reverting would need a manual/separate action, since
"undo this specific automated trim" isn't the same operation as "hide the
oldest N until a ratio is met").

Usage:
    docker exec jobmarket-web python scripts/trim_himalayas_dominance.py --dry-run
    docker exec jobmarket-web python scripts/trim_himalayas_dominance.py
    docker exec jobmarket-web python scripts/trim_himalayas_dominance.py --target-pct 0.25
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import db
from src.storage.db import get_connection, run_migrations, use_free_connection

DEFAULT_TARGET_PCT = 0.15


def _target_hide_count(total_active: int, himalayas_active: int, target_pct: float) -> int:
    """
    Solve (himalayas_active - X) / (total_active - X) = target_pct for X.
    Returns 0 (never negative) if Himalayas is already at or below target.
    """
    if total_active == 0 or himalayas_active == 0:
        return 0
    raw = (himalayas_active - target_pct * total_active) / (1 - target_pct)
    return max(0, round(raw))


def trim_connection(conn, target_pct: float, dry_run: bool = False) -> int:
    """
    Run the trim against an already-open connection. Returns the number of
    rows hidden (or that would be hidden, under --dry-run).

    Factored out from _run_pass() so it can be exercised directly against
    an isolated test database.
    """
    total_active = conn.execute("SELECT COUNT(*) FROM active_jobs").fetchone()[0]
    himalayas_active = conn.execute(
        "SELECT COUNT(*) FROM active_jobs WHERE source_name = 'Himalayas'"
    ).fetchone()[0]
    current_pct = (himalayas_active / total_active) if total_active else 0.0
    print(f"  Active jobs: {total_active} total, {himalayas_active} Himalayas ({current_pct:.1%})")

    hide_count = _target_hide_count(total_active, himalayas_active, target_pct)
    if hide_count == 0:
        print(f"  Already at or below {target_pct:.0%} target. Nothing to hide.")
        return 0

    print(f"  Target: {target_pct:.0%} -> hiding the oldest {hide_count} Himalayas job(s) by first_seen_at")

    if dry_run:
        remaining = himalayas_active - hide_count
        remaining_total = total_active - hide_count
        print(f"  --dry-run: no changes written. Would leave {remaining} Himalayas job(s) "
              f"({remaining / remaining_total:.1%} of {remaining_total} remaining active jobs).")
        return hide_count

    conn.execute(
        """
        UPDATE jobs SET listing_status = 'hidden'
        WHERE job_id IN (
            SELECT job_id FROM active_jobs
            WHERE source_name = 'Himalayas'
            ORDER BY first_seen_at ASC
            LIMIT ?
        )
        """,
        (hide_count,),
    )
    conn.commit()
    print(f"  Hidden {hide_count} job(s).")
    return hide_count


def _run_pass(label: str, target_pct: float, dry_run: bool) -> int:
    print(f"\n--- {label} ---")
    conn = get_connection()
    try:
        return trim_connection(conn, target_pct, dry_run=dry_run)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would be hidden without writing changes")
    parser.add_argument("--target-pct", type=float, default=DEFAULT_TARGET_PCT, help=f"Target Himalayas share of active jobs (default {DEFAULT_TARGET_PCT})")
    args = parser.parse_args()

    print("=" * 70)
    print("TRIM HIMALAYAS DOMINANCE")
    print(f"One-time correction, targeting {args.target_pct:.0%} of active jobs. Safe to re-run.")
    print("=" * 70)

    run_migrations()  # idempotent - guarantees jobs/active_jobs exist with current schema

    pointer = db._read_pointer()
    serving_name = db.serving_db_path().name
    free_name = db._free_path().name
    print(f"\nCurrent Serving pointer: '{pointer}' -> {serving_name}  (Free: {free_name})")

    hidden_serving = _run_pass(f"Pass 1/2: Serving ({serving_name}) - currently live", args.target_pct, args.dry_run)

    with use_free_connection():
        hidden_free = _run_pass(f"Pass 2/2: Free ({free_name}) - will become live at the next rotation", args.target_pct, args.dry_run)

    print("\n" + "=" * 70)
    verb = "Would hide" if args.dry_run else "Hid"
    print(f"Done. {verb} {hidden_serving + hidden_free} row(s) total across both serving-slot files.")
    print("=" * 70)


if __name__ == "__main__":
    main()
