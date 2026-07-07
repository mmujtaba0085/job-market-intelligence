import sqlite3

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 80)
print("CONFIDENCE BACKFILL SUMMARY")
print("=" * 80)

# Total jobs
cursor.execute("SELECT COUNT(*) as count FROM jobs")
total = cursor.fetchone()['count']
print(f"\nTotal jobs: {total:,}")

# Confidence distribution
cursor.execute("""
    SELECT 
        CASE 
            WHEN normalization_confidence >= 0.9 THEN 'High (>=90%)'
            WHEN normalization_confidence >= 0.6 THEN 'Medium (60-89%)'
            WHEN normalization_confidence > 0.0 THEN 'Low (1-59%)'
            ELSE 'None (0%)'
        END as level,
        COUNT(*) as count,
        ROUND(AVG(normalization_confidence) * 100, 1) as avg_pct
    FROM jobs
    GROUP BY level
    ORDER BY avg_pct DESC
""")

print("\n" + "=" * 80)
print("CONFIDENCE DISTRIBUTION")
print("=" * 80)
total_normalized = 0
for row in cursor.fetchall():
    pct = (row['count'] / total * 100)
    print(f"{row['level']:20} {row['count']:6,} jobs ({pct:5.1f}%) - avg: {row['avg_pct']}%")
    if row['level'] != 'None (0%)':
        total_normalized += row['count']

print(f"\nTotal normalized: {total_normalized:,} jobs ({total_normalized/total*100:.1f}%)")
print(f"No normalization: {total - total_normalized:,} jobs ({(total-total_normalized)/total*100:.1f}%)")

# Top normalized titles
cursor.execute("""
    SELECT 
        normalized_title,
        COUNT(*) as job_count,
        COUNT(DISTINCT title) as variant_count,
        ROUND(AVG(normalization_confidence) * 100, 1) as avg_conf
    FROM jobs
    WHERE normalization_confidence > 0.0
    GROUP BY normalized_title
    HAVING variant_count > 1
    ORDER BY job_count DESC
    LIMIT 10
""")

print("\n" + "=" * 80)
print("TOP CONSOLIDATIONS (multiple variants normalized to same title)")
print("=" * 80)
print(f"{'Normalized Title':<50} {'Jobs':>6} {'Variants':>8} {'Avg Conf':>9}")
print("-" * 80)
for row in cursor.fetchall():
    print(f"{row['normalized_title']:<50} {row['job_count']:>6} {row['variant_count']:>8} {row['avg_conf']:>8}%")

# Sample variants for top consolidation
cursor.execute("""
    SELECT DISTINCT title, normalized_title, normalization_confidence
    FROM jobs
    WHERE normalized_title = 'Software Engineer Intern'
    AND normalization_confidence > 0.0
    ORDER BY normalization_confidence DESC, title
    LIMIT 10
""")

print("\n" + "=" * 80)
print("EXAMPLE: 'Software Engineer Intern' variants")
print("=" * 80)
for row in cursor.fetchall():
    conf = int(row['normalization_confidence'] * 100)
    print(f"[{conf:3}%] '{row['title']}' -> '{row['normalized_title']}'")

# Check for truly low-confidence normalizations (>0% but <60%)
cursor.execute("""
    SELECT COUNT(*) as count
    FROM jobs
    WHERE normalization_confidence > 0.0 
    AND normalization_confidence < 0.6
""")

low_conf_count = cursor.fetchone()['count']
print("\n" + "=" * 80)
print(f"Low-confidence normalizations (>0% but <60%): {low_conf_count}")
print("=" * 80)
if low_conf_count == 0:
    print("✓ All normalizations have confidence >= 60%")
    print("✓ No low-confidence normalizations need review")

print("\n" + "=" * 80)
print("STATUS: Backfill complete ✓")
print("=" * 80)

conn.close()
