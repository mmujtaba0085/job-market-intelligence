"""
src/analytics/category_analytics.py
────────────────────────────────────
Skill category distribution and trend analysis.

Provides:
- Skills breakdown by category (ml_core, programming, cloud, soft_skills, etc.)
- Category growth rates (week-over-week)
- Category market share (% of total mentions)
- Dominant category identification

Uses the taxonomy categories from skill extraction.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from src.storage.db import get_connection

logger = logging.getLogger(__name__)


def get_category_breakdown(market_id: str, week_start: date, week_end: date) -> list[dict]:
    """
    Get skill frequency breakdown by category.
    
    Args:
        market_id: Market identifier
        week_start: Week start date
        week_end: Week end date
    
    Returns:
        List of {category: str, skill_count: int, total_mentions: int, pct: float}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Aggregate by category from skills table
    cursor.execute("""
        SELECT s.category, COUNT(DISTINCT s.normalized_skill) as skill_count, COUNT(*) as total_mentions
        FROM skills s
        JOIN jobs j ON s.job_id = j.job_id
        WHERE j.market_id = ?
          AND DATE(j.first_seen_at) >= ?
          AND DATE(j.first_seen_at) <= ?
        GROUP BY s.category
        ORDER BY total_mentions DESC
    """, (market_id, week_start, week_end))
    
    rows = cursor.fetchall()
    conn.close()
    
    total_all = sum(row[2] for row in rows)
    
    return [
        {
            "category": row[0],
            "skill_count": row[1],
            "total_mentions": row[2],
            "pct": round(100.0 * row[2] / total_all, 2) if total_all > 0 else 0.0
        }
        for row in rows
    ]


def get_category_trends(market_id: str, week_start: date, lookback_weeks: int = 4) -> list[dict]:
    """
    Get category growth trends (current week vs prior period).
    
    Args:
        market_id: Market identifier
        week_start: Current week start date
        lookback_weeks: How many weeks back to compare
    
    Returns:
        List of {category, current_mentions, prior_mentions, delta, growth_pct}
    """
    week_end = week_start + timedelta(days=7)
    prior_start = week_start - timedelta(weeks=lookback_weeks)
    prior_end = prior_start + timedelta(days=7)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get current week category counts
    cursor.execute("""
        SELECT s.category, COUNT(*) as total_mentions
        FROM skills s
        JOIN jobs j ON s.job_id = j.job_id
        WHERE j.market_id = ?
          AND DATE(j.first_seen_at) >= ?
          AND DATE(j.first_seen_at) <= ?
        GROUP BY s.category
    """, (market_id, week_start, week_end))
    
    current_categories = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Get prior week category counts
    cursor.execute("""
        SELECT s.category, COUNT(*) as total_mentions
        FROM skills s
        JOIN jobs j ON s.job_id = j.job_id
        WHERE j.market_id = ?
          AND DATE(j.first_seen_at) >= ?
          AND DATE(j.first_seen_at) <= ?
        GROUP BY s.category
    """, (market_id, prior_start, prior_end))
    
    prior_categories = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    
    # Calculate trends for all categories
    all_categories = set(current_categories.keys()) | set(prior_categories.keys())
    
    trends = []
    for category in all_categories:
        current = current_categories.get(category, 0)
        prior = prior_categories.get(category, 0)
        delta = current - prior
        growth_pct = ((current - prior) / prior * 100.0) if prior > 0 else 0.0
        
        trends.append({
            "category": category,
            "current_mentions": current,
            "prior_mentions": prior,
            "delta": delta,
            "growth_pct": round(growth_pct, 2)
        })
    
    # Sort by current mentions descending
    trends.sort(key=lambda x: x["current_mentions"], reverse=True)
    
    return trends


def get_top_skills_by_category(market_id: str, week_start: date, category: str, limit: int = 5) -> list[str]:
    """
    Get top skills within a specific category.
    
    Args:
        market_id: Market identifier
        week_start: Week start date
        category: Category to filter by
        limit: Number of skills to return
    
    Returns:
        List of skill names (top N by frequency)
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT skill_name, frequency
        FROM weekly_metrics
        WHERE market_id = ?
          AND week_start = ?
          AND category = ?
        ORDER BY frequency DESC
        LIMIT ?
    """, (market_id, week_start, category, limit))
    
    skills = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    return skills


def compute_category_stats(market_id: str, week_start: date, week_end: date, lookback_weeks: int = 4) -> dict:
    """
    Compute comprehensive category analytics.
    
    Returns dict with:
        - category_breakdown: List of {category, skill_count, total_mentions, pct, top_skills}
        - category_trends: List of {category, current_mentions, prior_mentions, delta, growth_pct}
        - dominant_category: str (category with most mentions)
        - total_categories: int
    """
    logger.info("[category_analytics] Computing category stats for %s week %s", market_id, week_start)
    
    # Get category breakdown with top skills for each
    breakdown = get_category_breakdown(market_id, week_start, week_end)
    
    # Enrich with top skills per category
    for cat_data in breakdown:
        cat_data["top_skills"] = get_top_skills_by_category(
            market_id, week_start, cat_data["category"], limit=5
        )
    
    # Get category trends
    trends = get_category_trends(market_id, week_start, lookback_weeks)
    
    # Identify dominant category
    dominant = breakdown[0]["category"] if breakdown else "unknown"
    
    result = {
        "category_breakdown": breakdown,
        "category_trends": trends,
        "dominant_category": dominant,
        "total_categories": len(breakdown)
    }
    
    logger.info("[category_analytics] Computed stats: %d categories, dominant=%s",
               len(breakdown), dominant)
    
    return result
