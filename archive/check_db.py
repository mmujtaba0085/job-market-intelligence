import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print(f"Database connected successfully")
print(f"Tables created: {tables}")

# Check table counts
for table in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"{table}: {count} rows")

# Show sample job if any
jobs_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
if jobs_count > 0:
    print("\nSample job:")
    job = conn.execute("SELECT job_id, title, company, source_name FROM jobs LIMIT 1").fetchone()
    print(f"  ID: {job[0]}, Title: {job[1]}, Company: {job[2]}, Source: {job[3]}")

conn.close()
