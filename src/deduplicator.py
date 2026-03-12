"""
src/deduplicator.py
────────────────────
Two-layer deduplication before DB insert.

Layer 1 — url_hash:    sha256(source_id + url)         — fast primary check
Layer 2 — canonical_hash: sha256(title+company+desc[:200]) — catches reposts (NO location - enables multi-location tracking)

Returns a DedupResult per job: "inserted" | "url_dup" | "canonical_dup" | "location_added"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.storage.db import upsert_job
from src.storage.models import JobNormalized

logger = logging.getLogger(__name__)


@dataclass
class DedupResult:
    job: JobNormalized
    job_id: Optional[int]
    status: str   # "inserted" | "url_dup" | "canonical_dup" | "location_added"

    @property
    def is_new(self) -> bool:
        return self.status == "inserted"
    
    @property
    def is_location_added(self) -> bool:
        return self.status == "location_added"


@dataclass
class DeduplicationSummary:
    total: int = 0
    inserted: int = 0
    url_dups: int = 0
    canonical_dups: int = 0
    location_added: int = 0

    def record(self, status: str) -> None:
        self.total += 1
        if status == "inserted":
            self.inserted += 1
        elif status == "url_dup":
            self.url_dups += 1
        elif status == "canonical_dup":
            self.canonical_dups += 1
        elif status == "location_added":
            self.location_added += 1


def deduplicate_and_store(
    jobs: list[JobNormalized],
) -> tuple[list[DedupResult], DeduplicationSummary]:
    """
    For each normalized job:
      - Check url_hash → update last_seen_at, return "url_dup"
      - Check canonical_hash → update last_seen_at, return "canonical_dup"
      - Otherwise insert → return "inserted"

    Returns:
        results: list of DedupResult (one per job)
        summary: aggregate counts
    """
    results: list[DedupResult] = []
    summary = DeduplicationSummary()

    for job in jobs:
        try:
            job_id, status = upsert_job(job)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[deduplicator] Failed to upsert job url=%s: %s", job.url, exc, exc_info=True
            )
            job_id, status = None, "error"

        if status == "canonical_dup":
            logger.debug(
                "[deduplicator] Canonical repost detected: '%s' @ %s",
                job.title[:60], job.company,
            )
        elif status == "location_added":
            logger.info(
                "[deduplicator] New location added: '%s' @ %s → %s",
                job.title[:60], job.company, job.location,
            )

        summary.record(status)
        results.append(DedupResult(job=job, job_id=job_id, status=status))

    logger.info(
        "[deduplicator] %d total → %d inserted, %d url_dup, %d canonical_dup, %d location_added",
        summary.total, summary.inserted, summary.url_dups, summary.canonical_dups, summary.location_added,
    )
    return results, summary
