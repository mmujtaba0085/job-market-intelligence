import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
c = conn.cursor()

# Check titles with ≥2 jobs
c.execute('''
    SELECT title, normalized_title, COUNT(*) as count 
    FROM jobs 
    GROUP BY title, normalized_title 
    HAVING count >= 2 
    ORDER BY count DESC 
    LIMIT 10
''')

rows = c.fetchall()

print("=" * 80)
print("SAMPLE TITLES IN ADMIN PANEL (Top 10 with ≥2 jobs)")
print("=" * 80)

if rows:
    for r in rows:
        count, title, normalized = r[2], r[0][:60], r[1][:60]
        print(f"{count:4d} jobs: '{title}' → '{normalized}'")
else:
    print("No titles with ≥2 jobs found!")

# Total stats
c.execute("SELECT COUNT(DISTINCT title) FROM jobs")
total_unique = c.fetchone()[0]

c.execute("SELECT COUNT(DISTINCT title) FROM jobs GROUP BY title HAVING COUNT(*) >= 2")
multi_job_titles = len(c.fetchall())

print("\n" + "=" * 80)
print(f"Total unique titles: {total_unique:,}")
print(f"Titles with ≥2 jobs: {multi_job_titles:,}")
print(f"Will show top 200 in admin panel")
print("=" * 80)

conn.close()
