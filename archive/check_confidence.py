import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check if database has data
cursor.execute("SELECT COUNT(*) as count FROM jobs")
total_count = cursor.fetchone()['count']
print(f"Total jobs in database: {total_count:,}\n")

# Check a specific title (case-insensitive search using LIKE)
cursor.execute("""
    SELECT title, normalized_title, normalization_confidence 
    FROM jobs 
    WHERE title LIKE '%Software%Engineer%Intern%'
    AND title NOT LIKE '%/%'
    LIMIT 10
""")

rows = cursor.fetchall()
if rows:
    print(f"Sample: Software Engineer Intern jobs (found {len(rows)})")
    print("=" * 80)
    for row in rows:
        conf_pct = int(row['normalization_confidence'] * 100) if row['normalization_confidence'] else 0
        print(f"Title: {row['title']}")
        print(f"Normalized: {row['normalized_title']}")
        print(f"Confidence: {conf_pct}%")
        print()
else:
    print("No Software Engineer Intern jobs found")

# Check distribution
cursor.execute("""
    SELECT 
        CASE 
            WHEN normalization_confidence >= 0.9 THEN 'High (>=90%)'
            WHEN normalization_confidence >= 0.6 THEN 'Medium (>=60%)'
            WHEN normalization_confidence > 0.0 THEN 'Low (<60%)'
            ELSE 'Zero (0.0)'
        END as level,
        COUNT(*) as count
    FROM jobs
    GROUP BY level
    ORDER BY MIN(normalization_confidence) DESC
""")

print("\nConfidence Distribution:")
print("=" * 80)
for row in cursor.fetchall():
    print(f"{row['level']:20} {row['count']:6,} jobs")

conn.close()
