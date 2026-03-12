"""Clear all data from the database."""
import sqlite3
from pathlib import Path

db_path = Path("data/jobs.sqlite")

if not db_path.exists():
    print("❌ Database doesn't exist yet")
    exit(0)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

print(f"Found {len(tables)} tables")

# Delete all data from each table
for (table_name,) in tables:
    cursor.execute(f"DELETE FROM {table_name}")
    deleted = cursor.rowcount
    print(f"  ✅ Cleared {deleted} rows from {table_name}")

conn.commit()
conn.close()

print("\n✅ All data cleared - database is empty and ready for fresh pull")
print("Run: python -m src.orchestrator --mode weekly")
