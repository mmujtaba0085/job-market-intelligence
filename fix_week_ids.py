"""
fix_week_ids.py
───────────────
Backfill week_id for existing jobs based on posted_date.

This script:
1. Adds week_id column if missing
2. Calculates week_id from posted_date (YYYY-WW format)
3. Updates all existing jobs
4. Does NOT re-ingest data
"""

import sqlite3
from datetime import datetime
from pathlib import Path


def get_week_id_from_date(date_str: str) -> str:
    """
    Convert ISO date string to week_id (YYYY-WW format).
    
    Args:
        date_str: ISO date string like "2026-02-15"
    
    Returns:
        Week ID like "2026-07"
    """
    if not date_str:
        return "unknown"
    
    try:
        dt = datetime.fromisoformat(date_str)
        # ISO week date: year and week number
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-{iso_week:02d}"
    except Exception:
        return "unknown"


def main():
    db_path = Path("data/jobs.sqlite")
    
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("=" * 60)
    print("WEEK_ID BACKFILL SCRIPT")
    print("=" * 60)
    
    # Step 1: Check if week_id column exists
    cursor.execute("PRAGMA table_info(jobs)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "week_id" not in columns:
        print("\n✅ Adding week_id column to jobs table...")
        cursor.execute("ALTER TABLE jobs ADD COLUMN week_id TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_week ON jobs(week_id)")
        conn.commit()
        print("   Column added successfully!")
    else:
        print("\n✅ week_id column already exists")
    
    # Step 2: Count jobs needing update
    cursor.execute("SELECT COUNT(*) FROM jobs WHERE week_id IS NULL OR week_id = ''")
    jobs_to_update = cursor.fetchone()[0]
    
    print(f"\n📊 Jobs needing week_id update: {jobs_to_update:,}")
    
    if jobs_to_update == 0:
        print("\n✅ All jobs already have week_id assigned!")
        conn.close()
        return
    
    # Step 3: Fetch all jobs that need week_id
    cursor.execute("""
        SELECT job_id, posted_date, ingested_at 
        FROM jobs 
        WHERE week_id IS NULL OR week_id = ''
    """)
    
    jobs = cursor.fetchall()
    print(f"\n🔄 Processing {len(jobs):,} jobs...")
    
    # Step 4: Calculate week_id for each job
    updates = []
    stats = {
        "from_posted_date": 0,
        "from_ingested_at": 0,
        "unknown": 0
    }
    
    for job_id, posted_date, ingested_at in jobs:
        # Prefer posted_date, fallback to ingested_at
        if posted_date:
            week_id = get_week_id_from_date(posted_date)
            stats["from_posted_date"] += 1
        elif ingested_at:
            week_id = get_week_id_from_date(ingested_at[:10])  # Extract date part
            stats["from_ingested_at"] += 1
        else:
            week_id = "unknown"
            stats["unknown"] += 1
        
        updates.append((week_id, job_id))
    
    # Step 5: Batch update
    print(f"\n💾 Updating database...")
    cursor.executemany("UPDATE jobs SET week_id = ? WHERE job_id = ?", updates)
    conn.commit()
    
    print(f"\n✅ Updated {len(updates):,} jobs!")
    print(f"   - From posted_date:  {stats['from_posted_date']:,}")
    print(f"   - From ingested_at:  {stats['from_ingested_at']:,}")
    print(f"   - Unknown:           {stats['unknown']:,}")
    
    # Step 6: Show week distribution
    print("\n" + "=" * 60)
    print("WEEK DISTRIBUTION")
    print("=" * 60)
    
    cursor.execute("""
        SELECT 
            week_id, 
            COUNT(*) as jobs,
            MIN(posted_date) as earliest,
            MAX(posted_date) as latest
        FROM jobs
        WHERE week_id != 'unknown'
        GROUP BY week_id
        ORDER BY week_id DESC
        LIMIT 20
    """)
    
    print(f"\n{'Week ID':<12} {'Jobs':>8} {'Earliest':>12} {'Latest':>12}")
    print("-" * 60)
    
    for row in cursor.fetchall():
        week_id, count, earliest, latest = row
        print(f"{week_id:<12} {count:>8,} {earliest or 'N/A':>12} {latest or 'N/A':>12}")
    
    # Step 7: Check if we have enough for growth metrics
    cursor.execute("""
        SELECT COUNT(DISTINCT week_id) 
        FROM jobs 
        WHERE week_id != 'unknown'
    """)
    unique_weeks = cursor.fetchone()[0]
    
    print("\n" + "=" * 60)
    print(f"📈 Total weeks with data: {unique_weeks}")
    
    if unique_weeks >= 2:
        print(f"✅ Growth metrics can now be calculated!")
        print(f"   Run: python -m src.orchestrator --mode weekly")
    else:
        print(f"⚠️  Only {unique_weeks} week(s) - need at least 2 for growth")
    
    print("=" * 60)
    
    conn.close()
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
