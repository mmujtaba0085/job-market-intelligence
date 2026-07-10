"""
src/collectors/workday_collector.py
─────────────────────────────────────
Collector for two Pakistan-relevant employers running on Workday's CXS
("Candidate Experience Site") API — one direct, one proxied:

  - S&P Global (SPGI): calls Workday's own host directly.
      List:   POST https://spgi.wd5.myworkdayjobs.com/wday/cxs/spgi/SPGI_Careers/jobs
      Detail: GET  https://spgi.wd5.myworkdayjobs.com/wday/cxs/spgi/SPGI_Careers/job/<externalPath>
    S&P Global is a huge global company (239 total postings) - the list
    request applies a `Location_Country` facet filter for Pakistan
    (id "567ef1bd0cc84d4e83b98d0013008264", confirmed stable/hardcodable -
    Workday facet ids are internal UUIDs, not content that changes per
    crawl) so only the ~13 Pakistan-relevant postings are fetched, not the
    full 239.

  - Contour Software: same CXS JSON shape (title/externalPath/
    locationsText/postedOn on list; title/location/country/timeType/
    jobDescription on detail) but reached through the company's own proxy,
    not the myworkdayjobs.com host directly:
      List:   POST https://contour-software.com/service.php?slug=jobs
      Detail: GET  https://contour-software.com/service.php?slug=<urlencoded externalPath>
    Confirmed by direct inspection: identical JSON keys to SPGI's native
    endpoints, just served from Contour's own domain. Contour is a single
    Pakistan-based company (~108 postings total) so no facet filter is
    needed - every posting is relevant.

  Both detail responses include an `externalUrl` field - the real,
  publicly-browsable Workday URL (for Contour: their actual
  talentmanagementsolution.wd3.myworkdayjobs.com tenant, not
  contour-software.com - confirmed by inspection). Used directly as the
  JobRaw url rather than constructing one from the proxy domain.

Both share one `WorkdayCollectorBase` (list-fetch with offset pagination +
per-job detail fetch); concrete subclasses set the two endpoint URLs, an
optional `applied_facets` filter, and whether pagination is even necessary.

Location cleanup: Contour's `locationsText`/detail `location` carries a
cryptic Workday site-code prefix, e.g. "PER - Karachi, PK" (confirmed by
inspection - "PER" is an internal Workday location-group code, not part of
the real place name). `_strip_site_code()` strips any leading
"<2-5 uppercase letters> - " token. This is a no-op on SPGI's own locations
("Islamabad, PK"), which never carry such a prefix, so the same cleanup
function is safe to apply to both without a subclass override.

Multi-location postings: some jobs (both employers, confirmed on SPGI) list
one primary `location` plus an `additionalLocations` array on the detail
response (e.g. a Hyderabad-primary role that also lists "Islamabad, PK" as
an additional location) - these all feed `parsed_fields["all_locations"]`
(cleaned the same way as the primary location), which the existing
job_locations/location_count machinery in src/storage/db.py already
handles - no new storage plumbing needed.

posted_date: the list endpoint's `postedOn` is a relative/bucketed string
("Posted Today", "30+ Days Ago") and is not used for storage - the detail
endpoint's `startDate` (a real ISO date) is used instead.
"""

from __future__ import annotations

import logging
import re
import urllib.parse

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"
_PAGE_SIZE = 20

# Strips a leading internal Workday location-group code, e.g.
# "PER - Karachi, PK" -> "Karachi, PK". No-op on locations that don't carry
# one (e.g. SPGI's plain "Islamabad, PK").
_SITE_CODE_RE = re.compile(r"^[A-Z]{2,5}\s*-\s*")


