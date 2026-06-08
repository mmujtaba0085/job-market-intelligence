"""Conservative structured location resolution shared by collectors."""

from __future__ import annotations

from dataclasses import dataclass

from src.enrichment.auto_enrich import _parse_location_string
from src.enrichment.location_data import US_STATES

COUNTRY_CODES = {
    "United States": "US", "United Kingdom": "GB", "Canada": "CA", "Germany": "DE",
    "France": "FR", "Australia": "AU", "Netherlands": "NL", "Spain": "ES",
    "Italy": "IT", "India": "IN", "Singapore": "SG", "Ireland": "IE",
    "Global": "ZZ",
}


@dataclass(frozen=True)
class LocationResolution:
    raw_location: str
    city: str | None
    region: str | None
    country: str | None
    country_code: str | None
    confidence: float
    method: str


def resolve_location(raw_location: str, fallback_country: str | None = None) -> LocationResolution:
    raw = (raw_location or "").strip()
    if not raw:
        return LocationResolution(raw, None, None, fallback_country, COUNTRY_CODES.get(fallback_country or ""), 0.55 if fallback_country else 0.0, "source_fallback" if fallback_country else "unresolved")

    city, country = _parse_location_string(raw)
    region = None
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) >= 2:
        candidate = parts[-1]
        if candidate.upper() in US_STATES:
            region = US_STATES[candidate.upper()]
        elif country and candidate.lower() not in {country.lower(), "uk", "us", "usa"}:
            region = candidate

    if country:
        return LocationResolution(raw, city, region, country, COUNTRY_CODES.get(country), 0.96, "structured_location")
    if fallback_country:
        return LocationResolution(raw, parts[0] if parts else None, region, fallback_country, COUNTRY_CODES.get(fallback_country), 0.65, "source_fallback")
    return LocationResolution(raw, None, region, None, None, 0.0, "unresolved")


def split_locations(raw_location: str) -> list[str]:
    """Split explicit multi-location separators without splitting city/state commas."""
    raw = (raw_location or "").strip()
    if not raw:
        return []
    for separator in (" / ", " | ", ";", "\n", "<br>", "<br/>", "<br />"):
        if separator in raw:
            return [part.strip() for part in raw.split(separator) if part.strip()]
    return [raw]

