"""
Dry-run fetch counter — calls each enabled collector and reports how many jobs
it returns without writing anything to the database.

Usage:
    python check_fetch_counts.py

Requires .env to be populated with any API keys (ADZUNA_APP_ID/KEY, etc.).
USAJobs is skipped automatically if USAJOBS_API_KEY is absent.
"""
import sys, time, logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.markets import TARGET_MARKETS
from src.collectors.remotive_collector   import RemotiveCollector
from src.collectors.arbeitnow_collector  import ArbeitnowCollector
from src.collectors.themuse_collector    import TheMuseCollector
from src.collectors.hireweb3_collector   import HireWeb3Collector
from src.collectors.adzuna_collector     import AdzunaCollector
from src.collectors.usajobs_collector    import USAJobsCollector

market = TARGET_MARKETS[0].copy()

COLLECTORS = [
    ("Remotive",   RemotiveCollector()),
    ("Arbeitnow",  ArbeitnowCollector()),
    ("TheMuse",    TheMuseCollector()),
    ("HireWeb3",   HireWeb3Collector()),
    ("Adzuna",     AdzunaCollector()),
    ("USAJobs",    USAJobsCollector()),
]

print(f"\nMarket : {market['market_id']}")
print(f"Cap    : {market.get('max_jobs_per_source', 500)} jobs/source")
print(f"KWs    : {len(market.get('keywords', []))} keywords\n")
print(f"{'Source':<14} {'Jobs':>6}  {'Time':>8}")
print("-" * 34)

grand_total = 0
for name, collector in COLLECTORS:
    t0 = time.time()
    try:
        jobs = collector._fetch_raw(market)
        n = len(jobs)
        grand_total += n
        tag = ""
    except Exception as e:
        n = 0
        tag = f"  ERROR: {e}"
    elapsed = time.time() - t0
    print(f"{name:<14} {n:>6}  {elapsed:>7.1f}s{tag}")

print("-" * 34)
print(f"{'TOTAL':<14} {grand_total:>6}")
print()
