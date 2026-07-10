"""
src/collectors/workable_collector.py
─────────────────────────────────────
Collector for company career boards hosted on Workable's public applicant
widget (https://apply.workable.com/api/v1/widget/accounts/<account-slug>).

Two clean JSON endpoints per account, no auth, no HTML scraping:
  1. List:   /api/v1/widget/accounts/<slug>
             -> {"name": ..., "jobs": [{title, shortcode, code, department,
                url, application_url, published_on, employment_type,
                telecommuting, country, city, state, ...}, ...]}
             Note: despite some Workable docs calling the employment-type
             field "type", the *widget* list endpoint actually names it
             "employment_type" (confirmed by inspection on both accounts
             below) — the per-job detail endpoint separately uses "type".
             Neither is read into parsed_fields (normalizer.py has no
             employment-type field), so it's carried in raw_json only.
  2. Detail: /api/v1/accounts/<slug>/jobs/<shortcode>
             -> adds "description", "requirements", "benefits" (HTML
             strings) and "workplace" ("on_site"/"remote"/"hybrid"), which
             is more reliable than the list endpoint's "telecommuting"
             bool for remote_type. The three HTML sections are concatenated
             (with section headings for requirements/benefits) so the
             stored description matches what a candidate actually sees on
             the job page, rather than just the intro paragraph.

Location: built from the list endpoint's own city/state/country fields
(already clean, structured values — no comma-blob parsing needed like
10Pearls). city falls back to state when null; state is Workable's name
for what the account-detail JSON calls "region". Some accounts (e.g. PMCL)
post a job under a placeholder city like "Multiple/Nationwide" with the
state defaulting to a specific city ("Islamabad") — this is passed through
as-is; no attempt is made to guess a "real" city out of it.

One WorkableCollector base class implements the actual fetch/parse logic;
concrete subclasses at the bottom just set source_id / source_name /
account_slug for each account.
"""

from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw
from src.utils.country_inference import infer_country

logger = logging.getLogger(__name__)

_BASE_URL = "https://apply.workable.com"
_LIST_URL_TMPL = _BASE_URL + "/api/v1/widget/accounts/{slug}"
_DETAIL_URL_TMPL = _BASE_URL + "/api/v1/accounts/{slug}/jobs/{shortcode}"
_TIMEOUT = 15
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"

_WORKPLACE_TO_REMOTE_TYPE = {
    "remote": "remote",
    "on_site": "on-site",
    "hybrid": "hybrid",
}


