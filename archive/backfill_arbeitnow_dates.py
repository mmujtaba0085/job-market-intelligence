"""Backfill posted_date for existing Arbeitnow jobs from raw_json."""
import sqlite3
import json
from datetime import datetime, timezone

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Find Arbeitnow jobs with missing posted_date but have raw_json
cursor.execute("""
    SELECT url_hash, raw_json, posted_date
    FROM jobs
    WHERE source_name = 'Arbeitnow'
    AND (posted_date IS NULL OR posted_date = '')
    AND raw_json IS NOT NULL
""")

jobs_to_fix = cursor.fetchall()

print("=" * 80)
print("Backfilling Arbeitnow job dates from Unix timestamps")
print("=" * 80)
print(f"\nFound {len(jobs_to_fix)} Arbeitnow jobs with missing dates\n")

if not jobs_to_fix:
    print("✓ No jobs need backfilling!")
    conn.close()
    exit(0)

updated = 0
failed = 0

for job in jobs_to_fix:
    try:
        data = json.loads(job['raw_json'])
        created_at = data.get('created_at')
        
        if created_at and isinstance(created_at, int):
            # Convert Unix timestamp to YYYY-MM-DD
            dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
            posted_date = dt.strftime("%Y-%m-%d")
            
            # Update database
            cursor.execute(
                "UPDATE jobs SET posted_date = ? WHERE url_hash = ?",
                (posted_date, job['url_hash'])
            )
            updated += 1
            
            if updated <= 5:  # Show first 5 examples
                print(f"Updated: {job['url_hash'][:16]}... → {posted_date}")
        else:
            failed += 1
            
    except Exception as e:
        print(f"Error processing {job['url_hash'][:16]}...: {e}")
        failed += 1

conn.commit()
conn.close()

print("\n" + "=" * 80)
print(f"✓ Backfill complete!")
print(f"  Updated: {updated} jobs")
print(f"  Failed:  {failed} jobs")
print("=" * 80)
