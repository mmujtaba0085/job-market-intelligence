"""
src/collectors/greenhouse_collector.py
───────────────────────────────────────
Collector for Greenhouse-hosted job boards (https://boards-api.greenhouse.io),
covering two Pakistan-relevant employers: Veeam Software and Motive
(formerly KeepTruckin).

Single-request fetch per board — confirmed by inspection:
  GET https://boards-api.greenhouse.io/v1/boards/<board-token>/jobs?content=true
returns every job on the board with full detail already inlined (title,
location, absolute_url, updated_at, content/description, departments,
offices, metadata), so unlike TenPearlsCollector there is no separate
per-job detail fetch — one `self._wait()` + one HTTP call covers a whole
board.

Both boards post far more than just Pakistan roles, so `_fetch_raw` filters
down to Pakistan-relevant postings via a per-subclass list of location
substrings checked against `location.name` (case-insensitive).

Location cleanup — Motive specific: Motive sometimes crams multiple
locations into one semicolon-separated `location.name` string, e.g.
"Pakistan - Islamabad; Pakistan - Karachi; Pakistan - Lahore; Pakistan -
Remote; Remote - Islamabad; Remote - Lahore" (same class of problem as
10Pearls' comma-crammed cities in tenpearls_collector.py, just semicolon-
delimited here). Verified against real fetched data (not just the spec
example above), which turned up two extra wrinkles the semicolon case
alone doesn't cover: (1) most Motive Pakistan postings are a *single*
segment, not semicolon-crammed, but still carry a "Pakistan - ", "Remote
- ", or "Hybrid - " prefix that needs stripping either way (e.g. "Pakistan
- Islamabad", "Hybrid - Islamabad"); (2) a handful of postings join two
cities with "&" inside one segment (e.g. "Hybrid - Islamabad & Lahore").
_split_locations() therefore always splits on ";", strips any of the three
known prefixes off every resulting segment (single or crammed alike), then
splits each remainder on "&" too, drops fragments that reduce to just
"remote"/"pakistan"/"anywhere" (no city information — "remote" segments
instead feed the remote_type fallback below via the raw string), and
de-duplicates case-insensitively. Confirmed by inspection: Veeam's
Pakistan postings use an unrelated "City, Pakistan" format (e.g. "Karachi,
Pakistan") that never matches these prefixes, so they pass through
_split_locations() as a single untouched city — the same function safely
handles both employers' formats. The resulting city list feeds
parsed_fields["all_locations"] (only set when 2+ cities are recovered),
which src/storage/db.py already turns into job_locations rows +
location_count — no new storage plumbing needed (see
src/enrichment/location_resolver.py / migrations/003_multi_location_
support.sql for the existing mechanism).

content field decoding: Greenhouse's `content` field is sometimes
double-HTML-entity-escaped (confirmed by inspection — e.g. literal
"&amp;lt;p&amp;gt;" instead of "<p>"). _decode_description() always
unescapes once, then unescapes a second time only if literal "&lt;"/"&gt;"
are still present afterwards, and keeps real HTML (job_detail.html renders
it via `{{ raw_description | safe }}`) rather than flattening to plain
text.

remote_type: read from the `metadata` array's "Work Type" entry when
present (e.g. "Hybrid" -> "hybrid", "Remote" -> "remote", "On-site"/
"Onsite" -> "on-site"). Confirmed by inspection: Veeam always populates
this metadata field; Motive never does (its metadata only ever carries
"Job Posting Department" / "Employment Type"), so every Motive job falls
back to reading the raw location string instead — and since most Motive
Pakistan postings are labelled "Hybrid - <city>" rather than containing
the word "remote", the fallback checks for "hybrid" and "on-site"/"onsite"
too, not just "remote" (checking only "remote" would leave the large
"Hybrid - ..." majority as "unknown", which the location string already
answers). Defaults to "unknown" if none of these signals are present.

Department pass-through: some Veeam Pakistan roles sit under messy
acquired-brand department names from the Securiti.ai acquisition (e.g.
"Securiti_JonathanCash 1009739" instead of a normal department). That
string is passed through untouched inside raw_json (cleaning it is out of
scope) but never used for the `company` field — company is always the
subclass's fixed `source_name`, regardless of internal department/brand.
"""

