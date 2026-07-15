"""
scripts/backfill_us_state_country_codes.py
─────────────────────────────────────────────
One-time backfill: a batch of active jobs have a raw 2-letter US state
abbreviation (e.g. "MA", "VA", "PA") sitting directly in the `country`
column instead of "United States". On the geo-distribution dashboard
(GET /api/dashboard/geo) this fragments the real "United States" total
across dozens of bogus pseudo-country buckets ("MA", "VA", "PA", ...).

Root cause (confirmed with evidence, not assumed): NOT a live bug in
src/utils/country_inference.py::infer_country() - that function already
resolves "City, ST" locations to "United States" correctly via its
_US_STATE_ABBR table, and has since the shared module was introduced
(commit 0c2c5e0). The actual leak was three collectors that never called
it at all and instead built `country` from their own inline comma-split
of the raw location string, falling back to the raw trailing fragment
verbatim whenever it didn't match one of a handful of hardcoded country
names: src/collectors/jooble_collector.py, src/collectors/findwork_crawler.py,
and src/collectors/findwork_collector.py. All three were fixed alongside
this script to route through infer_country() instead - see those files'
_extract_country()/_parse_job() - so going-forward ingestion should not
reproduce this. This script only cleans up the historical rows already
sitting in the database from before that fix.

Narrowly targeted: only touches rows where `country`, trimmed and
uppercased, is *exactly* one of the 50 US state abbreviations + "DC" (the
same US_STATES table scripts/warehouse_rollout.py already uses for this
same kind of check) - an equality match against a fixed list, never a
substring/LIKE match, so it can never touch an unrelated country value
(e.g. "Canada" is untouched even though it is not in the list; a country
value that merely contains a state code as a substring is also untouched,
since the comparison is exact-equality on the whole trimmed/uppercased
string). Safe to re-run: once a row's country is "United States", it is no
longer in the abbreviation list, so it simply won't match on a second run.

Scoped to active jobs (matches active_jobs' own definition: any row whose
listing_status is not 'hidden' - see src/storage/db.py migration 009), the
same set the dashboard's geo endpoint reads from. The UPDATE runs against
the underlying `jobs` table (SQLite views aren't directly updatable), using
the identical listing_status condition, so it corrects exactly the rows
active_jobs would have shown as a bogus state-code bucket.

Covers both rotating serving-slot files (serving_a.sqlite / serving_b.sqlite)
- Pass 1 against get_connection() (Serving, the file live traffic reads
right now), Pass 2 against get_free_connection() (Free, the other slot -
reached via use_free_connection() redirecting get_connection() for the
duration of the `with` block, same as scripts/recompute_summaries_catchup.py
does for its own two-pass backfill) - so both files are corrected
regardless of which one the pointer currently calls "Serving", including
the one that will become live at the next rotation.

Usage (matches the docker compose invocation pattern documented in
deploy/VPS_DEPLOY.md for other one-off scripts, e.g. warehouse_rollout.py):

    docker compose --profile jobs run --rm pipeline python scripts/backfill_us_state_country_codes.py

    # Preview affected row counts without writing anything:
    docker compose --profile jobs run --rm pipeline python scripts/backfill_us_state_country_codes.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.enrichment.location_data import US_STATES
from src.storage import db
from src.storage.db import get_connection, run_migrations, use_free_connection

# 50 US state abbreviations + "DC", e.g. "AL", "AK", ..., "WY", "DC".
_STATE_CODES = tuple(US_STATES)
_PLACEHOLDERS = ",".join("?" for _ in _STATE_CODES)

_SELECT_SQL = (
    "SELECT country, COUNT(*) as c FROM jobs "
    f"WHERE listing_status != 'hidden' AND UPPER(TRIM(country)) IN ({_PLACEHOLDERS}) "
    "GROUP BY country ORDER BY c DESC"
)
_UPDATE_SQL = (
    "UPDATE jobs SET country = 'United States' "
    f"WHERE listing_status != 'hidden' AND UPPER(TRIM(country)) IN ({_PLACEHOLDERS})"
)


def backfill_connection(conn, dry_run: bool = False) -> int:
    """
    Run the backfill against an already-open connection. Returns the number
    of active job rows that matched (updated, unless dry_run).

    Factored out from _run_pass() so it can be exercised directly against an
    isolated test database without going through get_connection()'s
    rotating-DB path resolution.
    """
    rows = conn.execute(_SELECT_SQL, _STATE_CODES).fetchall()
    total = sum(r["c"] for r in rows)

    if not rows:
        print("  No active jobs with a bare US state code in `country`. Nothing to do.")
        return 0

    for r in rows:
        print(f"  {r['country']!r}: {r['c']} job(s)")
    print(f"  Total: {total} active job(s) {'would be' if dry_run else 'will be'} updated to 'United States'")

    if dry_run:
        print("  --dry-run: no changes written.")
        return total

    conn.execute(_UPDATE_SQL, _STATE_CODES)
    conn.commit()
    print(f"  Updated {total} row(s) -> country = 'United States'")
    return total


def _run_pass(label: str, dry_run: bool) -> int:
    print(f"\n--- {label} ---")
    conn = get_connection()
    try:
        return backfill_connection(conn, dry_run=dry_run)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report affected rows without writing changes")
    args = parser.parse_args()

    print("=" * 70)
    print("BACKFILL: US STATE ABBREVIATIONS LEAKED INTO `country`")
    print("One-time cleanup for pre-fix collector output. Safe to re-run.")
    print("=" * 70)

    run_migrations()  # idempotent - guarantees jobs/active_jobs exist with current schema

    pointer = db._read_pointer()
    serving_name = db.serving_db_path().name
    free_name = db._free_path().name
    print(f"\nCurrent Serving pointer: '{pointer}' -> {serving_name}  (Free: {free_name})")

    updated_serving = _run_pass(f"Pass 1/2: Serving ({serving_name}) - currently live", args.dry_run)

    with use_free_connection():
        updated_free = _run_pass(f"Pass 2/2: Free ({free_name}) - will become live at the next rotation", args.dry_run)

    print("\n" + "=" * 70)
    verb = "Would update" if args.dry_run else "Updated"
    print(f"Done. {verb} {updated_serving + updated_free} row(s) total across both serving-slot files.")
    print("=" * 70)


if __name__ == "__main__":
    main()
