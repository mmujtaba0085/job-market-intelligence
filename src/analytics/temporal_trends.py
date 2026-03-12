"""
src/analytics/temporal_trends.py
─────────────────────────────────
Multi-week temporal trend analysis for skills.

Provides:
- Skill velocity (change rate over multiple weeks)
- Momentum detection (accelerating/decelerating growth)
- Multi-week frequency history
- Trend classification (rising, falling, stable, volatile)

Requires at least 4 weeks of historical data for meaningful analysis.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from src.storage.db import get_connection

logger = logging.getLogger(__name__)


def get_skill_history(market_id: str, skill_name: str, weeks_back: int = 8) -> list[dict]:
    """
    Get weekly frequency history for a specific skill.
    
    Args:
        market_id: Market identifier
        skill_name: Skill to analyze
        weeks_back: Number of weeks of history to fetch
    
    Returns:
        List of {week_start: date, frequency: int} ordered by week
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get weekly metrics for this skill
    cursor.execute("""
        SELECT week_start, frequency
        FROM weekly_metrics
        WHERE market_id = ?
          AND skill_name = ?
        ORDER BY week_start DESC
        LIMIT ?
    """, (market_id, skill_name, weeks_back))
    
    history = [
        {"week_start": row[0], "frequency": row[1]}
        for row in cursor.fetchall()
    ]
    
    conn.close()
    
    # Reverse to get chronological order
    history.reverse()
    
    return history


def compute_velocity(history: list[dict]) -> float:
    """
    Compute average weekly change rate (velocity).
    
    Args:
        history: List of {week_start, frequency} dicts
    
    Returns:
        Average jobs/week change rate
    """
    if len(history) < 2:
        return 0.0
    
    # Calculate week-over-week deltas
    deltas = []
    for i in range(1, len(history)):
        delta = history[i]["frequency"] - history[i-1]["frequency"]
        deltas.append(delta)
    
    # Average velocity
    velocity = sum(deltas) / len(deltas) if deltas else 0.0
    
    return round(velocity, 2)


def compute_momentum(history: list[dict]) -> str:
    """
    Classify momentum based on recent acceleration.
    
    Args:
        history: List of {week_start, frequency} dicts
    
    Returns:
        "accelerating", "decelerating", "stable", or "insufficient_data"
    """
    if len(history) < 4:
        return "insufficient_data"
    
    # Calculate deltas for each week
    deltas = []
    for i in range(1, len(history)):
        delta = history[i]["frequency"] - history[i-1]["frequency"]
        deltas.append(delta)
    
    # Compare first half vs second half average
    mid = len(deltas) // 2
    first_half_avg = sum(deltas[:mid]) / mid if mid > 0 else 0.0
    second_half_avg = sum(deltas[mid:]) / (len(deltas) - mid) if len(deltas) > mid else 0.0
    
    diff = second_half_avg - first_half_avg
    
    # Classify momentum
    if abs(diff) < 1.0:
        return "stable"
    elif diff > 0:
        return "accelerating"
    else:
        return "decelerating"


def compute_volatility(history: list[dict]) -> float:
    """
    Compute volatility (standard deviation of frequencies).
    
    Args:
        history: List of {week_start, frequency} dicts
    
    Returns:
        Standard deviation of frequencies
    """
    if len(history) < 2:
        return 0.0
    
    frequencies = [h["frequency"] for h in history]
    mean = sum(frequencies) / len(frequencies)
    variance = sum((f - mean) ** 2 for f in frequencies) / len(frequencies)
    std_dev = variance ** 0.5
    
    return round(std_dev, 2)


def get_skill_trends(market_id: str, week_start: date, top_n: int = 20, weeks_back: int = 8) -> list[dict]:
    """
    Get temporal trends for top N skills.
    
    Args:
        market_id: Market identifier
        week_start: Current week start date
        top_n: Number of skills to analyze
        weeks_back: Weeks of history to include
    
    Returns:
        List of {skill_name, category, current_freq, velocity, momentum, volatility, history}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get top N skills from current week
    cursor.execute("""
        SELECT skill_name, category, frequency
        FROM weekly_metrics
        WHERE market_id = ?
          AND week_start = ?
        ORDER BY frequency DESC
        LIMIT ?
    """, (market_id, week_start, top_n))
    
    top_skills = cursor.fetchall()
    conn.close()
    
    trends = []
    
    for skill_name, category, current_freq in top_skills:
        # Get historical data
        history = get_skill_history(market_id, skill_name, weeks_back)
        
        if len(history) < 2:
            continue
        
        # Compute metrics
        velocity = compute_velocity(history)
        momentum = compute_momentum(history)
        volatility = compute_volatility(history)
        
        trends.append({
            "skill_name": skill_name,
            "category": category,
            "current_freq": current_freq,
            "velocity": velocity,
            "momentum": momentum,
            "volatility": volatility,
            "history": history  # Full history for charting
        })
    
    logger.info("[temporal_trends] Analyzed %d skills with %d weeks history", len(trends), weeks_back)
    
    return trends


def compute_trend_stats(market_id: str, week_start: date, top_n: int = 20) -> dict:
    """
    Compute comprehensive temporal trend analytics.
    
    Returns dict with:
        - skill_trends: List of trend objects
        - accelerating_count: int
        - decelerating_count: int
        - stable_count: int
    """
    logger.info("[temporal_trends] Computing trend stats for %s week %s", market_id, week_start)
    
    trends = get_skill_trends(market_id, week_start, top_n, weeks_back=8)
    
    # Count momentum categories
    momentum_counts = {
        "accelerating": 0,
        "decelerating": 0,
        "stable": 0,
        "insufficient_data": 0
    }
    
    for trend in trends:
        momentum_counts[trend["momentum"]] += 1
    
    result = {
        "skill_trends": trends,
        "accelerating_count": momentum_counts["accelerating"],
        "decelerating_count": momentum_counts["decelerating"],
        "stable_count": momentum_counts["stable"]
    }
    
    logger.info("[temporal_trends] Momentum: %d accelerating, %d decelerating, %d stable",
               momentum_counts["accelerating"], momentum_counts["decelerating"], momentum_counts["stable"])
    
    return result
