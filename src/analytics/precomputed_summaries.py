"""
src/analytics/precomputed_summaries.py
────────────────────────────────────────
Precomputed analytics for two endpoints that were too expensive to
compute on every request once they became reachable by anonymous
traffic (see docs/superpowers/specs/2026-07-12-greywave-rebrand-and-anonymous-access-design.md
section 3 for the full empirical investigation).

/api/skills/combinations (top skill co-occurrence pairs): a self-join
across the full `skills` table took ~2.4-3 seconds against real
production data (260K+ rows). Reducing the LIMIT does NOT help - the
join and GROUP BY must fully complete before ORDER BY/LIMIT can even be
applied, since co_count is an aggregate that doesn't exist until then.
Only 192 distinct skills exist, so only ~13,500 pairs ever actually
co-occur - a small, stable output despite the large, growing input.
Precomputing once and reading from a small table is ~2,500-3,500x
faster than any on-the-fly query variant tested (including a covering
index, which only got ~29% faster - still far too slow for a live
request).

/api/titles/top (top job titles grouped by seniority-agnostic role
family): the previous implementation pulled all 73,734 distinct
normalized_title rows into Python and aggregated them via a role_family()
regex transform in a loop, taking ~2.3 seconds total. Titles don't
compress into a small vocabulary the way skills do (71,043 distinct role
families, barely fewer than the raw title count) - but this endpoint
only ever returns the top 30, so the summary table only needs to store
30 rows regardless of how many distinct families exist underneath.

Both are recomputed once per ingestion pipeline run (src/orchestrator.py,
alongside the existing diversity_rank recompute), not per-request.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from src.storage.db import get_connection

logger = logging.getLogger(__name__)

_SENIORITY_PREFIX_RE = re.compile(
    r'^(?:Senior|Junior|Jr\.?|Sr\.?|Associate|Mid[\s-]Level|Entry[\s-]Level)\s+',
    re.IGNORECASE,
)
_SENIORITY_SUFFIX_RE = re.compile(
    r'\s+(?:Intern|Internship)\s*$',
    re.IGNORECASE,
)


def _role_family(title: str) -> str:
    t = _SENIORITY_PREFIX_RE.sub('', title).strip()
    t = _SENIORITY_SUFFIX_RE.sub('', t).strip()
    return t


def recompute_skill_combinations(limit: int = 50) -> int:
    """Recompute the top N skill co-occurrence pairs into
    skill_combinations_summary. Safe to call repeatedly (full replace)."""
    conn = get_connection()
    try:
        return _recompute_skill_combinations(conn, limit=limit)
    finally:
        conn.close()


def _recompute_skill_combinations(conn: sqlite3.Connection, limit: int) -> int:
    conn.execute("DELETE FROM skill_combinations_summary")
    conn.execute("""
        INSERT INTO skill_combinations_summary (skill_a, skill_b, co_count)
        SELECT s1.normalized_skill, s2.normalized_skill, COUNT(*)
        FROM skills s1
        JOIN skills s2 ON s1.job_id = s2.job_id
        WHERE s1.normalized_skill < s2.normalized_skill
        GROUP BY s1.normalized_skill, s2.normalized_skill
        ORDER BY COUNT(*) DESC
        LIMIT ?
    """, (limit,))
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM skill_combinations_summary").fetchone()[0]
    logger.info("[precomputed_summaries] skill_combinations_summary: %d pairs", count)
    return count


def recompute_top_titles(limit: int = 30) -> int:
    """Recompute the top N role families into top_titles_summary. Safe to
    call repeatedly (full replace)."""
    conn = get_connection()
    try:
        return _recompute_top_titles(conn, limit=limit)
    finally:
        conn.close()


def _recompute_top_titles(conn: sqlite3.Connection, limit: int) -> int:
    conn.create_function("role_family", 1, _role_family)
    conn.execute("DELETE FROM top_titles_summary")
    conn.execute("""
        INSERT INTO top_titles_summary (title, count)
        SELECT role_family(normalized_title), SUM(cnt) FROM (
            SELECT normalized_title, COUNT(*) as cnt FROM active_jobs
            WHERE normalized_title IS NOT NULL AND normalized_title != '' AND normalized_title != 'Unknown'
            GROUP BY normalized_title
        )
        GROUP BY role_family(normalized_title)
        ORDER BY SUM(cnt) DESC
        LIMIT ?
    """, (limit,))
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM top_titles_summary").fetchone()[0]
    logger.info("[precomputed_summaries] top_titles_summary: %d role families", count)
    return count
