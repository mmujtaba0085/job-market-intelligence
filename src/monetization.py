"""
src/monetization.py
────────────────────
Splits full weekly metrics/report into free vs premium tiers.

Free tier:    top_skills + remote_ratio
Premium tier: top_skills + remote_ratio + growth_percentage + co_occurrence
              + country_breakdown + emerging_skills

Output: two separate report.md variants saved alongside the main report.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import TOP_SKILLS_LIMIT
from src.storage.models import WeeklyMetric

logger = logging.getLogger(__name__)

MONETIZATION_MODE = {
    "free": ["top_skills", "remote_ratio"],
    "premium": [
        "top_skills", "remote_ratio",
        "growth_percentage", "co_occurrence",
        "country_breakdown", "emerging_skills",
    ],
}


def generate_free_report(
    metrics: list[WeeklyMetric],
    remote_ratio: float,
    output_dir: Path,
    market_name: str,
    week: str,
) -> Path:
    """Write report_free.md — top skills + remote ratio only."""
    top = sorted(metrics, key=lambda m: m.frequency, reverse=True)[:TOP_SKILLS_LIMIT]
    lines = [
        f"# Week {week} — {market_name} (Free Edition)\n",
        "## Top Skills This Week\n",
        "| Rank | Skill | Category | Frequency |",
        "|------|-------|----------|-----------|",
    ]
    for i, m in enumerate(top, 1):
        lines.append(f"| {i} | {m.skill_name.title()} | {m.category} | {m.frequency} |")

    lines += [
        "",
        "## Remote Hiring Trend",
        f"{round(remote_ratio * 100, 1)}% of postings were fully remote this week.",
        "",
        "*Upgrade to premium for growth trends, emerging skill signals, and co-occurrence analysis.*",
    ]

    path = output_dir / "report_free.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[monetization] report_free.md → %s", path)
    return path


def generate_premium_report(
    metrics: list[WeeklyMetric],
    co_occurrence: dict | None,
    remote_ratio: float,
    output_dir: Path,
    market_name: str,
    week: str,
) -> Path:
    """
    Write report_premium.md — includes growth, emerging, co-occurrence.
    The full report.md already serves as the premium edition in practice;
    this generates a clearly labelled version for future paywall gating.
    """
    top = sorted(metrics, key=lambda m: m.frequency, reverse=True)[:TOP_SKILLS_LIMIT]
    growth = sorted(
        [m for m in metrics if m.growth_percentage > 0],
        key=lambda m: m.growth_percentage,
        reverse=True,
    )[:TOP_SKILLS_LIMIT]
    emerging = [m for m in metrics if m.emerging_flag]

    lines = [
        f"# Week {week} — {market_name} (Premium Edition)\n",
        "## Top Skills",
        "| Rank | Skill | Category | Frequency | Growth | Signal |",
        "|------|-------|----------|-----------|--------|--------|",
    ]
    for i, m in enumerate(top, 1):
        signal = "🚀" if m.emerging_flag else ("📉" if m.declining_flag else "—")
        lines.append(
            f"| {i} | {m.skill_name.title()} | {m.category} | {m.frequency} | {m.growth_percentage:+.1f}% | {signal} |"
        )

    lines += ["", "## Fastest Growing Skills",
              "| Rank | Skill | Growth | Frequency |",
              "|------|-------|--------|-----------|"]
    for i, m in enumerate(growth, 1):
        lines.append(f"| {i} | {m.skill_name.title()} | {m.growth_percentage:+.1f}% | {m.frequency} |")

    if emerging:
        lines += ["", "## Emerging Signals 🚀"]
        for m in emerging:
            lines.append(f"- **{m.skill_name.title()}** — {m.frequency} mentions, {m.growth_percentage:+.1f}% growth")

    lines += ["", f"**Remote ratio:** {round(remote_ratio * 100, 1)}%"]

    path = output_dir / "report_premium.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[monetization] report_premium.md → %s", path)
    return path
