"""
src/enrichment/location_data.py
────────────────────────────────
Static lookup tables for location → country mapping.
No external API calls — everything is offline.

Keep this file as the single source of truth for location data.
Add entries here instead of asking Claude to fix individual jobs.
"""

from __future__ import annotations

# ─── US States ────────────────────────────────────────────────────────────────
# Full names AND 2-letter codes
US_STATES: dict[str, str] = {
    # 2-letter codes
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "Washington DC",
}

# Major US cities (lowercase)
US_CITIES: set[str] = {
    "new york", "los angeles", "chicago", "houston", "phoenix", "philadelphia",
    "san antonio", "san diego", "dallas", "san jose", "austin", "jacksonville",
    "fort worth", "columbus", "charlotte", "indianapolis", "san francisco",
    "seattle", "denver", "boston", "nashville", "baltimore", "oklahoma city",
    "louisville", "portland", "las vegas", "memphis", "raleigh", "atlanta",
    "miami", "minneapolis", "colorado springs", "new orleans", "cleveland",
    "arlington", "honolulu", "anaheim", "tampa", "aurora", "santa ana",
    "pittsburgh", "cincinnati", "henderson", "irvine", "st. louis",
    "st louis", "orlando", "jersey city", "san bernardino", "madison",
    "lincoln", "reno", "buffalo", "lubbock", "chandler", "chula vista",
    "anchorage", "durham", "fremont", "riverside", "spokane", "salt lake city",
    "boise", "tucson", "mesa", "omaha", "bakersfield", "san francisco bay area",
    "silicon valley", "bay area", "research triangle", "herndon", "fairfax",
    "mclean", "tysons", "bethesda", "rockville", "cambridge", "palo alto",
    "menlo park", "mountain view", "sunnyvale", "santa clara", "cupertino",
    "redwood city", "san mateo", "burlingame", "foster city", "oakland",
    "berkeley", "emeryville", "burbank", "pasadena", "santa monica",
    "manhattan", "brooklyn", "queens", "bronx", "hoboken", "newark",
    "stamford", "greenwich", "hartford", "new haven", "providence",
    "richmond", "norfolk", "virginia beach", "arlington", "alexandria",
    "plano", "frisco", "mckinney", "irving", "garland", "grand prairie",
    "bellevue", "redmond", "kirkland", "bothell", "tacoma", "spokane",
    "ann arbor", "detroit", "grand rapids", "lansing", "flint",
    "st. paul", "rochester", "des moines", "wichita", "topeka",
    "baton rouge", "new orleans", "shreveport", "little rock",
    "albuquerque", "santa fe", "el paso",
}

# ─── Canadian cities & provinces (lowercase) ──────────────────────────────────
CA_CITIES: set[str] = {
    "toronto", "montreal", "vancouver", "calgary", "edmonton", "ottawa",
    "winnipeg", "quebec city", "hamilton", "kitchener", "victoria",
    "halifax", "oshawa", "windsor", "saskatoon", "regina", "richmond hill",
    "markham", "vaughan", "burnaby", "surrey", "mississauga", "brampton",
    "laval", "longueuil", "gatineau", "kingston", "waterloo", "guelph",
    "sherbrooke", "kelowna", "abbotsford", "coquitlam",
}
CA_PROVINCES: set[str] = {
    "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
    "prince edward island", "northwest territories", "yukon", "nunavut",
    "bc", "ab", "on", "qc", "mb", "sk", "ns", "nb", "nl", "pe",  # no "ca" — conflicts with California
}

# ─── UK cities ────────────────────────────────────────────────────────────────
UK_CITIES: set[str] = {
    "london", "manchester", "birmingham", "leeds", "glasgow", "sheffield",
    "bradford", "edinburgh", "liverpool", "bristol", "cardiff", "belfast",
    "leicester", "nottingham", "coventry", "hull", "bradford", "stoke",
    "wolverhampton", "derby", "swansea", "southampton", "brighton",
    "plymouth", "exeter", "cambridge", "oxford", "reading", "swindon",
    "milton keynes", "northampton", "luton", "peterborough", "watford",
    "newcastle", "sunderland", "middlesbrough", "york", "harrogate",
    "bath", "salisbury", "gloucester", "cheltenham", "worcester",
    "ipswich", "colchester", "norwich", "lincoln", "doncaster",
}

