"""
src/collectors/findwork_collector.py
─────────────────────────────────────
Collector for Findwork API (requires authentication).

Endpoint: GET https://findwork.dev/api/jobs/
Header: Authorization: Token <FINDWORK_API_KEY>
Response: JSON with count, next, previous, results[]
Pagination: Follow "next" URL until None or max_jobs reached

Query params:
  - search: keyword search
  - sort_by: relevance (when search provided), date_posted (default)
  - remote: true/false filter
  - location: location filter (optional)

Response fields used:
  - results[].role -> title
  - results[].company_name -> company
  - results[].location -> location
  - results[].remote (boolean) -> remote_type
  - results[].url -> url
  - results[].text -> raw_description
  - results[].date_posted -> posted_date

Rate limit: 60 requests/minute
Requires FINDWORK_API_KEY in .env
"""

from __future__ import annotations

import hashlib
import logging
import os
import time

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_BASE_URL = "https://findwork.dev/api/jobs/"
_TIMEOUT = 15
_MAX_PAGES = 3          # Conservative pagination limit
_RATE_LIMIT = 55        # Stay safely under the 60 req/min ceiling
_RATE_WINDOW = 60.0     # Sliding window in seconds


class FindworkCollector(BaseCollector):
    source_id = "findwork"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sliding-window request timestamps shared across all keywords/pages
        # for this collector instance.
        self._request_timestamps: list[float] = []

    # ------------------------------------------------------------------
    # Rate-limit aware request gate
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """Block until we are safely within the rate limit window."""
        now = time.monotonic()
        # Prune timestamps outside the current 60-second window
        self._request_timestamps = [
            t for t in self._request_timestamps if now - t < _RATE_WINDOW
        ]
        if len(self._request_timestamps) >= _RATE_LIMIT:
            # Sleep until the oldest timestamp falls outside the window
            sleep_for = _RATE_WINDOW - (now - self._request_timestamps[0]) + 0.1
            if sleep_for > 0:
                logger.debug(
                    "[findwork] Rate limit approached (%d/%d reqs in window), sleeping %.2fs",
                    len(self._request_timestamps), _RATE_LIMIT, sleep_for,
                )
                time.sleep(sleep_for)
        self._request_timestamps.append(time.monotonic())

    # ------------------------------------------------------------------
    # Main fetch
    # ------------------------------------------------------------------

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from Findwork API."""
        api_key = os.getenv("FINDWORK_API_KEY")
        if not api_key:
            logger.warning("[findwork] Missing FINDWORK_API_KEY in environment, skipping")
            return []

        results: list[JobRaw] = []
        seen_urls: set[str] = set()
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])

        consecutive_429s = 0
        max_429s = 2

        headers = {
            "Authorization": f"Token {api_key}",
            "Accept": "application/json",
            "User-Agent": "JobMarketIntel/1.0",
        }

        for keyword in keywords:
            if len(results) >= max_jobs:
                break
            if consecutive_429s >= max_429s:
                logger.warning("[findwork] Too many 429 responses, stopping for this run")
                break

            params = {
                "search": keyword,
                "sort_by": "relevance",
            }
            if market.get("remote_filter"):
                params["remote"] = "true"

            next_url: str | None = _BASE_URL
            page_count = 0

            while (
                next_url
                and page_count < _MAX_PAGES
                and len(results) < max_jobs
                and consecutive_429s < max_429s
            ):
                # Honour both the base-class _wait() and our own throttle
                self._wait()
                self._throttle()

                page_count += 1
                resp = self._request_with_retries(
                    url=next_url,
                    headers=headers,
                    params=params if page_count == 1 else None,
                    keyword=keyword,
                    page=page_count,
                )

                # 429 accounting
                if resp == 429:
                    consecutive_429s += 1
                    logger.warning(
                        "[findwork] Rate limited (429) for keyword=%s page=%d (%d/%d)",
                        keyword, page_count, consecutive_429s, max_429s,
                    )
                    # Back off before the next keyword
                    time.sleep(10)
                    break

                # Hard auth failure – abort entirely
                if resp == 401:
                    logger.error("[findwork] Authentication failed (401) – check FINDWORK_API_KEY")
                    return []

                # Any other failure
                if resp is None:
                    logger.warning(
                        "[findwork] Failed to fetch keyword=%s page=%d, skipping keyword",
                        keyword, page_count,
                    )
                    break

                # Reset 429 streak on a successful response
                consecutive_429s = 0

                try:
                    if resp.status_code != 200:
                        logger.warning(
                            "[findwork] HTTP %d for keyword=%s page=%d",
                            resp.status_code, keyword, page_count,
                        )
                        break

                    data = resp.json()
                    jobs_data = data.get("results", [])

                    if not jobs_data:
                        logger.debug("[findwork] No jobs for keyword=%s page=%d", keyword, page_count)
                        break

                    new_jobs = 0
                    for item in jobs_data:
                        if len(results) >= max_jobs:
                            break

                        url = item.get("url") or ""
                        job_id = item.get("id") or ""

                        if not url:
                            if job_id:
                                url = f"findwork://{job_id}"
                            else:
                                hash_input = (
                                    f"{item.get('role')}|"
                                    f"{item.get('company_name')}|"
                                    f"{item.get('date_posted')}"
                                )
                                url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                                url = f"findwork://{url_hash}"

                        if url in seen_urls:
                            continue
                        seen_urls.add(url)

                        title = item.get("role") or ""
                        company = item.get("company_name") or ""
                        location = item.get("location") or ""
                        description = item.get("text") or ""
                        date_posted = item.get("date_posted") or ""
                        posted_date = self._parse_date(date_posted)
                        country = self._extract_country(location)
                        is_remote = item.get("remote", False)
                        remote_type = "Remote" if is_remote else "On-site"

                        results.append(
                            JobRaw(
                                source_id=self.source_id,
                                source_name="Findwork",
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

                    logger.debug(
                        "[findwork] Keyword '%s' page %d: collected %d new jobs",
                        keyword, page_count, new_jobs,
                    )

                    next_url = data.get("next")
                    if not next_url:
                        logger.debug("[findwork] No more pages for keyword=%s", keyword)
                        break

                except (ValueError, KeyError) as e:
                    logger.error(
                        "[findwork] Data parsing error for keyword=%s page=%d: %s",
                        keyword, page_count, e,
                    )
                    break
                except Exception as e:
                    logger.error(
                        "[findwork] Unexpected error for keyword=%s page=%d: %s",
                        keyword, page_count, e, exc_info=True,
                    )
                    break

        logger.info(
            "[findwork] Collected %d raw jobs for market '%s'",
            len(results), market.get("market_id"),
        )
        return results[:max_jobs]

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    def _request_with_retries(
        self,
        url: str,
        headers: dict,
        params: dict | None,
        keyword: str,
        page: int,
        max_attempts: int = 3,
    ):
        """
        Make a GET request with retries for transient errors.

        Returns:
            - requests.Response on success
            - 429 (int) on rate-limit response
            - 401 (int) on auth failure
            - None on exhausted retries / unrecoverable error
        """
        for attempt in range(max_attempts):
            if attempt > 0:
                backoff = 2 ** attempt          # 2s, 4s
                logger.debug(
                    "[findwork] Retry %d/%d for keyword=%s page=%d (backoff=%ds)",
                    attempt + 1, max_attempts, keyword, page, backoff,
                )
                time.sleep(backoff)

            try:
                resp = requests.get(
                    url,
                    params=params if attempt == 0 else None,  # params only on first attempt
                    headers=headers,
                    timeout=_TIMEOUT,
                )

                if resp.status_code == 429:
                    return 429          # Signal caller to handle rate-limit

                if resp.status_code == 401:
                    return 401          # Signal caller to abort

                if resp.status_code >= 500:
                    if attempt < max_attempts - 1:
                        logger.warning(
                            "[findwork] HTTP %d for keyword=%s page=%d, will retry",
                            resp.status_code, keyword, page,
                        )
                        continue
                    logger.warning(
                        "[findwork] HTTP %d for keyword=%s page=%d, no more retries",
                        resp.status_code, keyword, page,
                    )
                    return None

                return resp             # 2xx / 3xx / 4xx (non-429/401)

            except requests.Timeout:
                if attempt < max_attempts - 1:
                    logger.warning("[findwork] Timeout keyword=%s page=%d, will retry", keyword, page)
                    continue
                logger.warning("[findwork] Timeout keyword=%s page=%d, no more retries", keyword, page)
                return None

            except requests.RequestException as exc:
                if attempt < max_attempts - 1:
                    logger.warning(
                        "[findwork] Request error keyword=%s page=%d: %s, will retry", keyword, page, exc
                    )
                    continue
                logger.warning(
                    "[findwork] Request error keyword=%s page=%d: %s, no more retries", keyword, page, exc
                )
                return None

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_country(self, location: str) -> str:
        """
        Extract country from location string via the shared country-
        inference helper (src.utils.country_inference.infer_country)
        instead of the previous inline comma-split, which fell through to
        returning the raw trailing fragment verbatim (e.g. "MA" for
        "Boston, MA") whenever it didn't match one of a handful of
        hardcoded country names. infer_country's keyword table and
        US-state-abbreviation lookup cover this correctly instead.
        """
        return infer_country(location)

    def _parse_date(self, date_str: str) -> str:
        """Parse ISO date to YYYY-MM-DD."""
        if not date_str:
            return ""
        try:
            return date_str.split("T")[0]
        except Exception:
            return ""