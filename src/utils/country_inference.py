"""
src/utils/country_inference.py
──────────────────────────────
Single shared function used by every collector to turn a raw location
string into a canonical country name.

Rules (applied in order):
  1. Empty / None  →  "Unknown"
  2. Remote / global keywords  →  "Global"
  3. Exact or substring match in the keyword table (longest key first)
  4. "City, ST" pattern  →  look up ST in US-state abbreviation table
  5. Nothing matched  →  "Unknown"

All matching is case-insensitive.  Caller gets back a Title-cased string
("United States", "Germany", …) or "Global" / "Unknown".
"""

from __future__ import annotations

import re

# ─── Remote / Global patterns ─────────────────────────────────────────────────

_GLOBAL_TOKENS = {
    "remote", "worldwide", "global", "anywhere", "work from home",
    "wfh", "fully remote", "100% remote", "distributed",
}

# ─── Keyword → country table ──────────────────────────────────────────────────
# Each entry is (set_of_lowercase_substrings, canonical_country_name).
# Listed longest-match first within each country so "new south wales" →
# Australia before "wales" → United Kingdom.

_KEYWORD_TABLE: list[tuple[set[str], str]] = [

    # ── United States ────────────────────────────────────────────────────────
    ({
        "united states", "usa", "u.s.a", "u.s.", "us only",
        # State names
        "alabama", "alaska", "arizona", "arkansas", "california",
        "colorado", "connecticut", "delaware", "florida", "georgia",
        "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
        "kentucky", "louisiana", "maine", "maryland", "massachusetts",
        "michigan", "minnesota", "mississippi", "missouri", "montana",
        "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
        "new york", "north carolina", "north dakota", "ohio", "oklahoma",
        "oregon", "pennsylvania", "rhode island", "south carolina",
        "south dakota", "tennessee", "texas", "utah", "vermont",
        "virginia", "washington", "west virginia", "wisconsin", "wyoming",
        "district of columbia", "washington d.c", "washington dc",
        # Major cities
        "san francisco", "los angeles", "chicago", "houston", "phoenix",
        "philadelphia", "san antonio", "san diego", "dallas", "san jose",
        "austin", "jacksonville", "fort worth", "columbus", "charlotte",
        "indianapolis", "san francisco", "seattle", "denver", "nashville",
        "oklahoma city", "el paso", "washington", "las vegas", "boston",
        "memphis", "louisville", "portland", "baltimore", "milwaukee",
        "albuquerque", "tucson", "fresno", "sacramento", "mesa",
        "kansas city", "atlanta", "omaha", "colorado springs", "raleigh",
        "long beach", "virginia beach", "minneapolis", "tampa", "new orleans",
        "honolulu", "anaheim", "aurora", "santa ana", "corpus christi",
        "riverside", "st. louis", "lexington", "pittsburgh", "anchorage",
        "stockton", "cincinnati", "st. paul", "toledo", "greensboro",
        "newark", "plano", "henderson", "lincoln", "buffalo", "fort wayne",
        "jersey city", "chula vista", "orlando", "st. petersburg",
        "norfolk", "madison", "durham", "lubbock", "winston-salem",
        "garland", "glendale", "hialeah", "reno", "baton rouge",
        "irvine", "chesapeake", "scottsdale", "north las vegas", "fremont",
        "gilbert", "san bernardino", "birmingham", "rochester",
        "richmond", "spokane", "des moines", "montgomery", "modesto",
        "fayetteville", "tacoma", "fontana", "moreno valley", "glendale",
        "akron", "yonkers", "huntington beach", "little rock", "tempe",
        "knoxville", "worcester", "oxnard", "aurora", "springfield",
        "overland park", "providence", "garden grove", "santa clarita",
        "fort lauderdale", "oceanside", "rancho cucamonga", "santa rosa",
        "salt lake city", "tallahassee", "huntsville", "grand rapids",
        "cape coral", "sioux falls", "peoria", "torrance", "ontario",
        "elk grove", "eugene", "palmdale", "salinas", "corona",
        "springfield", "fort collins", "jackson", "alexandria",
        "hayward", "lancaster", "sunnyvale", "pomona", "escondido",
        "kansas city", "savannah", "flint", "paterson", "hartford",
        "bridgeport", "mcallen", "lakewood", "cupertino", "palo alto",
        "menlo park", "mountain view", "redwood city", "san mateo",
        "santa monica", "burbank", "pasadena", "thousand oaks",
        "ann arbor", "cambridge", "bellevue", "kirkland", "redmond",
        "rogers",
    }, "United States"),

    # ── United Kingdom ───────────────────────────────────────────────────────
    ({
        "united kingdom", "uk", "u.k.", "great britain", "britain",
        "england", "scotland", "wales", "northern ireland",
        "london", "manchester", "birmingham", "glasgow", "liverpool",
        "edinburgh", "bristol", "leeds", "sheffield", "cardiff",
        "belfast", "newcastle", "nottingham", "leicester", "brighton",
        "coventry", "exeter", "oxford", "cambridge", "reading",
        "southampton", "portsmouth", "plymouth", "derby", "hull",
        "york", "bath", "ipswich", "norwich", "swansea", "dundee",
        "aberdeen", "inverness", "stoke-on-trent",
    }, "United Kingdom"),

    # ── Germany ──────────────────────────────────────────────────────────────
    ({
        "germany", "deutschland",
        # Federal states
        "bavaria", "bayern", "berlin", "brandenburg", "bremen",
        "hamburg", "hesse", "hessen", "lower saxony", "niedersachsen",
        "mecklenburg", "north rhine", "nordrhein", "rhineland",
        "saarland", "saxony", "sachsen", "saxony-anhalt", "schleswig",
        "thuringia", "thüringen", "württemberg", "westphalia",
        # Major cities
        "munich", "münchen", "frankfurt", "cologne", "köln", "düsseldorf",
        "dusseldorf", "stuttgart", "dortmund", "essen", "leipzig",
        "hanover", "hannover", "nuremberg", "nürnberg", "duisburg",
        "bochum", "wuppertal", "bielefeld", "bonn", "münster",
        "munster", "mannheim", "karlsruhe", "augsburg", "wiesbaden",
        "mönchengladbach", "gelsenkirchen", "aachen", "braunschweig",
        "kiel", "chemnitz", "magdeburg", "freiburg", "krefeld",
        "lübeck", "oberhausen", "erfurt", "mainz", "rostock",
        "kassel", "hagen", "saarbrücken", "hamm", "mülheim",
        "potsdam", "ludwigshafen", "oldenburg", "leverkusen",
        "darmstadt", "heidelberg", "regensburg", "ingolstadt",
        "würzburg", "ulm", "göttingen", "wolfsburg", "pforzheim",
        "offenbach", "heilbronn", "osnabrück", "trier", "cottbus",
    }, "Germany"),

    # ── India ─────────────────────────────────────────────────────────────────
    ({
        "india",
        "bangalore", "bengaluru", "mumbai", "bombay", "hyderabad",
        "chennai", "madras", "delhi", "new delhi", "kolkata", "calcutta",
        "pune", "ahmedabad", "surat", "jaipur", "lucknow", "kanpur",
        "nagpur", "visakhapatnam", "indore", "thane", "bhopal",
        "pimpri", "patna", "vadodara", "ghaziabad", "ludhiana",
        "agra", "nashik", "faridabad", "meerut", "rajkot", "kalyan",
        "vasai", "varanasi", "srinagar", "aurangabad", "dhanbad",
        "amritsar", "allahabad", "ranchi", "howrah", "coimbatore",
        "vijayawada", "jodhpur", "madurai", "raipur", "kota",
        "noida", "gurgaon", "gurugram", "chandigarh", "mysore",
        "trivandrum", "thiruvananthapuram", "kochi", "cochin",
        "bhubaneswar", "guwahati", "mangalore", "tirupati",
    }, "India"),

    # ── Canada ────────────────────────────────────────────────────────────────
    ({
        "canada",
        "ontario", "quebec", "british columbia", "alberta",
        "saskatchewan", "manitoba", "nova scotia", "new brunswick",
        "newfoundland", "prince edward island",
        "toronto", "montreal", "vancouver", "calgary", "edmonton",
        "ottawa", "winnipeg", "hamilton", "kitchener", "london ontario",
        "victoria", "halifax", "oshawa", "windsor", "saskatoon",
        "regina", "richmond hill", "markham", "vaughan", "laval",
        "mississauga", "brampton", "surrey", "burnaby",
    }, "Canada"),

    # ── Australia ─────────────────────────────────────────────────────────────
    ({
        "australia",
        "new south wales", "victoria", "queensland",
        "western australia", "south australia",
        "sydney", "melbourne", "brisbane", "perth", "adelaide",
        "gold coast", "newcastle", "canberra", "wollongong",
        "hobart", "geelong", "townsville", "cairns", "darwin",
        "toowoomba", "ballarat", "bendigo",
    }, "Australia"),

    # ── France ────────────────────────────────────────────────────────────────
    ({
        "france", "french",
        "paris", "marseille", "lyon", "toulouse", "nice", "nantes",
        "montpellier", "strasbourg", "bordeaux", "lille", "rennes",
        "reims", "toulon", "saint-étienne", "grenoble", "dijon",
        "angers", "villeurbanne", "nîmes", "aix-en-provence",
    }, "France"),

    # ── Netherlands ───────────────────────────────────────────────────────────
    ({
        "netherlands", "holland", "dutch",
        "amsterdam", "rotterdam", "the hague", "utrecht", "eindhoven",
        "tilburg", "groningen", "almere", "breda", "nijmegen",
        "apeldoorn", "haarlem", "arnhem", "enschede", "delft",
    }, "Netherlands"),

    # ── Spain ─────────────────────────────────────────────────────────────────
    ({
        "spain", "españa",
        "madrid", "barcelona", "valencia", "seville", "sevilla",
        "zaragoza", "málaga", "murcia", "palma", "bilbao",
        "alicante", "córdoba", "valladolid", "vigo", "gijón",
        "hospitalet", "vitoria", "granada", "elche", "oviedo",
    }, "Spain"),

    # ── Poland ────────────────────────────────────────────────────────────────
    ({
        "poland", "polska",
        "warsaw", "warszawa", "krakow", "kraków", "łódź", "wroclaw",
        "wrocław", "poznań", "gdańsk", "szczecin", "bydgoszcz",
        "lublin", "katowice", "białystok", "gdynia",
    }, "Poland"),

    # ── Ireland ───────────────────────────────────────────────────────────────
    ({
        "ireland", "éire",
        "dublin", "cork", "limerick", "galway", "waterford",
        "drogheda", "dundalk", "swords",
    }, "Ireland"),

    # ── Singapore ─────────────────────────────────────────────────────────────
    ({"singapore"}, "Singapore"),

    # ── Israel ────────────────────────────────────────────────────────────────
    ({
        "israel",
        "tel aviv", "tel-aviv", "jerusalem", "haifa", "rishon",
        "petah tikva", "ashdod", "netanya", "beer sheva", "herzliya",
        "ramat gan", "holon", "bnei brak",
    }, "Israel"),

    # ── Brazil ────────────────────────────────────────────────────────────────
    ({
        "brazil", "brasil",
        "são paulo", "sao paulo", "rio de janeiro", "brasília",
        "salvador", "fortaleza", "belo horizonte", "curitiba",
        "manaus", "recife", "porto alegre", "belém", "goiânia",
        "guarulhos", "campinas",
    }, "Brazil"),

    # ── Switzerland ───────────────────────────────────────────────────────────
    ({
        "switzerland", "schweiz",
        "zurich", "zürich", "geneva", "genève", "basel", "bern",
        "lausanne", "winterthur", "lucerne", "lugano",
    }, "Switzerland"),

    # ── Sweden ────────────────────────────────────────────────────────────────
    ({
        "sweden", "sverige",
        "stockholm", "gothenburg", "göteborg", "malmö", "malmo",
        "uppsala", "linköping", "örebro",
    }, "Sweden"),

    # ── Norway ────────────────────────────────────────────────────────────────
    ({
        "norway", "norge",
        "oslo", "bergen", "stavanger", "trondheim",
    }, "Norway"),

    # ── Denmark ───────────────────────────────────────────────────────────────
    ({"denmark", "danmark", "copenhagen", "København", "aarhus"}, "Denmark"),

    # ── Finland ───────────────────────────────────────────────────────────────
    ({"finland", "suomi", "helsinki", "espoo", "tampere", "vantaa"}, "Finland"),

    # ── Japan ─────────────────────────────────────────────────────────────────
    ({
        "japan",
        "tokyo", "osaka", "yokohama", "nagoya", "sapporo",
        "fukuoka", "kyoto", "kobe", "kawasaki", "hiroshima",
    }, "Japan"),

    # ── South Korea ───────────────────────────────────────────────────────────
    ({"south korea", "korea", "seoul", "busan", "incheon", "daegu"}, "South Korea"),

    # ── China ─────────────────────────────────────────────────────────────────
    ({
        "china", "beijing", "shanghai", "guangzhou", "shenzhen",
        "chengdu", "hangzhou", "wuhan", "tianjin", "nanjing",
        "xi'an", "chongqing",
    }, "China"),

    # ── UAE ───────────────────────────────────────────────────────────────────
    ({
        "united arab emirates", "uae", "dubai", "abu dhabi",
        "sharjah", "ajman",
    }, "United Arab Emirates"),

    # ── Mexico ────────────────────────────────────────────────────────────────
    ({
        "mexico", "méxico",
        "mexico city", "ciudad de méxico", "guadalajara", "monterrey",
        "puebla", "tijuana", "juárez", "cancún",
    }, "Mexico"),

    # ── Argentina ─────────────────────────────────────────────────────────────
    ({
        "argentina",
        "buenos aires", "córdoba", "rosario", "mendoza",
    }, "Argentina"),

    # ── Turkey ────────────────────────────────────────────────────────────────
    ({
        "turkey", "türkiye",
        "istanbul", "ankara", "izmir", "bursa", "antalya",
    }, "Turkey"),

    # ── Ukraine ───────────────────────────────────────────────────────────────
    ({
        "ukraine",
        "kyiv", "kiev", "kharkiv", "odessa", "dnipro", "lviv",
    }, "Ukraine"),

    # ── Pakistan ──────────────────────────────────────────────────────────────
    ({
        "pakistan",
        "karachi", "lahore", "islamabad", "rawalpindi", "faisalabad",
    }, "Pakistan"),

    # ── New Zealand ───────────────────────────────────────────────────────────
    ({
        "new zealand",
        "auckland", "wellington", "christchurch", "hamilton",
    }, "New Zealand"),

    # ── South Africa ──────────────────────────────────────────────────────────
    ({
        "south africa",
        "johannesburg", "cape town", "durban", "pretoria",
    }, "South Africa"),

    # ── Portugal ──────────────────────────────────────────────────────────────
    ({
        "portugal",
        "lisbon", "lisboa", "porto", "braga", "faro",
    }, "Portugal"),

    # ── Italy ─────────────────────────────────────────────────────────────────
    ({
        "italy", "italia",
        "rome", "roma", "milan", "milano", "naples", "napoli",
        "turin", "torino", "florence", "firenze", "bologna",
        "genoa", "genova", "palermo", "venice", "verona",
    }, "Italy"),

    # ── Romania ───────────────────────────────────────────────────────────────
    ({
        "romania",
        "bucharest", "bucurești", "cluj", "timișoara", "iași",
    }, "Romania"),

    # ── Czech Republic ────────────────────────────────────────────────────────
    ({
        "czech", "czechia",
        "prague", "praha", "brno", "ostrava",
    }, "Czech Republic"),

    # ── Hungary ───────────────────────────────────────────────────────────────
    ({"hungary", "budapest"}, "Hungary"),

    # ── Greece ────────────────────────────────────────────────────────────────
    ({"greece", "athens", "thessaloniki"}, "Greece"),

    # ── Belgium ───────────────────────────────────────────────────────────────
    ({
        "belgium", "belgique",
        "brussels", "bruxelles", "antwerp", "ghent", "bruges",
        "liège", "leuven",
    }, "Belgium"),

    # ── Austria ───────────────────────────────────────────────────────────────
    ({"austria", "österreich", "vienna", "wien", "graz", "linz"}, "Austria"),

    # ── Russia ────────────────────────────────────────────────────────────────
    ({
        "russia", "russian",
        "moscow", "moskva", "saint petersburg", "st. petersburg",
        "novosibirsk", "yekaterinburg",
    }, "Russia"),
]

# ─── US state abbreviation → full state name ──────────────────────────────────
# Used as a last-resort fallback when the location looks like "City, ST"

_US_STATE_ABBR: dict[str, str] = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
    "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
}

# Precompiled regex for the "City, ST" pattern (exactly 2 uppercase letters at end)
_CITY_STATE_RE = re.compile(r",\s*([A-Z]{2})\s*$")


def infer_country(location: str) -> str:
    """
    Infer country name from a raw location string.
    Returns one of: a canonical country name, "Global", or "Unknown".
    """
    if not location or not location.strip():
        return "Unknown"

    loc = location.strip().lower()

    # 1. Remote / global
    for token in _GLOBAL_TOKENS:
        if token in loc:
            return "Global"

    # 2. Keyword table (longer strings matched first within each entry)
    for keywords, country in _KEYWORD_TABLE:
        if any(kw in loc for kw in keywords):
            return country

    # 3. "City, ST" → US state abbreviation fallback
    m = _CITY_STATE_RE.search(location)  # use original case for the abbreviation
    if m:
        abbr = m.group(1).lower()
        if abbr in _US_STATE_ABBR:
            return "United States"

    return "Unknown"
