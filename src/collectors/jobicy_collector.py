"""
src/collectors/jobicy_collector.py
───────────────────────────────────
Collector for Jobicy JSON API (no auth required).

Endpoint: GET https://jobicy.com/api/v2/remote-jobs
Query params:
  - count: 1-50 (jobs per request)

Strategy (to avoid excessive API calls and 403 blocks):
  - Makes 1-2 requests per run (max 50 jobs each)
  - No tag/keyword filtering via API
  - Filters locally after fetching by matching market keywords against job text
  - Stops immediately on first 403

Response fields used:
  - jobTitle
  - companyName
  - url
  - jobDescription (HTML) or jobExcerpt
  - jobGeo (location string)
  - pubDate or similar date field

All jobs from Jobicy are remote positions.
"""

from __future__ import annotations

import hashlib
import html
import logging
import time

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://jobicy.com/api/v2/remote-jobs"
_TIMEOUT = 15
_MAX_COUNT = 50  # Conservative per-request limit

# Browser-like headers to avoid bot detection
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


class JobicyCollector(BaseCollector):
    source_id = "jobicy"

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from Jobicy JSON API."""
        results: list[JobRaw] = []
        seen_urls: set[str] = set()
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])
        
        # Calculate how many requests needed (max 50 per request)
        requests_needed = 1 if max_jobs <= 50 else 2
        jobs_to_fetch = min(max_jobs, 100)  # Max 100 total (2 * 50)
        
        all_jobs: list[dict] = []
        
        # Make 1-2 requests to get raw jobs
        for request_num in range(requests_needed):
            if len(all_jobs) >= jobs_to_fetch:
                break
            
            self._wait()
            
            try:
                count = min(50, jobs_to_fetch - len(all_jobs))
                
                params = {
                    "count": count,
                    # No tag parameter - get all remote jobs
                }
                
                logger.debug("[jobicy] Fetching count=%d (request %d/%d)", count, request_num + 1, requests_needed)
                resp = requests.get(_BASE_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
                
                if resp.status_code == 429:
                    logger.warning("[jobicy] Rate limited (429), stopping")
                    break
                
                if resp.status_code == 403:
                    logger.warning("[jobicy] HTTP 403 — stopping Jobicy collection for this run")
                    break
                
                if resp.status_code >= 500:
                    logger.warning("[jobicy] HTTP 5xx (%d), stopping", resp.status_code)
                    break
                
                if resp.status_code != 200:
                    logger.warning("[jobicy] HTTP %d, stopping", resp.status_code)
                    break
                
                data = resp.json()
                
                # Jobs might be in a "jobs" array or at root level
                jobs_data = data.get("jobs", []) if isinstance(data, dict) else []
                if not jobs_data and isinstance(data, list):
                    jobs_data = data
                
                if not jobs_data:
                    logger.debug("[jobicy] No jobs returned")
                    break
                
                all_jobs.extend(jobs_data)
                logger.debug("[jobicy] Fetched %d jobs from API", len(jobs_data))
                
            except requests.Timeout:
                logger.warning("[jobicy] Request timeout, stopping")
                break
            except Exception as e:
                logger.error("[jobicy] Request error: %s, stopping", e)
                break
        
        # Now filter locally by keywords
        filtered_count = 0
        for item in all_jobs:
            if len(results) >= max_jobs:
                break
            
            # Extract URL for deduplication
            url = item.get("url") or ""
            if not url:
                # Generate fallback URL
                job_id = item.get("id") or ""
                if job_id:
                    url = f"jobicy://{job_id}"
                else:
                    hash_input = f"{item.get('jobTitle')}|{item.get('companyName')}|{item.get('pubDate')}"
                    url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                    url = f"jobicy://{url_hash}"
            
            # Deduplicate
            if url in seen_urls:
                continue
            seen_urls.add(url)
            
            # Build searchable text (decode HTML entities)
            job_title = html.unescape(item.get("jobTitle") or "")
            job_excerpt = html.unescape(item.get("jobExcerpt") or "")
            job_description = html.unescape(item.get("jobDescription") or "")
            searchable_text = f"{job_title} {job_excerpt} {job_description}".lower()
            
            # Filter by keywords (if market has keywords)
            if keywords:
                keyword_found = any(kw.lower() in searchable_text for kw in keywords)
                if not keyword_found:
                    continue
            # If no keywords, keep all jobs
            
            # Extract description
            description = job_description or job_excerpt
            
            # Extract location (decode HTML entities)
            location = html.unescape(item.get("jobGeo") or item.get("jobLocation") or "")
            country = self._extract_country(location)
            
            # Extract date
            posted_date = self._parse_date(
                item.get("pubDate") or item.get("jobDatePosted") or item.get("annoncedDate")
            )
            
            results.append(
                JobRaw(
                    source_id=self.source_id,
                    source_name="Jobicy",
                    url=url,
                    fetched_at=self._now(),
                    raw_json=item,
                    parsed_fields={
                        "title": job_title,
                        "company": html.unescape(item.get("companyName") or ""),
                        "location": location,
                        "country": country,
                        "remote_type": "Remote",  # Jobicy = remote jobs only
                        "posted_date": posted_date,
                        "description": description,
                    },
                )
            )
            filtered_count += 1
        
        logger.info("[jobicy] Collected %d raw jobs for market '%s' (fetched %d, filtered to %d)",
                   len(results), market.get("market_id"), len(all_jobs), filtered_count)
        return results

    def _extract_country(self, location: str) -> str:
        """Extract country from location string."""
        if not location:
            return "Global"
        
        loc_lower = location.lower()
        
        # Common country detection
        if "us" in loc_lower or "usa" in loc_lower or "united states" in loc_lower:
            return "United States"
        elif "uk" in loc_lower or "united kingdom" in loc_lower:
            return "United Kingdom"
        elif "canada" in loc_lower:
            return "Canada"
        elif "germany" in loc_lower or "deutschland" in loc_lower:
            return "Germany"
        elif "france" in loc_lower:
            return "France"
        elif "global" in loc_lower or "worldwide" in loc_lower or "anywhere" in loc_lower:
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
