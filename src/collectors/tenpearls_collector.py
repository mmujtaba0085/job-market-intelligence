"""
src/collectors/tenpearls_collector.py
──────────────────────────────────────
Collector for 10Pearls' JazzHR-hosted careers board
(https://10pearls.applytojob.com/apply/jobs).

Single-page listing (no pagination — confirmed by inspection; every current
posting sits in one HTML table, duplicated in a hidden mobile-layout block
reusing the same row ids, which is why the table is scoped to
`table#jobs_table` below). Two-step fetch per run:
  1. Listing page → title, detail URL, and the row id, which embeds a
     YYYYMMDDHHMMSS creation timestamp (id="row_job_20260629073922_...").
     Used as the posted_date fallback for every job.
  2. Each detail page → most embed a JobPosting JSON-LD block with
     description/datePosted/validThrough/employmentType/jobLocation. A
     minority (older "evergreen" postings, some dated back to 2022) have no
     JSON-LD at all and fall back to the visible job_title/job_meta/
     job_description markup. Whenever JSON-LD is present, its datePosted
     matches the row-id timestamp exactly, confirming the fallback is safe
     to use for the postings that lack it.

Location cleanup: 10Pearls jams multiple candidate cities into one
comma-separated string with no separator distinguishing "multiple cities"
from "city, region" (e.g. "Karachi, Lahore, Islamabad, Pakistan"), with
inconsistent casing, duplicate entries, and even a missing comma in some
listings ("Karachi, Lahore Islamabad, Pakistan"). _clean_locations() splits
on comma against small known Pakistani city/province tables so it can
recover cases a naive split can't (fused city names with no comma, a
province name mixed in with cities). Clean cities are handed to the
multi-location machinery via parsed_fields["all_locations"], which
src/storage/db.py already turns into job_locations rows + location_count —
no new storage plumbing needed (see src/enrichment/location_resolver.py /
migrations/003_multi_location_support.sql for the existing mechanism).
"""

from __future__ import annotations

import html
import json
import logging
import re

import requests
from bs4 import BeautifulSoup

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_BASE_URL = "https://10pearls.applytojob.com"
_LISTING_URL = f"{_BASE_URL}/apply/jobs"
_TIMEOUT = 15
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

_ROW_ID_RE = re.compile(r"^row_job_(\d{14})_")

# Countries that show up as the trailing token in 10Pearls location strings.
_KNOWN_COUNTRY_TOKENS = {
    "pakistan": "Pakistan",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
}

# Pakistani provinces/territories 10Pearls sometimes lists alongside cities
# in the same comma-separated field (e.g. "Karachi, Lahore, Islamabad,
# Sindh, Punjab, Pakistan"). Kept out of the city list, tracked separately,
# so they don't get stored as if they were cities.
_PK_PROVINCES = {
    "punjab", "sindh", "khyber pakhtunkhwa", "kpk", "balochistan",
    "gilgit-baltistan", "azad kashmir", "islamabad capital territory",
}

