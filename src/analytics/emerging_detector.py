"""
src/analytics/emerging_detector.py
────────────────────────────────────
Pure logic for flagging emerging / declining skills.
No DB access — called by weekly_metrics.py after growth computation.
"""

from __future__ import annotations

from config.settings import DECLINING_THRESHOLD, GROWTH_THRESHOLD, MIN_FREQ


def assign_flags(frequency: int, growth_percentage: float) -> tuple[bool, bool]:
    """
    Compute emerging_flag and declining_flag for a single skill.

    Rules (configurable via settings.py):
      emerging:  frequency >= MIN_FREQ  AND  growth_pct >= GROWTH_THRESHOLD
      declining: frequency >= MIN_FREQ  AND  growth_pct <= DECLINING_THRESHOLD

    Returns:
        (emerging_flag, declining_flag) — both can be False; never both True
    """
    if frequency < MIN_FREQ:
        return False, False

    emerging = growth_percentage >= GROWTH_THRESHOLD
    declining = growth_percentage <= DECLINING_THRESHOLD
    return emerging, declining


def summarise_flags(
    metrics: list,    # list[WeeklyMetric] — typed loosely to avoid circular import
) -> dict:
    """Return summary counts of emerging / declining skills."""
    return {
        "total_skills": len(metrics),
        "emerging": sum(1 for m in metrics if m.emerging_flag),
        "declining": sum(1 for m in metrics if m.declining_flag),
        "stable": sum(1 for m in metrics if not m.emerging_flag and not m.declining_flag),
    }
