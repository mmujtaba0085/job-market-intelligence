"""
One-shot backfill: fetch the newspaper-ad image and "how to apply" link
for every existing Pakistan Jobs Bank job row that predates the feature
(1794fc0). Going-forward ingestion already fetches these per ad
(pakistanjobsbank_collector.py's _fetch_ad_detail()) - this script applies
the same fetch to the historical backlog.

One ad often covers several positions (e.g. one Bureau Veritas ad listing
20 roles), and each position becomes its own job row sharing the ad's URL
with a "#pos-N" fragment appended (see _parse_date_page()'s comment on
why). So this script groups job rows by their *base* ad URL (fragment
stripped) and fetches each distinct ad page exactly once, then applies the
result to every job row sharing that ad - not one fetch per job row.

Rate-limited via the collector's own configured rate_limit_per_minute (30/min
= one request every 2s), same as normal ingestion. At ~1,300 distinct ad
URLs this takes roughly 40-45 minutes; commits after every ad so it's safe
to interrupt and re-run (already-backfilled rows are skipped).

Usage:
    python scripts/backfill_pakistanjobsbank_ad_details.py            # full run
    python scripts/backfill_pakistanjobsbank_ad_details.py --limit 5  # smoke test
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.collectors.pakistanjobsbank_collector import PakistanJobsBankCollector
from src.storage.db import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SOURCE_NAME = "Pakistan Jobs Bank"


def _base_url(url: str) -> str:
    return url.split("#pos-", 1)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="only process the first N distinct ad URLs (smoke test)")
    args = parser.parse_args()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT job_id, url FROM jobs "
        "WHERE source_name = ? AND ad_image_url IS NULL AND apply_url IS NULL",
        (SOURCE_NAME,),
    )
    rows = cursor.fetchall()

    groups: dict[str, list[int]] = {}
    for row in rows:
        groups.setdefault(_base_url(row["url"]), []).append(row["job_id"])

    ad_urls = sorted(groups.keys())
    if args.limit:
        ad_urls = ad_urls[: args.limit]

    print(f"{len(rows)} job rows missing ad details, across {len(groups)} distinct ad URLs.")
    print(f"Processing {len(ad_urls)} ad URLs this run...")

    collector = PakistanJobsBankCollector()

    processed = 0
    updated_rows = 0
    failed = 0
    start = time.monotonic()

    for i, ad_url in enumerate(ad_urls, start=1):
        collector._wait()
        ad_image_url, apply_url = collector._fetch_ad_detail(ad_url)

        if ad_image_url is None and apply_url is None:
            failed += 1
            logger.warning("No ad_image_url or apply_url found for %s (job_ids=%s)", ad_url, groups[ad_url])
        else:
            job_ids = groups[ad_url]
            cursor.executemany(
                "UPDATE jobs SET ad_image_url = ?, apply_url = ? WHERE job_id = ?",
                [(ad_image_url, apply_url, job_id) for job_id in job_ids],
            )
            conn.commit()
            updated_rows += len(job_ids)

        processed += 1
        if processed % 50 == 0 or processed == len(ad_urls):
            elapsed = time.monotonic() - start
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = (len(ad_urls) - processed) / rate if rate > 0 else 0
            print(
                f"  [{processed}/{len(ad_urls)}] ad URLs done, "
                f"{updated_rows} job rows updated, {failed} ad fetches with no data found, "
                f"~{remaining/60:.1f} min remaining"
            )

    conn.close()

    print()
    print("Done.")
    print(f"  Ad URLs processed:     {processed}")
    print(f"  Job rows updated:      {updated_rows}")
    print(f"  Ad fetches with no data found: {failed}")


if __name__ == "__main__":
    main()
