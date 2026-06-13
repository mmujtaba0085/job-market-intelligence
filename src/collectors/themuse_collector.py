"""
src/collectors/themuse_collector.py
───────────────────────────────────
Collector for The Muse API v2 (no auth required).

Endpoint: https://www.themuse.com/api/public/jobs
Method: GET
Query params:
  - page: pagination (0-based)
  - api_key: optional (public access works without)
  - category: optional filter
  - location: optional filter

Response fields used:
  - results[].name (job title)
  - results[].company.name
  - results[].locations[].name
  - results[].publication_date
  - results[].refs.landing_page (apply URL)
  - results[].contents (description)

Free API - rate limits not documented, using conservative 30/min.
"""

from __future__ import annotations

import hashlib
import logging

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.themuse.com/api/public/jobs"
_TIMEOUT = 30


class TheMuseCollector(BaseCollector):
    source_id = "themuse"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from The Muse API."""
        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        page = 0
        max_pages = 50  # TheMuse supports up to page 99 at 20 results/page
        
        keywords = market.get("keywords", [])

        while len(results) < max_jobs and page < max_pages:
            self._wait()
            
            try:
                params = {
                    "page": str(page),
                    "descending": "true",  # Newest first
                }
                
                logger.debug("[themuse] Fetching page %d", page)
                resp = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT)
                
                if resp.status_code == 429:
                    logger.warning("[themuse] Rate limited (429), stopping")
                    break
                
                if resp.status_code != 200:
                    logger.warning("[themuse] HTTP %d on page %d", resp.status_code, page)
                    break

                data = resp.json()
                jobs_data = data.get("results", [])
                
                if not jobs_data:
                    logger.debug("[themuse] No more jobs on page %d", page)
                    break

                for item in jobs_data:
                    if len(results) >= max_jobs:
                        break
                    
                    # Filter by keywords client-side
                    if keywords and not self._matches_keywords(item, keywords):
                        continue
                    
                    # Extract location
                    locations = item.get("locations", [])
                    location = locations[0].get("name", "") if locations else ""
                    
                    # Infer country from location
                    country = self._infer_country(location)
                    
                    # Get URL
                    refs = item.get("refs", {})
                    url = refs.get("landing_page", "")
                    
                    if not url:
                        # Fallback URL
                        hash_input = f"{item.get('name')}|{item.get('company', {}).get('name')}|{item.get('id')}"
                        url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                        url = f"themuse://{url_hash}"
                    
                    # Company name
                    company_obj = item.get("company", {})
                    company = company_obj.get("name") or ""
                    
                    # Remote type detection
                    remote_type = self._detect_remote_type(
                        item.get("name", ""),
                        location,
                        item.get("contents", "")
                    )
                    
                    results.append(
                        JobRaw(
                            source_id=self.source_id,
                            source_name="TheMuse",
                            url=url,
                            fetched_at=self._now(),
                            raw_json=item,
                            parsed_fields={
                                "title": item.get("name") or "",
                                "company": company,
                                "location": location,
                                "country": country,
                                "remote_type": remote_type,
                                "posted_date": self._parse_date(item.get("publication_date")),
                                "description": item.get("contents") or "",
                            },
                        )
                    )

                logger.debug("[themuse] Page %d: collected %d matching jobs", page, len([j for j in jobs_data if not keywords or self._matches_keywords(j, keywords)]))
                page += 1

            except requests.Timeout:
                logger.warning("[themuse] Timeout on page %d", page)
                break
            except Exception as e:
                logger.error("[themuse] Error on page %d: %s", page, e)
                break

        return results[:max_jobs]

    def _matches_keywords(self, item: dict, keywords: list[str]) -> bool:
        """Check if job matches any keyword."""
        title = (item.get("name") or "").lower()
        contents = (item.get("contents") or "").lower()
        
        search_text = f"{title} {contents}"
        
        return any(kw.lower() in search_text for kw in keywords)

    def _infer_country(self, location: str) -> str:
        from src.utils.country_inference import infer_country
        return infer_country(location)

    def _detect_remote_type(self, title: str, location: str, description: str) -> str:
        """Detect if job is remote/hybrid/on-site."""
        search_text = f"{title} {location} {description}".lower()
        
        if "remote" in search_text or "work from home" in search_text:
            if "hybrid" in search_text:
                return "Hybrid"
            return "Remote"
        
        return "On-site"

    def _parse_date(self, date_str: str | None) -> str:
        """Parse date to YYYY-MM-DD."""
        if not date_str:
            return ""
        
        try:
            # Handle ISO format
            return date_str.split("T")[0]
        except Exception:
            return ""
