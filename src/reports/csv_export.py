"""
src/reports/csv_export.py
──────────────────────────
Exports CSV files into the week output folder:
- top_skills.csv
- growth_skills.csv
- sources_breakdown.csv
- locations.csv
- companies.csv
- movers_delta.csv
- movers_score.csv
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from src.storage.models import WeeklyMetric

logger = logging.getLogger(__name__)


def export_top_skills(metrics: list[WeeklyMetric], output_dir: Path, limit: int = 20) -> Path:
    """
    Write top skills ranked by frequency.
    Returns path to written file.
    """
    path = output_dir / "top_skills.csv"
    sorted_metrics = sorted(metrics, key=lambda m: m.frequency, reverse=True)[:limit]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["rank", "skill", "category", "frequency", "growth_pct",
                           "emerging", "declining"]
        )
        writer.writeheader()
        for rank, m in enumerate(sorted_metrics, start=1):
            writer.writerow({
                "rank": rank,
                "skill": m.skill_name,
                "category": m.category,
                "frequency": m.frequency,
                "growth_pct": m.growth_percentage,
                "emerging": "Y" if m.emerging_flag else "",
                "declining": "Y" if m.declining_flag else "",
            })

    logger.info("[csv_export] top_skills.csv → %s", path)
    return path


def export_growth_skills(metrics: list[WeeklyMetric], output_dir: Path, limit: int = 20) -> Path:
    """
    Write skills ranked by growth_percentage (only positive growth).
    Returns path to written file.
    """
    path = output_dir / "growth_skills.csv"
    sorted_metrics = sorted(
        [m for m in metrics if m.growth_percentage > 0],
        key=lambda m: m.growth_percentage,
        reverse=True,
    )[:limit]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["rank", "skill", "category", "frequency", "growth_pct", "emerging"]
        )
        writer.writeheader()
        for rank, m in enumerate(sorted_metrics, start=1):
            writer.writerow({
                "rank": rank,
                "skill": m.skill_name,
                "category": m.category,
                "frequency": m.frequency,
                "growth_pct": m.growth_percentage,
                "emerging": "Y" if m.emerging_flag else "",
            })

    logger.info("[csv_export] growth_skills.csv → %s", path)
    return path


def export_movers_by_delta(metrics: list[WeeklyMetric], output_dir: Path, limit: int = 20) -> Path:
    """
    Write skills ranked by absolute_delta (biggest movers by absolute job count change).
    Returns path to written file.
    """
    path = output_dir / "movers_delta.csv"
    sorted_metrics = sorted(metrics, key=lambda m: m.absolute_delta, reverse=True)[:limit]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["rank", "skill", "category", "absolute_delta", "frequency", "growth_pct"]
        )
        writer.writeheader()
        for rank, m in enumerate(sorted_metrics, start=1):
            writer.writerow({
                "rank": rank,
                "skill": m.skill_name,
                "category": m.category,
                "absolute_delta": m.absolute_delta,
                "frequency": m.frequency,
                "growth_pct": m.growth_percentage,
            })

    logger.info("[csv_export] movers_delta.csv → %s", path)
    return path


def export_movers_by_score(metrics: list[WeeklyMetric], output_dir: Path, limit: int = 20) -> Path:
    """
    Write skills ranked by mover_score (delta * log1p(frequency)).
    Returns path to written file.
    """
    path = output_dir / "movers_score.csv"
    sorted_metrics = sorted(metrics, key=lambda m: m.mover_score, reverse=True)[:limit]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["rank", "skill", "category", "mover_score", "absolute_delta", "frequency"]
        )
        writer.writeheader()
        for rank, m in enumerate(sorted_metrics, start=1):
            writer.writerow({
                "rank": rank,
                "skill": m.skill_name,
                "category": m.category,
                "mover_score": m.mover_score,
                "absolute_delta": m.absolute_delta,
                "frequency": m.frequency,
            })

    logger.info("[csv_export] movers_score.csv → %s", path)
    return path


def export_sources_breakdown(sources_data: list[dict], output_dir: Path) -> Path:
    """
    Write source performance breakdown.
    
    Args:
        sources_data: List of {source_name, job_count, pct} from coverage_metrics
    
    Returns path to written file.
    """
    path = output_dir / "sources_breakdown.csv"

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["source_name", "job_count", "pct_of_total"]
        )
        writer.writeheader()
        for source in sources_data:
            writer.writerow({
                "source_name": source["source_name"],
                "job_count": source["job_count"],
                "pct_of_total": source["pct"],
            })

    logger.info("[csv_export] sources_breakdown.csv → %s", path)
    return path


def export_locations(locations_data: list[dict], output_dir: Path) -> Path:
    """
    Write geographic distribution.
    
    Args:
        locations_data: List of {country, job_count, pct} from coverage_metrics
    
    Returns path to written file.
    """
    path = output_dir / "locations.csv"

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["country", "job_count", "pct_of_total"]
        )
        writer.writeheader()
        for location in locations_data:
            writer.writerow({
                "country": location["country"],
                "job_count": location["job_count"],
                "pct_of_total": location["pct"],
            })

    logger.info("[csv_export] locations.csv → %s", path)
    return path


def export_companies(companies_data: list[dict], output_dir: Path) -> Path:
    """
    Write top hiring companies.
    
    Args:
        companies_data: List of {company, job_count, pct} from coverage_metrics
    
    Returns path to written file.
    """
    path = output_dir / "companies.csv"

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["company", "job_count", "pct_of_total"]
        )
        writer.writeheader()
        for company in companies_data:
            writer.writerow({
                "company": company["company"],
                "job_count": company["job_count"],
                "pct_of_total": company["pct"],
            })

    logger.info("[csv_export] companies.csv → %s", path)
    return path


# ─── Phase 2: Deep-Dive Exports ───────────────────────────────────────────────


def export_skill_pairs(co_occurrence_matrix: dict, output_dir: Path, limit: int = 50) -> Path:
    """
    Write top skill pairs (co-occurrence) to CSV.
    
    Args:
        co_occurrence_matrix: Nested dict {skill_a: {skill_b: count}}
        output_dir: Output directory
        limit: Max pairs to export
    
    Returns path to written file.
    """
    path = output_dir / "skill_pairs.csv"
    
    # Flatten matrix into list of pairs
    pairs = []
    for skill_a, co_skills in co_occurrence_matrix.items():
        for skill_b, count in co_skills.items():
            pairs.append({
                "skill_a": skill_a,
                "skill_b": skill_b,
                "co_occurrence_count": count
            })
    
    # Sort by count descending
    pairs.sort(key=lambda p: p["co_occurrence_count"], reverse=True)
    pairs = pairs[:limit]
    
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["skill_a", "skill_b", "co_occurrence_count"]
        )
        writer.writeheader()
        writer.writerows(pairs)
    
    logger.info("[csv_export] skill_pairs.csv → %s", path)
    return path


def export_job_titles(titles_data: list[dict], output_dir: Path) -> Path:
    """
    Write top job titles with counts.
    
    Args:
        titles_data: List of {title, job_count, pct, top_skills}
    
    Returns path to written file.
    """
    path = output_dir / "job_titles.csv"
    
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["rank", "title", "job_count", "pct_of_total", "top_skills"]
        )
        writer.writeheader()
        for rank, title in enumerate(titles_data, start=1):
            writer.writerow({
                "rank": rank,
                "title": title["title"],
                "job_count": title["job_count"],
                "pct_of_total": title["pct"],
                "top_skills": ", ".join(title.get("top_skills", []))
            })
    
    logger.info("[csv_export] job_titles.csv → %s", path)
    return path


def export_title_trends(trends_data: list[dict], output_dir: Path) -> Path:
    """
    Write job title growth trends.
    
    Args:
        trends_data: List of {title, current_count, prior_count, delta, growth_pct}
    
    Returns path to written file.
    """
    path = output_dir / "title_trends.csv"
    
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["title", "current_count", "prior_count", "delta", "growth_pct"]
        )
        writer.writeheader()
        writer.writerows(trends_data)
    
    logger.info("[csv_export] title_trends.csv → %s", path)
    return path


def export_skill_trends(trends_data: list[dict], output_dir: Path) -> Path:
    """
    Write temporal skill trends (velocity, momentum).
    
    Args:
        trends_data: List of {skill_name, category, current_freq, velocity, momentum, volatility}
    
    Returns path to written file.
    """
    path = output_dir / "skill_trends.csv"
    
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["skill_name", "category", "current_freq", "velocity", "momentum", "volatility"]
        )
        writer.writeheader()
        for trend in trends_data:
            writer.writerow({
                "skill_name": trend["skill_name"],
                "category": trend["category"],
                "current_freq": trend["current_freq"],
                "velocity": trend["velocity"],
                "momentum": trend["momentum"],
                "volatility": trend["volatility"]
            })
    
    logger.info("[csv_export] skill_trends.csv → %s", path)
    return path


def export_categories(categories_data: list[dict], output_dir: Path) -> Path:
    """
    Write skill category breakdown.
    
    Args:
        categories_data: List of {category, skill_count, total_mentions, pct, top_skills}
    
    Returns path to written file.
    """
    path = output_dir / "categories.csv"
    
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["category", "skill_count", "total_mentions", "pct_of_total", "top_skills"]
        )
        writer.writeheader()
        for cat in categories_data:
            writer.writerow({
                "category": cat["category"],
                "skill_count": cat["skill_count"],
                "total_mentions": cat["total_mentions"],
                "pct_of_total": cat["pct"],
                "top_skills": ", ".join(cat.get("top_skills", []))
            })
    
    logger.info("[csv_export] categories.csv → %s", path)
    return path
