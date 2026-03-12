"""
Restage uploaded jobs for re-export with new tracking URLs.

This script resets jobs from 'staged'/'uploaded' status back to 'pending'
so they can be re-uploaded with updated tracking URLs (e.g., after
redeploying Google Apps Script and getting a new deployment URL).
"""
import sqlite3
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import DB_PATH

def restage_jobs():
    """Reset staged/uploaded jobs back to pending status."""
    conn = sqlite3.connect(DB_PATH)
    
    # Count jobs to restage
    count_query = """
        SELECT COUNT(*) FROM sheets_staging 
        WHERE status IN ('staged', 'uploaded')
    """
    
    count = conn.execute(count_query).fetchone()[0]
    
    if count == 0:
        print("✅ No jobs to restage - all jobs are already pending")
        conn.close()
        return
    
    print(f"🔄 Found {count} jobs to restage")
    print()
    
    # Show breakdown by status
    breakdown = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM sheets_staging
        WHERE status IN ('staged', 'uploaded')
        GROUP BY status
    """).fetchall()
    
    for status, num in breakdown:
        print(f"   {status}: {num} jobs")
    
    print()
    response = input(f"Reset these {count} jobs to 'pending' status? (yes/no): ")
    
    if response.lower() not in ['yes', 'y']:
        print("❌ Cancelled - no changes made")
        conn.close()
        return
    
    # Reset to pending
    update_query = """
        UPDATE sheets_staging
        SET status = 'pending',
            uploaded_at = NULL,
            upload_batch_id = NULL
        WHERE status IN ('staged', 'uploaded')
    """
    
    cursor = conn.execute(update_query)
    conn.commit()
    
    updated = cursor.rowcount
    
    print()
    print(f"✅ Successfully restaged {updated} jobs to 'pending' status")
    print()
    print("Next steps:")
    print("1. Update .env with new TRACKER_DEPLOYMENT_BASE_URL")
    print("2. Run: python -m src.orchestrator --mode weekly")
    print("3. Upload via web viewer")
    print()
    
    conn.close()

if __name__ == "__main__":
    restage_jobs()
