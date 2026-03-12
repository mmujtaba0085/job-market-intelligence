"""
src/analytics/title_analytics.py
─────────────────────────────────
Job title analytics for deep-dive reporting.

Provides:
- Top job titles by frequency
- Title trends (week-over-week growth)
- Title-to-skill mapping (most common skills per title)
- Title distribution stats

All functions query the jobs table for a specific market and week range.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from src.storage.db import get_connection

logger = logging.getLogger(__name__)


def get_top_titles(market_id: str, week_start: date, week_end: date, limit: int = 20) -> list[dict]:
    """
    Get most common job titles for the current week.
    
    Args:
        market_id: Market identifier
        week_start: Week start date
        week_end: Week end date
        limit: Number of titles to return
    
    Returns:
        List of {title: str, job_count: int, pct: float}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT title, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
          AND title IS NOT NULL
          AND title != ''
        GROUP BY title
        ORDER BY job_count DESC
        LIMIT ?
    """, (market_id, week_start, week_end, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    total = sum(row[1] for row in rows)
    
    return [
        {
            "title": row[0],
            "job_count": row[1],
            "pct": round(100.0 * row[1] / total, 2) if total > 0 else 0.0
        }
        for row in rows
    ]


def get_title_trends(market_id: str, week_start: date, lookback_weeks: int = 4, limit: int = 20) -> list[dict]:
    """
    Get title growth trends (current week vs prior period).
    
    Args:
        market_id: Market identifier
        week_start: Current week start date
        lookback_weeks: How many weeks back to compare
        limit: Number of titles to analyze
    
    Returns:
        List of {title, current_count, prior_count, delta, growth_pct}
    """
    week_end = week_start + timedelta(days=7)
    prior_start = week_start - timedelta(weeks=lookback_weeks)
    prior_end = prior_start + timedelta(days=7)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get current week titles
    cursor.execute("""
        SELECT title, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
          AND title IS NOT NULL
          AND title != ''
        GROUP BY title
        ORDER BY job_count DESC
        LIMIT ?
    """, (market_id, week_start, week_end, limit))
    
    current_titles = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Get prior week titles
    cursor.execute("""
        SELECT title, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
          AND title IS NOT NULL
          AND title != ''
        GROUP BY title
    """, (market_id, prior_start, prior_end))
    
    prior_titles = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    
    # Calculate trends
    trends = []
    for title, current_count in current_titles.items():
        prior_count = prior_titles.get(title, 0)
        delta = current_count - prior_count
        growth_pct = ((current_count - prior_count) / prior_count * 100.0) if prior_count > 0 else 0.0
        
        trends.append({
            "title": title,
            "current_count": current_count,
            "prior_count": prior_count,
            "delta": delta,
            "growth_pct": round(growth_pct, 2)
        })
    
    # Sort by absolute delta descending
    trends.sort(key=lambda x: abs(x["delta"]), reverse=True)
    
    return trends


def get_title_skills(market_id: str, week_start: date, week_end: date, title: str, limit: int = 5) -> list[str]:
    """
    Get top skills for a specific job title.
    
    Args:
        market_id: Market identifier
        week_start: Week start date
        week_end: Week end date
        title: Job title to analyze
        limit: Number of skills to return
    
    Returns:
        List of skill names (top N by frequency)
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.normalized_skill, COUNT(*) as skill_count
        FROM skills s
        JOIN jobs j ON s.job_id = j.job_id
        WHERE j.market_id = ?
          AND DATE(j.first_seen_at) >= ?
          AND DATE(j.first_seen_at) <= ?
          AND j.title = ?
        GROUP BY s.normalized_skill
        ORDER BY skill_count DESC
        LIMIT ?
    """, (market_id, week_start, week_end, title, limit))
    
    skills = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    return skills


def compute_title_stats(market_id: str, week_start: date, week_end: date, lookback_weeks: int = 4) -> dict:
    """
    Compute comprehensive title analytics.
    
    Returns dict with:
        - top_titles: List of {title, job_count, pct, top_skills}
        - title_trends: List of {title, current_count, prior_count, delta, growth_pct}
        - total_unique_titles: int
    """
    logger.info("[title_analytics] Computing title stats for %s week %s", market_id, week_start)
    
    # Get top titles with their skills
    top_titles = get_top_titles(market_id, week_start, week_end, limit=20)
    
    # Enrich with top skills for each title
    for title_data in top_titles:
        title_data["top_skills"] = get_title_skills(
            market_id, week_start, week_end, title_data["title"], limit=5
        )
    
    # Get title trends
    trends = get_title_trends(market_id, week_start, lookback_weeks, limit=20)
    
    # Count unique titles
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT title) 
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
          AND title IS NOT NULL
          AND title != ''
    """, (market_id, week_start, week_end))
    
    total_unique = cursor.fetchone()[0]
    conn.close()
    
    result = {
        "top_titles": top_titles,
        "title_trends": trends,
        "total_unique_titles": total_unique
    }
    
    logger.info("[title_analytics] Computed stats: %d unique titles, top=%s",
               total_unique, top_titles[0]["title"] if top_titles else "N/A")
    
    return result
