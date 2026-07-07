#!/usr/bin/env python3
"""Quick check for GitHub jobs with missing dates."""

import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import DB_PATH

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Count total jobs without dates
cursor.execute("""
  SELECT COUNT(*), COUNT(DISTINCT source_name)
    FROM jobs
    WHERE source_name LIKE '%github.com%'
      AND (posted_date IS NULL OR posted_date = '')
""")
total_jobs, affected_repos = cursor.fetchone()

print(f"GitHub jobs without dates: {total_jobs}")
print(f"Affected repos: {affected_repos}")
print()

# Show breakdown by repo
cursor.execute("""
    SELECT source_name, COUNT(*) as count
    FROM jobs
    WHERE source_name LIKE '%github.com%'
      AND (posted_date IS NULL OR posted_date = '')
    GROUP BY source_name
    ORDER BY count DESC
""")

repos = cursor.fetchall()
if repos:
    print("Breakdown by repo:")
    for repo_name, count in repos:
        print(f"  • {repo_name}: {count} jobs")

conn.close()
