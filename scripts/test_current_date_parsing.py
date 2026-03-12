"""Test current GitHub date parsing for different formats."""
import sys
import os
import re
from datetime import datetime, timezone, timedelta

# Add parent dir to path to import src modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import just the parsing method by reading from the module
from src.collectors.github_repo_collector import GitHubRepoCollector

# Get the method without initializing the collector
parse_date = GitHubRepoCollector._parse_any_date

# Test cases from different repos
test_cases = [
    # SimplifyJobs format (works)
    ("5d", "5 days ago"),
    ("2w", "2 weeks ago"),
    ("1mo", "1 month ago"),
    
    # ISO formats (works)
    ("2026-03-02", "ISO date"),
    ("03/02/2026", "US date format"),
    
    # Jobright and vanshb03 formats (should work now)
    ("Dec 13", "Month Day - no year"),
    ("13 Dec", "Day Month - no year"),
    ("Dec 13, 2025", "Month Day Year with comma"),
    ("13 December 2025", "Day Month Year"),
    ("January 5", "Full month name and day"),
    ("5 Jan", "Day and abbreviated month"),
    
    # Edge cases
    ("", "Empty string"),
    ("   ", "Whitespace"),
]

print("=" * 80)
print("Testing Current GitHub Date Parsing")
print("=" * 80)

failures = []
for input_val, description in test_cases:
    result = parse_date(None, input_val)  # None for self since we're calling unbound
    status = "✓" if result else "✗"
    print(f"\n{status} Input: '{input_val}' ({description})")
    print(f"  Output: {result or 'None'}")
    
    if not result and input_val.strip():
        failures.append((input_val, description))

print("\n" + "=" * 80)
if failures:
    print(f"FAILURES: {len(failures)}")
    print("=" * 80)
    print("\nFormats that still need to be supported:")
    for inp, desc in failures:
        print(f"  • {inp:20s} ({desc})")
else:
    print("ALL TESTS PASSED! ✓")
    print("=" * 80)
