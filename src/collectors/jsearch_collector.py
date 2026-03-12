"""
src/collectors/jsearch_collector.py
────────────────────────────────────
Collector for JSearch via RapidAPI.

CHANGES (per requirements):
- URL never empty: uses job_apply_link/job_google_link or generates stable hash-based fallback
- Standardized remote_type to exactly "Remote", "Hybrid", or "On-site"
- Fixed sampling bias: enforces fair per_query_limit across keywords/countries
- Circuit breaker: on 429 after retries, stops ALL collection immediately
- 5xx retry: exponential backoff (2s, 4s, 8s) up to 3 times
- No external dependencies added (hashlib, math from stdlib)

Usage:
  - Requires JSEARCH_API_KEY in .env (gracefully skips if missing)
  - Aggregates listings from Indeed, LinkedIn, Glassdoor and more
  - Endpoint: https://jsearch.p.rapidapi.com/search
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import JSEARCH_API_KEY
from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://jsearch.p.rapidapi.com/search"
_HOST = "jsearch.p.rapidapi.com"
_TIMEOUT = 60   # Increased from 20 - some queries are slow
_PAGE_SIZE = 10   # JSearch free tier max per page

# Country name to ISO-2 code mapping for RapidAPI
_COUNTRY_TO_ISO: dict[str, str] = {
    "United States": "us",
    "United Kingdom": "gb",
    "Germany": "de",
    "Canada": "ca",
    "France": "fr",
    "Spain": "es",
    "Italy": "it",
    "Netherlands": "nl",
    "Australia": "au",
    "India": "in",
}


class JSearchCollector(BaseCollector):
    source_id = "jsearch"

    def __init__(self) -> None:
        super().__init__()
        if not JSEARCH_API_KEY:
            logger.warning(
                "[jsearch] JSEARCH_API_KEY not set — collector will be skipped. "
                "Add it to .env to enable."
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        if not JSEARCH_API_KEY:
            return []

        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        rate_limited_ref = [False]  # Circuit breaker flag (use list for mutability in nested function)
        headers = {
            "X-RapidAPI-Key": JSEARCH_API_KEY,
            "X-RapidAPI-Host": _HOST,
        }

        # Calculate fair per-query limit to avoid sampling bias
        keywords = market["keywords"]
        countries = market.get("countries", ["United States"])
        num_queries = len(keywords) * len(countries)
        per_query_limit = max(5, min(max_jobs, math.ceil(max_jobs / num_queries)))
        logger.debug("[jsearch] per_query_limit=%d (max_jobs=%d, queries=%d)", per_query_limit, max_jobs, num_queries)

        for keyword in keywords:
            if rate_limited_ref[0]:
                break
            for country in countries:
                if rate_limited_ref[0]:
                    break

                # Convert country name to ISO-2 code (fallback: lowercase first 2 chars)
                country_code = _COUNTRY_TO_ISO.get(country, country[:2].lower())
                page = 1
                query_count = 0  # Track jobs collected for this query

                while query_count < per_query_limit and len(results) < max_jobs:
                    self._wait()
                    
                    # Make request with retry logic
                    resp_data = self._request_with_retry(
                        headers, keyword, country_code, page, rate_limited_ref
                    )
                    
                    if resp_data is None:
                        # Either rate-limited (circuit breaker) or unrecoverable error
                        break
                    
                    items = resp_data.get("data", [])
                    if not items:
                        break

                    for item in items:
                        if query_count >= per_query_limit or len(results) >= max_jobs:
                            break
                        
                        results.append(self._build_job_raw(item, country))
                        query_count += 1

                    logger.debug("[jsearch] keyword='%s' country=%s page=%d → %d items (query_total=%d)", 
                                keyword, country_code, page, len(items), query_count)
                    page += 1

        # Trim to max_jobs if over
        if len(results) > max_jobs:
            results = results[:max_jobs]
        
        return results

    def _request_with_retry(self, headers: dict, keyword: str, country_code: str, page: int, rate_limited_ref: list) -> dict | None:
        """Make HTTP request with 429 circuit breaker and 5xx retry logic.
        
        Args:
            headers: RapidAPI headers (X-RapidAPI-Key, X-RapidAPI-Host)
            keyword: Job search keyword (e.g., "machine learning")
            country_code: ISO-2 country code (e.g., "us", "gb", "de")
            page: Page number for pagination
            rate_limited_ref: Mutable list flag for circuit breaker
        
        Returns:
            dict: Response data if successful
            None: If rate-limited (circuit breaker) or unrecoverable error
        """
        params = {
            "query": keyword,
            "page": str(page),
            "num_pages": "1",
            "country": country_code,
            "date_posted": "week",
        }
        
        try:
            resp = requests.get(
                _BASE_URL,
                headers=headers,
                params=params,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
            
        except requests.HTTPError as exc:
            if exc.response is None:
                logger.warning("[jsearch] HTTP error with no response: %s", exc)
                return None
            
            status = exc.response.status_code
            
            # Handle 429 rate limiting with circuit breaker
            if status == 429:
                for retry_num in range(2):  # 2 retries
                    logger.info("[jsearch] Rate limited (429), sleeping 6s (retry %d/2)", retry_num + 1)
                    time.sleep(6)
                    try:
                        retry_resp = requests.get(
                            _BASE_URL,
                            headers=headers,
                            params=params,
                            timeout=_TIMEOUT,
                        )
                        retry_resp.raise_for_status()
                        return retry_resp.json()  # Success!
                    except requests.HTTPError as retry_exc:
                        if retry_exc.response and retry_exc.response.status_code != 429:
                            logger.warning("[jsearch] HTTP error on retry: %s", retry_exc)
                            return None
                    except requests.RequestException as retry_exc:
                        logger.warning("[jsearch] Request error on retry: %s", retry_exc)
                        return None
                
                # Still 429 after retries - activate circuit breaker
                logger.warning("[jsearch] Rate limit persists after retries — stopping all collection")
                rate_limited_ref[0] = True
                return None
            
            # Handle 5xx errors with exponential backoff
            elif 500 <= status < 600:
                for retry_num in range(3):  # 3 retries
                    backoff = 2 ** retry_num + random.uniform(0, 1)  # 2s, 4s, 8s + jitter
                    logger.info("[jsearch] 5xx error (%d), retrying in %.1fs (retry %d/3)", status, backoff, retry_num + 1)
                    time.sleep(backoff)
                    try:
                        retry_resp = requests.get(
                            _BASE_URL,
                            headers=headers,
                            params=params,
                            timeout=_TIMEOUT,
                        )
                        retry_resp.raise_for_status()
                        return retry_resp.json()  # Success!
                    except requests.RequestException:
                        continue
                logger.warning("[jsearch] 5xx error persists after 3 retries, skipping request")
                return None
            else:
                logger.warning("[jsearch] HTTP error %d: %s", status, exc)
                return None
                
        except requests.RequestException as exc:
            logger.warning("[jsearch] Request error: %s", exc)
            return None

    def _build_job_raw(self, item: dict, country: str) -> JobRaw:
        """Build JobRaw from API response item, ensuring URL is never empty."""
        # Ensure URL is never empty
        url = item.get("job_apply_link") or item.get("job_google_link") or ""
        if not url:
            # Try job_id first
            if item.get("job_id"):
                url = f"jsearch://{item['job_id']}"
            else:
                # Generate stable hash from job attributes
                hash_input = "|".join([
                    str(item.get("job_title", "")),
                    str(item.get("employer_name", "")),
                    str(item.get("job_city", "")),
                    str(item.get("job_posted_at_datetime_utc", "")),
                ])
                url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                url = f"jsearch://{url_hash}"
        
        return JobRaw(
            source_id=self.source_id,
            source_name="JSearch",
            url=url,
            fetched_at=self._now(),
            raw_json=item,
            parsed_fields={
                "title": item.get("job_title") or "",
                "company": item.get("employer_name") or "",
                "location": (item.get("job_city") or "")
                    + (", " + (item.get("job_state") or "") if item.get("job_state") else ""),
                "country": item.get("job_country") or country,
                "remote_type": _extract_remote_type(item),
                "posted_date": _extract_date(item),
                "description": item.get("job_description") or "",
                "salary_min": item.get("job_min_salary"),
                "salary_max": item.get("job_max_salary"),
                "currency": item.get("job_salary_currency"),
            },
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_remote_type(item: dict) -> str:
    """Standardize remote_type to exactly 'Remote', 'Hybrid', or 'On-site'."""
    # Rule 1: If job_is_remote is True, it's Remote
    if item.get("job_is_remote"):
        return "Remote"
    
    # Rule 2: Check title + location + description for "hybrid" or "remote"
    searchable = " ".join([
        str(item.get("job_title", "")),
        str(item.get("job_city", "")),
        str(item.get("job_state", "")),
        str(item.get("job_description", ""))
    ]).lower()
    
    if "hybrid" in searchable:
        return "Hybrid"
    if "remote" in searchable:
        return "Remote"
    
    # Rule 3: Default to On-site
    return "On-site"


def _extract_date(item: dict) -> str:
    ts = item.get("job_posted_at_timestamp")
    if ts:
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        except (ValueError, OSError):
            pass
    return item.get("job_posted_at_datetime_utc", "")[:10]
