"""
src/collectors/pakistanjobsbank_collector.py
─────────────────────────────────────────────
Collector for Pakistan Jobs Bank (pakistanjobsbank.com) — a public archive of
Pakistani newspaper job classifieds (Jang, Dawn, Express, Nawa-i-Waqt,
The News, The Nation).

No API is exposed; jobs are scraped from date-archive pages:
    https://www.pakistanjobsbank.com/Jobs-in-Pakistan/YYYY-MM-DD/
Each date page lists every ad published that day, across all newspapers,
directly in <tr class="job-ad"> rows — individual job detail pages don't need
to be fetched to get title/newspaper/location/positions.

Crawl strategy (persisted to data/pakistanjobsbank_state.json so it survives
restarts and spreads across many daily runs):
  - Phase 1 (backfill): walk backward day-by-day from the oldest date crawled
    so far, until _CONSECUTIVE_404_THRESHOLD consecutive HTTP 404s confirm the
    start of the archive has been reached.
  - Phase 2 (incremental): once backfill is complete, walk forward from the
    newest date crawled so far up to today, picking up new daily postings.
  - Each call advances by at most _MAX_DATES_PER_RUN dates so a single run
    (invoked daily via `--mode ingest-only`) stays bounded; the rest of the
    history is picked up automatically on subsequent runs.

This source deliberately covers every job category (government, banking,
medical, teaching, driving, ...), not just tech — see market
"pakistan_jobs_all" in config/markets.py, which restricts itself to just this
source via "source_allowlist" so it doesn't get mixed with the global tech
markets or invoke unrelated collectors.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.pakistanjobsbank.com"
_TIMEOUT = 15
_MAX_DATES_PER_RUN = 200
_CONSECUTIVE_404_THRESHOLD = 30
_EARLIEST_SAFETY_FLOOR = date(2010, 1, 1)  # hard stop in case 404 detection ever misfires
_STATE_FILE = Path("data/pakistanjobsbank_state.json")
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

_COMPANY_JOBS_IN_RE = re.compile(
    r"\bJobs?\s+in\s+(.+?)(?:\s+\d{4}\b|\s+(?:January|February|March|April|May|"
    r"June|July|August|September|October|November|December)\b|\s+Apply\b|\s+Latest\b|$)",
    re.IGNORECASE,
)
_COMPANY_LEADING_RE = re.compile(r"^(.*?)\s+Jobs?\b", re.IGNORECASE)


class PakistanJobsBankCollector(BaseCollector):
    source_id = "pakistanjobsbank"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── State ────────────────────────────────────────────────────────────────

    def _default_state(self) -> dict:
        return {
            "backfill_complete": False,
            "oldest_date_crawled": None,   # ISO date string; frontier walking backward
            "newest_date_crawled": None,   # ISO date string; frontier walking forward
            "consecutive_404": 0,
            "total_jobs_collected": 0,
            "total_runs": 0,
        }

    def _load_state(self) -> dict:
        if not _STATE_FILE.exists():
            return self._default_state()
        try:
            state = self._default_state()
            state.update(json.loads(_STATE_FILE.read_text(encoding="utf-8")))
            return state
        except Exception as exc:
            logger.warning("[pakistanjobsbank] Failed to load state, starting fresh: %s", exc)
            return self._default_state()

    def _save_state(self, state: dict) -> None:
        try:
            state["last_run_timestamp"] = datetime.now(timezone.utc).isoformat()
            _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("[pakistanjobsbank] Failed to save state: %s", exc)

    # ── Fetch + parse one date-archive page ─────────────────────────────────

    def _fetch_date_page(self, day: date) -> tuple[int, list[JobRaw]]:
        """Returns (http_status, jobs). http_status 0 = network error."""
        url = f"{_BASE_URL}/Jobs-in-Pakistan/{day.isoformat()}/"
        try:
            resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("[pakistanjobsbank] Request error for %s: %s", day, exc)
            return 0, []

        if resp.status_code == 404:
            return 404, []
        if resp.status_code != 200:
            logger.warning("[pakistanjobsbank] HTTP %d for %s", resp.status_code, day)
            return resp.status_code, []

        return 200, self._parse_date_page(resp.text, day)

    def _parse_date_page(self, html: str, day: date) -> list[JobRaw]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[JobRaw] = []

        for row in soup.select("tr.job-ad"):
            cells = row.find_all("td")
            if not cells:
                continue

            first_cell = cells[0]
            divs = first_cell.find_all("div", recursive=False)
            anchor = first_cell.find("a", href=True)
            if not anchor:
                continue

            title = anchor.get_text(strip=True)
            if not title:
                continue
            href = anchor["href"]
            url = href if href.startswith("http") else f"{_BASE_URL}{href}"

            # First div looks like: "05-Jul-2026 (Sunday) - Nawa-i-Waqt"
            newspaper = ""
            if divs:
                header_text = divs[0].get_text(" ", strip=True)
                if " - " in header_text:
                    newspaper = header_text.rsplit(" - ", 1)[-1].strip()

            # Second div looks like: "in Sahiwal, Punjab"
            location = ""
            if len(divs) > 1:
                loc_text = divs[1].get_text(" ", strip=True)
                location = re.sub(r"^in\s+", "", loc_text, flags=re.IGNORECASE).strip()

            positions: list[str] = []
            if len(cells) > 1:
                for li in cells[1].select("ul.Positions li"):
                    text = li.get_text(strip=True)
                    if text and not (text.startswith("===") and text.endswith("===")):
                        positions.append(text)

            company = self._extract_company(title)
            description_parts = []
            if positions:
                description_parts.append("Positions: " + ", ".join(positions))
            if location:
                description_parts.append(f"Location: {location}")
            if newspaper:
                description_parts.append(f"Published in {newspaper} on {day.isoformat()}")
            description = ". ".join(description_parts) or title

            jobs.append(
                JobRaw(
                    source_id=self.source_id,
                    source_name="Pakistan Jobs Bank",
                    url=url,
                    fetched_at=self._now(),
                    raw_json={
                        "title": title,
                        "newspaper": newspaper,
                        "location": location,
                        "positions": positions,
                        "ad_date": day.isoformat(),
                    },
                    parsed_fields={
                        "title": title,
                        "company": company,
                        "location": location,
                        "country": "Pakistan",
                        "remote_type": "on-site",
                        "posted_date": day.isoformat(),
                        "description": description,
                    },
                )
            )

        return jobs

    def _extract_company(self, title: str) -> str:
        """
        Best-effort org-name extraction from the ad title. Date-archive pages
        don't expose a separate company field (only individual job detail
        pages do, which aren't fetched here to keep backfill to one request
        per date), so this relies on common title phrasing:
        "<Role> Jobs in <Org> ..." or "<Org> Jobs <Month> <Year> ...".
        """
        m = _COMPANY_JOBS_IN_RE.search(title)
        if m:
            return m.group(1).strip(" -,")

        m = _COMPANY_LEADING_RE.match(title)
        if m:
            candidate = m.group(1).strip(" -,")
            if candidate and len(candidate) < 80:
                return candidate

        return ""

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        state = self._load_state()
        max_jobs = market.get("max_jobs_per_source")
        results: list[JobRaw] = []
        dates_crawled = 0

        if not state["backfill_complete"]:
            cursor = (
                date.fromisoformat(state["oldest_date_crawled"]) - timedelta(days=1)
                if state["oldest_date_crawled"]
                else date.today()
            )

            while dates_crawled < _MAX_DATES_PER_RUN and cursor >= _EARLIEST_SAFETY_FLOOR:
                self._wait()
                status, jobs = self._fetch_date_page(cursor)
                dates_crawled += 1

                if status == 404:
                    state["consecutive_404"] = state.get("consecutive_404", 0) + 1
                else:
                    state["consecutive_404"] = 0
                    state["oldest_date_crawled"] = cursor.isoformat()
                    if not state.get("newest_date_crawled"):
                        state["newest_date_crawled"] = cursor.isoformat()
                    results.extend(jobs)

                if state["consecutive_404"] >= _CONSECUTIVE_404_THRESHOLD:
                    state["backfill_complete"] = True
                    logger.info(
                        "[pakistanjobsbank] Backfill complete: reached archive start near %s",
                        cursor,
                    )
                    break

                cursor -= timedelta(days=1)

        else:
            cursor = date.fromisoformat(state["newest_date_crawled"]) + timedelta(days=1)
            today = date.today()

            while dates_crawled < _MAX_DATES_PER_RUN and cursor <= today:
                self._wait()
                status, jobs = self._fetch_date_page(cursor)
                dates_crawled += 1

                if status == 404:
                    # Not yet published for that date — stop advancing for now.
                    break

                state["newest_date_crawled"] = cursor.isoformat()
                results.extend(jobs)
                cursor += timedelta(days=1)

        if max_jobs is not None and len(results) > max_jobs:
            results = results[:max_jobs]

        state["total_jobs_collected"] = state.get("total_jobs_collected", 0) + len(results)
        state["total_runs"] = state.get("total_runs", 0) + 1
        self._save_state(state)

        logger.info(
            "[pakistanjobsbank] %s: crawled %d date page(s), collected %d jobs (backfill_complete=%s)",
            market.get("market_id"), dates_crawled, len(results), state["backfill_complete"],
        )
        return results
