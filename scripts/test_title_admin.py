"""Test the title admin filtering and is_manual flag."""
import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 80)
print("Testing Title Admin Query with Filters")
print("=" * 80)

# Test query with is_manual flag
query = """
    SELECT 
        title,
        normalized_title,
        COUNT(*) as count,
        ROUND(AVG(normalization_confidence) * 100, 1) as avg_conf,
        MAX(CASE WHEN normalization_confidence = 1.0 THEN 1 ELSE 0 END) as is_manual
    FROM jobs
    GROUP BY title, normalized_title
    HAVING count >= 2
    ORDER BY count DESC
    LIMIT 10
"""

cursor.execute(query)
rows = cursor.fetchall()

print(f"\nTop 10 titles (with is_manual flag):")
print("-" * 80)
for row in rows:
    manual_flag = "🔧 MANUAL" if row['is_manual'] == 1 else "🤖 AUTO  "
    title_short = row['title'][:50] if len(row['title']) > 50 else row['title']
    print(f"{manual_flag} | {row['count']:4d} jobs | {row['avg_conf']:5.1f}% | {title_short}")

# Count manually normalized
cursor.execute("SELECT COUNT(DISTINCT title) FROM jobs WHERE normalization_confidence = 1.0")
manual_count = cursor.fetchone()[0]

# Count titles with ≥2 jobs
cursor.execute("SELECT COUNT(*) FROM (SELECT title FROM jobs GROUP BY title HAVING COUNT(*) >= 2)")
multi_job_count = cursor.fetchone()[0]

print("\n" + "=" * 80)
print(f"Manually normalized titles: {manual_count}")
print(f"Titles with ≥2 jobs: {multi_job_count}")
print("=" * 80)

# Test manual filter
print("\nTesting MANUAL filter:")
manual_query = """
    SELECT 
        title,
        normalized_title,
        COUNT(*) as count,
        ROUND(AVG(normalization_confidence) * 100, 1) as avg_conf,
        MAX(CASE WHEN normalization_confidence = 1.0 THEN 1 ELSE 0 END) as is_manual
    FROM jobs
    GROUP BY title, normalized_title
    HAVING count >= 2
       AND MAX(CASE WHEN normalization_confidence = 1.0 THEN 1 ELSE 0 END) = 1
    ORDER BY count DESC
    LIMIT 10
"""
cursor.execute(manual_query)
manual_rows = cursor.fetchall()
print(f"Found {len(manual_rows)} manually normalized titles")

# Test low confidence filter
print("\nTesting LOW CONFIDENCE filter:")
low_conf_query = """
    SELECT 
        title,
        normalized_title,
        COUNT(*) as count,
        ROUND(AVG(normalization_confidence) * 100, 1) as avg_conf,
        MAX(CASE WHEN normalization_confidence = 1.0 THEN 1 ELSE 0 END) as is_manual
    FROM jobs
    GROUP BY title, normalized_title
    HAVING count >= 2
       AND ROUND(AVG(normalization_confidence) * 100, 1) < 60
       AND ROUND(AVG(normalization_confidence) * 100, 1) > 0
    ORDER BY count DESC
    LIMIT 10
"""
cursor.execute(low_conf_query)
low_conf_rows = cursor.fetchall()
print(f"Found {len(low_conf_rows)} low confidence titles")

conn.close()
print("\n✓ All queries executed successfully!")
