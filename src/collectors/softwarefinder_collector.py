"""
src/collectors/softwarefinder_collector.py
─────────────────────────────────────────────
Collector for Software Finder's Teamtailor-hosted careers board
(https://softwarefinder.na.teamtailor.com/jobs).

The listing page's own "Show more" button only ever server-renders a
subset of jobs, but Teamtailor exposes the FULL current job list via two
static feeds that need no JS/pagination handling:

  - https://softwarefinder.na.teamtailor.com/jobs.json  (JSON Feed format)
    Each item already embeds a full schema.org `_jobposting` block
    (title, description as clean HTML, datePosted, jobLocation.address
    with addressLocality/addressCountry) - confirmed by inspection this
    alone covers everything except one field: remote/on-site status.
  - https://softwarefinder.na.teamtailor.com/jobs.rss  adds exactly that
    one missing field (`<remoteStatus>none|fully</remoteStatus>`) per
    item, joined to the JSON feed by id/guid.

Both feeds return the complete current job list in one request each (~36
jobs at time of writing) - no pagination, no "Show more" JS to reverse-
engineer.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_JSON_FEED_URL = "https://softwarefinder.na.teamtailor.com/jobs.json"
_RSS_FEED_URL = "https://softwarefinder.na.teamtailor.com/jobs.rss"
_TIMEOUT = 20
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

_TT_NS = {"tt": "https://www.teamtailor.com/rss"}


class SoftwareFinderCollector(BaseCollector):
    source_id = "softwarefinder"

    # ── Fetch ────────────────────────────────────────────────────────────────

    def _fetch_json_items(self) -> list[dict]:
        try:
            resp = requests.get(_JSON_FEED_URL, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json().get("items") or []
        except (requests.RequestException, ValueError) as exc:
            logger.warning("[softwarefinder] Failed to fetch jobs.json: %s", exc)
            return []

    def _fetch_remote_status_by_guid(self) -> dict[str, str]:
        """
        Returns {guid: remoteStatus} ("none"/"fully"/...) parsed from the
        RSS feed - the only field the JSON feed doesn't already carry.
        A failure here degrades gracefully (empty dict -> every job falls
        back to "unknown" remote_type) rather than failing the whole run.
        """
        try:
            resp = requests.get(_RSS_FEED_URL, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning("[softwarefinder] Failed to fetch/parse jobs.rss: %s", exc)
            return {}

        result: dict[str, str] = {}
        for item in root.iter("item"):
            guid_el = item.find("guid")
            status_el = item.find("remoteStatus")
            if guid_el is not None and guid_el.text and status_el is not None and status_el.text:
                result[guid_el.text.strip()] = status_el.text.strip().lower()
        return result

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _remote_type(self, remote_status: str | None) -> str:
        if remote_status == "fully":
            return "remote"
        if remote_status == "none":
            return "on-site"
        return "unknown"

    def _build_job(self, item: dict, remote_status: str | None) -> JobRaw | None:
        title = (item.get("title") or "").strip()
        url = item.get("url") or ""
        posting = item.get("_jobposting") or {}
        description = (posting.get("description") or "").strip()
        if not title or not url or not description:
            return None

        job_locations = posting.get("jobLocation") or []
        address = (job_locations[0].get("address") or {}) if job_locations else {}
        city = (address.get("addressLocality") or "").strip()
        country_code = (address.get("addressCountry") or "").strip()

        country = "Pakistan" if country_code == "PK" else (country_code or None)
        location = f"{city}, {country}" if city and country else (city or country or "")
        if not country:
            country = infer_country(location)

        return JobRaw(
            source_id=self.source_id,
            source_name="Software Finder",
            url=url,
            fetched_at=self._now(),
            raw_json={"id": item.get("id")},
            parsed_fields={
                "title": title,
                "company": "Software Finder",
                "location": location,
                "country": country,
                "remote_type": self._remote_type(remote_status),
                "posted_date": item.get("date_published") or "",
                "description": description,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        self._wait()
        items = self._fetch_json_items()

        self._wait()
        remote_status_by_guid = self._fetch_remote_status_by_guid()

        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        for item in items:
            title = item.get("title") or ""
            if keywords and not any(kw in title.lower() for kw in keywords):
                continue

            remote_status = remote_status_by_guid.get(item.get("id") or "")
            built = self._build_job(item, remote_status)
            if built:
                results.append(built)

            if max_jobs is not None and len(results) >= max_jobs:
                break

        logger.info("[softwarefinder] Collected %d jobs from %d feed entries", len(results), len(items))
        return results
