"""Quick test to verify JSearch request parameters match RapidAPI format."""
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.markets import TARGET_MARKETS
from src.collectors.jsearch_collector import JSearchCollector

# Set up minimal logging to see debug output
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Create collector
collector = JSearchCollector()

# Get first market
market = TARGET_MARKETS[0]

# Show what parameters would be sent (without actually calling API)
print("\n" + "="*80)
print("JSEARCH REQUEST PARAMETER TEST")
print("="*80)
print(f"\nMarket: {market['market_id']}")
print(f"Keywords: {market['keywords'][:3]}...")  # Show first 3
print(f"Countries: {market['countries']}")
print("\nExpected request format per RapidAPI docs:")
print("  query: <keyword only>")
print("  country: <ISO-2 code>")
print("  page: <number>")
print("  num_pages: '1'")
print("  date_posted: 'week'")
print("\n" + "-"*80)
print("ATTEMPTING COLLECTION (will likely hit rate limit):")
print("-"*80 + "\n")

# Try to collect (will hit rate limit but we'll see the log messages)
raw = collector.collect(market)

print("\n" + "="*80)
print(f"Result: {len(raw)} jobs collected")
print("="*80)
if len(raw) > 0:
    print(f"\nSample job: {raw[0].title} @ {raw[0].company}")
