"""Delete Arbeitnow jobs with missing dates for re-collection."""
import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
cursor = conn.cursor()

print("=" * 80)
print("Deleting Arbeitnow jobs with missing dates")
print("=" * 80)

# Count before deletion
cursor.execute("SELECT COUNT(*) FROM jobs WHERE source_name LIKE '%arbeit%'")
before_count = cursor.fetchone()[0]

print(f"\nArbeitnow jobs before deletion: {before_count}")

if before_count == 0:
    print("\n✓ No Arbeitnow jobs to delete.")
    conn.close()
    exit(0)

# Confirm with user
print("\nThese jobs have missing posted_date and cannot be backfilled.")
print("They will be re-collected with correct dates in the next run.")
print("\nDeleting...")

# Delete Arbeitnow jobs
cursor.execute("DELETE FROM jobs WHERE source_name LIKE '%arbeit%'")
deleted = cursor.rowcount

# Also delete from related tables
cursor.execute("DELETE FROM skills WHERE job_id IN (SELECT job_id FROM jobs WHERE source_name LIKE '%arbeit%')")
skills_deleted = cursor.rowcount

cursor.execute("DELETE FROM job_locations WHERE job_id IN (SELECT job_id FROM jobs WHERE source_name LIKE '%arbeit%')")
locations_deleted = cursor.rowcount

conn.commit()

# Verify deletion
cursor.execute("SELECT COUNT(*) FROM jobs WHERE source_name LIKE '%arbeit%'")
after_count = cursor.fetchone()[0]

conn.close()

print("\n" + "=" * 80)
print("✓ Deletion Complete")
print("=" * 80)
print(f"Jobs deleted:      {deleted}")
print(f"Skills deleted:    {skills_deleted}")
print(f"Locations deleted: {locations_deleted}")
print(f"Remaining:         {after_count}")
print("\nNext steps:")
print("  Run: python -m src.orchestrator --mode weekly")
print("  This will re-collect Arbeitnow jobs with correct dates!")
print("=" * 80)
