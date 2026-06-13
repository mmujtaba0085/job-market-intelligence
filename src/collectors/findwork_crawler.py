"""
src/collectors/findwork_crawler.py
───────────────────────────────────
Continuous full-catalogue crawler for Findwork API.

Unlike FindworkCollector (keyword-based), this crawler:
- Paginates through the entire Findwork catalogue page by page
- Persists state to resume from last position across restarts
- Handles 429 rate limits gracefully with Retry-After headers
- Flags jobs as "relevant" based on crawl_keywords matching
- Runs continuously until killed (CTRL+C / SIGTERM)

Usage:
    python -m src.orchestrator --mode crawl

State file:
    data/findwork_crawler_state.json (configurable via FINDWORK_CRAWLER_STATE_FILE env var)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.collectors.base_collector import BaseCollector
from src.normalizer import normalize_batch
from src.deduplicator import deduplicate_and_store
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://findwork.dev/api/jobs/"
_TIMEOUT = 15
_RATE_LIMIT = 55        # Stay under 60 req/min
_RATE_WINDOW = 60.0     # Sliding window in seconds
_BATCH_SIZE = 100       # Save to DB every N jobs
_SAVE_INTERVAL = 300    # Also save every 5 minutes
_LOG_INTERVAL = 10      # Log progress every N pages
_DUPLICATE_THRESHOLD = 10  # Stop after N consecutive all-duplicate pages

# Global stop event for graceful shutdown
stop_event = threading.Event()


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle CTRL+C and SIGTERM gracefully."""
    logger.info("[findwork_crawler] Shutdown signal received, finishing current batch...")
    stop_event.set()


# Register signal handlers
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


