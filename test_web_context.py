"""Quick test to verify country detector works from web viewer context"""
import sys
sys.path.insert(0, '.')

from src.country_detector import detect_country, should_auto_apply, GEOPY_AVAILABLE

print(f"GEOPY_AVAILABLE: {GEOPY_AVAILABLE}")
print()

test_locations = [
    "LA Metro Area",
    "NYC or St Louis", 
    "St. Louis or NYC",
    "SFLA",
    "Eastern Time Zone",
    "AU or NZ",
]

print("Testing locations:")
for loc in test_locations:
    country, confidence = detect_country(loc, use_geopy=False)  # Skip geopy to test offline only
    auto_apply = should_auto_apply(confidence) if country else False
    status = "WILL UPDATE" if auto_apply else "SKIP"
    print(f"  {loc:35} -> {country or 'FAILED':20} ({confidence:.2f}) [{status}]")
