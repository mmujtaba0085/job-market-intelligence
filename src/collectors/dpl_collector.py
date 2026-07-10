"""
src/collectors/dpl_collector.py
──────────────────────────────────
Collector for DPL's Zoho Recruit-hosted careers board
(https://dplit.zohorecruit.com/jobs/Careers - "Dplit" is just the Zoho
subdomain slug; the company's real name, confirmed via the page's own
`org_info.company_name` and logo asset, is "DPL").

No separate API call needed - both pages are plain `requests`-able HTML
with job data embedded inline, but in two different encodings:

  - Listing page: a `<input type="hidden" id="jobs" value="...">` whose
    value is ordinary HTML-entity-encoded JSON (BeautifulSoup decodes the
    entities automatically via the normal `.get("value")` attribute
    access) - a lightweight array of job stubs (id/City/Country/Publish/
    Job_Type/Posting_Title), used only to get the list of published job
    ids to visit.

  - Detail page (`/jobs/Careers/<id>`): a `var jobs = JSON.parse('...')`
    script variable - NOT ordinary JSON, it's a JS single-quoted string
    literal (JSON.parse's argument) with its own escaping layered on top
    of the JSON escaping already inside it (confirmed by inspection: some
    substrings are escaped twice, e.g. a tag's "/" shows up as "\\/" even
    after one full unescape pass). _decode_jazzhr_blob... no wait, this
    isn't JazzHR - see _decode_detail_jobs() for the exact two-pass fix:
    (1) Python's `unicode_escape` codec resolves `\\xHH` hex escapes and
    collapses doubled backslashes; (2) any backslash STILL not followed by
    a valid JSON escape character afterward is a stray no-op JS escape
    (e.g. literal "\\-" before an ordinary hyphen) and is stripped before
    handing the result to `json.loads()`; (3) after parsing, the
    `Job_Description` field specifically can still carry one extra
    unresolved "\\/" layer (deeper original nesting than the rest of the
    object) - a final `.replace("\\/", "/")` on just that field cleans it,
    confirmed empirically against real fetched pages rather than assumed.

Only `Publish: true` listing entries are visited (one has `Publish: false`
and a junk city value "Islamabad Gpo" - correctly excluded by this filter).
All published postings are confirmed single-location: Islamabad, Pakistan.
"""

from __future__ import annotations

import json
import logging
import re

import requests
from bs4 import BeautifulSoup

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_BASE_URL = "https://dplit.zohorecruit.com"
_LISTING_URL = f"{_BASE_URL}/jobs/Careers"
_TIMEOUT = 15
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

_DETAIL_BLOB_RE = re.compile(r"var jobs = JSON\.parse\('(.*?)'\);")
# A backslash NOT followed by a valid JSON escape-starter character is a
# stray no-op JS escape (e.g. "\-") that unicode_escape leaves untouched
# and json.loads would otherwise reject outright.
_STRAY_BACKSLASH_RE = re.compile(r'\\(?!["\\/bfnrtu])')


class DPLCollector(BaseCollector):
    source_id = "dpl"

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> str:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    # ── Listing page ─────────────────────────────────────────────────────────

    def _parse_listing(self, listing_html: str) -> list[str]:
        """Returns the list of published job ids to visit."""
        soup = BeautifulSoup(listing_html, "html.parser")
        el = soup.select_one("input#jobs")
        if not el or not el.get("value"):
            return []

        try:
            stubs = json.loads(el["value"])
        except json.JSONDecodeError as exc:
            logger.warning("[dpl] Failed to parse listing jobs JSON: %s", exc)
            return []

        return [s["id"] for s in stubs if s.get("Publish") and s.get("id")]

    # ── Detail page ──────────────────────────────────────────────────────────

    def _decode_detail_job(self, detail_html: str) -> dict | None:
        m = _DETAIL_BLOB_RE.search(detail_html)
        if not m:
            return None

        raw = m.group(1)
        try:
            decoded = raw.encode().decode("unicode_escape")
            fixed = _STRAY_BACKSLASH_RE.sub("", decoded)
            data = json.loads(fixed)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("[dpl] Failed to decode embedded jobs blob: %s", exc)
            return None

        jobs = data if isinstance(data, list) else [data]
        if not jobs:
            return None

        job = jobs[0]
        if "Job_Description" in job and job["Job_Description"]:
            job["Job_Description"] = job["Job_Description"].replace("\\/", "/")
        return job

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, job_id: str) -> JobRaw | None:
        url = f"{_LISTING_URL}/{job_id}"
        try:
            detail_html = self._fetch(url)
        except requests.RequestException as exc:
            logger.warning("[dpl] Failed to fetch %s: %s", url, exc)
            return None

        job = self._decode_detail_job(detail_html)
        if not job:
            return None

        title = (job.get("Posting_Title") or job.get("Job_Opening_Name") or "").strip()
        description = (job.get("Job_Description") or "").strip()
        if not title or not description:
            return None

        city = (job.get("City") or "").strip()
        country_raw = (job.get("Country") or "").strip()
        location = f"{city}, {country_raw}" if city and country_raw else (city or country_raw)
        country = country_raw or infer_country(location)

        return JobRaw(
            source_id=self.source_id,
            source_name="DPL",
            url=url,
            fetched_at=self._now(),
            raw_json={"id": job.get("id"), "job_type": job.get("Job_Type") or ""},
            parsed_fields={
                "title": title,
                "company": "DPL",
                "location": location,
                "country": country,
                "remote_type": "on-site" if location else "unknown",
                "posted_date": job.get("Date_Opened") or "",
                "description": description,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        self._wait()
        listing_html = self._fetch(_LISTING_URL)
        job_ids = self._parse_listing(listing_html)

        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        for job_id in job_ids:
            self._wait()
            built = self._build_job(job_id)
            if not built:
                continue

            if keywords and not any(kw in built.parsed_fields["title"].lower() for kw in keywords):
                continue

            results.append(built)
            if max_jobs is not None and len(results) >= max_jobs:
                break

        logger.info("[dpl] Collected %d jobs from %d published listing entries", len(results), len(job_ids))
        return results
