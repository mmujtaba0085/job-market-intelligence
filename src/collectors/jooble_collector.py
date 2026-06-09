"""
src/collectors/jooble_collector.py
───────────────────────────────────
Collector for Jooble API (requires authentication).

Endpoint: POST https://jooble.org/api/<JOOBLE_API_KEY>
Auth: API key is part of the URL path (not header)
Request body: JSON with {"keywords": "...", "location": "...", "page": 1}
Response: JSON with jobs[] array

Response fields used:
  - jobs[].title -> title
  - jobs[].company -> company (fallback "Unknown")
  - jobs[].location -> location
  - jobs[].link or jobs[].url -> url
  - jobs[].snippet or jobs[].description -> raw_description
  - jobs[].updated or jobs[].posted -> posted_date
  - remote/hybrid detection from text -> remote_type

Rate limit: ~60 requests/minute (typical for free tier)
Requires JOOBLE_API_KEY in .env
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.enrichment.location_resolver import resolve_location
from src.enrichment.salary import parse_salary

logger = logging.getLogger(__name__)

_BASE_URL = "https://jooble.org/api/"
_TIMEOUT = 15
_MAX_PAGES = 2  # MVP conservative limit


class JoobleCollector(BaseCollector):
    source_id = "jooble"

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from Jooble API via POST requests."""
        # Check for required API key
        api_key = os.getenv("JOOBLE_API_KEY")
        
        if not api_key:
            logger.warning("[jooble] Missing JOOBLE_API_KEY in environment, skipping")
            return []
        
        results: list[JobRaw] = []
        seen_urls: set[str] = set()
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])
        countries = market.get("countries", [])
        
        error_count = 0
        max_errors = 3
        retry_429_count = 0
        max_429_retries = 2
        
        # Build endpoint URL with API key
        endpoint = f"{_BASE_URL}{api_key}"
        
        for keyword in keywords:
            if len(results) >= max_jobs:
                break
            
            # Stop if too many 429s globally
            if retry_429_count >= max_429_retries:
                logger.warning("[jooble] Too many 429 responses, stopping Jooble for this run")
                break
            
            # Loop over countries/locations
            locations = self._get_location_strings(countries)
            
            for location in locations:
                if len(results) >= max_jobs:
                    break
                
                # Paginate through results
                for page in range(1, _MAX_PAGES + 1):
                    if len(results) >= max_jobs:
                        break
                    
                    # Build JSON payload
                    payload = {
                        "keywords": keyword,
                        "location": location,
                        "page": str(page),
                    }
                    
                    # Rate limit
                    self._wait()
                    
                    # Retry logic for 429
                    attempt = 0
                    max_attempts = 3
                    success = False
                    
                    while attempt < max_attempts and not success:
                        try:
                            resp = requests.post(
                                endpoint,
                                json=payload,
                                timeout=_TIMEOUT,
                                headers={"User-Agent": "JobMarketIntel/1.0"},
                            )
                            
                            # Handle 429 rate limiting
                            if resp.status_code == 429:
                                retry_429_count += 1
                                if retry_429_count >= max_429_retries:
                                    logger.warning("[jooble] Hit max 429 retries, stopping collection")
                                    return results
                                logger.warning("[jooble] 429 rate limit hit, sleeping 6s then retrying...")
                                time.sleep(6)
                                attempt += 1
                                continue
                            
                            # Handle 5xx server errors
                            if 500 <= resp.status_code < 600:
                                error_count += 1
                                if error_count >= max_errors:
                                    logger.error("[jooble] Too many 5xx errors, stopping collection")
                                    return results
                                backoff = 2 * attempt if attempt > 0 else 2
                                logger.warning(
                                    "[jooble] 5xx error (status=%d), retrying in %ds...",
                                    resp.status_code, backoff
                                )
                                time.sleep(backoff)
                                attempt += 1
                                continue
                            
                            resp.raise_for_status()
                            success = True
                            
                        except requests.exceptions.Timeout:
                            logger.warning("[jooble] Request timeout for keyword='%s', location='%s', page=%d",
                                         keyword, location, page)
                            break
                        except requests.exceptions.RequestException as e:
                            logger.error("[jooble] Request failed: %s", e)
                            break
                    
                    if not success:
                        # Skip to next location if this one failed
                        break
                    
                    # Parse response
                    try:
                        data = resp.json()
                    except ValueError:
                        logger.warning("[jooble] Invalid JSON response for keyword='%s', location='%s'",
                                     keyword, location)
                        break
                    
                    # Extract jobs from response
                    jobs_list = data.get("jobs", [])
                    
                    if not jobs_list:
                        # No more results for this keyword/location combo
                        break
                    
                    for job in jobs_list:
                        if len(results) >= max_jobs:
                            break
                        
                        # Extract URL (required field)
                        url = job.get("link") or job.get("url") or ""
                        if not url:
                            # Generate fallback URL
                            job_str = f"{job.get('title', '')}{job.get('company', '')}{job.get('location', '')}"
                            url_hash = hashlib.sha256(job_str.encode()).hexdigest()[:16]
                            url = f"jooble://{url_hash}"
                        
                        # Skip duplicates
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        
                        # Extract and map fields
                        title = job.get("title", "").strip()
                        company = job.get("company", "").strip() or "Unknown"
                        job_location = job.get("location", "").strip()
                        
                        # Description
                        raw_description = job.get("snippet") or job.get("description") or ""
                        
                        # Posted date - try multiple field names
                        posted_date = (
                            job.get("updated") or 
                            job.get("posted") or 
                            job.get("created") or 
                            job.get("date") or
                            ""
                        )
                        # Parse date if needed (Jooble might return ISO format)
                        if posted_date and "T" in posted_date:
                            posted_date = posted_date.split("T")[0]
                        
                        # Infer remote type from text content
                        remote_type = self._infer_remote_type(title, raw_description, job_location)
                        
                        # Extract country from location string
                        country = self._extract_country(job_location, location)
                        resolved = resolve_location(job_location, country if country != "Unknown" else None)
                        salary = parse_salary(job.get("salary") or job.get("salaryRange") or "")
                        
                        # Create JobRaw object
                        job_raw = JobRaw(
                            source_id=self.source_id,
                            source_name="Jooble",
                            url=url,
                            fetched_at=self._now(),
                            raw_json=job,
                            parsed_fields={
                                "title": title,
                                "company": company,
                                "location": job_location,
                                "country": country,
                                "remote_type": remote_type,
                                "posted_date": posted_date,
                                "description": raw_description,
                                "structured_locations": [resolved.__dict__],
                                **salary,
                            },
                            source_record_id=str(job.get("id") or url),
                            structured_locations=[resolved.__dict__],
                        )
                        
                        results.append(job_raw)
        
        logger.info("[jooble] Collected %d raw jobs for market '%s'",
                   len(results), market.get("market_id", "unknown"))
        return results
    
    def _get_location_strings(self, countries: list[str]) -> list[str]:
        """
        Convert country codes to location strings for Jooble API.
        Jooble uses country names, not codes.
        """
        # Map common country codes to names
        country_map = {
            "US": "United States",
            "GB": "United Kingdom",
            "UK": "United Kingdom",
            "CA": "Canada",
            "DE": "Germany",
            "FR": "France",
            "ES": "Spain",
            "IT": "Italy",
            "NL": "Netherlands",
            "AU": "Australia",
            "IN": "India",
            "SG": "Singapore",
            "IE": "Ireland",
        }
        
        locations = []
        for country in countries:
            # Try to map code to name
            location = country_map.get(country.upper(), country)
            locations.append(location)
        
        # If no countries specified, use empty string (global search)
        if not locations:
            locations = [""]
        
        return locations
    
    def _infer_remote_type(self, title: str, description: str, location: str) -> str:
        """
        Infer remote type from job text content.
        Returns exactly: "Remote", "Hybrid", or "On-site"
        """
        combined_text = f"{title} {description} {location}".lower()
        
        # Check for remote indicators
        remote_keywords = [
            r'\bremote\b', r'\bwork from home\b', r'\bwfh\b',
            r'\banywhere\b', r'\bfully remote\b', r'\b100% remote\b'
        ]
        
        for pattern in remote_keywords:
            if re.search(pattern, combined_text):
                # Check if it's hybrid
                if re.search(r'\bhybrid\b', combined_text):
                    return "Hybrid"
                return "Remote"
        
        # Check for hybrid specifically
        if re.search(r'\bhybrid\b', combined_text):
            return "Hybrid"
        
        # Default to on-site
        return "On-site"
    
    def _extract_country(self, location_str: str, search_location: str) -> str:
        """
        Extract country from location string.
        Jooble location format is typically "City, Country" or "Country".
        """
        if not location_str:
            # Fall back to search location if available
            if search_location and search_location.strip():
                return search_location
            return "Unknown"
        
        # Split on comma and take last part as country
        parts = location_str.split(",")
        if len(parts) > 1:
            country = parts[-1].strip()
            return country if country else "Unknown"
        
        # If no comma, assume the whole string is the country
        return location_str.strip() or "Unknown"
