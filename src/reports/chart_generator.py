"""
src/reports/chart_generator.py
───────────────────────────────
Generates PNG chart images from charts.json using matplotlib.

Creates publication-ready charts for Substack:
- top_skills.png
- growth_skills.png
- movers_delta.png
- movers_score.png
- remote_split.png
- sources_breakdown.png
- top_locations.png
- top_companies.png

All charts saved to the same weekly output folder as charts.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib

# Use non-interactive backend for server environments
matplotlib.use('Agg')

logger = logging.getLogger(__name__)

# Chart styling constants
DPI = 150
FIGSIZE_BAR = (10, 6)
FIGSIZE_PIE = (8, 8)
COLOR_PRIMARY = '#2563eb'  # Blue
COLOR_SECONDARY = '#10b981'  # Green
COLOR_ACCENT = '#f59e0b'  # Orange
COLOR_NEGATIVE = '#ef4444'  # Red


def generate_all_charts(charts_json_path: Path) -> list[Path]:
    """
    Load charts.json and generate all PNG charts.
    
    Returns list of paths to generated chart files.
    """
    logger.info("[chart_generator] Loading charts.json from %s", charts_json_path)
    
    with charts_json_path.open("r", encoding="utf-8") as f:
        charts_data = json.load(f)
    
    output_dir = charts_json_path.parent
    generated_charts = []
    
    # ── Top skills bar chart ──────────────────────────────────────────────────
    if "top_skills_bar" in charts_data:
        path = _generate_horizontal_bar(
            data=charts_data["top_skills_bar"],
            output_path=output_dir / "top_skills.png",
            title="Top Skills This Week",
            xlabel="Number of Jobs",
            color=COLOR_PRIMARY,
        )
        if path:
            generated_charts.append(path)
    
    # ── Growth skills bar chart ───────────────────────────────────────────────
    if "growth_bar" in charts_data:
        path = _generate_horizontal_bar(
            data=charts_data["growth_bar"],
            output_path=output_dir / "growth_skills.png",
            title="Fastest Growing Skills (% Growth)",
            xlabel="Growth Percentage (%)",
            color=COLOR_SECONDARY,
        )
        if path:
            generated_charts.append(path)
    
    # ── Movers by delta ───────────────────────────────────────────────────────
    if "movers_by_delta" in charts_data:
        path = _generate_diverging_bar(
            data=charts_data["movers_by_delta"],
            output_path=output_dir / "movers_delta.png",
            title="Top Movers (Absolute Job Count Change)",
            xlabel="Change in Job Count",
        )
        if path:
            generated_charts.append(path)
    
    # ── Movers by score ───────────────────────────────────────────────────────
    if "movers_by_score" in charts_data:
        path = _generate_horizontal_bar(
            data=charts_data["movers_by_score"],
            output_path=output_dir / "movers_score.png",
            title="Top Movers (Weighted Score)",
            xlabel="Mover Score",
            color=COLOR_ACCENT,
        )
        if path:
            generated_charts.append(path)
    
    # ── Remote split pie chart ────────────────────────────────────────────────
    if "remote_split" in charts_data:
        path = _generate_pie_chart(
            data=charts_data["remote_split"],
            output_path=output_dir / "remote_split.png",
            title="Remote Type Distribution",
        )
        if path:
            generated_charts.append(path)
    
    # ── Sources breakdown ─────────────────────────────────────────────────────
    if "sources_bar" in charts_data:
        path = _generate_horizontal_bar(
            data=charts_data["sources_bar"],
            output_path=output_dir / "sources_breakdown.png",
            title="Jobs by Source",
            xlabel="Number of Jobs",
            color=COLOR_PRIMARY,
        )
        if path:
            generated_charts.append(path)
    
    # ── Top locations ─────────────────────────────────────────────────────────
    if "locations_bar" in charts_data:
        path = _generate_horizontal_bar(
            data=charts_data["locations_bar"],
            output_path=output_dir / "top_locations.png",
            title="Top Locations",
            xlabel="Number of Jobs",
            color=COLOR_SECONDARY,
        )
        if path:
            generated_charts.append(path)
    
    # ── Top companies ─────────────────────────────────────────────────────────
    if "companies_bar" in charts_data:
        path = _generate_horizontal_bar(
            data=charts_data["companies_bar"],
            output_path=output_dir / "top_companies.png",
            title="Top Hiring Companies",
            xlabel="Number of Jobs",
            color=COLOR_ACCENT,
        )
        if path:
            generated_charts.append(path)
    
    # ── Phase 2: Skill trends line chart ──────────────────────────────────────
    if "skill_trends_line" in charts_data:
        path = _generate_line_chart(
            data=charts_data["skill_trends_line"],
            output_path=output_dir / "skill_trends.png",
            title="Skill Frequency Trends (8-Week History)",
            ylabel="Job Mentions",
        )
        if path:
            generated_charts.append(path)
    
    # ── Phase 2: Categories breakdown ─────────────────────────────────────────
    if "categories_bar" in charts_data:
        path = _generate_horizontal_bar(
            data=charts_data["categories_bar"],
            output_path=output_dir / "categories.png",
            title="Skills by Category",
            xlabel="Total Mentions",
            color='#8b5cf6',  # Purple
        )
        if path:
            generated_charts.append(path)
    
    logger.info("[chart_generator] Generated %d chart images", len(generated_charts))
    return generated_charts


def _generate_horizontal_bar(
    data: dict,
    output_path: Path,
    title: str,
    xlabel: str,
    color: str,
    limit: int = 15,
) -> Path | None:
    """Generate horizontal bar chart."""
    try:
        labels = data.get("labels", [])[:limit]
        values = data.get("values", [])[:limit]
        
        if not labels or not values:
            logger.warning("[chart_generator] Skipping %s (no data)", output_path.name)
            return None
        
        # Reverse for top-to-bottom display
        labels = labels[::-1]
        values = values[::-1]
        
        fig, ax = plt.subplots(figsize=FIGSIZE_BAR)
        ax.barh(range(len(labels)), values, color=color, alpha=0.8)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        ax.set_xlabel(xlabel, fontsize=11)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        ax.grid(axis='x', alpha=0.3, linestyle='--')
        
        # Add value labels on bars
        for i, v in enumerate(values):
            ax.text(v, i, f' {v}', va='center', fontsize=9)
        
        plt.tight_layout()
        fig.savefig(output_path, dpi=DPI, bbox_inches='tight')
        plt.close(fig)
        
        logger.info("[chart_generator] %s → %s", output_path.name, output_path)
        return output_path
        
    except Exception as e:
        logger.error("[chart_generator] Error generating %s: %s", output_path.name, e)
        return None


def _generate_diverging_bar(
    data: dict,
    output_path: Path,
    title: str,
    xlabel: str,
    limit: int = 15,
) -> Path | None:
    """Generate diverging bar chart (for positive/negative values)."""
    try:
        labels = data.get("labels", [])[:limit]
        values = data.get("values", [])[:limit]
        
        if not labels or not values:
            logger.warning("[chart_generator] Skipping %s (no data)", output_path.name)
            return None
        
        # Reverse for top-to-bottom
        labels = labels[::-1]
        values = values[::-1]
        
        # Color bars based on positive/negative
        colors = [COLOR_SECONDARY if v >= 0 else COLOR_NEGATIVE for v in values]
        
        fig, ax = plt.subplots(figsize=FIGSIZE_BAR)
        ax.barh(range(len(labels)), values, color=colors, alpha=0.8)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        ax.set_xlabel(xlabel, fontsize=11)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        ax.axvline(x=0, color='black', linestyle='-', linewidth=0.8)
        ax.grid(axis='x', alpha=0.3, linestyle='--')
        
        # Add value labels
        for i, v in enumerate(values):
            ha = 'left' if v >= 0 else 'right'
            ax.text(v, i, f' {v:+d} ', va='center', ha=ha, fontsize=9)
        
        plt.tight_layout()
        fig.savefig(output_path, dpi=DPI, bbox_inches='tight')
        plt.close(fig)
        
        logger.info("[chart_generator] %s → %s", output_path.name, output_path)
        return output_path
        
    except Exception as e:
        logger.error("[chart_generator] Error generating %s: %s", output_path.name, e)
        return None


def _generate_pie_chart(
    data: dict,
    output_path: Path,
    title: str,
) -> Path | None:
    """Generate pie/donut chart."""
    try:
        labels = data.get("labels", [])
        values = data.get("values", [])
        
        if not labels or not values:
            logger.warning("[chart_generator] Skipping %s (no data)", output_path.name)
            return None
        
        # Color scheme for remote types
        colors_map = {
            "Remote": COLOR_SECONDARY,
            "Hybrid": COLOR_ACCENT,
            "On-site": COLOR_PRIMARY,
            "Unknown": '#6b7280',  # Gray
        }
        colors = [colors_map.get(label, COLOR_PRIMARY) for label in labels]
        
        fig, ax = plt.subplots(figsize=FIGSIZE_PIE)
        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct='%1.1f%%',
            startangle=90,
            textprops={'fontsize': 11},
        )
        
        # Make percentage text bold and white
        for autotext in autotexts:
            autotext.set_color('white')
            autotext.set_fontweight('bold')
            autotext.set_fontsize(12)
        
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        
        plt.tight_layout()
        fig.savefig(output_path, dpi=DPI, bbox_inches='tight')
        plt.close(fig)
        
        logger.info("[chart_generator] %s → %s", output_path.name, output_path)
        return output_path
        
    except Exception as e:
        logger.error("[chart_generator] Error generating %s: %s", output_path.name, e)
        return None


def _generate_line_chart(
    data: dict,
    output_path: Path,
    title: str,
    ylabel: str,
) -> Path | None:
    """Generate multi-line time series chart for skill trends."""
    try:
        # data format: {labels: [dates], series: [{name, values}]}
        
        if "series" not in data or not data["series"]:
            logger.warning("[chart_generator] Skipping %s (no series data)", output_path.name)
            return None
        
        labels = data.get("labels", [])  # Week labels (x-axis)
        series_list = data.get("series", [])  # List of {name, values}
        
        if not labels or not series_list:
            logger.warning("[chart_generator] Skipping %s (insufficient data)", output_path.name)
            return None
        
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # Plot each skill as a separate line
        colors = [COLOR_PRIMARY, COLOR_SECONDARY, COLOR_ACCENT, '#8b5cf6']  # Purple for 4th
        
        for idx, series in enumerate(series_list[:5]):  # Limit to 5 lines
            skill_name = series.get("name", f"Skill {idx+1}")
            values = series.get("values", [])
            color = colors[idx % len(colors)]
            
            ax.plot(labels, values, marker='o', linewidth=2, label=skill_name, color=color)
        
        ax.set_xlabel("Week", fontsize=11)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(loc='best', fontsize=10)
        
        # Rotate x-axis labels for readability
        plt.xticks(rotation=45, ha='right')
        
        plt.tight_layout()
        fig.savefig(output_path, dpi=DPI, bbox_inches='tight')
        plt.close(fig)
        
        logger.info("[chart_generator] %s → %s", output_path.name, output_path)
        return output_path
        
    except Exception as e:
        logger.error("[chart_generator] Error generating %s: %s", output_path.name, e)
        return None
