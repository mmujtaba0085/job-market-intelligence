"""
src/collectors/carbonteq_collector.py
───────────────────────────────────────
Collector for Carbonteq's careers page (https://www.carbonteq.com/careers).

Despite looking like a bespoke marketing site, the "Open Positions" section
is populated client-side from JazzHR's legacy "TheResumator" JSON API -
same ATS family as 10Pearls/VentureDive, but reached as a clean JSON API
here instead of HTML scraping:

  GET https://api.resumatorapi.com/v1/jobs/status/open?apikey=<key>

The API key is exposed in plain JS on the public careers page itself (not
a secret - it's shipped to every visitor's browser) as
`fetch("https://api.resumatorapi.com/v1/jobs/status/open?apikey=...")`.
Extracted fresh on every run via _fetch_api_key() rather than hardcoded,
since there's no guarantee it stays the same forever.

Response shape: a JSON array of open jobs - EXCEPT when there's exactly
one open job, in which case the API returns a bare object instead of a
one-element array (confirmed by inspection). _fetch_jobs() normalizes both
shapes to a list.

Fields: id, title, country_id (already a clean country name, e.g.
"Pakistan" - despite the "_id" suffix, it's not a numeric id), city, state,
department, description (real HTML), original_open_date, type (employment
type), board_code (-> apply URL "https://carbonteq.applytojob.com/apply/
<board_code>"), minimum_salary/maximum_salary (both "0" when unset -
treated as no salary data, not a real $0 figure).
"""

from __future__ import annotations

import logging
import re

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_CAREERS_PAGE_URL = "https://www.carbonteq.com/careers"
_API_URL_TMPL = "https://api.resumatorapi.com/v1/jobs/status/open?apikey={key}"
_APPLY_URL_TMPL = "https://carbonteq.applytojob.com/apply/{board_code}"
_TIMEOUT = 15
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

_API_KEY_RE = re.compile(r"resumatorapi\.com/v1/jobs/status/open\?apikey=([A-Za-z0-9]+)")


class CarbonteqCollector(BaseCollector):
    source_id = "carbonteq"

    # ── API key discovery ────────────────────────────────────────────────────

    def _fetch_api_key(self) -> str | None:
        try:
            resp = requests.get(_CAREERS_PAGE_URL, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[carbonteq] Failed to fetch careers page for API key: %s", exc)
            return None

        m = _API_KEY_RE.search(resp.text)
        if not m:
            logger.warning("[carbonteq] Could not find resumatorapi.com API key on careers page")
            return None
        return m.group(1)

    # ── Job fetch ────────────────────────────────────────────────────────────

    def _fetch_jobs(self, api_key: str) -> list[dict]:
        try:
            resp = requests.get(_API_URL_TMPL.format(key=api_key), headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("[carbonteq] Jobs API request failed: %s", exc)
            return []
        except ValueError as exc:
            logger.warning("[carbonteq] Invalid JSON from jobs API: %s", exc)
            return []

        # Bare object when exactly one job is open - normalize to a list.
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
        return []

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _to_float(self, value) -> float | None:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    def _build_job(self, job: dict) -> JobRaw | None:
        title = (job.get("title") or "").strip()
        description = (job.get("description") or "").strip()
        board_code = job.get("board_code")
        if not title or not description or not board_code:
            return None

        city = (job.get("city") or "").strip()
        country_raw = (job.get("country_id") or "").strip()  # despite the name, a country name string
        location = f"{city}, {country_raw}" if city and country_raw else (city or country_raw)
        country = country_raw or infer_country(location)

        return JobRaw(
            source_id=self.source_id,
            source_name="Carbonteq",
            url=_APPLY_URL_TMPL.format(board_code=board_code),
            fetched_at=self._now(),
            raw_json={"id": job.get("id"), "department": job.get("department") or ""},
            parsed_fields={
                "title": title,
                "company": "Carbonteq",
                "location": location,
                "country": country,
                "remote_type": "on-site" if location else "unknown",
                "posted_date": job.get("original_open_date") or "",
                "description": description,
                "salary_min": self._to_float(job.get("minimum_salary")),
                "salary_max": self._to_float(job.get("maximum_salary")),
                "currency": "USD" if self._to_float(job.get("minimum_salary")) else None,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        self._wait()
        api_key = self._fetch_api_key()
        if not api_key:
            return []

        self._wait()
        jobs = self._fetch_jobs(api_key)

        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        for job in jobs:
            if (job.get("status") or "").lower() != "open":
                continue

            title = job.get("title") or ""
            if keywords and not any(kw in title.lower() for kw in keywords):
                continue

            built = self._build_job(job)
            if built:
                results.append(built)

            if max_jobs is not None and len(results) >= max_jobs:
                break

        logger.info("[carbonteq] Collected %d open jobs", len(results))
        return results
