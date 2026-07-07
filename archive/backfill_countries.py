"""
scripts/backfill_countries.py
──────────────────────────────
One-time backfill: re-infer country for all rows where:
  - country IS NULL, empty, or 'Unknown'
  - location is non-empty and not 'Unknown'

Run inside the container:
  python scripts/backfill_countries.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.country_inference import infer_country
from src.storage.db import get_connection

conn = get_connection()
cursor = conn.execute("""
    SELECT job_id, location
    FROM jobs
    WHERE (country IS NULL OR TRIM(country) = '' OR LOWER(country) = 'unknown')
      AND location IS NOT NULL
      AND TRIM(location) != ''
      AND LOWER(location) != 'unknown'
""")
rows = cursor.fetchall()
print(f"Rows to process: {len(rows)}")

updated = 0
still_unknown = 0
by_country: dict[str, int] = {}

updates: list[tuple[str, int]] = []
for row in rows:
    inferred = infer_country(row["location"])
    updates.append((inferred, row["job_id"]))
    if inferred != "Unknown":
        by_country[inferred] = by_country.get(inferred, 0) + 1
        updated += 1
    else:
        still_unknown += 1

with conn:
    conn.executemany(
        "UPDATE jobs SET country = ? WHERE job_id = ?",
        updates,
    )

conn.close()

print(f"\nResults:")
print(f"  Updated (inferred):  {updated}")
print(f"  Still Unknown:       {still_unknown}")
print(f"\nBy country:")
for country, count in sorted(by_country.items(), key=lambda x: -x[1]):
    print(f"  {country:<30} {count}")
