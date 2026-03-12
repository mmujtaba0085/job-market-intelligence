"""
src/reports/charts_export.py
──────────────────────────────
Exports charts.json — chart-ready data arrays for dashboard and image generation.

Enhanced format includes:
- top_skills_bar, growth_bar (existing)
- movers_by_delta, movers_by_score (new)
- sources_bar, locations_bar, companies_bar (new)
- remote_split (new)
- emerging_skills, declining_skills (existing)
- co_occurrence (optional, existing)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.storage.models import WeeklyMetric

logger = logging.getLogger(__name__)


def export_charts(
    metrics: list[WeeklyMetric],
    output_dir: Path,
    coverage_data: dict | None = None,
    co_occurrence: dict | None = None,
    trend_stats: dict | None = None,
    category_stats: dict | None = None,
    limit: int = 20,
) -> Path:
    """
    Write charts.json with comprehensive chart data.
    
    Args:
        metrics: Weekly skill metrics
        output_dir: Output folder path
        coverage_data: Coverage stats from coverage_metrics.compute_coverage_stats()
        co_occurrence: Co-occurrence matrix (optional)
        trend_stats: Temporal trend stats (Phase 2)
        category_stats: Category breakdown (Phase 2)
        limit: Top N items for charts
    
    Returns path to written file.
    """
    top_by_freq = sorted(metrics, key=lambda m: m.frequency, reverse=True)[:limit]
    top_by_growth = sorted(
        [m for m in metrics if m.growth_percentage > 0],
        key=lambda m: m.growth_percentage,
        reverse=True,
    )[:limit]
    top_by_delta = sorted(metrics, key=lambda m: m.absolute_delta, reverse=True)[:limit]
    top_by_score = sorted(metrics, key=lambda m: m.mover_score, reverse=True)[:limit]

    charts = {
        # ── Existing charts ───────────────────────────────────────────────────
        "top_skills_bar": {
            "labels": [m.skill_name for m in top_by_freq],
            "values": [m.frequency for m in top_by_freq],
            "categories": [m.category for m in top_by_freq],
        },
        "growth_bar": {
            "labels": [m.skill_name for m in top_by_growth],
           "values": [m.growth_percentage for m in top_by_growth],
            "emerging": [m.emerging_flag for m in top_by_growth],
        },
        "emerging_skills": [
            {"skill": m.skill_name, "category": m.category,
             "frequency": m.frequency, "growth": m.growth_percentage}
            for m in metrics if m.emerging_flag
        ],
        "declining_skills": [
            {"skill": m.skill_name, "category": m.category,
             "frequency": m.frequency, "growth": m.growth_percentage}
            for m in metrics if m.declining_flag
        ],
        
        # ── New movers charts ─────────────────────────────────────────────────
        "movers_by_delta": {
            "labels": [m.skill_name for m in top_by_delta],
            "values": [m.absolute_delta for m in top_by_delta],
            "frequencies": [m.frequency for m in top_by_delta],
        },
        "movers_by_score": {
            "labels": [m.skill_name for m in top_by_score],
            "values": [m.mover_score for m in top_by_score],
            "deltas": [m.absolute_delta for m in top_by_score],
        },
    }

    # ── Coverage-based charts ─────────────────────────────────────────────────
    if coverage_data:
        sources = coverage_data.get("sources_breakdown", [])
        countries = coverage_data.get("countries_breakdown", [])
        companies = coverage_data.get("companies_breakdown", [])
        remote = coverage_data.get("remote_breakdown", {})
        
        charts["sources_bar"] = {
            "labels": [s["source_name"] for s in sources],
            "values": [s["job_count"] for s in sources],
            "percentages": [s["pct"] for s in sources],
        }
        
        charts["locations_bar"] = {
            "labels": [c["country"] for c in countries],
            "values": [c["job_count"] for c in countries],
            "percentages": [c["pct"] for c in countries],
        }
        
        charts["companies_bar"] = {
            "labels": [c["company"] for c in companies],
            "values": [c["job_count"] for c in companies],
            "percentages": [c["pct"] for c in companies],
        }
        
        charts["remote_split"] = {
            "labels": list(remote.keys()),
            "values": list(remote.values()),
            "percentages": [
                round(100.0 * count / sum(remote.values()), 2) if sum(remote.values()) > 0 else 0
                for count in remote.values()
            ],
        }

    # ── Phase 2: Temporal trends chart ───────────────────────────────────────
    if trend_stats:
        skill_trends = trend_stats.get("skill_trends", [])
        if skill_trends:
            # Prepare time series data for top 5 skills
            series_data = []
            for trend in skill_trends[:5]:
                history = trend.get("history", [])
                if history:
                    series_data.append({
                        "name": trend["skill_name"],
                        "values": [h["frequency"] for h in history]
                    })
            
            # Get week labels from first skill's history
            if skill_trends and skill_trends[0].get("history"):
                week_labels = [h["week_start"] for h in skill_trends[0]["history"]]
            else:
                week_labels = []
            
            charts["skill_trends_line"] = {
                "labels": week_labels,
                "series": series_data
            }

    # ── Phase 2: Category breakdown chart ────────────────────────────────────
    if category_stats:
        categories = category_stats.get("category_breakdown", [])
        if categories:
            charts["categories_bar"] = {
                "labels": [c["category"] for c in categories],
                "values": [c["total_mentions"] for c in categories],
                "percentages": [c["pct"] for c in categories],
            }

    # ── Co-occurrence (optional) ──────────────────────────────────────────────
    if co_occurrence:
        charts["co_occurrence"] = co_occurrence

    path = output_dir / "charts.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(charts, f, indent=2)

    logger.info("[charts_export] charts.json → %s", path)
    return path
