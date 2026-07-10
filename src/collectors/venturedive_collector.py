"""
src/collectors/venturedive_collector.py
─────────────────────────────────────────
Collector for VentureDive's JazzHR-hosted careers board
(https://venturedive.applytojob.com/apply/jobs) - the exact same platform
as 10Pearls (see src/collectors/tenpearls_collector.py), confirmed by
inspection: same `table#jobs_table` listing structure, same
`row_job_<YYYYMMDDHHMMSS>_<hash>` row ids (used as a posted_date fallback),
same per-job JSON-LD JobPosting block on most detail pages with an HTML
fallback (`.job_title`/`.job_meta`/`.job_description`) on the rest.

Kept as its own file rather than subclassing TenPearlsCollector - that
collector is already shipped/tested and this file's location-cleanup needs
differ enough (see below) that sharing a class hierarchy would risk the
existing, working collector for limited benefit.

Location cleanup: unlike 10Pearls' comma-crammed cities, VentureDive uses
"/" as its multi-city separator, e.g. "Karachi/ Lahore/ Islamabad"
(confirmed by inspection, inconsistent spacing around the slash). A second,
different quirk: at least one real posting has "Hybrid" as the *entire*
location value - a workplace-type leaking into the location field, not a
city - so any location that reduces to a known non-city token isn't stored
as a city; it still contributes to the country/remote_type read on the raw
string.
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

_BASE_URL = "https://venturedive.applytojob.com"
_LISTING_URL = f"{_BASE_URL}/apply/jobs"
_TIMEOUT = 15
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

_ROW_ID_RE = re.compile(r"^row_job_(\d{14})_")

_KNOWN_COUNTRY_TOKENS = {
    "pakistan": "Pakistan",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
}

# Values seen standing in for the whole location field that are workplace
# signals, not cities - e.g. a real posting where addressLocality is
# literally "Hybrid". Dropped from the city list rather than stored as if
# they were a place name.
_NON_CITY_LOCATION_VALUES = {"hybrid", "remote", "on-site", "onsite", ""}


class VentureDiveCollector(BaseCollector):
    source_id = "venturedive"

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> str:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    # ── Listing page ─────────────────────────────────────────────────────────

    def _parse_listing(self, listing_html: str) -> list[dict]:
        soup = BeautifulSoup(listing_html, "html.parser")
        rows: list[dict] = []

        for tr in soup.select("table#jobs_table tr[id^='row_job_']"):
            anchor = tr.select_one("a.job_title_link")
            if not anchor or not anchor.get("href"):
                continue

            href = anchor["href"].split("?")[0]
            detail_url = href if href.startswith("http") else f"{_BASE_URL}{href}"
            title = anchor.get_text(strip=True)

            cells = tr.find_all("td")
            location_raw = cells[1].get_text(strip=True) if len(cells) > 1 else ""

            m = _ROW_ID_RE.match(tr.get("id", ""))
            row_posted_date = None
            if m:
                ts = m.group(1)
                row_posted_date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"

            rows.append({
                "detail_url": detail_url,
                "listing_title": title,
                "listing_location_raw": location_raw,
                "row_posted_date": row_posted_date,
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
        """Keep real HTML structure - job_detail.html renders raw_description via `| safe`."""
        soup = BeautifulSoup(raw_html or "", "html.parser")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        for p in soup.find_all(["p", "li"]):
            if not p.get_text(strip=True):
                p.decompose()
        return str(soup).strip()

    # ── Location cleanup ─────────────────────────────────────────────────────

    def _clean_locations(self, raw: str) -> tuple[list[str], str | None]:
        """
        Split VentureDive's "/"-separated multi-city locations into a clean
        city list, and pull off a trailing country token when present.
        Locations that are entirely a workplace-type value (e.g. "Hybrid")
        yield no cities - see _NON_CITY_LOCATION_VALUES.
        """
        raw = (raw or "").strip()
        if not raw or raw.lower() in _NON_CITY_LOCATION_VALUES:
            return [], None

        # The country is comma-attached to the LAST "/"-segment, not a
        # segment of its own (e.g. "Karachi/ Lahore/ Islamabad, Pakistan"),
        # confirmed against real listing data - strip it off the raw string
        # before splitting on "/", rather than looking for it as a whole
        # split-off segment (which would never match).
        country_raw = None
        if "," in raw:
            head, _, tail = raw.rpartition(",")
            tail = tail.strip()
            if tail.lower() in _KNOWN_COUNTRY_TOKENS:
                country_raw = _KNOWN_COUNTRY_TOKENS[tail.lower()]
                raw = head.strip()

        parts = [p.strip() for p in raw.split("/") if p.strip()]

        # Rare standalone case: raw was just "Pakistan" with no "/" and no
        # comma (e.g. the no-JSON-LD fallback's job_meta location) - already
        # caught by the trailing-comma check above only when a comma is
        # present, so also check the whole remaining string here.
        if not country_raw and len(parts) == 1 and parts[0].lower() in _KNOWN_COUNTRY_TOKENS:
            country_raw = _KNOWN_COUNTRY_TOKENS[parts[0].lower()]
            parts = []

        cities: list[str] = []
        seen_lower: set[str] = set()
        for part in parts:
            if part.lower() in _NON_CITY_LOCATION_VALUES:
                continue
            key = part.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            cities.append(part.title() if part.islower() or part.isupper() else part)

        return cities, country_raw

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, row: dict) -> JobRaw | None:
        try:
            detail_html = self._fetch(row["detail_url"])
        except requests.RequestException as exc:
            logger.warning("[venturedive] Failed to fetch %s: %s", row["detail_url"], exc)
            return None

        job_ld = self._extract_job_ld(detail_html)

        if job_ld:
            title = job_ld.get("title") or row["listing_title"]
            description = self._clean_description_html(job_ld.get("description") or "")
            posted_date = job_ld.get("datePosted") or row["row_posted_date"]

            addr = (job_ld.get("jobLocation") or {}).get("address") or {}
            ld_locality = addr.get("addressLocality") or ""
            location_source = row["listing_location_raw"] or ld_locality
        else:
            soup = BeautifulSoup(detail_html, "html.parser")
            title_el = soup.select_one("h1.job_title")
            desc_el = soup.select_one("div.job_description")

            title = title_el.get_text(strip=True) if title_el else row["listing_title"]
            description = self._clean_description_html(desc_el.decode_contents()) if desc_el else ""
            posted_date = row["row_posted_date"]
            location_source = row["listing_location_raw"]

        if not description:
            return None

        cities, country_raw = self._clean_locations(location_source)
        country = country_raw or infer_country(location_source)

        loc_lower = location_source.lower()
        if "hybrid" in loc_lower:
            remote_type = "hybrid"
        elif "remote" in loc_lower:
            remote_type = "remote"
        elif cities:
            remote_type = "on-site"
        else:
            remote_type = "unknown"

        location = cities[0] if cities else location_source

        return JobRaw(
            source_id=self.source_id,
            source_name="VentureDive",
            url=row["detail_url"],
            fetched_at=self._now(),
            raw_json={"location_raw": location_source},
            parsed_fields={
                "title": title.strip(),
                "company": "VentureDive",
                "location": location,
                "all_locations": cities if len(cities) > 1 else None,
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

        logger.info("[venturedive] Collected %d jobs from %d listing rows", len(results), len(rows))
        return results
