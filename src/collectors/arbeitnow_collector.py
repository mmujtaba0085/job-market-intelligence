"""
src/collectors/arbeitnow_collector.py
─────────────────────────────────────
Collector for Arbeitnow Job Board API (no auth required).

Endpoint: https://www.arbeitnow.com/api/job-board-api
Method: GET
Query params: page (optional, starts at 1)
Response fields used:
  - title (job title)
  - company_name
  - location
  - remote (boolean)
  - url (apply link)
  - description
  - tags (array, optional)
  - created_at (ISO timestamp)

Supports pagination via "page" param. Free API - no rate limits documented.
"""

from __future__ import annotations

import hashlib
import logging

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"
_TIMEOUT = 30


class ArbeitnowCollector(BaseCollector):
    source_id = "arbeitnow"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from Arbeitnow API."""
        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        page = 1
        max_pages = 20  # Raised from 5 to reach max_jobs_per_source=500

        keywords = market.get("keywords", [])

        while len(results) < max_jobs and page <= max_pages:
            self._wait()
            
            try:
                params = {"page": str(page)}
                
                logger.debug("[arbeitnow] Fetching page %d", page)
                resp = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT)
                
                if resp.status_code == 429:
                    logger.warning("[arbeitnow] Rate limited (429), stopping collection")
                    break
                
                if resp.status_code != 200:
                    logger.warning("[arbeitnow] HTTP %d on page %d, skipping", resp.status_code, page)
                    break

                data = resp.json()
                jobs_data = data.get("data", [])
                
                if not jobs_data:
                    logger.debug("[arbeitnow] No more jobs on page %d, stopping", page)
                    break

                for item in jobs_data:
                    if len(results) >= max_jobs:
                        break
                    
                    # Filter by keywords (client-side)
                    if keywords and not self._matches_keywords(item, keywords):
                        continue
                    
                    # Extract location and infer country
                    location = item.get("location") or ""
                    country = self._infer_country(location)
                    
                    # Determine remote type
                    is_remote = item.get("remote", False)
                    remote_type = "Remote" if is_remote else "On-site"
                    
                    # URL with fallback
                    url = item.get("url") or ""
                    if not url:
                        hash_input = f"{item.get('title')}|{item.get('company_name')}|{location}"
                        url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                        url = f"arbeitnow://{url_hash}"
                    
                    results.append(
                        JobRaw(
                            source_id=self.source_id,
                            source_name="Arbeitnow",
                            url=url,
                            fetched_at=self._now(),
                            raw_json=item,
                            parsed_fields={
                                "title": item.get("title") or "",
                                "company": item.get("company_name") or "",
                                "location": location,
                                "country": country,
                                "remote_type": remote_type,
                                "posted_date": self._parse_date(item.get("created_at")),
                                "description": item.get("description") or "",
                                "tags": item.get("tags", []) if isinstance(item.get("tags"), list) else [],
                            },
                        )
                    )

                logger.debug("[arbeitnow] Page %d: collected %d matching jobs", page, len([j for j in jobs_data if not keywords or self._matches_keywords(j, keywords)]))
                page += 1

            except requests.Timeout:
                logger.warning("[arbeitnow] Timeout on page %d", page)
                break
            except Exception as e:
                logger.error("[arbeitnow] Error on page %d: %s", page, e)
                break

        return results[:max_jobs]

    def _matches_keywords(self, item: dict, keywords: list[str]) -> bool:
        """Check if job matches any market keyword."""
        title = (item.get("title") or "").lower()
        desc = (item.get("description") or "").lower()
        tags = " ".join(item.get("tags") or []).lower()
        
        search_text = f"{title} {desc} {tags}"
        
        return any(kw.lower() in search_text for kw in keywords)

    def _infer_country(self, location: str) -> str:
        """Infer country from location string."""
        if not location:
            return "Global"
        
        location_lower = location.lower()
        
        # Simple country detection
        country_keywords = {
            "germany": "Germany",
            "berlin": "Germany",
            "munich": "Germany",
            "usa": "United States",
            "united states": "United States",
            "new york": "United States",
            "san francisco": "United States",
            "uk": "United Kingdom",
            "united kingdom": "United Kingdom",
            "london": "United Kingdom",
            "remote": "Global",
            "worldwide": "Global",
        }
        
        for keyword, country in country_keywords.items():
            if keyword in location_lower:
                return country
        
        return "Unknown"

    def _parse_date(self, date_str: str | int | None) -> str:
        """Parse Unix timestamp or ISO timestamp to YYYY-MM-DD."""
        if not date_str:
            return ""
        
        try:
            # Handle Unix timestamp (integer)
            if isinstance(date_str, int):
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(date_str, tz=timezone.utc)
                return dt.strftime("%Y-%m-%d")
            
            # Handle ISO format string like "2024-01-15T10:30:00Z"
            if isinstance(date_str, str):
                return date_str.split("T")[0]
            
            return ""
        except Exception as exc:
            logger.warning("[arbeitnow] Failed to parse date '%s': %s", date_str, exc)
            return ""
