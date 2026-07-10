"""
src/collectors/ashby_collector.py
──────────────────────────────────
Collector for Ashby-hosted job boards (public "posting-api", no auth).

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/<org-slug>
A single request returns the org's *entire* current job list — no
pagination, no per-job detail fetch needed, since each list item already
carries the full HTML description (`descriptionHtml`).

Response shape (per job): id, title, department, team, employmentType,
location, secondaryLocations, publishedAt, isListed, isRemote,
workplaceType, address: {postalAddress: {addressLocality, addressRegion,
addressCountry, postalCode, streetAddress}}, jobUrl, applyUrl,
descriptionHtml, descriptionPlain.

`AshbyCollectorBase` does the actual fetch/parse; concrete collectors only
set `source_id`, `company_name` (used as both source_name and the
`company` parsed field), and `org_slug`:

  - VyroCollector (org_slug="imagineart", ~30 jobs). The board's public
    brand is "ImagineArt", but the postings' own body text names the
    legal/parent company as Vyro ("Vyro is redefining the future of
    digital creativity... Vyro's 20+ AI-powered apps") — company_name is
    set to "Vyro", not the board's slug-derived brand. Known source
    quirks are passed through as-is, not "fixed" here (the app's
    canonical-hash dedup already handles near-duplicates downstream):
      * one listing has the location typo "San Franciso Office" (missing
        a "t") — left untouched, no spelling correction attempted.
      * ~4 near-duplicate "Go Lang Developer" postings, one per country
        (China/Malaysia/India/Indonesia), posted minutes apart.
      * `workplaceType` is null on 2 of 30 records (both also have
        `isRemote=None`) — falls through to "unknown" per the mapping
        rules below.

  - KodiflyCollector (org_slug="kodifly", ~2 jobs — small board, expected
    to stay small). The flat `location` field is country-level only
    ("Pakistan"), even though a real city sits nested at
    `address.postalAddress.addressLocality` ("Islamabad") in the very
    same list-endpoint response. KodiflyCollector overrides
    `_refine_location()` to prefer that nested city, producing
    "Islamabad, Pakistan" instead of bare "Pakistan". `department`/`team`
    also carry inconsistent trailing whitespace in the source
    ("Design - Pakistan" vs "Projects - Pakistan ") — stripped along with
    every other text field pulled from the API, as a matter of course.

Country: prefers `address.postalAddress.addressCountry` (already a clean,
canonical country name straight from Ashby, e.g. "Turkey", "Pakistan")
over regex-based `infer_country()` on the flat `location` string. This
matters because at least one real Vyro listing's `location` is "Turkiye"
(no diacritic), which does not match country_inference.py's accented
"türkiye" keyword and would otherwise resolve to "Unknown". Falls back to
`infer_country(location)` when no address block is present at all
(observed on 2/30 Vyro records, the "PR Manager | USA" duplicates).

remote_type: workplaceType "OnSite"/"Remote"/"Hybrid" map directly to
"on-site"/"remote"/"hybrid"; null falls back to the `isRemote` bool
(True → "remote"); anything else → "unknown".

No compensation/salary field is present anywhere in either org's
job-board response, so salary_min/salary_max/currency/salary_period are
simply omitted from parsed_fields — same convention as other JSON-API
collectors with no salary data (see arbeitnow_collector.py).
"""

from __future__ import annotations

import logging

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_JOB_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_TIMEOUT = 20
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

# workplaceType (Ashby) → remote_type (this app's canonical values)
_WORKPLACE_TYPE_MAP = {
    "OnSite": "on-site",
    "Remote": "remote",
    "Hybrid": "hybrid",
}


def _s(value) -> str:
    """Strip a possibly-None/possibly-whitespace-padded API string field."""
    return (value or "").strip()


