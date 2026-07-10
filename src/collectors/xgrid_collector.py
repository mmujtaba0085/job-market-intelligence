"""
src/collectors/xgrid_collector.py
────────────────────────────────────
Collector for Xgrid's Freshteam-hosted careers board
(https://xgrid.freshteam.com/jobs).

Unlike some Freshteam boards, this one is plain server-rendered HTML - no
JS-rendering workaround needed. The listing page's own job links carry
useful data attributes directly (`data-portal-location`,
`data-portal-remote-location`, `data-portal-job-type`) - confirmed clean
and preferred over the detail page's JSON-LD for location specifically,
see below. ~7 jobs, single page, no pagination.

Detail pages additionally embed a `<script type="application/ld+json">`
JobPosting block with `datePosted`, `employmentType`, `remote` (a string
"true"/"false"), and the full HTML `description` - used for all of those,
but NOT for location: confirmed by inspection that the JSON-LD
`jobLocation.address` has region/locality fields swapped/misused (e.g. a
real San Jose posting has `addressRegion: "Islamabad"` - actually the
*city* - `addressLocality: ""` empty), so it can't be trusted for
location. The listing page's own `data-portal-location` attribute
("Islamabad, Pakistan") is clean and used instead.

Both `title` and `description` inside that JSON-LD are themselves
HTML-entity-escaped (confirmed by inspection - e.g. a real title's JSON
string value is literally "Marketing &amp; Customer Growth Specialist",
and description bodies contain "&lt;h2 dir=&quot;ltr&quot;...&gt;" instead
of real "<h2 ...>" tags), on top of - not instead of - JSON's own string
escaping, which `json.loads()` already resolved. One `html.unescape()`
pass on each fixes this; any "&amp;" that was itself originally a
double-escaped "&" is left as ordinary, valid, well-formed HTML markup
after that single pass (browsers decode it further on render), so a
second pass isn't needed.
"""

from __future__ import annotations

import html
import json
import logging

import requests
from bs4 import BeautifulSoup

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_BASE_URL = "https://xgrid.freshteam.com"
_LISTING_URL = f"{_BASE_URL}/jobs"
_TIMEOUT = 15
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"


class XgridCollector(BaseCollector):
    source_id = "xgrid"

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> str:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    # ── Listing page ─────────────────────────────────────────────────────────

    def _parse_listing(self, listing_html: str) -> list[dict]:
        soup = BeautifulSoup(listing_html, "html.parser")
        rows: list[dict] = []

        for a in soup.select("a.heading"):
            href = a.get("href")
            if not href:
                continue
            detail_url = href if href.startswith("http") else f"{_BASE_URL}{href}"

            rows.append({
                "detail_url": detail_url,
                "listing_title": a.get_text(strip=True),
                "location": (a.get("data-portal-location") or "").strip(),
                "is_remote": (a.get("data-portal-remote-location") or "").strip().lower() == "true",
            })

        return rows

    # ── Detail page ──────────────────────────────────────────────────────────

    def _extract_job_ld(self, detail_html: str) -> dict | None:
        soup = BeautifulSoup(detail_html, "html.parser")
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                return data
        return None

    def _clean_description_html(self, raw_html: str) -> str:
        soup = BeautifulSoup(raw_html or "", "html.parser")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        for p in soup.find_all(["p", "li"]):
            if not p.get_text(strip=True):
                p.decompose()
        return str(soup).strip()

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, row: dict) -> JobRaw | None:
        try:
            detail_html = self._fetch(row["detail_url"])
        except requests.RequestException as exc:
            logger.warning("[xgrid] Failed to fetch %s: %s", row["detail_url"], exc)
            return None

        job_ld = self._extract_job_ld(detail_html) or {}
        title = html.unescape(job_ld.get("title") or "") or row["listing_title"]
        description = self._clean_description_html(html.unescape(job_ld.get("description") or ""))
        if not title or not description:
            return None

        posted_date = job_ld.get("datePosted") or ""
        location = row["location"]
        country = infer_country(location)

        is_remote = row["is_remote"] or str(job_ld.get("remote", "")).strip().lower() == "true"
        remote_type = "remote" if is_remote else ("on-site" if location else "unknown")

        return JobRaw(
            source_id=self.source_id,
            source_name="Xgrid",
            url=row["detail_url"],
            fetched_at=self._now(),
            raw_json={"employmentType": job_ld.get("employmentType") or ""},
            parsed_fields={
                "title": title.strip(),
                "company": "Xgrid",
                "location": location,
                "country": country,
                "remote_type": remote_type,
                "posted_date": posted_date,
                "description": description,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        self._wait()
        listing_html = self._fetch(_LISTING_URL)
        rows = self._parse_listing(listing_html)

        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        for row in rows:
            if keywords and not any(kw in row["listing_title"].lower() for kw in keywords):
                continue

            self._wait()
            job = self._build_job(row)
            if job:
                results.append(job)

            if max_jobs is not None and len(results) >= max_jobs:
                break

        logger.info("[xgrid] Collected %d jobs from %d listing rows", len(results), len(rows))
        return results
