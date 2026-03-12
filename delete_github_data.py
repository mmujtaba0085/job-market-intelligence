"""Delete GitHub-sourced jobs from the database."""
import sys
from src.storage.db import get_connection

def main():
    conn = get_connection()
    
    # Check what GitHub data exists
    print("Checking GitHub data in database...")
    cursor = conn.execute(
        "SELECT source_name, COUNT(*) as count FROM jobs WHERE source_name LIKE 'GitHub:%' GROUP BY source_name"
    )
    jobs = cursor.fetchall()
    
    if not jobs:
        print("No GitHub data found in database.")
        conn.close()
        return
    
    print("\nGitHub data found:")
    for row in jobs:
        print(f"  {row[0]}: {row[1]} jobs")
    
    cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE source_name LIKE 'GitHub:%'")
    total = cursor.fetchone()[0]
    print(f"\nTotal GitHub jobs: {total}")
    
    # Auto-confirm if --yes flag provided
    if len(sys.argv) > 1 and sys.argv[1] == '--yes':
        print("\nAuto-confirming deletion (--yes flag provided)...")
    else:
        print("\nTo delete, run: python delete_github_data.py --yes")
        conn.close()
        return
    
    # Delete job_locations first (foreign key constraint)
    print("\nDeleting job_locations for GitHub jobs...")
    cursor = conn.execute("""
        DELETE FROM job_locations 
        WHERE job_id IN (SELECT job_id FROM jobs WHERE source_name LIKE 'GitHub:%')
    """)
    locations_deleted = cursor.rowcount
    
    # Delete jobs
    print("Deleting GitHub jobs...")
    cursor = conn.execute("DELETE FROM jobs WHERE source_name LIKE 'GitHub:%'")
    jobs_deleted = cursor.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"\n✓ Successfully deleted {jobs_deleted} GitHub jobs and {locations_deleted} locations.")
    print("Database cleaned.")

if __name__ == "__main__":
    main()