class AshbyCollectorBase(BaseCollector):
    """
    Shared fetch/parse logic for any Ashby "posting-api/job-board" org.
    Concrete subclasses set source_id, company_name, org_slug.
    """

    org_slug: str = ""       # override in subclass
    company_name: str = ""   # override in subclass — used as source_name + company

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _fetch_board(self) -> list[dict]:
        url = _JOB_BOARD_URL.format(slug=self.org_slug)
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("jobs") or []

    # ── Location / country ──────────────────────────────────────────────────

    def _refine_location(self, location_raw: str, address_locality: str, country: str) -> str:
        """
        Default: pass the flat `location` field through unchanged — it's
        already reasonably clean for most Ashby boards, and "fixing" it
        (e.g. correcting typos) is explicitly out of scope. Overridden by
        KodiflyCollector to enrich with the nested address city.
        """
        return location_raw

    def _resolve_country(self, location_raw: str, address_country: str) -> str:
        # address.postalAddress.addressCountry is already a clean, canonical
        # country name straight from Ashby — prefer it over regex inference,
        # which can miss ASCII-folded spellings (e.g. "Turkiye" vs the
        # accented "türkiye" key country_inference.py matches on).
        if address_country:
            return address_country
        return infer_country(location_raw)

    @staticmethod
    def _remote_type(workplace_type: str | None, is_remote) -> str:
        if workplace_type in _WORKPLACE_TYPE_MAP:
            return _WORKPLACE_TYPE_MAP[workplace_type]
        if is_remote is True:
            return "remote"
        return "unknown"

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, job: dict) -> JobRaw | None:
        title = _s(job.get("title"))
        url = job.get("jobUrl") or job.get("applyUrl")
        description = (job.get("descriptionHtml") or job.get("descriptionPlain") or "").strip()
        if not title or not url or not description:
            return None

        location_raw = _s(job.get("location"))
        address = job.get("address") or {}
        postal = address.get("postalAddress") or {}
        address_locality = _s(postal.get("addressLocality"))
        address_country = _s(postal.get("addressCountry"))

        country = self._resolve_country(location_raw, address_country)
        location = self._refine_location(location_raw, address_locality, country) or location_raw

        remote_type = self._remote_type(job.get("workplaceType"), job.get("isRemote"))

        secondary_names = [
            _s(loc.get("location")) if isinstance(loc, dict) else _s(loc)
            for loc in (job.get("secondaryLocations") or [])
        ]
        secondary_names = [s for s in secondary_names if s]
        all_locations = [location, *secondary_names] if secondary_names else None

        return JobRaw(
            source_id=self.source_id,
            source_name=self.company_name,
            url=url,
            fetched_at=self._now(),
            raw_json={
                "id": job.get("id"),
                "department": _s(job.get("department")),
                "team": _s(job.get("team")),
                "employmentType": job.get("employmentType"),
                "location_raw": location_raw,
                "address": address,
                "workplaceType": job.get("workplaceType"),
                "isRemote": job.get("isRemote"),
                "publishedAt": job.get("publishedAt"),
            },
            parsed_fields={
                "title": title,
                "company": self.company_name,
                "location": location,
                "all_locations": all_locations,
                "country": country,
                "remote_type": remote_type,
                "posted_date": job.get("publishedAt"),
                "description": description,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        self._wait()
        jobs = self._fetch_board()

        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        for job in jobs:
            if job.get("isListed") is False:
                continue

            title = _s(job.get("title"))
            if keywords and not any(kw in title.lower() for kw in keywords):
                continue

            built = self._build_job(job)
            if built:
                results.append(built)

            if max_jobs is not None and len(results) >= max_jobs:
                break

        logger.info(
            "[%s] Collected %d jobs from %d listing entries",
            self.source_id, len(results), len(jobs),
        )
        return results


class VyroCollector(AshbyCollectorBase):
    source_id = "vyro"
    org_slug = "imagineart"
    company_name = "Vyro"


class KodiflyCollector(AshbyCollectorBase):
    source_id = "kodifly"
    org_slug = "kodifly"
    company_name = "Kodifly"

    def _refine_location(self, location_raw: str, address_locality: str, country: str) -> str:
        """
        Kodifly's flat `location` is country-level only ("Pakistan"); the
        nested address block carries a real city ("Islamabad") in the same
        list-endpoint response. Prefer "<city>, <country-level location>"
        when the nested city adds something the flat field doesn't already
        say (guards against boards where addressLocality just repeats the
        country name, as seen on some Vyro records).
        """
        if (
            address_locality
            and address_locality.lower() != location_raw.lower()
            and address_locality.lower() != country.lower()
        ):
            return f"{address_locality}, {location_raw}" if location_raw else address_locality
        return location_raw