class WorkdayCollectorBase(BaseCollector):
    """
    Shared fetch/parse logic for any Workday CXS job board (direct or
    proxied through a company's own domain). Concrete subclasses set
    `list_url`, `source_name`, `applied_facets` (dict passed as the POST
    body's "appliedFacets" - leave empty to fetch every posting unfiltered),
    and override `_build_detail_url()` since the two known integration
    styles build it differently:
      - Direct Workday host (SPGI): externalPath ("/job/City-PK/Title_123")
        is itself the URL path to append to the API's own base - it must
        NOT be percent-encoded (its slashes are real path separators).
      - Proxied through a company's own domain (Contour): externalPath is
        passed whole as a single query-string value ("?slug=<value>") and
        DOES need percent-encoding, since here its slashes are just
        characters inside one query value, not path separators.
    Getting these mixed up produces a doubled "/job/job/..." path and a
    guaranteed 400 from Workday - confirmed by hitting exactly that bug
    during testing before this split was added.
    """

    list_url: str = ""             # override in subclass
    source_name: str = ""          # override in subclass
    applied_facets: dict = {}      # override in subclass if filtering is needed

    def _build_detail_url(self, external_path: str) -> str:
        raise NotImplementedError

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _fetch_list_page(self, offset: int) -> dict | None:
        body = {
            "appliedFacets": self.applied_facets,
            "limit": _PAGE_SIZE,
            "offset": offset,
            "searchText": "",
        }
        try:
            resp = requests.post(
                self.list_url, json=body,
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("[%s] List request failed at offset %d: %s", self.source_id, offset, exc)
            return None

    def _fetch_detail(self, external_path: str) -> dict | None:
        url = self._build_detail_url(external_path)
        try:
            resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json().get("jobPostingInfo")
        except requests.RequestException as exc:
            logger.warning("[%s] Detail request failed for %s: %s", self.source_id, external_path, exc)
            return None
        except ValueError as exc:
            logger.warning("[%s] Invalid JSON detail for %s: %s", self.source_id, external_path, exc)
            return None

    # ── Location cleanup ─────────────────────────────────────────────────────

    def _clean_location(self, raw: str) -> str:
        return _SITE_CODE_RE.sub("", (raw or "").strip()).strip()

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, external_path: str) -> JobRaw | None:
        detail = self._fetch_detail(external_path)
        if not detail:
            return None

        title = (detail.get("title") or "").strip()
        description = (detail.get("jobDescription") or "").strip()
        url = (detail.get("externalUrl") or "").strip()
        if not title or not description or not url:
            return None

        location = self._clean_location(detail.get("location") or "")
        additional = [self._clean_location(loc) for loc in (detail.get("additionalLocations") or [])]
        all_locs = [location, *[a for a in additional if a]] if additional else None
        if all_locs:
            seen: set[str] = set()
            deduped = []
            for loc in all_locs:
                key = loc.lower()
                if loc and key not in seen:
                    seen.add(key)
                    deduped.append(loc)
            all_locs = deduped if len(deduped) > 1 else None

        country_descriptor = ((detail.get("country") or {}).get("descriptor") or "").strip()
        country = country_descriptor or infer_country(location)

        return JobRaw(
            source_id=self.source_id,
            source_name=self.source_name,
            url=url,
            fetched_at=self._now(),
            raw_json={
                "externalPath": external_path,
                "timeType": detail.get("timeType") or "",
                "remote": detail.get("remote"),
            },
            parsed_fields={
                "title": title,
                "company": self.source_name,
                "location": location,
                "all_locations": all_locs,
                "country": country,
                "remote_type": "remote" if detail.get("remote") else "unknown",
                "posted_date": detail.get("startDate") or "",
                "description": description,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        offset = 0
        total = None

        while total is None or offset < total:
            self._wait()
            page = self._fetch_list_page(offset)
            if not page:
                break

            total = page.get("total", 0)
            postings = page.get("jobPostings") or []
            if not postings:
                break

            for posting in postings:
                title = posting.get("title") or ""
                if keywords and not any(kw in title.lower() for kw in keywords):
                    continue

                external_path = posting.get("externalPath")
                if not external_path:
                    continue

                self._wait()
                built = self._build_job(external_path)
                if built:
                    results.append(built)

                if max_jobs is not None and len(results) >= max_jobs:
                    logger.info("[%s] Hit max_jobs_per_source cap (%d)", self.source_id, max_jobs)
                    return results

            offset += _PAGE_SIZE

        logger.info("[%s] Collected %d jobs (board total: %s)", self.source_id, len(results), total)
        return results


class SPGlobalCollector(WorkdayCollectorBase):
    source_id = "spglobal"
    source_name = "S&P Global"
    list_url = "https://spgi.wd5.myworkdayjobs.com/wday/cxs/spgi/SPGI_Careers/jobs"
    _detail_base = "https://spgi.wd5.myworkdayjobs.com/wday/cxs/spgi/SPGI_Careers"
    # "Location_Country" facet, value = Pakistan's Workday facet id (confirmed
    # by inspection against the live /jobs facets response - stable internal
    # UUID, not something that changes per crawl). Narrows a 239-job global
    # board down to the ~13 Pakistan-relevant postings.
    applied_facets = {"Location_Country": ["567ef1bd0cc84d4e83b98d0013008264"]}

    def _build_detail_url(self, external_path: str) -> str:
        # externalPath already starts with "/job/..." - it's a real URL
        # path, not a value to percent-encode. Simple concatenation.
        return f"{self._detail_base}{external_path}"


class ContourCollector(WorkdayCollectorBase):
    source_id = "contour"
    source_name = "Contour Software"
    list_url = "https://contour-software.com/service.php?slug=jobs"
    _detail_base = "https://contour-software.com/service.php?slug="
    # No facet filter - Contour is a single Pakistan-based company, every
    # posting on its board is relevant.
    applied_facets = {}

    def _build_detail_url(self, external_path: str) -> str:
        # Here externalPath is passed whole as one query-string value, so
        # it does need percent-encoding (confirmed working during testing).
        return f"{self._detail_base}{urllib.parse.quote(external_path, safe='')}"
