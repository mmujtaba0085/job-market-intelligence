"""Test analytics query to see if data displays correctly."""
import sys
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db import get_connection
from datetime import datetime, timedelta

try:
    conn = get_connection()
    
    # Same query as analytics page
    cutoff_date = (datetime.now() - timedelta(days=30)).isoformat()
    print(f"Filtering clicks after: {cutoff_date}")
    
    most_clicked = conn.execute("""
        SELECT 
            country,
            tab_name,
            COUNT(*) as click_count,
            COUNT(DISTINCT user_identifier) as unique_users,
            MAX(clicked_at) as last_clicked
        FROM sheets_click_tracking
        WHERE clicked_at >= ?
        GROUP BY country, tab_name
        ORDER BY click_count DESC
        LIMIT 20
    """, [cutoff_date]).fetchall()
    
    print(f"\nMost clicked tabs: {len(most_clicked)} results")
    for row in most_clicked:
        print(f"  {row['country']} / {row['tab_name']}: {row['click_count']} clicks, {row['unique_users']} users, last: {row['last_clicked']}")
    
    conn.close()
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)
