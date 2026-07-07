"""Check status of Arbeitnow jobs in database."""
import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 80)
print("Arbeitnow Jobs Status Check")
print("=" * 80)

# Count total Arbeitnow jobs
cursor.execute("SELECT COUNT(*) as count FROM jobs WHERE source_name LIKE '%arbeit%'")
total = cursor.fetchone()['count']
print(f"\nTotal Arbeitnow jobs: {total}")

if total == 0:
    print("\n✓ No Arbeitnow jobs in database yet.")
    conn.close()
    exit(0)

# Check posted_date status
cursor.execute("""
    SELECT 
        CASE 
            WHEN posted_date IS NULL OR posted_date = '' THEN 'Missing'
            ELSE 'Has Date'
        END as status,
        COUNT(*) as count
    FROM jobs 
    WHERE source_name LIKE '%arbeit%'
    GROUP BY status
""")

print("\nPosted Date Status:")
for row in cursor.fetchall():
    print(f"  {row['status']:12s}: {row['count']:4d} jobs")

# Show sample jobs with missing dates using current schema
cursor.execute("""
    SELECT url_hash, posted_date, ingested_at
    FROM jobs
    WHERE source_name LIKE '%arbeit%'
    AND (posted_date IS NULL OR posted_date = '')
    LIMIT 5
""")

missing_jobs = cursor.fetchall()

if missing_jobs:
    print("\n" + "=" * 80)
    print("Sample jobs with missing dates:")
    print("=" * 80)
    
    for job in missing_jobs:
        print(f"\nJob: {job['url_hash'][:20]}...")
        print(f"  posted_date in DB: '{job['posted_date']}'")
        print(f"  ingested_at: {job['ingested_at']}")
    
    print("\n" + "=" * 80)
    print(f"Missing-date jobs sampled: {len(missing_jobs)}")
    print("=" * 80)

conn.close()
