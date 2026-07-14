"""
src/collectors/pakistanjobsbank_collector.py
─────────────────────────────────────────────
Collector for Pakistan Jobs Bank (pakistanjobsbank.com) — a public archive of
Pakistani newspaper job classifieds (Jang, Dawn, Express, Nawa-i-Waqt,
The News, The Nation).

No API is exposed; jobs are scraped from date-archive pages:
    https://www.pakistanjobsbank.com/Jobs-in-Pakistan/YYYY-MM-DD/
Each date page lists every ad published that day, across all newspapers,
directly in <tr class="job-ad"> rows — title/newspaper/location/positions
all come from that one page, no per-ad request needed for those fields.

Two things aren't on the date-archive page and do need one request per ad
(not per position — a single ad can list several positions, e.g. "Control
Room Operator" + "Mali" from the same ad): the ad is a scanned newspaper
clipping (an image, #Contents_AdImage on the ad's own detail page), and
some ads separately include an external "how to apply" link (inside
td.job-information on that same detail page) — see _fetch_ad_detail().
Both are attached identically to every position parsed from that ad.

Crawl strategy (persisted to data/pakistanjobsbank_state.json so it survives
restarts and spreads across many daily runs). Two frontiers advance every
call, so recent postings are never stuck waiting behind the historical
backfill:
  - Forward frontier (checked first, every call): walks forward from the
    newest date crawled so far up to today, picking up new daily postings.
    Cheap in steady state — usually just the single new day since the last
    run.
  - Backward frontier (uses whatever budget is left): walks backward
    day-by-day from the oldest date crawled so far, until either
    _BACKFILL_WINDOW_DAYS of history has been covered or
    _CONSECUTIVE_404_THRESHOLD consecutive HTTP 404s confirm the archive's
    start has been reached first (only relevant if the window is ever
    widened past the site's actual history). Once either condition hits,
    "backfill_complete" is set and this frontier stops advancing.
  - Each call processes at most _MAX_DATES_PER_RUN dates total (across both
    frontiers) so a single run (invoked daily via `--mode ingest-only`)
    stays bounded.

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
_BACKFILL_WINDOW_DAYS = 270  # ~9 months of history; older ads are past relevance for this market
_CONSECUTIVE_404_THRESHOLD = 30
_RECENT_RECHECK_DAYS = 14  # see _fetch_raw's recheck pass docstring below
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

    def _fetch_ad_detail(self, ad_url: str) -> tuple[str | None, str | None]:
        """
        Visit one ad's own detail page for the two things the date-archive
        listing doesn't expose: the scanned ad image, and an external "how
        to apply" link (present only on some ads — the rest are applied to
        via whatever's written inside the image itself, e.g. an email or a
        government portal). Returns (ad_image_url, apply_url); either may
        be None on a failed fetch or when the source simply doesn't have it.
        """
        try:
            resp = requests.get(ad_url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[pakistanjobsbank] Failed to fetch ad detail %s: %s", ad_url, exc)
            return None, None

        soup = BeautifulSoup(resp.text, "html.parser")

        ad_image_url = None
        img = soup.select_one("#Contents_AdImage")
        if img and img.get("src"):
            src = img["src"]
            ad_image_url = src if src.startswith("http") else f"{_BASE_URL}{src}"

        # The "how to apply" link (when present) lives in a dedicated text
        # block, separate from the share-icon links elsewhere on the page.
        apply_url = None
        info_block = soup.select_one("td.job-information")
        if info_block:
            for a in info_block.select("a[href]"):
                href = a["href"]
                if href.startswith("http") and _BASE_URL not in href:
                    apply_url = href
                    break

        return ad_image_url, apply_url

    def _parse_date_page(self, html: str, day: date) -> list[JobRaw]:
        """
        One newspaper ad often advertises several distinct roles at once (e.g.
        one Bureau Veritas ad listing 20 different engineering positions). To
        keep title/skill analytics meaningful, each position becomes its own
        JobRaw (its own title, sharing the ad's company/location/newspaper/
        date) instead of collapsing every role in the ad into one record whose
        title is just the ad's marketing headline.
        """
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

            ad_title = anchor.get_text(strip=True)
            if not ad_title:
                continue
            href = anchor["href"]
            ad_url = href if href.startswith("http") else f"{_BASE_URL}{href}"

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

            company = self._extract_company(ad_title)

            # One title per distinct position; ads with no positions list
            # (rare) fall back to the ad's own headline as a single job.
            titles = [self._clean_position_title(p) for p in positions] or [ad_title]
            split = len(titles) > 1

            description_parts = [f"Ad: {ad_title}"]
            if location:
                description_parts.append(f"Location: {location}")
            description = ". ".join(description_parts)

            self._wait()
            ad_image_url, apply_url = self._fetch_ad_detail(ad_url)

            for i, position_title in enumerate(titles, start=1):
                # The ad page is the only URL available for every position in
                # it; a fragment keeps each position's url_hash unique so the
                # dedup layer doesn't collapse them into a single row.
                url = f"{ad_url}#pos-{i}" if split else ad_url

                jobs.append(
                    JobRaw(
                        source_id=self.source_id,
                        source_name="Pakistan Jobs Bank",
                        url=url,
                        fetched_at=self._now(),
                        raw_json={
                            "ad_title": ad_title,
                            "newspaper": newspaper,
                            "location": location,
                            "positions": positions,
                            "ad_date": day.isoformat(),
                            "ad_image_url": ad_image_url,
                            "apply_url": apply_url,
                        },
                        parsed_fields={
                            "title": position_title,
                            "company": company,
                            "location": location,
                            "country": "Pakistan",
                            "remote_type": "on-site",
                            "posted_date": day.isoformat(),
                            "description": description,
                            "newspaper": newspaper,
                            "ad_image_url": ad_image_url,
                            "apply_url": apply_url,
                        },
                    )
                )

        return jobs

    _LEADING_COUNT_RE = re.compile(r"^\d+\s*[-–:]*\s*")

    def _clean_position_title(self, position: str) -> str:
        """Strip a leading headcount ("03 ", "1500 ") some positions carry."""
        cleaned = self._LEADING_COUNT_RE.sub("", position).strip()
        return cleaned or position

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
        budget = _MAX_DATES_PER_RUN

        # ── Forward frontier: always catch up on newly published dates first ──
        if state.get("newest_date_crawled"):
            cursor = date.fromisoformat(state["newest_date_crawled"]) + timedelta(days=1)
            today = date.today()

            while budget > 0 and cursor <= today:
                self._wait()
                status, jobs = self._fetch_date_page(cursor)
                dates_crawled += 1
                budget -= 1

                if status == 404:
                    # Not yet published for that date — stop advancing for now.
                    break

                state["newest_date_crawled"] = cursor.isoformat()
                results.extend(jobs)
                cursor += timedelta(days=1)

        # ── Backward frontier: bounded backfill within the recent window ──
        if not state["backfill_complete"] and budget > 0:
            floor_date = max(
                date.today() - timedelta(days=_BACKFILL_WINDOW_DAYS),
                _EARLIEST_SAFETY_FLOOR,
            )
            cursor = (
                date.fromisoformat(state["oldest_date_crawled"]) - timedelta(days=1)
                if state.get("oldest_date_crawled")
                else date.today()
            )

            while budget > 0 and cursor >= floor_date:
                self._wait()
                status, jobs = self._fetch_date_page(cursor)
                dates_crawled += 1
                budget -= 1

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
                        "[pakistanjobsbank] Backfill complete: hit %d consecutive 404s near %s",
                        _CONSECUTIVE_404_THRESHOLD, cursor,
                    )
                    break

                cursor -= timedelta(days=1)

            if (
                not state["backfill_complete"]
                and state.get("oldest_date_crawled")
                and date.fromisoformat(state["oldest_date_crawled"]) <= floor_date
            ):
                state["backfill_complete"] = True
                logger.info(
                    "[pakistanjobsbank] Backfill complete: reached %d-day window floor (%s)",
                    _BACKFILL_WINDOW_DAYS, floor_date,
                )

        # ── Recent recheck: some ads get added to their nominal date's page
        # days after that date's page first went live (confirmed directly
        # against production: a date the forward frontier had already
        # marked "crawled" with 0 jobs had 328 real jobs when independently
        # re-fetched days later). The forward frontier above only ever
        # visits a date once and permanently advances past it, and the
        # backward frontier that would otherwise eventually retry old dates
        # is disabled for good once backfill_complete=True - so without
        # this, a date checked before its content landed loses that content
        # forever. Re-fetching an unchanged date is harmless: the same jobs
        # just get deduplicated downstream by url_hash.
        if budget > 0 and state.get("newest_date_crawled"):
            newest = date.fromisoformat(state["newest_date_crawled"])
            recheck_floor = max(
                date.today() - timedelta(days=_RECENT_RECHECK_DAYS),
                _EARLIEST_SAFETY_FLOOR,
            )
            cursor = recheck_floor
            while budget > 0 and cursor <= newest:
                self._wait()
                status, jobs = self._fetch_date_page(cursor)
                dates_crawled += 1
                budget -= 1
                if status not in (404, 0) and jobs:
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