# ─── German cities ────────────────────────────────────────────────────────────
DE_CITIES: set[str] = {
    "berlin", "hamburg", "münchen", "munich", "köln", "cologne", "frankfurt",
    "stuttgart", "düsseldorf", "dortmund", "essen", "leipzig", "bremen",
    "dresden", "hannover", "nürnberg", "nuremberg", "duisburg", "bochum",
    "wuppertal", "bielefeld", "bonn", "münster", "karlsruhe", "mannheim",
    "augsburg", "wiesbaden", "gelsenkirchen", "mönchengladbach", "aachen",
    "braunschweig", "kiel", "chemnitz", "halle", "magdeburg", "freiburg",
    "krefeld", "lübeck", "oberhausen", "erfurt", "rostock", "mainz",
    "kassel", "hagen", "saarbrücken", "hamm", "mülheim", "potsdam",
    "ludwigshafen", "oldenburg", "osnabrück", "leverkusen", "heidelberg",
    "darmstadt", "würzburg", "regensburg", "münchengladbach", "ingolstadt",
    "offenbach", "göttingen", "wolfsburg", "recklinghausen", "heilbronn",
    "pforzheim", "ulm", "ingolstadt", "fürth", "erlangen", "hildesheim",
    "koblenz", "kaiserslautern", "trier", "jena", "gera", "bremerhaven",
    "lüneburg", "siegen", "gütersloh", "oberhausen", "remscheid",
    "solingen", "bergisch gladbach", "bottrop", "reutlingen", "paderborn",
    "gronau", "dormagen", "ratingen", "neuss", "mettmann", "viersen",
    "wesel", "kleve", "emden", "flensburg", "heide", "schwerin",
    "greifswald", "stralsund", "rostock", "cottbus", "zwickau",
    "plauen", "görlitz", "bautzen", "pirna", "riesa",
}

# ─── Other European cities ────────────────────────────────────────────────────
EU_CITIES: dict[str, str] = {
    # France
    "paris": "France", "lyon": "France", "marseille": "France", "toulouse": "France",
    "nice": "France", "nantes": "France", "strasbourg": "France", "bordeaux": "France",
    "lille": "France", "rennes": "France", "grenoble": "France", "montpellier": "France",

    # Netherlands
    "amsterdam": "Netherlands", "rotterdam": "Netherlands", "the hague": "Netherlands",
    "utrecht": "Netherlands", "eindhoven": "Netherlands", "tilburg": "Netherlands",
    "groningen": "Netherlands", "almere": "Netherlands", "breda": "Netherlands",

    # Spain
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain", "seville": "Spain",
    "zaragoza": "Spain", "málaga": "Spain", "malaga": "Spain", "bilbao": "Spain",

    # Italy
    "rome": "Italy", "milan": "Italy", "naples": "Italy", "turin": "Italy",
    "palermo": "Italy", "genoa": "Italy", "bologna": "Italy", "florence": "Italy",

    # Poland
    "warsaw": "Poland", "krakow": "Poland", "łódź": "Poland", "wroclaw": "Poland",
    "poznan": "Poland", "gdansk": "Poland", "szczecin": "Poland", "lublin": "Poland",

    # Sweden
    "stockholm": "Sweden", "gothenburg": "Sweden", "göteborg": "Sweden",
    "malmö": "Sweden", "malmo": "Sweden", "uppsala": "Sweden",

    # Switzerland
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland",
    "basel": "Switzerland", "bern": "Switzerland", "lausanne": "Switzerland",

    # Austria
    "vienna": "Austria", "wien": "Austria", "graz": "Austria", "linz": "Austria",
    "salzburg": "Austria", "innsbruck": "Austria",

    # Belgium
    "brussels": "Belgium", "bruxelles": "Belgium", "antwerp": "Belgium",
    "ghent": "Belgium", "gent": "Belgium", "liège": "Belgium",

    # Ireland
    "dublin": "Ireland", "cork": "Ireland", "galway": "Ireland", "limerick": "Ireland",

    # Portugal
    "lisbon": "Portugal", "porto": "Portugal", "braga": "Portugal",

    # Denmark
    "copenhagen": "Denmark", "aarhus": "Denmark", "odense": "Denmark",

    # Norway
    "oslo": "Norway", "bergen": "Norway", "trondheim": "Norway",

    # Finland
    "helsinki": "Finland", "tampere": "Finland", "espoo": "Finland", "vantaa": "Finland",

    # Czech Republic
    "prague": "Czech Republic", "brno": "Czech Republic", "ostrava": "Czech Republic",

    # Romania
    "bucharest": "Romania", "cluj-napoca": "Romania", "timisoara": "Romania",

    # Hungary
    "budapest": "Hungary", "debrecen": "Hungary",

    # Ukraine
    "kyiv": "Ukraine", "kharkiv": "Ukraine", "lviv": "Ukraine", "odesa": "Ukraine",

    # Israel
    "tel aviv": "Israel", "jerusalem": "Israel", "haifa": "Israel",

    # Singapore
    "singapore": "Singapore",

    # India
    "bangalore": "India", "bengaluru": "India", "mumbai": "India", "delhi": "India",
    "new delhi": "India", "hyderabad": "India", "chennai": "India", "pune": "India",
    "kolkata": "India", "ahmedabad": "India", "noida": "India", "gurgaon": "India",
    "gurugram": "India",

    # Australia
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia",
    "perth": "Australia", "adelaide": "Australia", "canberra": "Australia",
    "gold coast": "Australia", "newcastle": "Australia",

    # New Zealand
    "auckland": "New Zealand", "wellington": "New Zealand", "christchurch": "New Zealand",

    # Japan
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan", "yokohama": "Japan",
    "nagoya": "Japan", "sapporo": "Japan", "fukuoka": "Japan",

    # South Korea
    "seoul": "South Korea", "busan": "South Korea", "incheon": "South Korea",

    # China
    "beijing": "China", "shanghai": "China", "shenzhen": "China", "guangzhou": "China",
    "hangzhou": "China", "chengdu": "China", "wuhan": "China", "xi'an": "China",

    # Brazil
    "são paulo": "Brazil", "sao paulo": "Brazil", "rio de janeiro": "Brazil",
    "brasilia": "Brazil", "salvador": "Brazil", "fortaleza": "Brazil",

    # Mexico
    "mexico city": "Mexico", "guadalajara": "Mexico", "monterrey": "Mexico",

    # UAE / Middle East
    "dubai": "UAE", "abu dhabi": "UAE", "riyadh": "Saudi Arabia",
    "doha": "Qatar", "manama": "Bahrain", "muscat": "Oman",

    # South Africa
    "johannesburg": "South Africa", "cape town": "South Africa", "durban": "South Africa",
}

