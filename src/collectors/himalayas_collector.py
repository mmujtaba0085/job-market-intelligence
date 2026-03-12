"""
src/collectors/himalayas_collector.py
─────────────────────────────────────
Collector for Himalayas JSON API (no auth required).

Endpoint: GET https://himalayas.app/jobs/api
Query params:
  - limit: max 20 per request
  - offset: pagination offset

Response fields used:
  - title (job title)
  - companyName
  - link or jobUrl (apply URL)
  - description or html (job description)
  - pubDate/postedAt/updatedAt (posted date)
  - location/country restrictions
  - totalCount (for pagination)

All jobs from Himalayas are remote positions.
"""

from __future__ import annotations

import hashlib
import logging

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://himalayas.app/jobs/api"
_TIMEOUT = 15  # Shorter timeout for responsiveness
_PAGE_LIMIT = 20  # API max per request


class HimalayasCollector(BaseCollector):
    source_id = "himalayas"

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from Himalayas JSON API."""
        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])
        
        offset = 0
        total_count = None
        error_count = 0
        max_errors = 3  # Circuit breaker

        while len(results) < max_jobs:
            self._wait()
            
            try:
                params = {
                    "limit": _PAGE_LIMIT,
                    "offset": offset,
                }
                
                logger.debug("[himalayas] Fetching offset=%d", offset)
                resp = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT)
                
                if resp.status_code == 429:
                    logger.warning("[himalayas] Rate limited (429), stopping")
                    break
                
                if resp.status_code >= 500:
                    error_count += 1
                    if error_count >= max_errors:
                        logger.warning("[himalayas] Too many 5xx errors, stopping")
                        break
                    continue
                
                if resp.status_code != 200:
                    logger.warning("[himalayas] HTTP %d at offset %d", resp.status_code, offset)
                    break

                data = resp.json()
                
                # Get total count for pagination
                if total_count is None:
                    total_count = data.get("totalCount", 0)
                    logger.debug("[himalayas] Total jobs available: %d", total_count)
                
                jobs_data = data.get("jobs", [])
                
                if not jobs_data:
                    logger.debug("[himalayas] No more jobs at offset %d", offset)
                    break

                for item in jobs_data:
                    if len(results) >= max_jobs:
                        break
                    
                    # Filter by keywords client-side if provided
                    if keywords and not self._matches_keywords(item, keywords):
                        continue
                    
                    # Extract URL
                    url = item.get("link") or item.get("jobUrl") or ""
                    if not url:
                        hash_input = f"{item.get('title')}|{item.get('companyName')}|{item.get('pubDate')}"
                        url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                        url = f"himalayas://{url_hash}"
                    
                    # Extract location
                    location = item.get("location") or ""
                    country = self._extract_country(item)
                    
                    # Extract description
                    description = item.get("description") or item.get("html") or ""
                    
                    # Extract date
                    posted_date = self._parse_date(
                        item.get("pubDate") or item.get("postedAt") or item.get("updatedAt")
                    )
                    
                    results.append(
                        JobRaw(
                            source_id=self.source_id,
                            source_name="Himalayas",
                            url=url,
                            fetched_at=self._now(),
                            raw_json=item,
                            parsed_fields={
                                "title": item.get("title") or "",
                                "company": item.get("companyName") or "",
                                "location": location,
                                "country": country,
                                "remote_type": "Remote",  # Himalayas = remote jobs only
                                "posted_date": posted_date,
                                "description": description,
                            },
                        )
                    )

                logger.debug("[himalayas] Offset %d: collected %d matching jobs", offset, len([j for j in jobs_data if not keywords or self._matches_keywords(j, keywords)]))
                
                # Check if we've reached the end
                offset += _PAGE_LIMIT
                if total_count and offset >= total_count:
                    logger.debug("[himalayas] Reached end of results")
                    break
                
                # Reset error counter on success
                error_count = 0

            except requests.Timeout:
                error_count += 1
                logger.warning("[himalayas] Timeout at offset %d (%d/%d)", offset, error_count, max_errors)
                if error_count >= max_errors:
                    break
            except Exception as e:
                error_count += 1
                logger.error("[himalayas] Error at offset %d: %s (%d/%d)", offset, e, error_count, max_errors)
                if error_count >= max_errors:
                    break

        return results[:max_jobs]

    def _matches_keywords(self, item: dict, keywords: list[str]) -> bool:
        """Check if job matches any keyword."""
        title = (item.get("title") or "").lower()
        desc = (item.get("description") or item.get("html") or "").lower()
        company = (item.get("companyName") or "").lower()
        
        search_text = f"{title} {desc} {company}"
        
        return any(kw.lower() in search_text for kw in keywords)

    def _extract_country(self, item: dict) -> str:
        """Extract country from job data."""
        location = (item.get("location") or "").lower()
        country_field = item.get("country") or ""
        
        if country_field:
            return country_field
        
        # Try to infer from location
        if "us" in location or "usa" in location or "united states" in location:
            return "United States"
        elif "uk" in location or "united kingdom" in location:
            return "United Kingdom"
        elif "canada" in location:
            return "Canada"
        elif "global" in location or "worldwide" in location or "anywhere" in location:
            return "Global"
        
        return "Global"  # Default for remote jobs

    def _parse_date(self, date_str: str | None) -> str:
        """Parse date to YYYY-MM-DD."""
        if not date_str:
            return ""
        
        try:
            # Handle ISO format
            return date_str.split("T")[0]
        except Exception:
            return ""
