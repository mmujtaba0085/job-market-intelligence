"""
scripts/normalize_countries.py
────────────────────────────────
Cleans up malformed country values in the jobs table:
  - Zip codes          → "United States"
  - "State + Zip"      → "United States"
  - Street addresses   → "Unknown"
  - Multi-country      → "Unknown"
  - Runs infer_country() on everything else

Run inside the container:
  python scripts/normalize_countries.py
"""

import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.country_inference import infer_country, _US_STATE_ABBR
from src.storage.db import get_connection

# ── Canonical country names that are already correct ──────────────────────────
_CANONICAL = {
    "United States", "Germany", "United Kingdom", "Canada", "India",
    "France", "China", "Singapore", "Switzerland", "Australia", "Netherlands",
    "United Arab Emirates", "Brazil", "Spain", "Mexico", "Japan", "Italy",
    "Israel", "Poland", "Ireland", "South Korea", "Argentina", "Turkey",
    "Sweden", "Norway", "Denmark", "Finland", "Ukraine", "Pakistan",
    "New Zealand", "South Africa", "Portugal", "Romania", "Czech Republic",
    "Hungary", "Greece", "Belgium", "Austria", "Russia", "Philippines",
    "Thailand", "Malaysia", "Luxembourg", "Bulgaria", "Liechtenstein",
    "Cayman Islands", "Unknown", "Global",
}

# ── Patterns ──────────────────────────────────────────────────────────────────
_ZIP_ONLY      = re.compile(r'^\d{5}(-\d{4})?$')
_STATE_ZIP     = re.compile(r'^([A-Z]{2})\s+\d{4,6}', re.I)
_STREET_ADDR   = re.compile(
    r'\d+\s+[A-Za-z].*(?:St|Ave|Blvd|Dr|Rd|Ln|Pl|Way|Pkwy|Hwy|Plaza|Blvd)\b',
    re.I,
)
_MULTI_COUNTRY = re.compile(r',.*,')   # two or more commas = multi-country list


def _classify(raw: str) -> str:
    s = raw.strip()
    if not s:
        return "Unknown"

    # Already canonical
    if s in _CANONICAL:
        return s

    low = s.lower()

    # Zip code only
    if _ZIP_ONLY.match(s):
        return "United States"

    # "STATE ZIP" like "CO 80033", "AR 72701", "AL36701"
    m = _STATE_ZIP.match(s)
    if m and m.group(1).lower() in _US_STATE_ABBR:
        return "United States"

    # Street address
    if _STREET_ADDR.search(s):
        return "Unknown"

    # Multi-country list like "Brazil, Argentina, Philippines"
    if _MULTI_COUNTRY.search(s):
        return "Unknown"

    # Strip noise suffixes: "(ONSITE)", "(Hybrid)", "(In-Person)", "Area", etc.
    cleaned = re.sub(
        r'\s*[\(\[].*?[\)\]]'         # remove (…) and […]
        r'|\b(onsite|hybrid|remote|area|metro|region|metropolitan|hq|corporate)\b',
        '', s, flags=re.I,
    ).strip().rstrip(',').strip()

    # Try inference on the cleaned string
    result = infer_country(cleaned)
    if result != "Unknown":
        return result

    # Try inference on the original
    result = infer_country(s)
    if result != "Unknown":
        return result

    # All-caps short string that looks like an abbreviation
    if s.isupper() and len(s) <= 4:
        return "Unknown"

    return "Unknown"


def main():
    conn = get_connection()

    # Get all distinct non-canonical country values
    rows = conn.execute("""
        SELECT DISTINCT country, COUNT(*) as c
        FROM jobs
        WHERE country IS NOT NULL
          AND TRIM(country) != ''
          AND country NOT IN ({})
        GROUP BY country
        ORDER BY c DESC
    """.format(",".join("?" * len(_CANONICAL))),
        list(_CANONICAL),
    ).fetchall()

    print(f"Distinct non-canonical values: {len(rows)}")

    updates: list[tuple[str, str]] = []
    unchanged: list[str] = []

    for row in rows:
        raw = row["country"]
        new = _classify(raw)
        if new != raw:
            updates.append((new, raw))
        else:
            unchanged.append(raw)

    print(f"Will update: {len(updates)}")
    print(f"Unchanged:   {len(unchanged)}")

    if unchanged:
        print("\nStill non-canonical after cleanup:")
        for v in unchanged[:30]:
            print(f"  {repr(v)}")

    if not updates:
        print("Nothing to do.")
        conn.close()
        return

    with conn:
        conn.executemany(
            "UPDATE jobs SET country = ? WHERE country = ?",
            updates,
        )

    conn.close()

    # Summary
    from collections import Counter
    c: Counter = Counter()
    for new, _ in updates:
        c[new] += 1
    print("\nMapped to:")
    for country, count in c.most_common():
        print(f"  {country:<30} {count} distinct source values")


if __name__ == "__main__":
    main()