class WorkableCollector(BaseCollector):
    """
    Shared fetch/parse logic for any company on Workable's public widget
    API. Concrete per-account subclasses only need to set source_id,
    source_name, and account_slug.
    """

    source_id: str = ""        # override in subclass
    source_name: str = ""      # override in subclass (display name)
    account_slug: str = ""     # override in subclass (Workable account slug)

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get_json(self, url: str) -> dict | None:
        try:
            resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("[%s] Request failed for %s: %s", self.source_id, url, exc)
            return None
        except ValueError as exc:  # JSON decode error
            logger.warning("[%s] Invalid JSON from %s: %s", self.source_id, url, exc)
            return None

    # ── Description assembly ────────────────────────────────────────────────

    def _clean_html(self, raw_html: str) -> str:
        """
        Keep real HTML structure (paragraphs, bullet lists, bold text)
        instead of flattening to plain text — job_detail.html renders
        raw_description via `| safe`, so real markup is what should be
        stored, matching how the other HTML-bearing collectors do it.
        """
        soup = BeautifulSoup(raw_html or "", "html.parser")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        for el in soup.find_all(["p", "li"]):
            if not el.get_text(strip=True):
                el.decompose()
        return str(soup).strip()

    def _build_description(self, detail: dict) -> str:
        """
        Concatenate description + requirements + benefits (each an HTML
        string on the detail endpoint) into one description, matching what
        a candidate sees scrolling down the real job page. Missing
        sections are simply omitted.
        """
        parts: list[str] = []

        description_html = (detail.get("description") or "").strip()
        if description_html:
            parts.append(description_html)

        requirements_html = (detail.get("requirements") or "").strip()
        if requirements_html:
            parts.append("<h3>Requirements</h3>" + requirements_html)

        benefits_html = (detail.get("benefits") or "").strip()
        if benefits_html:
            parts.append("<h3>Benefits</h3>" + benefits_html)

        return self._clean_html("".join(parts))

    # ── Location / remote-type ──────────────────────────────────────────────

    def _build_location(self, city: str | None, state: str | None, country: str | None) -> str:
        """
        Build "City, Country" from the list endpoint's own structured
        fields. Falls back to state (Workable's "region") when city is
        missing, and tolerates placeholder city strings (e.g.
        "Multiple/Nationwide") by simply passing them through — no
        guessing at a "real" city.
        """
        city = (city or "").strip()
        state = (state or "").strip()
        country = (country or "").strip()

        primary = city or state
        if primary and country and primary.lower() != country.lower():
            return f"{primary}, {country}"
        return primary or country

    def _remote_type(self, workplace: str | None, telecommuting: bool) -> str:
        if workplace:
            mapped = _WORKPLACE_TO_REMOTE_TYPE.get(str(workplace).strip().lower())
            if mapped:
                return mapped
        if telecommuting:
            return "remote"
        return "unknown"

    # ── Job assembly ─────────────────────────────────────────────────────────

    def _build_job(self, job: dict) -> JobRaw | None:
        shortcode = job.get("shortcode") or job.get("code")
        if not shortcode:
            logger.warning("[%s] Skipping job with no shortcode: %r", self.source_id, job.get("title"))
            return None

        self._wait()
        detail = self._get_json(_DETAIL_URL_TMPL.format(slug=self.account_slug, shortcode=shortcode))

        description = self._build_description(detail) if detail else ""
        if not description:
            logger.warning("[%s] No description for job %r (%s) — skipping.",
                            self.source_id, job.get("title"), shortcode)
            return None

        workplace = detail.get("workplace") if detail else None
        remote_type = self._remote_type(workplace, bool(job.get("telecommuting")))

        city = job.get("city")
        state = job.get("state")
        country_raw = (job.get("country") or "").strip()
        location = self._build_location(city, state, country_raw)
        country = country_raw or infer_country(location)

        url = job.get("url") or job.get("application_url") or job.get("shortlink") \
            or f"{_BASE_URL}/j/{shortcode}"

        return JobRaw(
            source_id=self.source_id,
            source_name=self.source_name,
            url=url,
            fetched_at=self._now(),
            raw_json={
                "shortcode": shortcode,
                "department": job.get("department") or "",
                "employment_type": job.get("employment_type") or "",
                "workplace": workplace or "",
            },
            parsed_fields={
                "title": (job.get("title") or "").strip(),
                "company": self.source_name,
                "location": location,
                "country": country,
                "remote_type": remote_type,
                "posted_date": job.get("published_on") or "",
                "description": description,
            },
        )

    # ── BaseCollector contract ──────────────────────────────────────────────

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        self._wait()
        list_data = self._get_json(_LIST_URL_TMPL.format(slug=self.account_slug))
        if not list_data:
            return []

        jobs = list_data.get("jobs") or []
        keywords = [k.lower() for k in market.get("keywords", [])]
        max_jobs = market.get("max_jobs_per_source")

        results: list[JobRaw] = []
        for job in jobs:
            title = job.get("title") or ""
            if keywords and not any(kw in title.lower() for kw in keywords):
                continue

            built = self._build_job(job)
            if built:
                results.append(built)

            if max_jobs is not None and len(results) >= max_jobs:
                break

        logger.info("[%s] Collected %d jobs from %d listing entries", self.source_id, len(results), len(jobs))
        return results


# ── Concrete collector classes ──────────────────────────────────────────────

class DevsincCollector(WorkableCollector):
    source_id = "devsinc"
    source_name = "Devsinc"
    account_slug = "devsinc-17"


class PMCLCollector(WorkableCollector):
    source_id = "pmcl"
    # Confirmed: the Workable account's own "name" field, and the branded
    # name candidates actually see on the page, is "JazzWorld" (the
    # company behind it is Pakistan Mobile Communication Limited / Jazz).
    source_name = "JazzWorld"
    account_slug = "pakistan-mobile-communication-limited-pmcl"
