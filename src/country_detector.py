"""
src/country_detector.py
──────────────────────
Intelligent country detection with weighted voting system.

Uses multiple methods with different confidence weights:
1. Offline pattern matching (fast, high confidence for known patterns)
2. Geopy geocoding (accurate but requires internet, rate-limited)
3. Manual mapping fallback

Results are combined with weighted voting for maximum accuracy.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Optional

try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Method weights (higher = more trusted)
WEIGHTS = {
    "exact_pattern": 1.0,      # Exact matches like "United States"
    "city_pattern": 0.8,       # Known city patterns
    "state_code": 0.7,         # US state codes like ", CA"
    "geopy": 0.9,              # Geocoding API (when available)
    "keyword": 0.5,            # Keyword matching
}

# Minimum confidence threshold for auto-apply
MIN_CONFIDENCE = 0.5  # Lowered from 0.7 to catch more valid matches


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN DATABASES
# ═══════════════════════════════════════════════════════════════════════════════

# Country exact matches (weight: 1.0)
COUNTRY_EXACT = {
    "united states", "usa", "u.s.a", "u.s.", "united states of america",
    "united kingdom", "uk", "u.k.", "great britain",
    "germany", "deutschland",
    "canada",
    "france",
    "australia",
    "netherlands", "holland",
    "spain", "españa",
    "italy", "italia",
    "india",
    "singapore",
    "japan",
    "poland", "polska",
    "portugal",
    "sweden", "sverige",
    "switzerland", "schweiz",
    "belgium", "belgique",
    "ireland",
    "austria", "österreich",
    "denmark", "danmark",
    "norway", "norge",
    "finland", "suomi",
    "brazil", "brasil",
    "mexico", "méxico",
    "argentina",
    "chile",
    "colombia",
    "south africa",
    "israel",
    "uae", "united arab emirates",
    "new zealand",
    "luxembourg",
    "bulgaria",
}

# City to country mappings (weight: 0.8)
CITY_TO_COUNTRY = {
    # United States - Major cities
    "new york": "United States",
    "los angeles": "United States",
    "chicago": "United States",
    "houston": "United States",
    "phoenix": "United States",
    "philadelphia": "United States",
    "san antonio": "United States",
    "san diego": "United States",
    "dallas": "United States",
    "san jose": "United States",
    "austin": "United States",
    "jacksonville": "United States",
    "san francisco": "United States",
    "columbus": "United States",
    "charlotte": "United States",
    "indianapolis": "United States",
    "seattle": "United States",
    "denver": "United States",
    "washington": "United States",
    "boston": "United States",
    "nashville": "United States",
    "detroit": "United States",
    "portland": "United States",
    "memphis": "United States",
    "oklahoma city": "United States",
    "las vegas": "United States",
    "atlanta": "United States",
    "miami": "United States",
    "raleigh": "United States",
    "tampa": "United States",
    "minneapolis": "United States",
    "cleveland": "United States",
    "orlando": "United States",
    "salt lake city": "United States",
    "pittsburgh": "United States",
    "santa monica": "United States",
    "santa clara": "United States",
    "lincoln": "United States",
    "scottsdale": "United States",
    "golden": "United States",
    "missouri": "United States",
    "oakland": "United States",
    "la grange": "United States",
    "bala cynwyd": "United States",
    "dayton": "United States",
    "conshohocken": "United States",
    "nyc": "United States",
    "st louis": "United States",
    "st. louis": "United States",
    
    # United Kingdom
    "london": "United Kingdom",
    "manchester": "United Kingdom",
    "birmingham": "United Kingdom",
    "edinburgh": "United Kingdom",
    "glasgow": "United Kingdom",
    "liverpool": "United Kingdom",
    "bristol": "United Kingdom",
    "leeds": "United Kingdom",
    "sheffield": "United Kingdom",
    "belfast": "United Kingdom",
    "cardiff": "United Kingdom",
    "nottingham": "United Kingdom",
    "brighton": "United Kingdom",
    "cambridge": "United Kingdom",
    "oxford": "United Kingdom",
    
    # Germany (including umlaut variations)
    "berlin": "Germany",
    "munich": "Germany",
    "munchen": "Germany",
    "hamburg": "Germany",
    "frankfurt": "Germany",
    "cologne": "Germany",
    "koln": "Germany",
    "köln": "Germany",
    "stuttgart": "Germany",
    "dusseldorf": "Germany",
    "düsseldorf": "Germany",
    "dortmund": "Germany",
    "essen": "Germany",
    "leipzig": "Germany",
    "bremen": "Germany",
    "dresden": "Germany",
    "hannover": "Germany",
    "nurnberg": "Germany",
    "nürnberg": "Germany",
    "nuremberg": "Germany",
    "kassel": "Germany",
    "karlsruhe": "Germany",
    "mannheim": "Germany",
    "rottendorf": "Germany",
    "bonn": "Germany",
    "freiburg": "Germany",
    "augsburg": "Germany",
    "wiesbaden": "Germany",
    "gelsenkirchen": "Germany",
    "monchengladbach": "Germany",
    "mönchengladbach": "Germany",
    "braunschweig": "Germany",
    "kiel": "Germany",
    "aachen": "Germany",
    "chemnitz": "Germany",
    "halle": "Germany",
    "magdeburg": "Germany",
    "krefeld": "Germany",
    "lubeck": "Germany",
    "lübeck": "Germany",
    "erfurt": "Germany",
    "mainz": "Germany",
    "rostock": "Germany",
    "potsdam": "Germany",
    "friedrichshafen": "Germany",
    "holzgerlingen": "Germany",
    "lindlar": "Germany",
    "koblenz": "Germany",
    "kleinmachnow": "Germany",
    "neuwied": "Germany",
    "waiblingen": "Germany",
    "peine": "Germany",
    "weilheim an der teck": "Germany",
    "buchholz in der nordheide": "Germany",
    "villingen-schwenningen": "Germany",
    "wernigerode": "Germany",
    "zweibrucken": "Germany",
    "zweibrücken": "Germany",
    "wasserburg am inn": "Germany",
    "friedrichsdorf": "Germany",
    "dissen": "Germany",
    "garbsen": "Germany",
    "passau": "Germany",
    "ilmenau": "Germany",
    "filderstadt": "Germany",
    "werne": "Germany",
    "singen": "Germany",
    "ulm": "Germany",
    "elmshorn": "Germany",
    "garching": "Germany",
    "pforzheim": "Germany",
    "hilden": "Germany",
    "hofoldinger forst": "Germany",
    "molbergen": "Germany",
    "aschersleben": "Germany",
    "offenburg": "Germany",
    "walldorf": "Germany",
    "niedernberg": "Germany",
    "planegg": "Germany",
    "darmstadt": "Germany",
    "jena": "Germany",
    "dachau": "Germany",
    "endingen am kaiserstuhl": "Germany",
    "regensburg": "Germany",
    "korbach": "Germany",
    "altdorf": "Germany",
    "duisburg": "Germany",
    "gilching": "Germany",
    "neu wulmstorf": "Germany",
    "osnabruck": "Germany",
    "osnabrück": "Germany",
    "munster": "Germany",
    "münster": "Germany",
    "linden": "Germany",
    "schomberg": "Germany",
    "schömberg": "Germany",
    "bad kreuznach": "Germany",
    "leonberg": "Germany",
    "herne": "Germany",
    "hanover": "Germany",
    "hildesheim": "Germany",
    "kitzingen": "Germany",
    "kaiserslautern": "Germany",
    "grossbeeren": "Germany",
    "großbeeren": "Germany",
    "speyer": "Germany",
    "norderstedt": "Germany",
    "pfaffenhofen": "Germany",
    "heilbronn": "Germany",
    
    # Canada
    "toronto": "Canada",
    "vancouver": "Canada",
    "montreal": "Canada",
    "calgary": "Canada",
    "ottawa": "Canada",
    "edmonton": "Canada",
    "winnipeg": "Canada",
    "quebec": "Canada",
    "hamilton": "Canada",
    "kitchener": "Canada",
    
    # France
    "paris": "France",
    "marseille": "France",
    "lyon": "France",
    "toulouse": "France",
    "nice": "France",
    "nantes": "France",
    "strasbourg": "France",
    "montpellier": "France",
    "bordeaux": "France",
    
    # Australia
    "sydney": "Australia",
    "melbourne": "Australia",
    "brisbane": "Australia",
    "perth": "Australia",
    "adelaide": "Australia",
    "gold coast": "Australia",
    "canberra": "Australia",
    
    # Netherlands
    "amsterdam": "Netherlands",
    "rotterdam": "Netherlands",
    "utrecht": "Netherlands",
    "eindhoven": "Netherlands",
    "hague": "Netherlands",
    "groningen": "Netherlands",
    
    # Spain
    "madrid": "Spain",
    "barcelona": "Spain",
    "valencia": "Spain",
    "seville": "Spain",
    "bilbao": "Spain",
    "málaga": "Spain",
    
    # Other major cities
    "rome": "Italy",
    "milan": "Italy",
    "naples": "Italy",
    "turin": "Italy",
    "florence": "Italy",
    
    "bangalore": "India",
    "mumbai": "India",
    "delhi": "India",
    "hyderabad": "India",
    "pune": "India",
    "chennai": "India",
    "kolkata": "India",
    
    "singapore": "Singapore",
    
    "tokyo": "Japan",
    "osaka": "Japan",
    "kyoto": "Japan",
    
    "warsaw": "Poland",
    "krakow": "Poland",
    "wroclaw": "Poland",
    
    "lisbon": "Portugal",
    "porto": "Portugal",
    
    "stockholm": "Sweden",
    "gothenburg": "Sweden",
    
    "zurich": "Switzerland",
    "geneva": "Switzerland",
    "bern": "Switzerland",
    
    "brussels": "Belgium",
    "antwerp": "Belgium",
    
    "dublin": "Ireland",
    "cork": "Ireland",
    
    "vienna": "Austria",
    "salzburg": "Austria",
    
    "copenhagen": "Denmark",
    "oslo": "Norway",
    "helsinki": "Finland",
    
    # South America
    "sao paulo": "Brazil",
    "são paulo": "Brazil",
    "rio de janeiro": "Brazil",
    "brasilia": "Brazil",
    "belo horizonte": "Brazil",
    "buenos aires": "Argentina",
    "santiago": "Chile",
    "bogota": "Colombia",
    "bogotá": "Colombia",
    "lima": "Peru",
    "mexico city": "Mexico",
    "guadalajara": "Mexico",
    "monterrey": "Mexico",
    
    # Other
    "kazanlak": "Bulgaria",
    "sofia": "Bulgaria",
}

# US State codes (weight: 0.7)
US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy"
}

US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming"
]

# Remote indicators (weight: 0.9)
REMOTE_KEYWORDS = ["remote", "anywhere", "worldwide", "global", "work from home", "wfh", "virtual"]


# ═══════════════════════════════════════════════════════════════════════════════
# DETECTION METHODS
# ═══════════════════════════════════════════════════════════════════════════════

def _method_exact_pattern(location: str) -> Optional[tuple[str, float]]:
    """Check for exact country name matches."""
    loc_lower = location.lower().strip()
    
    # Check if location has format "City, Country" (e.g., "São Paulo, Brazil")
    if ', ' in loc_lower:
        parts = loc_lower.split(', ')
        if len(parts) >= 2:
            potential_country = parts[-1].strip()
            # Check if last part is a country name
            if potential_country in COUNTRY_EXACT:
                # Normalize common country names
                country_map = {
                    "brazil": "Brazil", "brasil": "Brazil",
                    "united states": "United States", "usa": "United States", "u.s.a": "United States", "u.s.": "United States",
                    "united kingdom": "United Kingdom", "uk": "United Kingdom", "u.k.": "United Kingdom",
                    "germany": "Germany", "deutschland": "Germany",
                    "canada": "Canada",
                    "france": "France",
                    "india": "India",
                    "mexico": "Mexico", "méxico": "Mexico",
                    "bulgaria": "Bulgaria",
                    "spain": "Spain", "españa": "Spain",
                    "italy": "Italy", "italia": "Italy",
                    "australia": "Australia",
                    "netherlands": "Netherlands", "holland": "Netherlands",
                }
                if potential_country in country_map:
                    return (country_map[potential_country], WEIGHTS["exact_pattern"])
                # If not in map but in COUNTRY_EXACT, capitalize first letter
                return (potential_country.title(), WEIGHTS["exact_pattern"])
    
    # Direct country name
    if loc_lower in COUNTRY_EXACT:
        # Normalize to proper country names
        country_map = {
            "united states": "United States", "usa": "United States", "u.s.a": "United States", "u.s.": "United States",
            "united states of america": "United States",
            "united kingdom": "United Kingdom", "uk": "United Kingdom", "u.k.": "United Kingdom", 
            "great britain": "United Kingdom",
            "germany": "Germany", "deutschland": "Germany",
            "canada": "Canada",
            "france": "France",
            "australia": "Australia",
            "netherlands": "Netherlands", "holland": "Netherlands",
            "spain": "Spain", "españa": "Spain",
            "italy": "Italy", "italia": "Italy",
            "india": "India",
            "singapore": "Singapore",
            "japan": "Japan",
            "poland": "Poland", "polska": "Poland",
            "portugal": "Portugal",
            "sweden": "Sweden", "sverige": "Sweden",
            "switzerland": "Switzerland", "schweiz": "Switzerland",
            "belgium": "Belgium", "belgique": "Belgium",
            "ireland": "Ireland",
            "austria": "Austria", "österreich": "Austria",
            "denmark": "Denmark", "danmark": "Denmark",
            "norway": "Norway", "norge": "Norway",
            "finland": "Finland", "suomi": "Finland",
            "brazil": "Brazil", "brasil": "Brazil",
            "mexico": "Mexico", "méxico": "Mexico",
            "argentina": "Argentina",
            "chile": "Chile",
            "colombia": "Colombia",
            "south africa": "South Africa",
            "israel": "Israel",
            "uae": "United Arab Emirates", "united arab emirates": "United Arab Emirates",
            "new zealand": "New Zealand",
            "luxembourg": "Luxembourg",
            "bulgaria": "Bulgaria",
        }
        if loc_lower in country_map:
            return (country_map[loc_lower], WEIGHTS["exact_pattern"])
        # Fallback: capitalize
        return (loc_lower.title(), WEIGHTS["exact_pattern"])
    
    return None


def _method_city_pattern(location: str) -> Optional[tuple[str, float]]:
    """Check for known city names."""
    loc_lower = location.lower().strip()
    
    # Check if location contains any known city (with word boundary checking)
    for city, country in CITY_TO_COUNTRY.items():
        # For short city names (≤3 chars), require exact match or word boundary
        if len(city) <= 3:
            # Check for exact match or surrounded by word boundaries
            pattern = r'\b' + re.escape(city) + r'\b'
            if re.search(pattern, loc_lower):
                return (country, WEIGHTS["city_pattern"])
        else:
            # For longer names, allow substring match (existing behavior)
            if city in loc_lower:
                return (country, WEIGHTS["city_pattern"])
    
    return None


def _method_state_code(location: str) -> Optional[tuple[str, float]]:
    """Check for US state codes (e.g., ', CA', ', NY', ' PA')."""
    loc_lower = location.lower().strip()
    
    # Pattern 1: ", XX" where XX is a state code
    state_pattern = re.search(r',\s*([a-z]{2})\s*$', loc_lower)
    if state_pattern:
        state_code = state_pattern.group(1)
        if state_code in US_STATE_CODES:
            return ("United States", WEIGHTS["state_code"])
    
    # Pattern 2: " XX" (space + two letters) at end, for "City ST" format
    state_pattern = re.search(r'\s+([a-z]{2})\s*$', loc_lower)
    if state_pattern:
        state_code = state_pattern.group(1)
        if state_code in US_STATE_CODES:
            return ("United States", WEIGHTS["state_code"])
    
    # Full state names (match whole word or at start/end)
    for state_name in US_STATE_NAMES:
        # Check if state name is the entire location or part of it
        if loc_lower == state_name or loc_lower.startswith(state_name + ',') or loc_lower.endswith(', ' + state_name):
            return ("United States", WEIGHTS["state_code"])
        # Also check if it's just the state name alone
        if state_name in loc_lower and len(loc_lower) <= len(state_name) + 5:  # Allow for minor variations
            return ("United States", WEIGHTS["state_code"])
    
    return None


def _method_keyword(location: str) -> Optional[tuple[str, float]]:
    """Check for remote/global keywords and metro areas."""
    loc_lower = location.lower().strip()
    
    # Check for metro areas (treat as city match)
    if "la metro" in loc_lower or "los angeles metro" in loc_lower:
        return ("United States", WEIGHTS["city_pattern"])
    if "nyc metro" in loc_lower or "ny metro" in loc_lower:
        return ("United States", WEIGHTS["city_pattern"])
    if "sf metro" in loc_lower or "bay area" in loc_lower:
        return ("United States", WEIGHTS["city_pattern"])
    
    # Check for "City or City" format - detect if both cities are in same country
    if " or " in loc_lower:
        # Common US city combinations
        us_city_pairs = ["nyc", "st louis", "st. louis", "san francisco", "los angeles", "chicago", "boston", "seattle"]
        parts = [p.strip() for p in loc_lower.split(" or ")]
        if all(any(city in part for city in us_city_pairs) for part in parts):
            return ("United States", WEIGHTS["keyword"])  # Lower confidence for ambiguous
    
    # Remote keywords
    for keyword in REMOTE_KEYWORDS:
        if keyword in loc_lower:
            return ("Remote/Global", WEIGHTS["keyword"])
    
    return None


@lru_cache(maxsize=1000)
def _method_geopy(location: str) -> Optional[tuple[str, float]]:
    """Use geopy geocoding API (cached, rate-limited)."""
    if not GEOPY_AVAILABLE:
        return None
    
    try:
        geolocator = Nominatim(
            user_agent="job_market_intelligence_v1",
            timeout=3
        )
        
        # Rate limiting
        time.sleep(0.2)  # 5 requests per second max
        
        result = geolocator.geocode(location, language='en', addressdetails=True)
        
        if result and result.raw:
            address = result.raw.get('address', {})
            country = address.get('country')
            
            if country:
                # Normalize common variations
                if country.lower() in ["united states", "usa", "united states of america"]:
                    country = "United States"
                elif country.lower() in ["united kingdom", "uk"]:
                    country = "United Kingdom"
                
                return (country, WEIGHTS["geopy"])
    
    except (GeocoderTimedOut, GeocoderServiceError):
        # Silently fail - offline methods will handle it
        pass
    except Exception:
        # Any other error - skip geopy
        pass
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# WEIGHTED VOTING
# ═══════════════════════════════════════════════════════════════════════════════

def detect_country(location: str, use_geopy: bool = True) -> tuple[Optional[str], float]:
    """
    Detect country using weighted voting from multiple methods.
    
    Args:
        location: Location string to analyze
        use_geopy: Whether to use geopy (set False for batch processing)
    
    Returns:
        (country_name, confidence_score)
        confidence_score is between 0.0 and 1.0
    """
    if not location or location.lower() in ["unknown", "n/a", "null", ""]:
        return None, 0.0
    
    # Collect votes from all methods
    votes: dict[str, float] = {}  # {country: total_weight}
    
    # Method 1: Exact pattern (fastest)
    result = _method_exact_pattern(location)
    if result:
        country, weight = result
        votes[country] = votes.get(country, 0) + weight
    
    # Method 2: City pattern
    result = _method_city_pattern(location)
    if result:
        country, weight = result
        votes[country] = votes.get(country, 0) + weight
    
    # Method 3: State code
    result = _method_state_code(location)
    if result:
        country, weight = result
        votes[country] = votes.get(country, 0) + weight
    
    # Method 4: Keyword matching
    result = _method_keyword(location)
    if result:
        country, weight = result
        votes[country] = votes.get(country, 0) + weight
    
    # Method 5: Geopy (slowest, only if enabled)
    if use_geopy and GEOPY_AVAILABLE:
        result = _method_geopy(location)
        if result:
            country, weight = result
            votes[country] = votes.get(country, 0) + weight
    
    # No votes = cannot determine
    if not votes:
        return None, 0.0
    
    # Winner = country with highest total weight
    winner = max(votes, key=votes.get)
    total_weight = votes[winner]
    
    # Calculate confidence - use the actual weight directly as confidence
    # If multiple methods agree, weight is additive (capped at 1.0)
    # Single method: use its weight as confidence
    confidence = min(total_weight, 1.0)
    
    return winner, confidence


def detect_country_batch(locations: list[str], use_geopy: bool = False) -> list[tuple[Optional[str], float]]:
    """
    Batch detect countries for multiple locations.
    By default, skips geopy to avoid rate limiting.
    """
    results = []
    for location in locations:
        country, confidence = detect_country(location, use_geopy=use_geopy)
        results.append((country, confidence))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def should_auto_apply(confidence: float) -> bool:
    """Check if confidence is high enough for auto-application."""
    return confidence >= MIN_CONFIDENCE


def get_confidence_label(confidence: float) -> str:
    """Get human-readable confidence label."""
    if confidence >= 0.85:
        return "high"
    elif confidence >= 0.5:
        return "medium"
    else:
        return "low"
