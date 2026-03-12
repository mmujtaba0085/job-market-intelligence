" ""Test Adzuna collector setup."""
import os

print("=" * 80)
print("ADZUNA COLLECTOR SETUP TEST")
print("=" * 80)

# 1. Check environment variables
print("\n1. Checking environment variables...")
app_id = os.getenv("ADZUNA_APP_ID")
app_key = os.getenv("ADZUNA_APP_KEY")

if app_id and app_key:
    print(f"   ✅ ADZUNA_APP_ID: {app_id[:8]}***")
    print(f"   ✅ ADZUNA_APP_KEY: {app_key[:8]}***")
else:
    print("   ❌ Missing credentials")
    if not app_id:
        print("      - ADZUNA_APP_ID not found in environment")
    if not app_key:
        print("      - ADZUNA_APP_KEY not found in environment")
    print("\n   To add credentials:")
    print("      1. Create/edit .env file in project root")
    print("      2. Add these lines:")
    print("         ADZUNA_APP_ID=your_app_id_here")
    print("         ADZUNA_APP_KEY=your_app_key_here")
    print("      3. Get credentials from: https://developer.adzuna.com/signup")

# 2. Check collector import
print("\n2. Testing collector import...")
try:
    from src.collectors.adzuna_collector import AdzunaCollector
    print("   ✅ AdzunaCollector imported successfully")
except Exception as e:
    print(f"   ❌ Import failed: {e}")
    exit(1)

# 3. Check source registration
print("\n3. Checking source registration...")
try:
    from config.sources import SOURCES_BY_ID
    if "adzuna" in SOURCES_BY_ID:
        source = SOURCES_BY_ID["adzuna"]
        print(f"   ✅ Source registered: {source['display_name']}")
        print(f"   - Enabled: {source.get('enabled', False)}")
        print(f"   - Requires auth: {source.get('requires_auth', False)}")
        print(f"   - Base URL: {source.get('base_url')}")
    else:
        print("   ❌ Adzuna not found in SOURCES_BY_ID")
except Exception as e:
    print(f"   ❌ Source check failed: {e}")

# 4. Check orchestrator registration
print("\n4. Checking orchestrator registration...")
try:
    from src.orchestrator import _COLLECTOR_CLASSES
    adzuna_registered = any(c.source_id == "adzuna" for c in _COLLECTOR_CLASSES)
    if adzuna_registered:
        print("   ✅ AdzunaCollector registered in orchestrator")
    else:
        print("   ❌ AdzunaCollector not found in _COLLECTOR_CLASSES")
except Exception as e:
    print(f"   ❌ Orchestrator check failed: {e}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

if app_id and app_key:
    print("\n✅ Adzuna collector is ready to use!")
    print("\nTo enable Adzuna in the pipeline:")
    print("   1. Edit config/sources.py")
    print("   2. Find the 'adzuna' source")
    print("   3. Change 'enabled': False → 'enabled': True")
    print("   4. Run: python -m src.orchestrator --mode weekly")
else:
    print("\n⚠️  Adzuna collector is installed but needs API credentials")
    print("\nNext steps:")
    print("   1. Get free API credentials from https://developer.adzuna.com/signup")
    print("   2. Add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env file")
    print("   3. Enable in config/sources.py")
    print("   4. Run: python -m src.orchestrator --mode weekly")

print("\n" + "=" * 80)
