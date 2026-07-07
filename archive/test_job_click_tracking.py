"""
Test job click tracking functionality.

This script tests:
1. Database schema has job_id and click_type columns
2. format_job_row returns the current export schema
3. Tracking URLs include tracker doc/id/token parameters
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from src.reports.google_sheets_export import format_job_row, JOB_COLUMNS
from src.storage.db import get_connection

print("=" * 70)
print("Testing Job Click Tracking")
print("=" * 70)

# Test 1: Verify database schema
print("\n[1/3] Verifying database schema...")
conn = get_connection()
schema = conn.execute("PRAGMA table_info(sheets_click_tracking)").fetchall()
columns = {row['name']: row for row in schema}

assert 'job_id' in columns, "FAIL: job_id column missing"
assert 'click_type' in columns, "FAIL: click_type column missing"
print("PASS: Database schema has job_id and click_type columns")

# Test 2: Verify current column headers
print("\n[2/3] Verifying column headers...")
expected_columns = [
    "link_id", "title", "company", "location", "country",
    "remote_type", "posted_date", "url"
]
assert JOB_COLUMNS == expected_columns, f"FAIL: JOB_COLUMNS mismatch: {JOB_COLUMNS}"
url_index = JOB_COLUMNS.index("url")
print("PASS: JOB_COLUMNS matches current schema")
print(f"   Columns: {', '.join(JOB_COLUMNS)}")

# Test 3: Test format_job_row with click tracking
print("\n[3/3] Testing format_job_row with click tracking...")

# Create a mock job
mock_job = {
    'job_id': 12345,
    'title': 'Senior Software Engineer',
    'normalized_title': 'Software Engineer',
    'company': 'Acme Corp',
    'location': 'Toronto, ON',
    'country': 'Canada',
    'remote_type': 'Hybrid',
    'posted_date': '2026-03-01',
    'source_name': 'JSearch',
    'url': 'https://example.com/jobs/12345'
}

row = format_job_row(
    mock_job,
    click_count=5,
    country="Canada",
    doc_key="ca",
    tracker_deployment_url="https://script.google.com/macros/s/TEST/exec",
    tracker_token="test_token"
)

assert len(row) == len(JOB_COLUMNS), f"FAIL: Row has {len(row)} values, expected {len(JOB_COLUMNS)}"

# Check URL fields
assert row[0] == "job_12345", f"FAIL: link_id mismatch: {row[0]}"
url_cell = row[url_index]
assert "script.google.com" in url_cell, "FAIL: Tracking URL missing tracker host"
assert "doc=ca" in url_cell, "FAIL: Tracking URL missing doc parameter"
assert "id=job_12345" in url_cell, "FAIL: Tracking URL missing id parameter"
assert "token=test_token" in url_cell, "FAIL: Tracking URL missing token parameter"

print("PASS: format_job_row creates correct tracker URL")
print(f"\n   Sample row:")
print(f"   Link ID: {row[0]}")
print(f"   Title: {row[1]}")
print(f"   Company: {row[2]}")
print(f"   URL Formula: {url_cell[:80]}...")

# Test 4: Verify click count query works
print("\n[BONUS] Testing click count query...")
test_job_id = 999999  # Non-existent for clean test
click_count = conn.execute("""
    SELECT COUNT(*) as count 
    FROM sheets_click_tracking 
    WHERE job_id = ? AND click_type = 'job_posting'
""", [test_job_id]).fetchone()['count']

assert click_count == 0, f"FAIL: Expected 0 clicks for test job, got {click_count}"
print(f"PASS: Click count query works (test job has {click_count} clicks)")

conn.close()

print("\n" + "=" * 70)
print("SUCCESS: All tests passed!")
print("=" * 70)
print("\nFeatures enabled:")
print("  - Country sheets use a privacy-safe schema")
print("  - Job rows include link_id + tracker redirect URL")
print("  - Apply URLs and click counters are centralized in Tracker->Directory")
print("\nNext steps:")
print("  1. Upload jobs to Google Sheets")
print("  2. Click a job URL from the sheet")
print("  3. Check /admin/sheets_analytics to see the click recorded")
print("\nNote: For Google Sheets to call your tracking URL, you need a")
print("      public URL (use ngrok or deploy to a server). Localhost won't work.")
