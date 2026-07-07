#!/usr/bin/env python3
"""
Check for GitHub repo jobs with missing posted_date values.
Recommends deleting and re-collecting with the enhanced date parser.
"""

import sys
import os
import sqlite3

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.settings import DB_PATH


def get_repos_with_missing_dates():
    """Get list of GitHub sources that have jobs with missing dates."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT DISTINCT source_name, COUNT(*) as missing_count
        FROM jobs
        WHERE source_name LIKE 'GitHub:%'
          AND (posted_date IS NULL OR posted_date = '')
        GROUP BY source_name
        ORDER BY source_name
    """
    
    cursor.execute(query)
    repos = cursor.fetchall()
    conn.close()
    
    return repos


def main():
    print("=" * 80)
    print("GitHub Repo Date Check")
    print("=" * 80)
    print()
    print("Checking for GitHub jobs with missing dates...")
    print()
    
    # Get repos with missing dates
    repos = get_repos_with_missing_dates()
    
    if not repos:
        print("✓ No GitHub jobs found with missing dates!")
        print()
        print("Either:")
        print("  • No GitHub repos have been collected yet")
        print("  • All existing GitHub jobs have correct dates")
        print()
        return
    
    print(f"Found {len(repos)} GitHub source(s) with missing dates:")
    total_jobs_missing = 0
    for source_name, count in repos:
        print(f"  • {source_name}: {count} jobs")
        total_jobs_missing += count
    
    print()
    print(f"Total jobs without dates: {total_jobs_missing}")
    print()
    print("-" * 80)
    print("RECOMMENDED FIX")
    print("-" * 80)
    print()
    print("Since GitHub jobs are quick to re-collect (just README files),")
    print("delete and re-collect for accurate dates:")
    print()
    print("1. Delete all GitHub jobs:")
    print("   sqlite3 data/jobs.sqlite \"DELETE FROM jobs WHERE source_name LIKE 'GitHub:%'\"")
    print()
    print("2. Run orchestrator to collect with enhanced date parser:")
    print("   python src/orchestrator.py")
    print()
    print("This ensures all dates are parsed correctly from the start.")
    print()


if __name__ == "__main__":
    main()
