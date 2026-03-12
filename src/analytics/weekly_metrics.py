"""
src/analytics/weekly_metrics.py
────────────────────────────────
Computes frequency + week-over-week growth for all skills in a market.

Algorithm:
  1. Determine ISO week boundaries for the current and prior comparison week
  2. Query skill frequencies from the skills + jobs tables
  3. Compute growth_percentage vs the comparison week
  4. Compute absolute_delta (this_week - prior_week)
  5. Compute mover_score (delta * log1p(frequency)) to penalize low-base spikes
  6. Delegate to emerging_detector for flag assignment
  7. Write all WeeklyMetric rows to DB
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta

from config.settings import EMERGING_LOOKBACK_WEEKS, MIN_FREQ
from src.analytics.emerging_detector import assign_flags
from src.storage.db import (
    get_skill_frequencies,
    get_prior_skill_frequency,
    upsert_weekly_metric,
)
from src.storage.models import WeeklyMetric

logger = logging.getLogger(__name__)


def compute_weekly_metrics(
    market_id: str,
    week_start: date,
) -> list[WeeklyMetric]:
    """
    Compute all skill metrics for a given market + ISO week.

    Args:
        market_id:  e.g. "ai_ml_global"
        week_start: Monday of the target week (date object)

    Returns:
        list of WeeklyMetric (also written to DB)
    """
    week_end = week_start + timedelta(days=7)
    prior_start = week_start - timedelta(weeks=EMERGING_LOOKBACK_WEEKS)
    prior_end = prior_start + timedelta(days=7)

    week_start_str = week_start.isoformat()
    week_end_str = week_end.isoformat()
    prior_start_str = prior_start.isoformat()
    prior_end_str = prior_end.isoformat()
    week_number = week_start.isocalendar()[1]

    logger.info(
        "[analytics] Computing metrics for %s week %s → %s (prior: %s → %s)",
        market_id, week_start_str, week_end_str, prior_start_str, prior_end_str,
    )

    # ── Current week frequencies ───────────────────────────────────────────────
    current_rows = get_skill_frequencies(market_id, week_start_str, week_end_str)

    if not current_rows:
        logger.warning(
            "[analytics] No skill data found for %s in week %s", market_id, week_start_str
        )
        return []

    metrics: list[WeeklyMetric] = []

    for row in current_rows:
        skill_name = row["normalized_skill"]
        category = row["category"]
        frequency = row["frequency"]

        if frequency < MIN_FREQ:
            continue   # below reporting threshold

        # ── Prior week frequency for growth calc ──────────────────────────────
        prior_freq = get_prior_skill_frequency(
            market_id, skill_name, prior_start_str, prior_end_str
        )

        # ── Growth calculations ────────────────────────────────────────────────
        # Absolute delta (simple difference)
        absolute_delta = frequency - prior_freq
        
        # Growth percentage (handle first week case)
        if prior_freq == 0:
            # New skill this period - set growth to NULL concept (use None, will be stored as 0)
            # Don't inflate to 100% to avoid misleading "growth" signals
            growth_pct = 0.0 if frequency > 0 else 0.0
        else:
            growth_pct = round(((frequency - prior_freq) / prior_freq) * 100, 2)
        
        # Mover score: penalize low-base spikes with log weighting
        # Formula: delta * log1p(frequency)
        # This prevents "1 job → 2 jobs = +100%" from ranking higher than "50 → 60"
        mover_score = round(absolute_delta * math.log1p(frequency), 2)

        emerging, declining = assign_flags(frequency, growth_pct)

        metric = WeeklyMetric(
            market_id=market_id,
            week_start_date=week_start,
            week_number=week_number,
            skill_name=skill_name,
            category=category,
            frequency=frequency,
            growth_percentage=growth_pct,
            absolute_delta=absolute_delta,
            mover_score=mover_score,
            emerging_flag=emerging,
            declining_flag=declining,
        )
        upsert_weekly_metric(metric)
        metrics.append(metric)

    logger.info(
        "[analytics] %s week %s → %d skill metrics written (%d emerging, %d declining)",
        market_id,
        week_start_str,
        len(metrics),
        sum(1 for m in metrics if m.emerging_flag),
        sum(1 for m in metrics if m.declining_flag),
    )
    return metrics
