"""
src/collectors/usajobs_collector.py
───────────────────────────────────
Collector for USAJOBS Search API (requires API key + user agent).

Endpoint: https://data.usajobs.gov/api/Search
Method: GET
Headers:
  - Host: data.usajobs.gov
  - User-Agent: {email from USAJOBS_USER_AGENT env var}
  - Authorization-Key: {USAJOBS_API_KEY env var}
  - Accept: application/json

Query params:
  - Keyword: search term from market
  - Page: pagination (1-based)
  - ResultsPerPage: max 500, default 25

Response fields used:
  - SearchResult.SearchResultItems[].MatchedObjectDescriptor
    - PositionTitle
    - OrganizationName
    - PositionLocationDisplay (array)
    - PublicationStartDate
    - ApplyURI (array, first element)
    - UserArea.Details.JobSummary
    - PositionRemuneration (salary data)

Requires USAJOBS_API_KEY and USAJOBS_USER_AGENT in .env
"""

from __future__ import annotations

import hashlib
import logging
import os

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://data.usajobs.gov/api/Search"
_TIMEOUT = 30


class USAJobsCollector(BaseCollector):
    source_id = "usajobs"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = os.getenv("USAJOBS_API_KEY", "")
        self.user_agent = os.getenv("USAJOBS_USER_AGENT", "")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from USAJOBS API."""
        if not self.api_key or not self.user_agent:
            logger.warning("[usajobs] Missing USAJOBS_API_KEY or USAJOBS_USER_AGENT, skipping")
            return []

        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", ["technology"])

        page_size = 50
        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": self.user_agent,
            "Authorization-Key": self.api_key,
            "Accept": "application/json",
        }

        for keyword in keywords:
            if len(results) >= max_jobs:
                break

            page = 1
            while len(results) < max_jobs:
                self._wait()

                try:
                    params = {
                        "Keyword": keyword,
                        "ResultsPerPage": page_size,
                        "Page": str(page),
                    }

                    logger.debug("[usajobs] Fetching keyword='%s' page=%d", keyword, page)
                    resp = requests.get(_BASE_URL, headers=headers, params=params, timeout=_TIMEOUT)

                    if resp.status_code == 429:
                        logger.warning("[usajobs] Rate limited (429), stopping")
                        break

                    if resp.status_code != 200:
                        logger.warning("[usajobs] HTTP %d for keyword '%s' page %d", resp.status_code, keyword, page)
                        break

                    data = resp.json()
                    items = data.get("SearchResult", {}).get("SearchResultItems", [])

                    if not items:
                        logger.debug("[usajobs] No more results for keyword '%s' on page %d", keyword, page)
                        break

                    for item in items:
                        if len(results) >= max_jobs:
                            break

                        job_data = item.get("MatchedObjectDescriptor", {})

                        # Extract locations
                        locations = job_data.get("PositionLocationDisplay", [])
                        location = locations[0] if locations else ""

                        # Extract apply URL
                        apply_uris = job_data.get("ApplyURI", [])
                        url = apply_uris[0] if apply_uris else ""

                        # Fallback URL if empty
                        if not url:
                            hash_input = f"{job_data.get('PositionTitle')}|{job_data.get('OrganizationName')}|{job_data.get('PositionID')}"
                            url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                            url = f"usajobs://{url_hash}"

                        # Extract description
                        user_area = job_data.get("UserArea", {})
                        details = user_area.get("Details", {})
                        description = details.get("JobSummary") or ""

                        # Parse salary
                        remuneration = job_data.get("PositionRemuneration", [])
                        salary_min = None
                        salary_max = None

                        if remuneration and isinstance(remuneration, list):
                            try:
                                first_rem = remuneration[0]
                                salary_min = float(first_rem.get("MinimumRange", 0)) or None
                                salary_max = float(first_rem.get("MaximumRange", 0)) or None
                            except Exception:
                                pass

                        results.append(
                            JobRaw(
                                source_id=self.source_id,
                                source_name="USAJobs",
                                url=url,
                                fetched_at=self._now(),
                                raw_json=job_data,
                                parsed_fields={
                                    "title": job_data.get("PositionTitle") or "",
                                    "company": job_data.get("OrganizationName") or "",
                                    "location": location,
                                    "country": "United States",
                                    "remote_type": "On-site",  # Most gov jobs are on-site
                                    "posted_date": self._parse_date(job_data.get("PublicationStartDate")),
                                    "description": description,
                                    "salary_min": salary_min,
                                    "salary_max": salary_max,
                                    "currency": "USD",
                                },
                            )
                        )

                    logger.debug("[usajobs] Keyword '%s' page %d: got %d items", keyword, page, len(items))

                    if len(items) < page_size:
                        break  # no more pages
                    page += 1

                except requests.Timeout:
                    logger.warning("[usajobs] Timeout for keyword '%s' page %d", keyword, page)
                    break
                except Exception as e:
                    logger.error("[usajobs] Error for keyword '%s' page %d: %s", keyword, page, e)
                    break

        return results[:max_jobs]

    def _parse_date(self, date_str: str | None) -> str:
        """Parse date to YYYY-MM-DD."""
        if not date_str:
            return ""
        
        try:
            # Handle format like "2024-01-15T00:00:00.0000"
            return date_str.split("T")[0]
        except Exception:
            return ""