class FindworkCrawler(BaseCollector):
    """Continuous full-catalogue crawler for Findwork API."""
    
    source_id = "findwork_crawl"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # State file path (configurable via env var)
        default_state_file = "data/findwork_crawler_state.json"
        state_path = os.getenv("FINDWORK_CRAWLER_STATE_FILE", default_state_file)
        self.state_file = Path(state_path)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Sliding-window rate limiting
        self._request_timestamps: list[float] = []
        
        # Session tracking
        self.session_start = time.time()
        self.jobs_this_session = 0
        self.relevant_this_session = 0
        self.last_save_time = time.time()

    # ────────────────────────────────────────────────────────────────
    # State Management
    # ────────────────────────────────────────────────────────────────

    def _load_state(self) -> dict[str, Any]:
        """Load crawler state from disk."""
        if not self.state_file.exists():
            return {
                "last_run_timestamp": None,
                "total_jobs_collected": 0,
                "relevant_jobs_flagged": 0,
                "total_runs": 0,
            }
        
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[findwork_crawler] Failed to load state, starting fresh: %s", exc)
            return {
                "last_run_timestamp": None,
                "total_jobs_collected": 0,
                "relevant_jobs_flagged": 0,
                "total_runs": 0,
            }

    def _save_state(self, state: dict[str, Any]) -> None:
        """Save crawler state to disk."""
        try:
            state["last_run_timestamp"] = datetime.now(timezone.utc).isoformat()
            self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            logger.debug("[findwork_crawler] State saved: page %d", state.get("last_completed_page", 0))
        except Exception as exc:
            logger.error("[findwork_crawler] Failed to save state: %s", exc)

    # ────────────────────────────────────────────────────────────────
    # Rate Limiting (same pattern as FindworkCollector)
    # ────────────────────────────────────────────────────────────────

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
                    "[findwork_crawler] Rate limit approached (%d/%d reqs), sleeping %.2fs",
                    len(self._request_timestamps), _RATE_LIMIT, sleep_for,
                )
                time.sleep(sleep_for)
        self._request_timestamps.append(time.monotonic())

    # ────────────────────────────────────────────────────────────────
    # Relevance Filtering
    # ────────────────────────────────────────────────────────────────

    def _is_relevant(self, job_data: dict, crawl_keywords: list[str]) -> bool:
        """
        Check if job is relevant based on crawl_keywords.
        Matches against: role field + keywords array (case-insensitive substring).
        """
        if not crawl_keywords:
            return False
        
        # Extract fields to search
        role = (job_data.get("role") or "").lower()
        job_keywords = [k.lower() for k in job_data.get("keywords", [])]
        
        # Check if any crawl_keyword matches role or job keywords
        for keyword in crawl_keywords:
            keyword_lower = keyword.lower()
            
            # Check role field
            if keyword_lower in role:
                return True
            
            # Check keywords array
            if any(keyword_lower in jk for jk in job_keywords):
                return True
        
        return False

    # ────────────────────────────────────────────────────────────────
    # Utility Methods
    # ────────────────────────────────────────────────────────────────

    def _parse_date(self, date_str: str) -> str:
        """Parse ISO date to YYYY-MM-DD."""
        if not date_str:
            return ""
        try:
            return date_str.split("T")[0]
        except Exception:
            return ""

    # ────────────────────────────────────────────────────────────────
    # Job Parsing
    # ────────────────────────────────────────────────────────────────

    def _parse_job(self, item: dict, is_relevant: bool) -> JobRaw:
        """Convert API response item to JobRaw."""
        # Same parsing logic as FindworkCollector
        url = item.get("url") or ""
        job_id = item.get("id") or ""
        
        if not url:
            if job_id:
                url = f"findwork://{job_id}"
            else:
                hash_input = f"{item.get('role')}|{item.get('company_name')}|{item.get('date_posted')}"
                url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                url = f"findwork://{url_hash}"
        
        title = item.get("role") or ""
        company = item.get("company_name") or ""
        location = item.get("location") or ""
        description = item.get("text") or ""
        date_posted = item.get("date_posted") or ""
        posted_date = self._parse_date(date_posted)
        
        # Extract country from location (simple heuristic)
        country = "Unknown"
        if location:
            # Very basic country extraction - could be improved
            if "United States" in location or "USA" in location or ", " in location:
                parts = location.split(",")
                if len(parts) >= 2:
                    country = parts[-1].strip()
        
        # Determine remote type
        is_remote = item.get("remote", False)
        remote_type = "Remote" if is_remote else "On-site"
        
        return JobRaw(
            source_id=self.source_id,
            source_name="Findwork Crawler",
            url=url,
            fetched_at=datetime.now(timezone.utc),
            raw_json=item,
            parsed_fields={
                "title": title,
                "company": company,
                "location": location,
                "country": country,
                "remote_type": remote_type,
                "posted_date": posted_date,
                "description": description,
                "relevant": is_relevant,  # Custom flag for crawler
            },
        )

    # ────────────────────────────────────────────────────────────────
    # Page Fetching
    # ────────────────────────────────────────────────────────────────

    def _fetch_page(self, page: int, api_key: str) -> tuple[list[dict], str | None]:
        """
        Fetch one page from Findwork API.
        Returns: (list of job dicts, next_url or None)
        Handles 429 with Retry-After header.
        """
        headers = {
            "Authorization": f"Token {api_key}",
            "Accept": "application/json",
            "User-Agent": "JobMarketIntel/1.0",
        }
        
        url = f"{_BASE_URL}?page={page}" if page > 1 else _BASE_URL
        
        # Retry loop for network errors and 5xx
        for attempt in range(3):
            if stop_event.is_set():
                return [], None
            
            try:
                if attempt > 0:
                    backoff = 2 * attempt
                    logger.debug("[findwork_crawler] Retry attempt %d/3 (sleeping %ds)", attempt + 1, backoff)
                    time.sleep(backoff)
                
                self._throttle()
                resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
                
                # Handle 429 rate limiting
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning("[findwork_crawler] Rate limited (429), sleeping %ds", retry_after)
                    
                    # Interruptible sleep
                    for _ in range(retry_after):
                        if stop_event.is_set():
                            return [], None
                        time.sleep(1)
                    
                    # Retry after waiting
                    continue
                
                # Handle 5xx errors
                if resp.status_code >= 500:
                    if attempt < 2:
                        logger.warning("[findwork_crawler] HTTP %d, will retry", resp.status_code)
                        continue
                    else:
                        logger.error("[findwork_crawler] HTTP %d, no more retries", resp.status_code)
                        return [], None
                
                # Handle auth errors
                if resp.status_code == 401:
                    logger.error("[findwork_crawler] Authentication failed (401) - check FINDWORK_API_KEY")
                    stop_event.set()  # Stop crawler on auth failure
                    return [], None
                
                # Handle other errors
                if resp.status_code != 200:
                    logger.warning("[findwork_crawler] HTTP %d for page %d", resp.status_code, page)
                    return [], None
                
                # Parse response
                data = resp.json()
                jobs = data.get("results", [])
                next_url = data.get("next")
                
                return jobs, next_url
                
            except requests.Timeout:
                if attempt < 2:
                    logger.warning("[findwork_crawler] Timeout, will retry")
                    continue
                else:
                    logger.error("[findwork_crawler] Timeout, no more retries")
                    return [], None
            
            except requests.RequestException as exc:
                if attempt < 2:
                    logger.warning("[findwork_crawler] Request error: %s, will retry", exc)
                    continue
                else:
                    logger.error("[findwork_crawler] Request error: %s, no more retries", exc)
                    return [], None
        
        return [], None

    # ────────────────────────────────────────────────────────────────
    # Database Saving
    # ────────────────────────────────────────────────────────────────

    def _save_jobs_to_db(self, jobs: list[JobRaw], market_id: str) -> tuple[int, int, int]:
        """
        Save jobs to database via normalize + deduplicate pipeline.
        Returns: (inserted_count, deduped_count, total_processed)
        """
        if not jobs:
            return 0, 0, 0
        
        try:
            # Normalize jobs
            normalized = normalize_batch(jobs, market_id)
            logger.debug("[findwork_crawler] Normalized %d/%d jobs", len(normalized), len(jobs))
            
            # Deduplicate and store
            results, summary = deduplicate_and_store(normalized)
            inserted = summary.inserted
            deduped = summary.url_dups + summary.canonical_dups
            total = len(normalized)
            
            logger.info("[findwork_crawler] Saved to DB: %d inserted, %d deduped", inserted, deduped)
            return inserted, deduped, total
            
        except Exception as exc:
            logger.error("[findwork_crawler] Failed to save jobs to DB: %s", exc, exc_info=True)
            return 0, 0, 0

    # ────────────────────────────────────────────────────────────────
    # Main Crawler Loop
    # ────────────────────────────────────────────────────────────────

    def crawl_forever(self, market: dict, max_runtime_seconds: int | None = None) -> None:
        """
        Continuous crawler that starts from page 1 each run.
        Stops when it encounters jobs already collected in previous run,
        when stop_event is set (CTRL+C / SIGTERM), or when max_runtime_seconds elapses.
        """
        api_key = os.getenv("FINDWORK_API_KEY")
        if not api_key:
            logger.error("[findwork_crawler] Missing FINDWORK_API_KEY in environment")
            return

        crawl_keywords = market.get("crawl_keywords", [])
        market_id = market.get("market_id", "unknown")

        if not crawl_keywords:
            logger.warning("[findwork_crawler] No crawl_keywords defined in market config")

        if max_runtime_seconds:
            logger.info(
                "[findwork_crawler] Starting crawler (max runtime: %dm)",
                max_runtime_seconds // 60,
            )
        else:
            logger.info("[findwork_crawler] Starting crawler from page 1 (CTRL+C to stop)")
        logger.info("[findwork_crawler] Market: %s", market_id)
        logger.info("[findwork_crawler] Crawl keywords: %s", crawl_keywords)
        logger.info("[findwork_crawler] Strategy: Stop when encountering previously collected jobs")
        
        # Load state (for statistics only, not pagination)
        state = self._load_state()
        current_page = 1  # Always start from page 1
        
        jobs_buffer: list[JobRaw] = []
        consecutive_duplicate_pages = 0
        total_new_jobs_this_run = 0
        
        while not stop_event.is_set():
            # Stop if we've exceeded the time budget
            if max_runtime_seconds and (time.time() - self.session_start) >= max_runtime_seconds:
                logger.info(
                    "[findwork_crawler] Max runtime (%dm) reached at page %d, stopping",
                    max_runtime_seconds // 60, current_page,
                )
                break

            # Fetch one page
            logger.debug("[findwork_crawler] Fetching page %d...", current_page)
            page_jobs, next_url = self._fetch_page(current_page, api_key)
            
            if stop_event.is_set():
                break
            
            # Check if we've reached the end
            if not next_url or not page_jobs:
                logger.info("[findwork_crawler] Reached end of catalogue at page %d", current_page)
                break
            
            # Process jobs for this page
            page_buffer: list[JobRaw] = []
            for job_data in page_jobs:
                is_relevant = self._is_relevant(job_data, crawl_keywords)
                job_raw = self._parse_job(job_data, is_relevant)
                page_buffer.append(job_raw)
                
                if is_relevant:
                    self.relevant_this_session += 1
            
            self.jobs_this_session += len(page_jobs)
            
            # Save this page to check for duplicates
            if page_buffer:
                inserted, deduped, total = self._save_jobs_to_db(page_buffer, market_id)
                
                # Check if all jobs on this page were duplicates
                if inserted == 0 and total > 0:
                    consecutive_duplicate_pages += 1
                    logger.info(
                        "[findwork_crawler] Page %d: All %d jobs were duplicates (consecutive: %d/%d)",
                        current_page, total, consecutive_duplicate_pages, _DUPLICATE_THRESHOLD
                    )
                    
                    # Stop if we've hit too many consecutive duplicate pages
                    if consecutive_duplicate_pages >= _DUPLICATE_THRESHOLD:
                        logger.info(
                            "[findwork_crawler] Stopping: %d consecutive pages with all duplicates. "
                            "Already have recent data from previous run.",
                            consecutive_duplicate_pages
                        )
                        break
                else:
                    # Reset counter if we found new jobs
                    consecutive_duplicate_pages = 0
                    total_new_jobs_this_run += inserted
                    
                    # Add to session buffer for state tracking
                    jobs_buffer.extend(page_buffer)
            
            # Log progress every N pages
            if current_page % _LOG_INTERVAL == 0:
                uptime_hours = (time.time() - self.session_start) / 3600
                logger.info(
                    "[findwork_crawler] Page %d: %d new jobs this run | Session: %d total, %.1fh uptime",
                    current_page, total_new_jobs_this_run, self.jobs_this_session, uptime_hours
                )
            
            # Save state periodically
            if time.time() - self.last_save_time >= _SAVE_INTERVAL:
                relevant_count = sum(1 for j in jobs_buffer if j.parsed_fields.get("relevant"))
                state["total_jobs_collected"] = state.get("total_jobs_collected", 0) + len(jobs_buffer)
                state["relevant_jobs_flagged"] = state.get("relevant_jobs_flagged", 0) + relevant_count
                self._save_state(state)
                
                jobs_buffer = []  # Clear after saving state
                self.last_save_time = time.time()
            
            current_page += 1
        
        # Update final state
        if jobs_buffer:
            relevant_count = sum(1 for j in jobs_buffer if j.parsed_fields.get("relevant"))
            state["total_jobs_collected"] = state.get("total_jobs_collected", 0) + len(jobs_buffer)
            state["relevant_jobs_flagged"] = state.get("relevant_jobs_flagged", 0) + relevant_count
        
        state["total_runs"] = state.get("total_runs", 0) + 1
        self._save_state(state)
        
        logger.info(
            "[findwork_crawler] Run completed. New jobs: %d | Total processed: %d (%d relevant)",
            total_new_jobs_this_run, self.jobs_this_session, self.relevant_this_session
        )

    # ────────────────────────────────────────────────────────────────
    # BaseCollector Interface (not used in crawl mode)
    # ────────────────────────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Not used in crawl mode - use crawl_forever() instead."""
        logger.warning("[findwork_crawler] _fetch_raw() called but crawler uses crawl_forever()")
        return []