# ─── Country name canonicalisation ────────────────────────────────────────────
COUNTRY_ALIASES: dict[str, str] = {
    "us": "United States", "usa": "United States", "u.s.": "United States",
    "u.s.a": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "great britain": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "deutschland": "Germany", "de": "Germany",
    "fr": "France", "nl": "Netherlands", "the netherlands": "Netherlands",
    "españa": "Spain", "es": "Spain",
    "italia": "Italy", "it": "Italy",
    "polska": "Poland", "pl": "Poland",
    "österreich": "Austria", "schweiz": "Switzerland", "suomi": "Finland",
    "norge": "Norway", "sverige": "Sweden", "danmark": "Denmark",
    "belgique": "Belgium", "be": "Belgium",
    "nederland": "Netherlands",
    "brasil": "Brazil", "br": "Brazil",
    "méxico": "Mexico", "mx": "Mexico",
    "in": "India", "au": "Australia", "sg": "Singapore",
    "jp": "Japan", "kr": "South Korea", "cn": "China",
    "za": "South Africa",
    "uae": "UAE", "united arab emirates": "UAE",
    "ca": "Canada",  # province code fallback (but "CA" also means California — handle carefully)
}

# ─── Remote keywords ──────────────────────────────────────────────────────────
REMOTE_KEYWORDS = [
    "fully remote", "100% remote", "work from home", "wfh",
    "remote only", "remote-first", "remote first",
    "work remotely", "anywhere in the world", "globally remote",
    "distributed team", "fully distributed", "remote position",
    "remote role", "remote opportunity", "location: remote",
    "location : remote", "location:remote",
    "no geographical restrictions", "remote with no geo",
]

HYBRID_KEYWORDS = [
    "hybrid", "2 days", "3 days", "2-3 days", "part remote",
    "flex", "flexible work", "partially remote",
]

ONSITE_KEYWORDS = [
    "on-site", "onsite", "on site", "in-office", "in office",
    "must be local", "not remote", "office based", "office-based",
    "required to be in", "required to come in", "office daily",
    "report to the office", "report to office",
]

# ─── Salary patterns ─────────────────────────────────────────────────────────
# Matches: $80k, $80,000, $80,000/yr, $80-120k, 80000 USD, £50,000
SALARY_PATTERNS = [
    # Range: $80,000 - $120,000
    r'[\$£€¥][\s]?(\d{2,3})[,\s]?(\d{3})[\s]?[-–—to]+[\s]?[\$£€¥]?[\s]?(\d{2,3})[,\s]?(\d{3})',
    # Range with k: $80k - $120k
    r'[\$£€¥][\s]?(\d{2,3})k[\s]?[-–—to]+[\s]?[\$£€¥]?[\s]?(\d{2,3})k',
    # Single: $120,000
    r'[\$£€¥][\s]?(\d{2,3})[,\s]?(\d{3})',
    # Single k: $120k
    r'[\$£€¥][\s]?(\d{2,3})k',
    # Plain number + currency word: 80000 USD, 80 000 EUR
    r'(\d{4,6})[\s]?(USD|EUR|GBP|CAD|AUD|INR)',
    # Salary: 80,000 to 120,000 (no symbol)
    r'salary[:\s]+(\d{2,3})[,\s]?(\d{3})[\s]?[-–—to]+[\s]?(\d{2,3})[,\s]?(\d{3})',
]

CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY",
    "USD": "USD", "EUR": "EUR", "GBP": "GBP", "CAD": "CAD",
    "AUD": "AUD", "INR": "INR", "SGD": "SGD",
}
