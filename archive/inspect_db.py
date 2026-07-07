"""Quick script to inspect database contents and show where all job data is stored."""
import sqlite3
from pathlib import Path

db_path = Path("data/jobs.sqlite")

if not db_path.exists():
    print(f"❌ Database not found at: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get total counts
cursor.execute("SELECT COUNT(*) FROM jobs")
total_jobs = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM skills")
total_skills = cursor.fetchone()[0]

print("="*80)
print("DATABASE OVERVIEW")
print("="*80)
print(f"Location: {db_path.absolute()}")
print(f"Total jobs: {total_jobs}")
print(f"Total skills: {total_skills}")
print()

# Show table structure
print("="*80)
print("JOBS TABLE STRUCTURE (all fields)")
print("="*80)
cursor.execute("PRAGMA table_info(jobs)")
columns = cursor.fetchall()
for col in columns:
    col_id, name, type_, notnull, default, pk = col
    print(f"  {name:20} {type_:10} {'PRIMARY KEY' if pk else ''} {'NOT NULL' if notnull else ''}")
print()

# Show sample job with ALL fields
print("="*80)
print("SAMPLE JOB RECORD (showing all stored data)")
print("="*80)
cursor.execute("""
    SELECT 
        job_id, market_id, source_name, url, title, company, 
        country, location, remote_type, posted_date, 
        salary_min, salary_max, currency, 
        raw_description, first_seen_at, last_seen_at, ingested_at
    FROM jobs 
    LIMIT 1
""")
row = cursor.fetchone()

if row:
    (job_id, market_id, source_name, url, title, company, 
     country, location, remote_type, posted_date,
     salary_min, salary_max, currency,
     raw_description, first_seen, last_seen, ingested) = row
    
    print(f"Job ID:          {job_id}")
    print(f"Market:          {market_id}")
    print(f"Source:          {source_name}")
    print(f"URL:             {url}")
    print(f"Title:           {title}")
    print(f"Company:         {company}")
    print(f"Country:         {country}")
    print(f"Location:        {location}")
    print(f"Remote Type:     {remote_type}")
    print(f"Posted Date:     {posted_date}")
    print(f"Salary Range:    {salary_min} - {salary_max} {currency}")
    print(f"First Seen:      {first_seen}")
    print(f"Last Seen:       {last_seen}")
    print(f"Ingested:        {ingested}")
    print()
    print(f"Description (first 500 chars):")
    print("-" * 80)
    print(raw_description[:500] if raw_description else "(None)")
    print("-" * 80)
    if raw_description and len(raw_description) > 500:
        print(f"... (total length: {len(raw_description)} characters)")
else:
    print("No jobs in database yet")

conn.close()
print()
print("="*80)
print("HOW TO ACCESS THE DATA:")
print("="*80)
print("1. Direct SQLite query:")
print(f"   sqlite3 {db_path}")
print("   SELECT * FROM jobs WHERE job_id = 1;")
print()
print("2. Python script:")
print("   import sqlite3")
print(f"   conn = sqlite3.connect('{db_path}')")
print("   cursor = conn.cursor()")
print("   cursor.execute('SELECT * FROM jobs')")
print("   jobs = cursor.fetchall()")
print()
print("3. Via src/storage/db.py functions:")
print("   from src.storage.db import get_connection")
print("   conn = get_connection()")
print("="*80)
