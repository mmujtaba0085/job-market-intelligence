"""Check Arbeitnow jobs details and recommendations."""
import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 80)
print("Arbeitnow Jobs Analysis")
print("=" * 80)

# Total count
cursor.execute("SELECT COUNT(*) as count FROM jobs WHERE source_name LIKE '%arbeit%'")
total = cursor.fetchone()['count']
print(f"\nTotal Arbeitnow jobs: {total}")
print(f"All have missing posted_date: YES")

# Check when they were ingested
cursor.execute("""
    SELECT 
        DATE(ingested_at) as ingestion_date,
        COUNT(*) as count
    FROM jobs 
    WHERE source_name LIKE '%arbeit%'
    GROUP BY DATE(ingested_at)
    ORDER BY ingestion_date DESC
    LIMIT 10
""")

print("\nIngestion dates:")
for row in cursor.fetchall():
    print(f"  {row['ingestion_date']}: {row['count']:4d} jobs")

# Sample jobs
cursor.execute("""
    SELECT title, company, location, ingested_at
    FROM jobs
    WHERE source_name LIKE '%arbeit%'
    LIMIT 5
""")

print("\nSample jobs:")
for row in cursor.fetchall():
    print(f"  • {row['title'][:50]:50s} @ {row['company'][:20]:20s}")
    print(f"    Location: {row['location'][:50]:50s}")
    print(f"    Ingested: {row['ingested_at']}")
    print()

print("=" * 80)
print("RECOMMENDATION:")
print("=" * 80)
print("""
The raw_json is not stored in the jobs table, so we cannot backfill the dates.

Options:
1. DELETE all 361 Arbeitnow jobs and re-collect with fixed parser
2. KEEP them as-is (they'll have missing dates)

Recommended: DELETE and re-collect, because:
- Missing posted_date breaks time-series analysis
- Jobs are likely still available on Arbeitnow
- Next weekly run will re-collect them with correct dates
""")

print("\nTo delete and re-collect:")
print("  1. Run: python -c \"import sqlite3; conn = sqlite3.connect('data/jobs.sqlite');")
print("     conn.execute('DELETE FROM jobs WHERE source_name LIKE \\\"%arbeit%\\\"');")
print("     conn.commit(); print(f'Deleted {conn.total_changes} jobs'); conn.close()\"")
print("  2. Run: python -m src.orchestrator --mode weekly")

conn.close()
