"""
src/enrichment/auto_enrich.py
──────────────────────────────
Self-running enrichment engine — no external APIs, no Claude calls.

Fills in missing fields (country, city, remote_type, salary) by
extracting signals from the job description text.

Design principles:
  - Fully offline: regex + static lookup tables only
  - Idempotent: safe to run multiple times on same record
  - Conservative: only overwrites 'Unknown'/empty, never a real value
  - Logged: every change is recorded so you can audit what changed

Usage:
  # Enrich a single row dict (e.g. from sqlite3.Row)
  from src.enrichment import enrich_job
  changes = enrich_job(job_id, job_dict, conn)

  # Bulk enrich all incomplete rows in the DB
  from src.enrichment.auto_enrich import run_batch_enrichment
  run_batch_enrichment()
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from config.settings import DB_PATH
from .location_data import (
    US_STATES, US_CITIES, CA_CITIES, CA_PROVINCES,
    UK_CITIES, DE_CITIES, EU_CITIES, COUNTRY_ALIASES,
    REMOTE_KEYWORDS, HYBRID_KEYWORDS, ONSITE_KEYWORDS,
    SALARY_PATTERNS, CURRENCY_SYMBOLS,
)

logger = logging.getLogger(__name__)


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class EnrichmentResult:
    job_id: int
    changes: dict[str, tuple[str, str]] = field(default_factory=dict)  # field → (old, new)
    skipped: list[str] = field(default_factory=list)    # fields already populated
    signals: dict[str, str] = field(default_factory=dict)  # field → extraction method used

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def __repr__(self) -> str:
        if not self.changes:
            return f"EnrichmentResult(job_id={self.job_id}, no changes)"
        parts = [f"{k}: '{v[0]}' → '{v[1]}'" for k, v in self.changes.items()]
        return f"EnrichmentResult(job_id={self.job_id}, changes=[{', '.join(parts)}])"


# ─── HTML / text cleaning ─────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")
_HTML_ENTITIES: dict[str, str] = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&nbsp;": " ", "&#8203;": "", "&mdash;": "—",
    "&ndash;": "–", "&laquo;": "«", "&raquo;": "»",
}


def strip_html(html: str) -> str:
    """Strip HTML tags and decode entities."""
    if not html:
        return ""
    for ent, char in _HTML_ENTITIES.items():
        html = html.replace(ent, char)
    text = _HTML_TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()


# ─── Location extraction ──────────────────────────────────────────────────────

# Pattern: "Location: San Francisco, CA" or "Location: Remote"
_LOC_LABEL_RE = re.compile(
    r"""(?ix)
    (?:location|office|based\s+in|located\s+in|position\s+(?:is\s+)?(?:in|based))
    [\s:–\-]+
    ([^<\n\r]{3,80})
    """,
    re.IGNORECASE,
)

# "This is a remote position in New York"
_REMOTE_CITY_RE = re.compile(
    r"(?:remote|work\s+from\s+home)\s+(?:in|based\s+in|position\s+in)\s+([A-Z][a-zA-Z\s,]+)",
    re.IGNORECASE,
)

# "candidates in CA, CO, FL" — captures the state list
_CANDIDATES_IN_RE = re.compile(
    r"candidates?\s+(?:must\s+be\s+)?in\s+([A-Z]{2}(?:\s*,\s*[A-Z]{2})+)",
    re.IGNORECASE,
)

# "this role is in [City, State]"
_ROLE_IN_RE = re.compile(
    r"(?:this\s+(?:role|position|job|opening)\s+(?:is\s+)?(?:in|based\s+in)|"
    r"(?:role|position|job|opening)\s+(?:is\s+)?based\s+in|"
    r"we\s+(?:are\s+)?(?:located\s+)?in|our\s+office\s+(?:is\s+)?in)\s+"
    r"([A-Z][a-zA-Z\s,\.]+(?:[A-Z]{2})?)",
    re.IGNORECASE,
)

_CITY_OFFICE_RE = re.compile(
    r"\b(?:our|the)\s+([A-Z][A-Za-z.\- ]{2,40}?)\s+office\b",
)


def extract_location_from_text(text: str) -> tuple[Optional[str], Optional[str], str]:
    """
    Extract (city, country, method) from free text or HTML description.
    Returns (None, None, '') if nothing found.
    """
    plain = strip_html(text)

    # 1. Explicit "Location:" label
    m = _LOC_LABEL_RE.search(plain)
    if m:
        loc_raw = m.group(1).strip().rstrip(".,;")
        city, country = _parse_location_string(loc_raw)
        if country:
            return city, country, "location_label"

    # 2. Remote + city pattern
    m = _REMOTE_CITY_RE.search(plain)
    if m:
        loc_raw = m.group(1).strip().rstrip(".,;")
        city, country = _parse_location_string(loc_raw)
        if country:
            return city, country, "remote_city_pattern"

    # 3. "candidates in XX, YY, ZZ" → infer US
    m = _CANDIDATES_IN_RE.search(plain)
    if m:
        codes = [c.strip().upper() for c in m.group(1).split(",")]
        valid = [c for c in codes if c in US_STATES]
        if valid:
            return None, "United States", "candidates_in_states"

    # 4. "role is in / office in" pattern
    m = _ROLE_IN_RE.search(plain)
    if m:
        loc_raw = m.group(1).strip().rstrip(".,;")
        city, country = _parse_location_string(loc_raw)
        if country:
            return city, country, "role_in_pattern"

    # 5. Explicitly possessive/location-labelled office phrase.
    m = _CITY_OFFICE_RE.search(plain)
    if m:
        city, country = _parse_location_string(m.group(1).strip())
        if country:
            return city, country, "city_office_pattern"

    return None, None, ""


def _parse_location_string(loc: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse 'San Francisco, CA' or 'London, UK' or 'Remote' into (city, country).
    """
    if not loc:
        return None, None

    loc_strip = loc.strip()
    loc_lower = loc_strip.lower()

    # Remote / global signals → country = "Global"
    if any(k in loc_lower for k in ["remote", "anywhere", "worldwide", "globally", "distributed"]):
        return None, "Global"

    # Split on comma
    parts = [p.strip() for p in loc_strip.split(",")]

    # Check if last part is a known country alias
    if len(parts) >= 2:
        last = parts[-1].strip().lower()

        # Preserve case when resolving short codes. In structured locations,
        # "CA" is California while lowercase "ca" can be a Canada alias.
        last_upper = parts[-1].strip().upper()
        if parts[-1].strip() == last_upper and last_upper in US_STATES:
            city = parts[0]
            return city, "United States"

        # CA province
        if last in CA_PROVINCES:
            city = parts[0]
            return city, "Canada"

        if last in COUNTRY_ALIASES:
            city = ", ".join(parts[:-1])
            return city, COUNTRY_ALIASES[last]

    # Single token — check city databases
    first = parts[0].lower()

    if first in US_CITIES:
        return parts[0], "United States"
    # London without a province/country is overwhelmingly the UK in global
    # listings. Explicit "London, Ontario" is handled above.
    if first in UK_CITIES:
        return parts[0], "United Kingdom"
    if first in CA_CITIES:
        return parts[0], "Canada"
    if first in DE_CITIES:
        return parts[0], "Germany"
    if first in EU_CITIES:
        return parts[0], EU_CITIES[first]

    # Last resort: if single token matches a country alias
    if first in COUNTRY_ALIASES:
        return None, COUNTRY_ALIASES[first]

    return None, None


