"""
src/analytics/coverage_metrics.py
──────────────────────────────────
Coverage and distribution analytics for weekly reports.

Provides breakdown by:
- Source (jobs per collector)
- Country/Location (geographic distribution)
- Company (top hiring companies)
- Remote type (remote/hybrid/on-site split)

All functions query the jobs table for a specific market and week range.
"""

from __future__ import annotations

import logging
from datetime import date

from src.storage.db import get_connection

logger = logging.getLogger(__name__)


def get_jobs_by_source(market_id: str, week_start: date, week_end: date) -> list[dict]:
    """
    Get job count breakdown by source for the current week.
    
    Returns:
        List of {source_name: str, job_count: int, pct: float}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT source_name, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
        GROUP BY source_name
        ORDER BY job_count DESC
    """, (market_id, week_start, week_end))
    
    rows = cursor.fetchall()
    conn.close()
    
    total = sum(row[1] for row in rows)
    
    return [
        {
            "source_name": row[0],
            "job_count": row[1],
            "pct": round(100.0 * row[1] / total, 2) if total > 0 else 0.0
        }
        for row in rows
    ]


def get_jobs_by_country(market_id: str, week_start: date, week_end: date, limit: int = 10) -> list[dict]:
    """
    Get job count breakdown by country (top N).
    
    Returns:
        List of {country: str, job_count: int, pct: float}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT country, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
        GROUP BY country
        ORDER BY job_count DESC
        LIMIT ?
    """, (market_id, week_start, week_end, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    total = sum(row[1] for row in rows)
    
    return [
        {
            "country": row[0],
            "job_count": row[1],
            "pct": round(100.0 * row[1] / total, 2) if total > 0 else 0.0
        }
        for row in rows
    ]


def get_jobs_by_location(market_id: str, week_start: date, week_end: date, limit: int = 10) -> list[dict]:
    """
    Get job count breakdown by specific location (city/region).
    
    Returns:
        List of {location: str, country: str, job_count: int}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT location, country, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
          AND location IS NOT NULL
          AND location != ''
        GROUP BY location, country
        ORDER BY job_count DESC
        LIMIT ?
    """, (market_id, week_start, week_end, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "location": row[0],
            "country": row[1],
            "job_count": row[2]
        }
        for row in rows
    ]


def get_jobs_by_company(market_id: str, week_start: date, week_end: date, limit: int = 10) -> list[dict]:
    """
    Get job count breakdown by company (top N hiring companies).
    
    Returns:
        List of {company: str, job_count: int, pct: float}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT company, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
          AND company IS NOT NULL
          AND company != ''
          AND company != 'Unknown'
        GROUP BY company
        ORDER BY job_count DESC
        LIMIT ?
    """, (market_id, week_start, week_end, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    total = sum(row[1] for row in rows)
    
    return [
        {
            "company": row[0],
            "job_count": row[1],
            "pct": round(100.0 * row[1] / total, 2) if total > 0 else 0.0
        }
        for row in rows
    ]


def get_remote_breakdown(market_id: str, week_start: date, week_end: date) -> dict[str, int]:
    """
    Get job count breakdown by remote type.
    
    Returns:
        Dict of {remote_type: count}, e.g., {"Remote": 120, "Hybrid": 30, "On-site": 50}
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT remote_type, COUNT(*) as job_count
        FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
        GROUP BY remote_type
        ORDER BY job_count DESC
    """, (market_id, week_start, week_end))
    
    rows = cursor.fetchall()
    conn.close()
    
    # Normalize remote type names (capitalize consistently)
    breakdown = {}
    for remote_type, count in rows:
        # Normalize: "remote" → "Remote", "on-site" → "On-site", etc.
        normalized = remote_type.capitalize() if remote_type else "Unknown"
        if normalized == "On-site":
            normalized = "On-site"  # Keep hyphen lowercase
        breakdown[normalized] = count
    
    return breakdown


def get_company_top_skills(market_id: str, week_start: date, week_end: date, company: str, limit: int = 3) -> list[str]:
    """
    Get top N skills for a specific company (for companies.csv enrichment).
    
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
          AND j.company = ?
        GROUP BY s.normalized_skill
        ORDER BY skill_count DESC
        LIMIT ?
    """, (market_id, week_start, week_end, company, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [row[0] for row in rows]


def compute_coverage_stats(market_id: str, week_start: date, week_end: date) -> dict:
    """
    Compute comprehensive coverage statistics for weekly report.
    
    Returns dict with:
        - total_jobs
        - sources_breakdown
        - countries_breakdown
        - companies_breakdown
        - remote_breakdown
        - remote_pct (percentage of remote jobs)
    """
    logger.info("[coverage_metrics] Computing coverage stats for %s week %s", market_id, week_start)
    
    # Total jobs count
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE market_id = ?
          AND DATE(first_seen_at) >= ?
          AND DATE(first_seen_at) <= ?
    """, (market_id, week_start, week_end))
    total_jobs = cursor.fetchone()[0]
    conn.close()
    
    # Get all breakdowns
    sources = get_jobs_by_source(market_id, week_start, week_end)
    countries = get_jobs_by_country(market_id, week_start, week_end, limit=10)
    companies = get_jobs_by_company(market_id, week_start, week_end, limit=10)
    remote = get_remote_breakdown(market_id, week_start, week_end)
    
    # Calculate remote percentage
    remote_count = remote.get("Remote", 0)
    remote_pct = round(100.0 * remote_count / total_jobs, 2) if total_jobs > 0 else 0.0
    
    result = {
        "total_jobs": total_jobs,
        "sources_breakdown": sources,
        "countries_breakdown": countries,
        "companies_breakdown": companies,
        "remote_breakdown": remote,
        "remote_pct": remote_pct,
    }
    
    logger.info("[coverage_metrics] Computed stats: %d jobs, %d sources, %d countries",
               total_jobs, len(sources), len(countries))
    
    return result
