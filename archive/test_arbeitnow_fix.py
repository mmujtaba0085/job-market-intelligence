"""Test the fixed Arbeitnow date parsing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.collectors.arbeitnow_collector import ArbeitnowCollector

collector = ArbeitnowCollector()

print("=" * 80)
print("Testing Arbeitnow _parse_date method")
print("=" * 80)

# Test cases
test_cases = [
    (1772443887, "Unix timestamp (int)"),
    (1704121800, "Unix timestamp (int) - older date"),
    ("2024-01-15T10:30:00Z", "ISO string format"),
    ("2026-03-02", "Plain date string"),
    (None, "None value"),
    ("", "Empty string"),
]

for value, description in test_cases:
    result = collector._parse_date(value)
    print(f"\nInput: {value!r} ({description})")
    print(f"Output: '{result}'")
    
    if isinstance(value, int):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
        print(f"Expected: {dt.strftime('%Y-%m-%d')}")
        print(f"✓ PASS" if result == dt.strftime('%Y-%m-%d') else f"✗ FAIL")

print("\n" + "=" * 80)
print("✓ All tests completed!")
print("=" * 80)
