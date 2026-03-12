"""
src/analytics/co_occurrence.py
────────────────────────────────
Optional: compute skill co-occurrence matrix for jobs in a week.

Output is a dict suitable for charts.json:
  {
    "python": {"pytorch": 42, "tensorflow": 30, ...},
    ...
  }

Only the top N skill pairs are returned to keep data size manageable.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from itertools import combinations

from src.storage.db import get_connection

logger = logging.getLogger(__name__)

_TOP_N_SKILLS = 30    # only include the top N skills in the matrix


def compute_co_occurrence(
    market_id: str, week_start: str, week_end: str
) -> dict[str, dict[str, int]]:
    """
    For all jobs in the given week, compute how often skill A and skill B
    appear together in the same job description.

    Returns a nested dict: {skill_a: {skill_b: count}}
    Only skills in the top _TOP_N_SKILLS are included.
    """
    # Convert week_start date to week_id format (YYYY-WW)
    from datetime import datetime
    week_dt = datetime.fromisoformat(week_start)
    iso_year, iso_week, _ = week_dt.isocalendar()
    week_id = f"{iso_year}-{iso_week:02d}"
    
    conn = get_connection()
    try:
        # Fetch (job_id, normalized_skill) pairs for this week
        rows = conn.execute(
            """
            SELECT s.job_id, s.normalized_skill
            FROM skills s
            JOIN jobs j ON j.job_id = s.job_id
            WHERE j.market_id = ?
              AND j.week_id = ?
            ORDER BY s.job_id
            """,
            (market_id, week_id),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {}

    # Group skills by job
    job_skills: dict[int, list[str]] = defaultdict(list)
    skill_freq: dict[str, int] = defaultdict(int)

    for row in rows:
        skill = row["normalized_skill"]
        job_skills[row["job_id"]].append(skill)
        skill_freq[skill] += 1

    # Keep only the top N skills to limit matrix size
    top_skills = set(
        sorted(skill_freq, key=lambda s: skill_freq[s], reverse=True)[:_TOP_N_SKILLS]
    )

    # Count co-occurrences
    co_matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for skills in job_skills.values():
        # Filter to top skills only
        filtered = [s for s in skills if s in top_skills]
        for skill_a, skill_b in combinations(sorted(filtered), 2):
            co_matrix[skill_a][skill_b] += 1
            co_matrix[skill_b][skill_a] += 1

    logger.info(
        "[co_occurrence] %s week %s → matrix for %d skills",
        market_id, week_start, len(co_matrix),
    )

    # Convert defaultdicts to regular dicts for serialisation
    return {k: dict(v) for k, v in co_matrix.items()}