from __future__ import annotations

import html
import logging
import re

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

# Fragments that reduce to just this (case-insensitive) after prefix-stripping
# and "&"-splitting carry no city information and are dropped from the city
# list — "remote"/"pakistan" segments still feed remote_type detection via
# the raw (unsplit) location string, they just aren't cities in their own right.
_NON_CITY_SEGMENTS = {"remote", "anywhere", "pakistan"}

# Known non-city prefixes Motive prepends to a segment, e.g.
# "Pakistan - Islamabad", "Remote - Lahore", "Hybrid - Islamabad & Lahore".
_LOCATION_PREFIX_RE = re.compile(r"^(?:Pakistan|Remote|Hybrid)\s*-\s*", re.IGNORECASE)

# Splits a stripped segment on "&" to recover cities joined like
# "Islamabad & Lahore".
_AMPERSAND_RE = re.compile(r"\s*&\s*")


class GreenhouseCollector(BaseCollector):
    """
    Shared fetch/parse logic for Greenhouse-hosted job boards. Subclasses
    set `board_token`, `source_id`, `source_name`, and `_location_filters`
    (case-insensitive substrings checked against `location.name` to decide
    whether a posting is Pakistan-relevant).
    """

    board_token: str = ""            # override in subclass
    source_name: str = ""            # override in subclass
    _location_filters: tuple[str, ...] = ()  # override in subclass

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _fetch(self) -> dict:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs?content=true"
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ── Filtering ────────────────────────────────────────────────────────────

    def _is_relevant(self, location_name: str) -> bool:
        loc = (location_name or "").lower()
        return any(f.lower() in loc for f in self._location_filters)

    # ── Description decoding ────────────────────────────────────────────────

    def _decode_description(self, raw_content: str) -> str:
        """
        Unescape once; if the result still shows literal "&lt;"/"&gt;" (i.e.
        the source was double-escaped), unescape a second time. Verified
        against real fetched data rather than assumed.
        """
        if not raw_content:
            return ""
        decoded = html.unescape(raw_content)
        if "&lt;" in decoded or "&gt;" in decoded:
            decoded = html.unescape(decoded)
        return decoded.strip()

    # ── remote_type ──────────────────────────────────────────────────────────

    def _remote_type(self, metadata: list[dict] | None, location_name: str) -> str:
        for entry in metadata or []:
            if (entry.get("name") or "").strip().lower() == "work type":
                value = (entry.get("value") or "").strip().lower()
                if "hybrid" in value:
                    return "hybrid"
                if "remote" in value:
                    return "remote"
                if "on-site" in value or "onsite" in value or "on site" in value:
                    return "on-site"
        # No "Work Type" metadata (always true for Motive — confirmed by
        # inspection) — fall back to the raw location string. Checked in
        # this order because a string can contain both "hybrid" and
        # "remote" (e.g. "Hybrid - Islamabad & Lahore; Pakistan - Remote"),
        # and the explicit "Hybrid"/"Remote" prefix token is the more
        # specific signal.
        loc = (location_name or "").lower()
        if "hybrid" in loc:
            return "hybrid"
        if "remote" in loc:
            return "remote"
        if "on-site" in loc or "onsite" in loc or "on site" in loc:
            return "on-site"
        return "unknown"

    # ── Location cleanup ─────────────────────────────────────────────────────

    def _split_locations(self, location_name: str) -> list[str]:
        """
        Split a location string into a clean, deduplicated city list.
        Splits on ";" first (Motive's crammed multi-location format), then
        strips a leading "Pakistan - " / "Remote - " / "Hybrid - " token off
        every resulting segment — single-segment strings need this just as
        much as crammed ones, since most Motive postings are a single
        segment like "Pakistan - Islamabad" or "Hybrid - Islamabad" rather
        than semicolon-crammed. Each stripped segment is further split on
        "&" to recover cities joined like "Islamabad & Lahore". Fragments
        that reduce to "remote"/"pakistan"/"anywhere" carry no city
        information and are dropped (that signal instead feeds
        _remote_type() via the raw, unsplit string).

        Veeam's "City, Pakistan" format (e.g. "Karachi, Pakistan") never
        matches the prefix regex or contains "&", so it passes through
        unchanged as a single city — this one function safely handles both
        employers' formats.
        """
        raw = (location_name or "").strip()
        if not raw:
            return []

        segments = [s.strip() for s in raw.split(";") if s.strip()]

        cities: list[str] = []
        seen_lower: set[str] = set()
        for seg in segments:
            stripped = _LOCATION_PREFIX_RE.sub("", seg).strip()
            for fragment in _AMPERSAND_RE.split(stripped):
                city = fragment.strip()
                if not city or city.lower() in _NON_CITY_SEGMENTS:
                    continue
                key = city.lower()
                if key in seen_lower:
                    continue
                seen_lower.add(key)
                cities.append(city)
        return cities

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, job: dict) -> JobRaw | None:
        url = job.get("absolute_url") or ""
        if not url:
            return None

        title = (job.get("title") or "").strip()
        description = self._decode_description(job.get("content") or "")
        if not description:
            return None

        location_name = (job.get("location") or {}).get("name") or ""
        cities = self._split_locations(location_name)
        if len(cities) > 1:
            location = cities[0]
            all_locations = cities
        elif len(cities) == 1:
            location = cities[0]
            all_locations = None
        else:
            location = location_name
            all_locations = None

        # Infer country from the cleaned location (a real city, once one was
        # recovered) rather than the raw location_name - confirmed by
        # inspection that infer_country() checks its "Global" bucket (which
        # "remote" belongs to) before country keywords, so a genuinely
        # Pakistan-based posting like "Pakistan - Islamabad; ...; Remote -
        # Lahore" was wrongly resolving to "Global" purely because "Remote"
        # also appears as one of several crammed segments in the same
        # string. When no city survives cleaning (e.g. the whole field is
        # just "Pakistan - Remote"), the country name is still explicitly
        # present as a token in the raw string and takes priority over the
        # generic Global/remote inference - these are exactly the postings
        # _is_relevant() matched via that same "Pakistan" substring, so
        # discarding it at this point would erase the signal that got the
        # posting included in the first place.
        loc_lower = location_name.lower()
        if cities:
            country = infer_country(location)
        elif "pakistan" in loc_lower:
            country = "Pakistan"
        else:
            country = infer_country(location_name)
        remote_type = self._remote_type(job.get("metadata"), location_name)
        posted_date = job.get("updated_at") or ""

        return JobRaw(
            source_id=self.source_id,
            source_name=self.source_name,
            url=url,
            fetched_at=self._now(),
            raw_json=job,
            parsed_fields={
                "title": title,
                "company": self.source_name,
                "location": location,
                "all_locations": all_locations,
                "country": country,
                "remote_type": remote_type,
                "posted_date": posted_date,
                "description": description,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        self._wait()
        data = self._fetch()
        jobs = data.get("jobs") or []

        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        for job in jobs:
            location_name = (job.get("location") or {}).get("name") or ""
            if not self._is_relevant(location_name):
                continue

            title = job.get("title") or ""
            if keywords and not any(kw in title.lower() for kw in keywords):
                continue

            built = self._build_job(job)
            if built:
                results.append(built)

            if max_jobs is not None and len(results) >= max_jobs:
                break

        logger.info(
            "[%s] Collected %d Pakistan-relevant jobs from %d board postings",
            self.source_id, len(results), len(jobs),
        )
        return results


class VeeamCollector(GreenhouseCollector):
    source_id = "veeam"
    board_token = "veeamsoftware"
    source_name = "Veeam Software"
    _location_filters = ("Pakistan", "Islamabad", "Karachi")


class MotiveCollector(GreenhouseCollector):
    source_id = "motive"
    board_token = "gomotive"
    source_name = "Motive"
    _location_filters = ("Pakistan", "Islamabad", "Karachi", "Lahore")
