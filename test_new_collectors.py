"""
Quick test script to verify new collectors are properly registered.
Run: python test_new_collectors.py
"""

from config.sources import SOURCES_BY_ID, ALLOWED_SOURCES
from config.markets import TARGET_MARKETS

print("="*80)
print("NEW COLLECTORS REGISTRATION TEST")
print("="*80)

# Test 1: Check sources are registered
print("\n1. Checking source registration...")
required_sources = ["arbeitnow", "usajobs", "themuse", "graphqljobs"]

for source_id in required_sources:
    if source_id in SOURCES_BY_ID:
        source = SOURCES_BY_ID[source_id]
        status = "🟢 ENABLED" if source.get("enabled") else "🔴 DISABLED"
        print(f"   ✅ {source['display_name']:20} → {status}")
    else:
        print(f"   ❌ {source_id} NOT FOUND in SOURCES_BY_ID")

# Test 2: Try to instantiate collectors
print("\n2. Testing collector instantiation...")

try:
    from src.collectors.arbeitnow_collector import ArbeitnowCollector
    print("   ✅ ArbeitnowCollector imported successfully")
except Exception as e:
    print(f"   ❌ ArbeitnowCollector failed: {e}")

try:
    from src.collectors.usajobs_collector import USAJobsCollector
    print("   ✅ USAJobsCollector imported successfully")
except Exception as e:
    print(f"   ❌ USAJobsCollector failed: {e}")

try:
    from src.collectors.themuse_collector import TheMuseCollector
    print("   ✅ TheMuseCollector imported successfully")
except Exception as e:
    print(f"   ❌ TheMuseCollector failed: {e}")

try:
    from src.collectors.graphqljobs_collector import GraphQLJobsCollector
    print("   ✅ GraphQLJobsCollector imported successfully")
except Exception as e:
    print(f"   ❌ GraphQLJobsCollector failed: {e}")

# Test 3: Check orchestrator registration
print("\n3. Checking orchestrator registration...")
try:
    from src.orchestrator import COLLECTORS
    collector_ids = [c.source_id for c in COLLECTORS]
    
    for source_id in required_sources:
        if source_id in collector_ids:
            print(f"   ✅ {source_id} registered in orchestrator")
        else:
            print(f"   ⚠️  {source_id} NOT in orchestrator (may be disabled)")
    
    print(f"\n   Total collectors registered: {len(COLLECTORS)}")
    print(f"   Collectors: {', '.join(collector_ids)}")
    
except Exception as e:
    print(f"   ❌ Orchestrator check failed: {e}")

# Test 4: Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"Total sources in config: {len(ALLOWED_SOURCES)}")
print(f"Enabled sources: {len([s for s in ALLOWED_SOURCES if s.get('enabled')])}")
print(f"Disabled sources: {len([s for s in ALLOWED_SOURCES if not s.get('enabled')])}")

print("\nTo enable a source:")
print("  1. Edit config/sources.py")
print("  2. Find the source dict (e.g., 'arbeitnow')")
print("  3. Change 'enabled': False → 'enabled': True")
print("  4. For USAJobs, also add API key to .env")

print("\nTo test collection:")
print("  python -m src.orchestrator --mode weekly")
print("\n" + "="*80)
