"""Check Arbeitnow job dates in the current jobs schema."""
import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Check posted_date distribution
c.execute("""
    SELECT posted_date, COUNT(*) as count
    FROM jobs 
    WHERE source_name = 'Arbeitnow'
    GROUP BY posted_date
    ORDER BY count DESC
    LIMIT 10
""")

print("=" * 80)
print("Arbeitnow posted_date distribution:")
print("=" * 80)
rows = c.fetchall()
for r in rows:
    date_val = r['posted_date'] if r['posted_date'] else 'NULL'
    print(f"  {date_val:12s} : {r['count']:4d} jobs")

# Show sample records from current schema
c.execute("""
    SELECT url_hash, posted_date, ingested_at
    FROM jobs 
    WHERE source_name = 'Arbeitnow' 
    LIMIT 3
""")

print("\n" + "=" * 80)
print("Sample Arbeitnow rows:")
print("=" * 80)
for row in c.fetchall():
    print(
        f"  url_hash={row['url_hash'][:16]}... "
        f"posted_date={row['posted_date']} ingested_at={row['ingested_at']}"
    )

conn.close()