def _scan_for_cities(text_lower: str) -> tuple[Optional[str], Optional[str]]:
    """
    Scan lowercased text for any known city name.
    Sorts by length descending so 'new york' matches before 'york',
    and 'san francisco' matches before 'san'.
    Uses word-boundary anchoring to avoid substring false-positives.
    """
    import re as _re

    def _matches(city: str) -> bool:
        # Use word-boundary regex for single-word cities to avoid 'york' in 'yorkshire'
        if " " in city:
            return city in text_lower
        pattern = r'\b' + _re.escape(city) + r'\b'
        return bool(_re.search(pattern, text_lower))

    # Build combined list sorted longest-first
    all_cities: list[tuple[str, str]] = []
    for city, country in EU_CITIES.items():
        all_cities.append((city, country))
    for city in sorted(UK_CITIES, key=len, reverse=True):
        all_cities.append((city, "United Kingdom"))
    for city in sorted(DE_CITIES, key=len, reverse=True):
        all_cities.append((city, "Germany"))
    for city in sorted(CA_CITIES, key=len, reverse=True):
        all_cities.append((city, "Canada"))
    for city in sorted(US_CITIES, key=len, reverse=True):
        all_cities.append((city, "United States"))

    # Sort entire list longest-first so multi-word names win
    all_cities.sort(key=lambda x: len(x[0]), reverse=True)

    for city, country in all_cities:
        if _matches(city):
            return city.title(), country

    return None, None


# ─── Remote type extraction ───────────────────────────────────────────────────

