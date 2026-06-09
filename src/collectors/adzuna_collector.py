"""
src/collectors/adzuna_collector.py
───────────────────────────────────
Collector for Adzuna API (requires authentication).

Endpoint: GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
Query params:
  - app_id: Application ID (required)
  - app_key: Application key (required)
  - what: keyword search
  - results_per_page: 1-50
  - sort_by: date (optional)

Response fields used:
  - results[].title
  - results[].company.display_name
  - results[].location.display_name
  - results[].description
  - results[].redirect_url or adref
  - results[].created
  - results[].salary_min, salary_max (optional)

Requires ADZUNA_APP_ID and ADZUNA_APP_KEY in .env
"""

from __future__ import annotations

import hashlib
import logging
import os
import time

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.adzuna.com/v1/api/jobs"
_TIMEOUT = 15
_MAX_RESULTS = 50  # Adzuna max per page

# Country name to ISO-2 code mapping
_COUNTRY_TO_ISO = {
    "united states": "us",
    "united kingdom": "gb",
    "uk": "gb",
    "germany": "de",
    "france": "fr",
    "canada": "ca",
    "australia": "au",
    "netherlands": "nl",
    "global": "us",  # Default to US for global searches
}


class AdzunaCollector(BaseCollector):
    source_id = "adzuna"

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from Adzuna API."""
        # Check for required credentials
        app_id = os.getenv("ADZUNA_APP_ID")
        app_key = os.getenv("ADZUNA_APP_KEY")
        
        if not app_id or not app_key:
            logger.warning("[adzuna] Missing ADZUNA_APP_ID or ADZUNA_APP_KEY in environment, skipping")
            return []
        
        results: list[JobRaw] = []
        seen_urls: set[str] = set()
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])
        
        # Determine countries to search
        countries = self._get_search_countries(market)
        
        error_count = 0
        max_errors = 3
        retry_429_count = 0
        max_429_retries = 2
        
        for keyword in keywords:
            if len(results) >= max_jobs:
                break
            
            for country_code in countries:
                if len(results) >= max_jobs:
                    break
                
                # Stop if too many 429s
                if retry_429_count >= max_429_retries:
                    logger.warning("[adzuna] Too many 429 responses, stopping Adzuna for this run")
                    return results[:max_jobs]
                
                page = 1
                while len(results) < max_jobs:
                    self._wait()

                    try:
                        remaining = max_jobs - len(results)
                        per_page = min(_MAX_RESULTS, remaining)

                        # Build URL with current page
                        fetch_url = f"{_BASE_URL}/{country_code}/search/{page}"
                        params = {
                            "app_id": app_id,
                            "app_key": app_key,
                            "what": keyword,
                            "results_per_page": per_page,
                            "sort_by": "date",
                        }

                        logger.debug("[adzuna] Fetching %s/page%d: keyword=%s, per_page=%d",
                                    country_code, page, keyword, per_page)

                        resp = requests.get(fetch_url, params=params, timeout=_TIMEOUT,
                                          headers={"Accept": "application/json"})

                        if resp.status_code == 429:
                            # Rate limited - retry with backoff
                            retry_429_count += 1
                            logger.warning("[adzuna] Rate limited (429), sleeping 5s and retrying (%d/%d)",
                                         retry_429_count, max_429_retries)
                            time.sleep(5)

                            resp = requests.get(fetch_url, params=params, timeout=_TIMEOUT,
                                              headers={"Accept": "application/json"})

                            if resp.status_code == 429:
                                logger.warning("[adzuna] Still 429 after retry")
                                break  # stop paging this combo
                            # If retry succeeded, reset counter
                            retry_429_count = 0

                        if resp.status_code >= 500:
                            error_count += 1
                            logger.warning("[adzuna] HTTP %d for %s/%s", resp.status_code, country_code, keyword)
                            if error_count >= max_errors:
                                logger.warning("[adzuna] Too many 5xx errors, stopping")
                            break  # stop paging this combo

                        if resp.status_code == 401:
                            logger.error("[adzuna] Authentication failed (401) - check ADZUNA_APP_ID and ADZUNA_APP_KEY")
                            return []  # Stop entirely if auth fails

                        if resp.status_code != 200:
                            logger.warning("[adzuna] HTTP %d for %s/%s", resp.status_code, country_code, keyword)
                            break  # stop paging this combo

                        data = resp.json()
                        jobs_data = data.get("results", [])

                        if not jobs_data:
                            logger.debug("[adzuna] No jobs for %s/page%d '%s'", country_code, page, keyword)
                            break

                        new_jobs = 0
                        for item in jobs_data:
                            if len(results) >= max_jobs:
                                break

                            # Extract URL for deduplication
                            url = item.get("redirect_url") or item.get("adref") or ""
                            if not url:
                                # Generate fallback URL
                                job_id = item.get("id") or ""
                                if job_id:
                                    url = f"adzuna://{job_id}"
                                else:
                                    hash_input = f"{item.get('title')}|{self._get_company(item)}|{item.get('created')}"
                                    url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                                    url = f"adzuna://{url_hash}"

                            # Deduplicate across keywords/countries
                            if url in seen_urls:
                                continue
                            seen_urls.add(url)

                            # Extract fields
                            title = item.get("title") or ""
                            company = self._get_company(item)
                            location = self._get_location(item)
                            description = item.get("description") or ""
                            created = item.get("created") or ""
                            posted_date = self._parse_date(created)

                            # Infer country from location or use search country
                            country = self._extract_country(location, country_code)

                            # Infer remote type from title/description
                            remote_type = self._infer_remote_type(title, description, location)

                            results.append(
                                JobRaw(
                                    source_id=self.source_id,
                                    source_name="Adzuna",
                                    url=url,
                                    fetched_at=self._now(),
                                    raw_json=item,
                                    parsed_fields={
                                        "title": title,
                                        "company": company,
                                        "location": location,
                                        "country": country,
                                        "remote_type": remote_type,
                                        "posted_date": posted_date,
                                        "description": description,
                                    },
                                )
                            )
                            new_jobs += 1

                        logger.debug("[adzuna] %s/page%d '%s': collected %d new jobs",
                                   country_code, page, keyword, new_jobs)

                        # Reset error counter on success
                        error_count = 0

                        if len(jobs_data) < per_page:
                            break  # exhausted pages for this keyword/country combo
                        page += 1

                    except requests.Timeout:
                        error_count += 1
                        logger.warning("[adzuna] Timeout for %s/%s (%d/%d)", country_code, keyword, error_count, max_errors)
                        break  # stop paging this combo
                    except Exception as e:
                        error_count += 1
                        logger.error("[adzuna] Error for %s/%s: %s (%d/%d)", country_code, keyword, e, error_count, max_errors)
                        break  # stop paging this combo

                if error_count >= max_errors:
                    break  # stop remaining countries for this keyword
        
        logger.info("[adzuna] Collected %d raw jobs for market %s", len(results), market.get("market_id"))
        return results[:max_jobs]

    def _get_search_countries(self, market: dict) -> list[str]:
        """Get list of ISO country codes to search."""
        # Check if market specifies countries
        market_countries = market.get("countries", [])
        
        if not market_countries:
            # Default to major markets
            return ["us", "gb", "de"]
        
        # Map country names to ISO codes
        iso_codes = []
        for country in market_countries:
            country_lower = country.lower()
            iso_code = _COUNTRY_TO_ISO.get(country_lower, "us")
            if iso_code not in iso_codes:
                iso_codes.append(iso_code)
        
        return iso_codes if iso_codes else ["us"]

    def _get_company(self, item: dict) -> str:
        """Extract company name from item."""
        company_data = item.get("company", {})
        if isinstance(company_data, dict):
            return company_data.get("display_name") or ""
        return ""

    def _get_location(self, item: dict) -> str:
        """Extract location from item."""
        location_data = item.get("location", {})
        if isinstance(location_data, dict):
            return location_data.get("display_name") or ""
        return ""

    def _extract_country(self, location: str, search_country_code: str) -> str:
        """Extract country from location or use search country."""
        if not location:
            # Use search country code
            for name, code in _COUNTRY_TO_ISO.items():
                if code == search_country_code:
                    return name.title()
            return "United States"
        
        loc_lower = location.lower()
        
        # Try to detect country from location string
        if "us" in loc_lower or "usa" in loc_lower or "united states" in loc_lower:
            return "United States"
        elif "uk" in loc_lower or "united kingdom" in loc_lower or "london" in loc_lower:
            return "United Kingdom"
        elif "germany" in loc_lower or "berlin" in loc_lower or "munich" in loc_lower:
            return "Germany"
        elif "france" in loc_lower or "paris" in loc_lower:
            return "France"
        elif "canada" in loc_lower or "toronto" in loc_lower:
            return "Canada"
        
        # Default to search country
        for name, code in _COUNTRY_TO_ISO.items():
            if code == search_country_code:
                return name.title()
        
        return "United States"

    def _infer_remote_type(self, title: str, description: str, location: str) -> str:
        """Infer remote type from job data."""
        search_text = f"{title} {description} {location}".lower()
        
        if "remote" in search_text or "work from home" in search_text or "wfh" in search_text:
            if "hybrid" in search_text:
                return "Hybrid"
            return "Remote"
        
        return "On-site"

    def _parse_date(self, date_str: str) -> str:
        """Parse date to YYYY-MM-DD."""
        if not date_str:
            return ""
        
        try:
            # Adzuna uses ISO format: "2024-02-28T12:00:00Z"
            return date_str.split("T")[0]
        except Exception:
            return ""
