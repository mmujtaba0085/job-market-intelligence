"""
src/collectors/graphqljobs_collector.py
───────────────────────────────────────
Collector for GraphQL Jobs API (no auth required).

Endpoint: https://api.graphql.jobs/
Method: POST (GraphQL)
Query fields:
  - jobs { id, title, company { name }, cities { name, country { name } }, commitment { title }, postedAt, description, applyUrl, tags { name } }

Client-side keyword filtering on title + description.
Free API - using conservative rate limit.
"""

from __future__ import annotations

import hashlib
import logging

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_GRAPHQL_ENDPOINT = "https://api.graphql.jobs/"
_TIMEOUT = 30


class GraphQLJobsCollector(BaseCollector):
    source_id = "graphqljobs"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from GraphQL Jobs API."""
        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])

        self._wait()

        try:
            # GraphQL query to fetch all jobs
            query = """
            query {
              jobs(first: 100) {
                id
                title
                company {
                  name
                }
                cities {
                  name
                  country {
                    name
                  }
                }
                commitment {
                  title
                }
                postedAt
                description
                applyUrl
                tags {
                  name
                }
              }
            }
            """
            
            payload = {"query": query}
            
            logger.debug("[graphqljobs] Sending GraphQL query")
            resp = requests.post(_GRAPHQL_ENDPOINT, json=payload, timeout=_TIMEOUT)
            
            if resp.status_code == 429:
                logger.warning("[graphqljobs] Rate limited (429)")
                return []
            
            if resp.status_code != 200:
                logger.warning("[graphqljobs] HTTP %d", resp.status_code)
                return []

            data = resp.json()
            jobs_data = data.get("data", {}).get("jobs", [])
            
            for item in jobs_data:
                if len(results) >= max_jobs:
                    break
                
                # Filter by keywords client-side
                if keywords and not self._matches_keywords(item, keywords):
                    continue
                
                # Extract location and country
                cities = item.get("cities", [])
                location = ""
                country = "Unknown"
                
                if cities:
                    first_city = cities[0]
                    location = first_city.get("name", "")
                    country_obj = first_city.get("country", {})
                    country = country_obj.get("name", "Unknown")
                
                # Company name
                company_obj = item.get("company", {})
                company = company_obj.get("name") or ""
                
                # URL
                url = item.get("applyUrl") or ""
                if not url:
                    # Fallback URL
                    job_id = item.get("id", "")
                    if job_id:
                        url = f"graphqljobs://{job_id}"
                    else:
                        hash_input = f"{item.get('title')}|{company}|{location}"
                        url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                        url = f"graphqljobs://{url_hash}"
                
                # Remote type from commitment
                commitment_obj = item.get("commitment", {})
                commitment_title = (commitment_obj.get("title") or "").lower()
                
                if "remote" in commitment_title:
                    remote_type = "Remote"
                elif "hybrid" in commitment_title:
                    remote_type = "Hybrid"
                else:
                    # Check description
                    remote_type = self._detect_remote_type(
                        item.get("title", ""),
                        location,
                        item.get("description", "")
                    )
                
                results.append(
                    JobRaw(
                        source_id=self.source_id,
                        source_name="GraphQLJobs",
                        url=url,
                        fetched_at=self._now(),
                        raw_json=item,
                        parsed_fields={
                            "title": item.get("title") or "",
                            "company": company,
                            "location": location,
                            "country": country,
                            "remote_type": remote_type,
                            "posted_date": self._parse_date(item.get("postedAt")),
                            "description": item.get("description") or "",
                            "tags": [t.get("name", "") for t in item.get("tags", [])],
                        },
                    )
                )

            logger.debug("[graphqljobs] Collected %d matching jobs from %d total", len(results), len(jobs_data))

        except requests.Timeout:
            logger.warning("[graphqljobs] Request timeout")
        except Exception as e:
            logger.error("[graphqljobs] Error: %s", e)

        return results[:max_jobs]

    def _matches_keywords(self, item: dict, keywords: list[str]) -> bool:
        """Check if job matches any keyword."""
        title = (item.get("title") or "").lower()
        desc = (item.get("description") or "").lower()
        tags = " ".join([t.get("name", "") for t in item.get("tags", [])]).lower()
        
        search_text = f"{title} {desc} {tags}"
        
        return any(kw.lower() in search_text for kw in keywords)

    def _detect_remote_type(self, title: str, location: str, description: str) -> str:
        """Detect remote type from text."""
        search_text = f"{title} {location} {description}".lower()
        
        if "remote" in search_text:
            if "hybrid" in search_text:
                return "Hybrid"
            return "Remote"
        
        return "On-site"

    def _parse_date(self, date_str: str | None) -> str:
        """Parse date to YYYY-MM-DD."""
        if not date_str:
            return ""
        
        try:
            return date_str.split("T")[0]
        except Exception:
            return ""
