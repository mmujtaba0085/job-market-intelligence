import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get count
total = cur.execute('SELECT COUNT(*) FROM jobs WHERE country="Unknown"').fetchone()[0]
print(f"Total Unknown jobs: {total}")

# Get count with location filter (same as endpoint)
total_with_location = cur.execute('''
    SELECT COUNT(*) FROM jobs 
    WHERE (country = "Unknown" OR country IS NULL OR country = "")
      AND location IS NOT NULL 
      AND location != ""
      AND location != "Unknown"
''').fetchone()[0]
print(f"Unknown jobs with valid location: {total_with_location}")

# Get sample locations
rows = cur.execute('''
    SELECT DISTINCT location 
    FROM jobs 
    WHERE country = "Unknown" 
    AND location IS NOT NULL 
    AND location != ''
    LIMIT 20
''').fetchall()

print(f"\nSample Unknown locations ({len(rows)}):")
for row in rows:
    print(f"  - {row[0]}")

conn.close()
