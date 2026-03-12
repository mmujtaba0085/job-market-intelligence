#!/usr/bin/env python3
"""
Temporary GitHub Date Backfill via Matching
Re-scrapes GitHub repos and updates posted_date for existing jobs by matching title+company.
Run once, then delete this file.
"""

import sys
import os
import sqlite3
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.collectors.github_repo_collector import GitHubRepoCollector
from config.settings import DB_PATH
from config.sources import ALLOWED_SOURCES


def get_github_source_configs():
    """Get GitHub source configurations from config."""
    github_sources = {}
    for source in ALLOWED_SOURCES:
        if source.get("source_type") == "GITHUB_LIST":
            source_id = source["source_id"]
            repos = source.get("repos", [])
            github_sources[source_id] = {
                "display_name": source["display_name"],
                "repos": repos
            }
    return github_sources


def get_jobs_by_source(source_pattern):
    """Get all jobs from DB matching source_name pattern."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT job_id, title, company, location, posted_date, url, source_name
        FROM jobs
        WHERE source_name LIKE ?
          AND (posted_date IS NULL OR posted_date = '')
    """
    
    cursor.execute(query, (source_pattern,))
    jobs = cursor.fetchall()
    conn.close()
    
    return jobs


def normalize_text(text):
    """Normalize text for matching."""
    if not text:
        return ""
    return text.lower().strip().replace("  ", " ")


def find_match(db_job, fresh_jobs):
    """Find matching job from fresh data by title+company."""
    db_title = normalize_text(db_job[1])
    db_company = normalize_text(db_job[2])
    
    for fresh in fresh_jobs:
        fresh_title = normalize_text(fresh.title)
        fresh_company = normalize_text(fresh.company)
        
        # Exact match
        if db_title == fresh_title and db_company == fresh_company:
            return fresh
    
    # Fuzzy match on title only (80% of words match)
    for fresh in fresh_jobs:
        fresh_title = normalize_text(fresh.title)
        if db_title == fresh_title:
            db_words = set(db_company.split())
            fresh_words = set(normalize_text(fresh.company).split())
            if db_words and fresh_words:
                overlap = len(db_words & fresh_words) / max(len(db_words), len(fresh_words))
                if overlap >= 0.6:
                    return fresh
    
    return None


def update_posted_date(job_id, new_date):
    """Update posted_date for a specific job."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET posted_date = ? WHERE job_id = ?", (new_date, job_id))
    conn.commit()
    conn.close()


def main():
    print("=" * 80)
    print("GitHub Date Backfill via Matching (Temporary Script)")
    print("=" * 80)
    print()
    print("This script:")
    print("  1. Re-scrapes GitHub repos with enhanced date parser")
    print("  2. Matches existing DB jobs by title+company")
    print("  3. Updates posted_date for matched jobs")
    print()
    print("-" * 80)
    
    # Get GitHub sources with missing dates
    print("Checking for GitHub jobs with missing dates...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT source_name, COUNT(*)
        FROM jobs
        WHERE source_name LIKE 'GitHub:%'
          AND (posted_date IS NULL OR posted_date = '')
        GROUP BY source_name
    """)
    sources_with_missing = cursor.fetchall()
    conn.close()
    
    if not sources_with_missing:
        print("✓ No jobs with missing dates found!")
        return
    
    print(f"\nFound {len(sources_with_missing)} sources:")
    for source_name, count in sources_with_missing:
        print(f"  • {source_name}: {count} jobs")
    
    print("\n" + "-" * 80)
    print("Starting backfill process...")
    print("-" * 80 + "\n")
    
    # Get config
    github_configs = get_github_source_configs()
    
    # We'll initialize collector for each source
    total_updated = 0
    total_unmatched = 0    
    # Process each source
    for source_name, count in sources_with_missing:
        print(f"\nProcessing {source_name} ({count} jobs)...")
        
        # Get DB jobs without dates
        db_jobs = get_jobs_by_source(source_name)
        
        # Find matching config and re-scrape
        # Extract source_id from source_name pattern
        # source_name format: "GitHub:owner/repo"
        
        # Try to match with configs
        fresh_jobs = []
        matched_config = False
        
        for config_id, config_data in github_configs.items():
            for repo_spec in config_data["repos"]:
                repo_tag = f"{repo_spec['repo_owner']}/{repo_spec['repo_name']}"
                if repo_tag in source_name:
                    # Found matching config, create collector and re-scrape
                    print(f"  Re-scraping {repo_tag}...")
                    try:
                        # Create collector with correct source_id
                        collector = GitHubRepoCollector()
                        collector.source_id = config_id
                        collector._source_cfg = [s for s in ALLOWED_SOURCES if s["source_id"] == config_id][0]
                        
                        spec = collector._parse_repo_spec(repo_spec)
                        fresh_jobs = collector._collect_repo(spec, max_jobs=5000)
                        print(f"  Collected {len(fresh_jobs)} fresh jobs")
                        matched_config = True
                        break
                    except Exception as e:
                        print(f"  ERROR: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
            
            if matched_config:
                break
        
        if not fresh_jobs:
            print(f"  WARNING: Could not re-scrape this repo.")
            total_unmatched += len(db_jobs)
            continue
        
        # Match and update
        updated = 0
        unmatched = 0
        
        for job_id, title, company, location, old_date, url, src_name in db_jobs:
            match = find_match((job_id, title, company, location, old_date, url, src_name), fresh_jobs)
            
            if match and match.posted_date:
                update_posted_date(job_id, match.posted_date)
                updated += 1
                if updated <= 5:  # Show first 5
                    print(f"    ✓ {title[:40]}... → {match.posted_date}")
            else:
                unmatched += 1
        
        if updated > 5:
            print(f"    ... and {updated - 5} more")
        
        print(f"  Result: {updated} updated, {unmatched} unmatched")
        total_updated += updated
        total_unmatched += unmatched
    
    print("\n" + "=" * 80)
    print("BACKFILL COMPLETE")
    print("=" * 80)
    print(f"✓ Jobs updated: {total_updated}")
    print(f"✗ Jobs unmatched: {total_unmatched}")
    print()
    
    if total_unmatched > 0:
        print(f"NOTE: {total_unmatched} jobs could not be matched.")
        print("Possible reasons:")
        print("  • Job removed from repo")
        print("  • Title/company changed in repo")
        print("  • Repo format changed")
        print()
        print("You can delete these manually or leave them.")
        print()
    
    print("SUCCESS! You can now delete this temporary script:")
    print("  Remove-Item scripts\\backfill_via_matching.py")
    print()


if __name__ == "__main__":
    main()
