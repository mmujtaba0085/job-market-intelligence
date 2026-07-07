import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.country_detector import detect_country, should_auto_apply

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get sample Unknown jobs (same query as endpoint!)
rows = cur.execute('''
    SELECT DISTINCT location 
    FROM jobs 
    WHERE (country = "Unknown" OR country IS NULL OR country = "")
      AND location IS NOT NULL 
      AND location != ""
      AND location != "Unknown"
''').fetchall()  # Get ALL, not just 50

print(f"Testing {len(rows)} unique locations:\n")

# First check encoding
print("Sample location encoding check:")
for i, row in enumerate(rows[:3]):
    location = row[0]
    print(f"  {i+1}. {location!r}")
print()

stats = {"will_apply": 0, "low_conf": 0, "failed": 0}

for row in rows:
    location = row[0]
    country, confidence = detect_country(location, use_geopy=False)  # Skip geopy to avoid rate limits
    
    if country and should_auto_apply(confidence):
        stats["will_apply"] += 1
        print(f"OK {location:30} -> {country:20} ({confidence:.2f})")
    elif country:
        stats["low_conf"] += 1
        print(f"?? {location:30} -> {country:20} ({confidence:.2f}) [LOW]")
    else:
        stats["failed"] += 1
        print(f"XX {location:30} -> FAILED")

print(f"\n{'='*70}")
print(f"Will apply: {stats['will_apply']}")
print(f"Low confidence: {stats['low_conf']}")
print(f"Failed: {stats['failed']}")

conn.close()
