"""Display current source configuration."""
from config.sources import SOURCES_BY_ID, ENABLED_SOURCES

print("=" * 80)
print("CURRENT SOURCE CONFIGURATION")
print("=" * 80)

print("\n✅ ENABLED SOURCES:")
for s in ENABLED_SOURCES:
    print(f"  - {s['source_id']:15} → {s['display_name']}")

print(f"\n❌ DISABLED SOURCES:")
disabled = [s for s in SOURCES_BY_ID.values() if not s.get('enabled', True)]
for s in disabled:
    reason = ""
    if "requires_auth" in s['tos_note'] and s['requires_auth']:
        reason = "(requires API key)"
    elif "403" in s.get('tos_note', ''):
        reason = "(Cloudflare blocked)"
    elif s['source_id'] == 'himalayas':
        reason = "(slow API)"
    elif s['source_id'] == 'graphqljobs':
        reason = "(DNS error)"
    print(f"  - {s['source_id']:15} → {s['display_name']} {reason}")

print(f"\n📊 SUMMARY:")
print(f"  Total sources: {len(SOURCES_BY_ID)}")
print(f"  Enabled: {len(ENABLED_SOURCES)}")
print(f"  Disabled: {len(disabled)}")

print("\n" + "=" * 80)
