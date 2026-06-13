"""
src/collectors/remotive_collector.py
─────────────────────────────────────
Collector for the Remotive public REST API.
  - No API key required
  - Free, JSON response
  - Returns remote-first job listings
  - Endpoint: https://remotive.com/api/remote-jobs?search={keyword}
"""

from __future__ import annotations

import logging

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import JSEARCH_API_KEY
from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://remotive.com/api/remote-jobs"
_TIMEOUT = 15


class RemotiveCollector(BaseCollector):
    source_id = "remotive"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 500)

        for keyword in market["keywords"]:
            self._wait()
            try:
                resp = requests.get(
                    _BASE_URL,
                    params={"search": keyword},
                    timeout=_TIMEOUT,
                    headers={"User-Agent": "JobMarketIntelligence/1.0 (research)"},
                )
                resp.raise_for_status()
                data = resp.json()
                jobs_raw_list = data.get("jobs", [])

                for item in jobs_raw_list:
                    results.append(
                        JobRaw(
                            source_id=self.source_id,
                            source_name="Remotive",
                            url=item.get("url", ""),
                            fetched_at=self._now(),
                            raw_json=item,
                            parsed_fields={
                                "title": item.get("title", ""),
                                "company": item.get("company_name", ""),
                                "location": item.get("candidate_required_location", ""),
                                "country": _infer_country(
                                    item.get("candidate_required_location", "")
                                ),
                                "remote_type": "remote",   # Remotive = always remote
                                "posted_date": item.get("publication_date", "")[:10],
                                "description": item.get("description", ""),
                                "salary": item.get("salary", ""),
                                "tags": item.get("tags", []) if isinstance(item.get("tags", []), list) else [],
                            },
                        )
                    )

                logger.debug(
                    "[remotive] keyword='%s' → %d listings", keyword, len(jobs_raw_list)
                )

            except requests.HTTPError as exc:
                logger.warning("[remotive] HTTP error for keyword '%s': %s", keyword, exc)
            except requests.RequestException as exc:
                logger.warning("[remotive] Request failed for keyword '%s': %s", keyword, exc)

            if len(results) >= max_jobs:
                break

        return results[:max_jobs]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _infer_country(location_str: str) -> str:
    from src.utils.country_inference import infer_country
    return infer_country(location_str)
