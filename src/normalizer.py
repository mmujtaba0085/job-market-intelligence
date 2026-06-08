"""
src/normalizer.py
─────────────────
Transforms JobRaw → JobNormalized.

Responsibilities:
  - Pull structured fields from parsed_fields (API) or raw_html/raw_json
  - Infer remote_type from text signals
  - Parse posted_date from multiple formats
  - Compute url_hash, canonical_hash (NO location - enables multi-location dedup), description_hash, job_group_id
  - Clean + trim all string fields
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Optional
import logging

from dateutil import parser as dateutil_parser

from src.storage.models import JobNormalized, JobRaw
from src.title_normalizer import normalize_title

logger = logging.getLogger(__name__)


def normalize(job_raw: JobRaw, market_id: str) -> Optional[JobNormalized]:
    """
    Convert a JobRaw into a JobNormalized.
    Returns None if critical fields (url, description) are missing.
    """
    pf = job_raw.parsed_fields or {}

    # ── Extract core fields ───────────────────────────────────────────────────
    url = _clean(job_raw.url or pf.get("url", ""))
    if not url:
        return None

    title = _clean(pf.get("title", ""))
    
    # Normalize title for analytics (preserves original in 'title' field)
    normalized_title, confidence = normalize_title(title)
    
    # Log low-confidence normalizations for review (skip empty titles - expected behavior)
    if confidence < 0.6 and title != normalized_title and title.strip():
        logger.warning(
            f"Low-confidence title normalization: '{title}' → '{normalized_title}' "
            f"(confidence: {confidence:.2f})"
        )
    
    company = _clean(pf.get("company", ""))
    location = _clean(pf.get("location", ""))
    all_locations = pf.get("all_locations")  # Optional list of all locations (for GitHub sources)
    country = _clean(pf.get("country", ""))
    description = _clean(pf.get("description", ""))

    if not description:
        return None

    # ── Remote type ───────────────────────────────────────────────────────────
    remote_type = _infer_remote_type(
        pf.get("remote_type", ""),
        title,
        description,
    )

    # ── Date parsing ──────────────────────────────────────────────────────────
    posted_date = _parse_date(pf.get("posted_date", ""))

    # ── Salary ────────────────────────────────────────────────────────────────
    salary_min = _to_float(pf.get("salary_min"))
    salary_max = _to_float(pf.get("salary_max"))
    currency = _clean(pf.get("currency", "")) or None

    # ── Hashes ────────────────────────────────────────────────────────────────
    url_hash = _sha256(job_raw.source_id + url)
    
    # Canonical hash NO LONGER includes location - this allows multi-location deduplication
    canonical_hash = _sha256(
        f"{title.lower()}|{company.lower()}|{description[:200].lower()}"
    )
    
    description_hash = _sha256(description)
    
    # Job group ID - first 16 chars of canonical hash for grouping multi-location postings
    job_group_id = canonical_hash[:16]

    return JobNormalized(
        url_hash=url_hash,
        canonical_hash=canonical_hash,
        description_hash=description_hash,
        job_group_id=job_group_id,
        market_id=market_id,
        source_name=job_raw.source_name,
        title=title,
        normalized_title=normalized_title,
        normalization_confidence=confidence,
        company=company,
        country=country,
        location=location,
        all_locations=all_locations,
        remote_type=remote_type,
        posted_date=posted_date,
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        description_text=description,
        url=url,
    )


def normalize_batch(
    jobs_raw: list[JobRaw], market_id: str
) -> list[JobNormalized]:
    """Normalize a list of raw jobs, skipping any that return None."""
    results = []
    for job in jobs_raw:
        normalized = normalize(job, market_id)
        if normalized:
            results.append(normalized)
    return results


# ─── Field helpers ────────────────────────────────────────────────────────────

_REMOTE_SIGNALS = ["remote", "work from home", "wfh", "distributed", "virtual"]
_HYBRID_SIGNALS = ["hybrid"]
_ONSITE_SIGNALS = ["on-site", "onsite", "in-office", "in office", "on site", "office"]

# Canonical map: all variant spellings → one of "remote"|"hybrid"|"on-site"|"unknown"
_REMOTE_TYPE_CANONICAL: dict[str, str] = {
    "remote": "remote", "fully remote": "remote", "fully_remote": "remote",
    "full_remote": "remote", "full remote": "remote",
    "remote only": "remote", "remote-only": "remote",
    "remote first": "remote", "remote_first": "remote",
    "remote-first": "remote", "remote work": "remote",
    "work from home": "remote", "wfh": "remote",
    "no office": "remote", "distributed": "remote",
    "fully-remote": "remote",
    "hybrid": "hybrid", "partially remote": "hybrid",
    "partially_remote": "hybrid", "partial_remote": "hybrid",
    "flexible_remote": "hybrid", "flexible remote": "hybrid",
    "hybrid remote": "hybrid", "hybrid-remote": "hybrid",
    "on-site": "on-site", "on_site": "on-site", "onsite": "on-site",
    "in-office": "on-site", "in office": "on-site",
    "office": "on-site", "office based": "on-site",
    "office-based": "on-site",
    "unknown": "unknown",
}


def _infer_remote_type(declared: str, title: str, description: str) -> str:
    """
    Priority: explicit declared value > title signals > description signals.
    Returns one of: "remote" | "hybrid" | "on-site" | "unknown"
    """
    d = declared.lower().strip()
    # Check canonical map first (covers all variant spellings)
    if d in _REMOTE_TYPE_CANONICAL:
        return _REMOTE_TYPE_CANONICAL[d]

    combined = (title + " " + description[:500]).lower()
    if any(s in combined for s in _HYBRID_SIGNALS):
        return "hybrid"
    if any(s in combined for s in _REMOTE_SIGNALS):
        return "remote"
    if any(s in combined for s in _ONSITE_SIGNALS):
        return "on-site"
    return "unknown"


def _parse_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    raw = raw.strip()
    # Fast path: ISO format
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    # Fallback: dateutil
    try:
        return dateutil_parser.parse(raw, fuzzy=True).date()
    except Exception:  # noqa: BLE001
        return None


def _clean(value: str) -> str:
    """Strip whitespace and collapse internal whitespace."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
