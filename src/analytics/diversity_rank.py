"""
src/analytics/diversity_rank.py
────────────────────────────────
Computes a per-source recency rank ("diversity_rank") for active jobs, so the
/jobs page's default view can interleave sources evenly instead of a single
high-volume source dominating a strict posted_date sort.

Algorithm:
  1. Rank each active job within its own source by recency
     (ROW_NUMBER() OVER PARTITION BY source_name ORDER BY posted_date DESC,
     ingested_at DESC)
  2. Write that rank back to jobs.diversity_rank
  3. Sorting ORDER BY diversity_rank ASC then interleaves every source's most
     recent job first, then every source's second-most-recent, and so on —
     a deterministic round-robin, not randomized sampling.

Scoped deliberately to the exact population the /jobs page's default view
queries (listing_status IS NULL OR listing_status = 'active') — not the
broader active_jobs view (listing_status != 'hidden') — so there's no mismatch
between what's ranked and what's displayed.
"""

from __future__ import annotations

import logging
import sqlite3

from src.storage.db import get_connection

logger = logging.getLogger(__name__)


def recompute_diversity_ranks() -> int:
    """
    Recompute diversity_rank for every active job. Idempotent — safe to call
    repeatedly; jobs outside the active population are left with
    diversity_rank NULL.

    Returns:
        Number of active job rows updated.
    """
    conn = get_connection()
    try:
        return _recompute(conn)
    finally:
        conn.close()


def _recompute(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("""
        UPDATE jobs
        SET diversity_rank = ranked.rn
        FROM (
            SELECT job_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY source_name
                       ORDER BY posted_date DESC, ingested_at DESC
                   ) AS rn
            FROM jobs
            WHERE listing_status IS NULL OR listing_status = 'active'
        ) AS ranked
        WHERE jobs.job_id = ranked.job_id
    """)
    updated = cursor.rowcount
    conn.commit()
    logger.info("[diversity_rank] Recomputed diversity_rank for %d active jobs", updated)
    return updated