# Cities 10Pearls actually posts jobs in. Used both for title-casing and to
# split comma-less runs of fused city names (e.g. "Lahore Islamabad" ->
# "Lahore", "Islamabad" — a missing comma in the source data).
_PK_CITIES = ["Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad"]
_PK_CITY_RUN_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(_PK_CITIES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


class TenPearlsCollector(BaseCollector):
    source_id = "tenpearls"

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
                ts = m.group(1)  # YYYYMMDDHHMMSS
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
        """Find the application/ld+json block whose @type is JobPosting."""
        soup = BeautifulSoup(detail_html, "html.parser")
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                return data
        return None

    def _strip_html_to_text(self, raw_html: str) -> str:
        text = BeautifulSoup(html.unescape(raw_html or ""), "html.parser").get_text("\n")
        text = re.sub(r"[^\S\n]+", " ", text)   # collapse runs of any whitespace (incl. unicode spaces), keep newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ── Location cleanup ─────────────────────────────────────────────────────

    def _split_fused_cities(self, part: str) -> list[str] | None:
        """
        If `part` is entirely 2+ known Pakistani city names run together with
        only whitespace between them (a missing comma in the source data,
        e.g. "Lahore Islamabad"), return the individual city names.
        Otherwise return None — declines to guess on anything ambiguous.
        """
        matches = list(_PK_CITY_RUN_RE.finditer(part))
        if len(matches) < 2:
            return None
        remainder = _PK_CITY_RUN_RE.sub("", part)
        if remainder.strip():
            return None
        return [m.group(1).title() for m in matches]

    def _clean_locations(self, raw: str) -> tuple[list[str], str | None, list[str]]:
        """
        Split a raw 10Pearls location string into (clean city list,
        country_raw, notes-about-what-was-fixed). Handles the comma-blob
        format, missing commas, mixed casing, duplicate cities, and
        province names mixed in with cities.
        """
        notes: list[str] = []
        raw = (raw or "").strip()
        if not raw or raw.lower() == "remote":
            return [], None, notes

        parts = [p.strip() for p in raw.split(",") if p.strip()]

        country_raw = None
        if parts and parts[-1].lower() in _KNOWN_COUNTRY_TOKENS:
            country_raw = _KNOWN_COUNTRY_TOKENS[parts[-1].lower()]
            parts = parts[:-1]

        cities: list[str] = []
        seen_city_lower: set[str] = set()
        case_variants: dict[str, set[str]] = {}
        dup_found = False
        fused_found = False

        for part in parts:
            if part.lower() in _PK_PROVINCES:
                continue  # tracked as a province, not a city — see notes below

            fused = self._split_fused_cities(part)
            if fused:
                fused_found = True
                candidates = fused
            else:
                candidates = [part.title() if part.islower() or part.isupper() else part]

            for c in candidates:
                key = c.lower()
                case_variants.setdefault(key, set()).add(c)
                if key in seen_city_lower:
                    dup_found = True
                    continue
                seen_city_lower.add(key)
                cities.append(c)

        if fused_found:
            notes.append("source had city names fused together with no separating comma; split via known-city lookup")
        if dup_found:
            notes.append("duplicate city entries in source string, de-duplicated")
        if any(len(v) > 1 for v in case_variants.values()):
            notes.append("inconsistent casing across repeated city names in source string")
        if any(p.lower() in _PK_PROVINCES for p in parts):
            notes.append("province name mixed in with city names in source string, dropped from city list")
        if len(cities) > 1:
            notes.append(f"source crammed {len(cities)} cities into one field with no distinguishing separator")

        return cities, country_raw, notes

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, row: dict) -> JobRaw | None:
        try:
            detail_html = self._fetch(row["detail_url"])
        except requests.RequestException as exc:
            logger.warning("[tenpearls] Failed to fetch %s: %s", row["detail_url"], exc)
            return None

        job_ld = self._extract_job_ld(detail_html)

        if job_ld:
            title = job_ld.get("title") or row["listing_title"]
            description = self._strip_html_to_text(job_ld.get("description") or "")
            posted_date = job_ld.get("datePosted") or row["row_posted_date"]
            code = job_ld.get("uniqueJobCode") or row["detail_url"].rstrip("/").rsplit("/", 1)[-1]

            addr = (job_ld.get("jobLocation") or {}).get("address") or {}
            ld_locality = addr.get("addressLocality") or ""
            # JSON-LD addressLocality omits the country; the listing page's
            # flat text includes it, so prefer that as the source of truth.
            location_source = row["listing_location_raw"] or ld_locality
        else:
            soup = BeautifulSoup(detail_html, "html.parser")
            title_el = soup.select_one("h1.job_title")
            desc_el = soup.select_one("div.job_description")

            title = title_el.get_text(strip=True) if title_el else row["listing_title"]
            description = self._strip_html_to_text(str(desc_el)) if desc_el else ""
            posted_date = row["row_posted_date"]
            code = row["detail_url"].rstrip("/").rsplit("/", 1)[-1]
            location_source = row["listing_location_raw"]

        if not description:
            return None

        cities, country_raw, notes = self._clean_locations(location_source)
        if notes:
            logger.debug("[tenpearls] Location cleanup for %r (%r): %s", title, location_source, "; ".join(notes))

        country = country_raw or infer_country(location_source)
        remote_type = "remote" if "remote" in location_source.lower() else ("on-site" if cities else "unknown")
        location = cities[0] if cities else location_source

        return JobRaw(
            source_id=self.source_id,
            source_name="10Pearls",
            url=row["detail_url"],
            fetched_at=self._now(),
            raw_json={"job_code": code, "location_raw": location_source},
            parsed_fields={
                "title": title.strip(),
                "company": "10Pearls",
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

        logger.info("[tenpearls] Collected %d jobs from %d listing rows", len(results), len(rows))
        return results
