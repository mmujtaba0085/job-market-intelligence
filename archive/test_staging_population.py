"""
Test Google Sheets staging population with sample data
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, timedelta
from src.orchestrator import populate_sheets_staging
from src.storage.db import get_connection as get_db_connection

if __name__ == "__main__":
    print("Testing Google Sheets Staging Population...")
    print("=" * 60)
    
    # Get current week
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)
    
    print(f"Week: {week_start} to {week_end}")
    print()
    
    # Check how many jobs exist for this week
    conn = get_db_connection()
    cursor = conn.execute("""
        SELECT COUNT(*) as count
        FROM jobs
        WHERE posted_date >= ? AND posted_date < ?
          AND country IN ('Canada', 'United Kingdom', 'United States')
    """, (week_start.isoformat(), week_end.isoformat()))
    
    jobs_count = cursor.fetchone()[0]
    print(f"Jobs in database (Canada/UK/US): {jobs_count}")
    
    if jobs_count == 0:
        print("\nNo jobs found for this week. Run the orchestrator first:")
        print("   python -m src.orchestrator --mode weekly")
        conn.close()
        sys.exit(0)
    
    # Clear existing staging for testing
    print("\nClearing existing staging data...")
    conn.execute("DELETE FROM sheets_staging")
    conn.commit()
    
    # Populate staging
    print("Populating staging...")
    populate_sheets_staging("ai_ml_global", week_start, week_end)
    
    # Check results
    print("\nStaging Results:")
    print("-" * 60)
    
    cursor = conn.execute("""
        SELECT 
            assigned_sheet,
            assigned_tab,
            status,
            COUNT(*) as count
        FROM sheets_staging
        GROUP BY assigned_sheet, assigned_tab, status
        ORDER BY assigned_sheet, assigned_tab
    """)
    
    results = cursor.fetchall()
    
    if not results:
        print("No jobs added to staging (check SHEETS_ENABLED in .env)")
    else:
        for row in results:
            country = row[0]
            tab = row[1]
            status = row[2]
            count = row[3]
            print(f"{country:20s} | {tab:30s} | {status:10s} | {count:4d} jobs")
    
    # Summary
    total = conn.execute("SELECT COUNT(*) FROM sheets_staging").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM sheets_staging WHERE status='pending'").fetchone()[0]
    
    conn.close()
    
    print("-" * 60)
    print(f"\nTotal jobs in staging: {total}")
    print(f"Pending upload: {pending}")
    print("\nNext steps:")
    print("1. Start web viewer: python web_viewer.py")
    print("2. Visit: http://localhost:5000/admin/sheets_staging")
    print("3. Review and upload jobs")