def extract_remote_type(text: str, location: str = "") -> Optional[str]:
    """
    Return 'remote' | 'hybrid' | 'on-site' | None.
    None = cannot determine from text.
    """
    combined = f"{strip_html(text)} {location}".lower()

    # Check remote keywords first (strongest signal)
    for kw in REMOTE_KEYWORDS:
        if kw in combined:
            return "remote"

    # Hybrid
    for kw in HYBRID_KEYWORDS:
        if kw in combined:
            return "hybrid"

    # On-site
    for kw in ONSITE_KEYWORDS:
        if kw in combined:
            return "on-site"

    # If a city/location is present with no remote mention → likely on-site
    if location and location.lower() not in ("", "unknown", "global", "none"):
        return "on-site"

    return None


# ─── Salary extraction ────────────────────────────────────────────────────────

_COMPILED_SALARY: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in SALARY_PATTERNS]


def extract_salary(text: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Extract (salary_min, salary_max, currency) from text.
    Returns (None, None, None) if not found.
    """
    plain = strip_html(text)

    # Detect currency symbol
    currency = None
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in plain:
            currency = code
            break

    # Try each pattern in priority order
    # Pattern 0: full range with 6-digit numbers
    m = re.search(
        r'([\$£€¥])[\s]?(\d{2,3})[,\s]?(\d{3})[\s]?[-–—to]+[\s]?(?:[\$£€¥][\s]?)?(\d{2,3})[,\s]?(\d{3})',
        plain, re.IGNORECASE
    )
    if m:
        sym = m.group(1)
        lo = float(m.group(2) + m.group(3))
        hi = float(m.group(4) + m.group(5))
        return lo, hi, CURRENCY_SYMBOLS.get(sym, currency or "USD")

    # Pattern 1: k-range  $80k - $120k
    m = re.search(
        r'([\$£€¥])[\s]?(\d{2,3})k[\s]?[-–—to]+[\s]?(?:[\$£€¥][\s]?)?(\d{2,3})k',
        plain, re.IGNORECASE
    )
    if m:
        sym = m.group(1)
        lo = float(m.group(2)) * 1000
        hi = float(m.group(3)) * 1000
        return lo, hi, CURRENCY_SYMBOLS.get(sym, currency or "USD")

    # Pattern 2: single 6-digit  $120,000
    m = re.search(
        r'([\$£€¥])[\s]?(\d{2,3})[,\s]?(\d{3})',
        plain, re.IGNORECASE
    )
    if m:
        sym = m.group(1)
        val = float(m.group(2) + m.group(3))
        if val > 10000:  # sanity — not a phone number
            return val, None, CURRENCY_SYMBOLS.get(sym, currency or "USD")

    # Pattern 3: single k  $120k
    m = re.search(
        r'([\$£€¥])[\s]?(\d{2,3})k',
        plain, re.IGNORECASE
    )
    if m:
        sym = m.group(1)
        val = float(m.group(2)) * 1000
        return val, None, CURRENCY_SYMBOLS.get(sym, currency or "USD")

    # Pattern 4: plain number + currency word
    m = re.search(r'(\d{5,6})[\s]?(USD|EUR|GBP|CAD|AUD)', plain, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return val, None, m.group(2).upper()

    return None, None, None


# ─── Main enrichment function ─────────────────────────────────────────────────

_MISSING_VALUES = {"", "unknown", "none", "null", "n/a"}


def _is_missing(val) -> bool:
    return val is None or str(val).strip().lower() in _MISSING_VALUES


def enrich_job(job_id: int, job: dict, conn: sqlite3.Connection) -> EnrichmentResult:
    """
    Enrich a single job record in-place and write updates to DB.

    job dict must have: job_id, title, company, location, country,
                        remote_type, raw_description, salary_min, salary_max, currency
    """
    result = EnrichmentResult(job_id=job_id)
    updates: dict[str, object] = {}

    desc = job.get("raw_description", "") or ""
    location = job.get("location", "") or ""
    country = job.get("country", "") or ""

    # ── 1. Country + city from description ───────────────────────────────────
    if _is_missing(country):
        city_found, country_found, method = extract_location_from_text(desc)

        # Also try the location field itself if not empty
        if not country_found and location and not _is_missing(location):
            _, country_found, method = extract_location_from_text(location)
            if not country_found:
                _, country_found = _parse_location_string(location)
                if country_found:
                    method = "location_field_parse"

        if country_found:
            result.changes["country"] = (country, country_found)
            result.signals["country"] = method
            updates["country"] = country_found

            if city_found and _is_missing(location):
                result.changes["location"] = (location, city_found)
                result.signals["location"] = method
                updates["location"] = city_found
    else:
        result.skipped.append("country")

    # ── 2. Remote type from description ──────────────────────────────────────
    remote = job.get("remote_type", "") or ""
    if _is_missing(remote) or remote.lower() == "unknown":
        inferred = extract_remote_type(desc, location=updates.get("location", location))
        if inferred:
            result.changes["remote_type"] = (remote, inferred)
            result.signals["remote_type"] = "desc_keywords"
            updates["remote_type"] = inferred
    else:
        result.skipped.append("remote_type")

    # ── 3. Salary from description ────────────────────────────────────────────
    sal_min = job.get("salary_min")
    sal_max = job.get("salary_max")
    if _is_missing(sal_min) and _is_missing(sal_max):
        s_min, s_max, s_cur = extract_salary(desc)
        if s_min is not None:
            result.changes["salary_min"] = (str(sal_min), str(s_min))
            updates["salary_min"] = s_min
            result.signals["salary_min"] = "desc_salary_pattern"
        if s_max is not None:
            result.changes["salary_max"] = (str(sal_max), str(s_max))
            updates["salary_max"] = s_max
        if s_cur and _is_missing(job.get("currency")):
            result.changes["currency"] = (str(job.get("currency")), s_cur)
            updates["currency"] = s_cur
    else:
        result.skipped.append("salary")

    # ── Write to DB ───────────────────────────────────────────────────────────
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [job_id]
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id=?", values)
        conn.commit()
        logger.debug("[enrich] job_id=%d updated: %s", job_id, list(updates.keys()))

    return result


def enrich_batch(
    conn: sqlite3.Connection,
    source_filter: Optional[str] = None,
    limit: int = 0,
    dry_run: bool = False,
) -> list[EnrichmentResult]:
    """
    Enrich all jobs that have missing country, remote_type, or salary.

    Args:
        conn:          Open SQLite connection.
        source_filter: If set, only enrich jobs from this source.
        limit:         Max jobs to process (0 = all).
        dry_run:       If True, compute but don't write to DB.

    Returns list of EnrichmentResult (changed ones only).
    """
    where_parts = [
        "(country = '' OR country = 'Unknown' OR country IS NULL"
        " OR remote_type = 'unknown' OR remote_type IS NULL"
        " OR salary_min IS NULL)"
    ]
    if source_filter:
        where_parts.append("source_name = ?")
        params: list = [source_filter]
    else:
        params = []

    sql = (
        "SELECT job_id, title, company, location, country, remote_type, "
        "raw_description, salary_min, salary_max, currency, source_name "
        "FROM jobs WHERE " + " AND ".join(where_parts)
    )
    if limit:
        sql += f" LIMIT {int(limit)}"

    rows = conn.execute(sql, params).fetchall()
    logger.info("[enrich] Processing %d incomplete jobs (dry_run=%s)", len(rows), dry_run)

    results: list[EnrichmentResult] = []
    changed = 0

    for row in rows:
        job = dict(row)
        job_id = job["job_id"]

        if dry_run:
            # Compute without writing
            tmp_conn = _NullConn()
            r = enrich_job(job_id, job, tmp_conn)  # type: ignore[arg-type]
        else:
            r = enrich_job(job_id, job, conn)

        if r.changed:
            results.append(r)
            changed += 1

    logger.info("[enrich] Updated %d/%d jobs", changed, len(rows))
    return results


# ─── Null connection for dry-run ─────────────────────────────────────────────

class _NullConn:
    """Fake connection that silently drops all writes."""
    def execute(self, *a, **kw): return self
    def commit(self): pass
    def fetchall(self): return []


# ─── Standalone CLI entrypoint ────────────────────────────────────────────────

def run_batch_enrichment(source: Optional[str] = None, dry_run: bool = False, limit: int = 0) -> dict:
    """
    Enrich the live jobs.sqlite database.
    Returns summary stats.
    """
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    results = enrich_batch(conn, source_filter=source, limit=limit, dry_run=dry_run)
    conn.close()

    # Summary
    by_field: dict[str, int] = {}
    for r in results:
        for f in r.changes:
            by_field[f] = by_field.get(f, 0) + 1

    summary = {
        "jobs_changed": len(results),
        "by_field": by_field,
        "dry_run": dry_run,
    }
    return summary


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description="Auto-enrich missing job fields")
    ap.add_argument("--source", help="Limit to one source_name")
    ap.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    ap.add_argument("--limit", type=int, default=0, help="Max rows to process")
    args = ap.parse_args()

    summary = run_batch_enrichment(source=args.source, dry_run=args.dry_run, limit=args.limit)
    print(f"\n{'DRY RUN — ' if summary['dry_run'] else ''}Enrichment complete")
    print(f"  Jobs updated: {summary['jobs_changed']}")
    print("  By field:")
    for f, n in summary["by_field"].items():
        print(f"    {f}: {n}")
