"""
scripts/recompute_summaries_catchup.py
────────────────────────────────────────
One-time catch-up for the recompute-before-rotate ordering bug fixed in
src/orchestrator.py: recompute_diversity_ranks() / recompute_skill_combinations()
/ recompute_top_titles() used to run BEFORE the ingest-only rotate() block, so
every ingest-only cycle's recompute wrote to whatever was Serving at that
moment - the file about to be demoted - and db_rotation.py's
_refresh_demoted_file() then overwrote that very file with the new Serving's
(recompute-less) content anyway. The file that actually went live never
received the fresh write. That left Skills Intelligence (/skills/intelligence),
Titles Analytics (/titles/analytics), and the /jobs page's diversity-ranked
default sort showing stale/incomplete data even after jobs/skills themselves
were rotating correctly.

The orchestrator.py fix (moving the three recompute calls to after rotate())
only stops the bug from recurring on FUTURE ingest-only cycles - it does
nothing for data that is already stale right now. This script is the
one-time fix for that: it calls all three recompute functions once against
each of the two serving-slot files (serving_a.sqlite and serving_b.sqlite) -
first against whichever get_connection() resolves to right now (Serving, the
live file), then again redirected via use_free_connection() to whichever
get_free_connection() resolves to (Free). That covers both files regardless
of which one the pointer currently calls "Serving", including the one that
will become live at the next rotation - so it doesn't have to wait for that
rotation before this fix's reordering gets a chance to refresh it on its own.

Safe to re-run: every recompute is a full DELETE + re-INSERT (skill/title
summaries) or a full UPDATE (diversity_rank) of derived data computed fresh
from jobs/skills each time - never additive - so running this script twice
in a row is a no-op the second time.

Usage (matches the docker compose invocation pattern documented in
deploy/VPS_DEPLOY.md for other one-off scripts, e.g. warehouse_rollout.py):

    docker compose --profile jobs run --rm pipeline python scripts/recompute_summaries_catchup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics.diversity_rank import recompute_diversity_ranks
from src.analytics.precomputed_summaries import recompute_skill_combinations, recompute_top_titles
from src.storage import db
from src.storage.db import run_migrations, use_free_connection


def _run_pass(label: str) -> None:
    print(f"\n--- {label} ---")

    diversity_updated = recompute_diversity_ranks()
    print(f"  diversity_rank:             {diversity_updated} active job(s) updated")

    pairs = recompute_skill_combinations()
    print(f"  skill_combinations_summary: {pairs} pair(s)")

    titles = recompute_top_titles()
    noun = "role family" if titles == 1 else "role families"
    print(f"  top_titles_summary:         {titles} {noun}")


def main() -> None:
    print("=" * 70)
    print("RECOMPUTE SUMMARIES CATCH-UP")
    print("One-time fix for the recompute-before-rotate ordering bug -")
    print("see this script's module docstring for details. Safe to re-run.")
    print("=" * 70)

    run_migrations()  # idempotent - guarantees the summary tables/columns exist

    pointer = db._read_pointer()
    serving_name = db.serving_db_path().name
    free_name = db._free_path().name
    print(f"\nCurrent Serving pointer: '{pointer}' -> {serving_name}  (Free: {free_name})")

    # Pass 1: whatever get_connection() resolves to right now - Serving, the
    # file real traffic reads from.
    _run_pass(f"Pass 1/2: Serving ({serving_name}) - currently live")

    # Pass 2: whatever get_free_connection() resolves to - the other
    # serving-slot file. use_free_connection() redirects get_connection()
    # (which is what recompute_diversity_ranks/recompute_skill_combinations/
    # recompute_top_titles all call internally) to the same file
    # get_free_connection() would open, so this covers it via the public API
    # without duplicating each function's connection-lifecycle handling.
    with use_free_connection():
        _run_pass(f"Pass 2/2: Free ({free_name}) - will become live at the next rotation")

    print("\n" + "=" * 70)
    print("Done - both serving_a.sqlite and serving_b.sqlite now have current")
    print("skill_combinations_summary / top_titles_summary / diversity_rank data.")
    print("=" * 70)


if __name__ == "__main__":
    main()
