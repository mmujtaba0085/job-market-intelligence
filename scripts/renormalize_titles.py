"""
scripts/renormalize_titles.py
──────────────────────────────
Re-normalize all job titles using the updated title_normalizer.py mappings.

This script updates existing jobs in the database to use the new,
more aggressive title normalization rules to reduce tab fragmentation.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.db import get_connection
from src.title_normalizer import normalize_title

def main():
    conn = get_connection()
    cursor = conn.cursor()
    
    print("=" * 80)
    print("Re-normalizing Job Titles")
    print("=" * 80)
    
    # Get all jobs
    cursor.execute("SELECT job_id, title, normalized_title FROM jobs")
    jobs = cursor.fetchall()
    
    print(f"\nFound {len(jobs)} jobs to process")
    
    # Track changes
    changed = 0
    unchanged = 0
    changes_by_type = {}
    
    for job_id, title, old_normalized in jobs:
        # Re-normalize with new mappings
        new_normalized, confidence = normalize_title(title)
        
        if new_normalized != old_normalized:
            # Update the job
            cursor.execute(
                "UPDATE jobs SET normalized_title = ?, normalization_confidence = ? WHERE job_id = ?",
                (new_normalized, confidence, job_id)
            )
            
            # Track the change
            change_key = f"{old_normalized} -> {new_normalized}"
            changes_by_type[change_key] = changes_by_type.get(change_key, 0) + 1
            changed += 1
            
            # Show progress every 50 changes
            if changed % 50 == 0:
                print(f"  Processed {changed} changes...")
        else:
            unchanged += 1
    
    # Commit all changes
    conn.commit()
    
    print("\n" + "=" * 80)
    print("Results Summary")
    print("=" * 80)
    print(f"Total jobs processed: {len(jobs)}")
    print(f"Changed: {changed}")
    print(f"Unchanged: {unchanged}")
    print(f"Change rate: {changed / len(jobs) * 100:.1f}%")
    
    if changes_by_type:
        print("\n" + "=" * 80)
        print("Top 30 Changes (by frequency)")
        print("=" * 80)
        
        sorted_changes = sorted(changes_by_type.items(), key=lambda x: -x[1])
        for change, count in sorted_changes[:30]:
            print(f"{count:4} jobs: {change}")
    
    # Show new tab distribution
    print("\n" + "=" * 80)
    print("New Tab Distribution (Top 30)")
    print("=" * 80)
    
    cursor.execute("""
        SELECT normalized_title, COUNT(*) as cnt 
        FROM jobs 
        GROUP BY normalized_title 
        ORDER BY cnt DESC 
        LIMIT 30
    """)
    
    for normalized, count in cursor.fetchall():
        print(f"{count:4} jobs: {normalized}")
    
    # Count distinct normalized titles
    cursor.execute("SELECT COUNT(DISTINCT normalized_title) FROM jobs")
    distinct_count = cursor.fetchone()[0]
    
    print("\n" + "=" * 80)
    print(f"Total distinct tabs: {distinct_count}")
    print("=" * 80)
    
    conn.close()
    
    print("\nRe-normalization complete!")
    print("Next steps:")
    print("1. Run: python archive/backfill_normalized_titles.py  # Update sheets_staging")
    print("2. Check the staging UI: http://localhost:5000/admin/sheets_staging")
    print("3. Upload to Google Sheets to see consolidated tabs")

if __name__ == "__main__":
    main()
