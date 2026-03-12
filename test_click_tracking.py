"""Quick test to verify click tracking database setup."""
from src.storage.db import get_connection

conn = get_connection()

# Check if table exists
table_check = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_click_tracking'"
).fetchone()

print(f"Table exists: {table_check is not None}")

if table_check:
    # Check row count
    count = conn.execute("SELECT COUNT(*) FROM sheets_click_tracking").fetchone()[0]
    print(f"Total clicks recorded: {count}")
    
    # Show sample data if any
    if count > 0:
        print("\nSample clicks:")
        rows = conn.execute("""
            SELECT country, tab_name, clicked_at 
            FROM sheets_click_tracking 
            ORDER BY clicked_at DESC 
            LIMIT 5
        """).fetchall()
        for row in rows:
            print(f"  {row['country']} / {row['tab_name']} - {row['clicked_at']}")
    else:
        print("\nNo clicks recorded yet.")

conn.close()
