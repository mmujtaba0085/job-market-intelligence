"""
src/reports/markdown_report.py
────────────────────────────────
Generates report.md using Jinja2 templating.
Output follows the Substack formatting rules exactly as defined in the plan.

Report structure:
  # Week W{XX} — {Market Name}
  ## Executive Summary
  ## Top Skills This Week
  ## Fastest Growing Skills
  ## Emerging Signals
  ## Remote Hiring Trend
  ## Methodology & Caveats
  ## Data Sources
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from jinja2 import Environment, BaseLoader

from config.settings import GROWTH_THRESHOLD, MIN_FREQ, TOP_SKILLS_LIMIT, GROWTH_SKILLS_LIMIT
from src.storage.models import WeeklyMetric

logger = logging.getLogger(__name__)

_REPORT_TEMPLATE = """\
# {{ market_name }} — Week W{{ week_number }}

*{{ week_start }} · {{ jobs_collected }} jobs analysed · {{ sources_count }} sources*

---

## Executive Summary

{%- set top3 = top_skills[:3] %}
{%- set emerging_count = metrics | selectattr('emerging_flag') | list | length %}
{%- set remote_pct = (remote_ratio * 100) | round(1) %}
{%- set top_mover = movers_by_score[0] if movers_by_score else None %}
{%- set top_source = coverage.sources_breakdown[0] if coverage and coverage.sources_breakdown else None %}
- **{{ jobs_collected }} jobs analyzed** this week ({{ coverage.total_jobs if coverage else jobs_collected }} after deduplication)
- **Top skill:** {{ top3[0].skill_name | title }} ({{ top3[0].frequency }} mentions)
{%- if top_mover %}
- **Biggest mover:** {{ top_mover.skill_name | title }} (+{{ top_mover.absolute_delta }} jobs, mover score: {{ top_mover.mover_score }})
{%- endif %}
- **{{ remote_pct }}%** remote positions
{%- if top_source %}
- **Top source:** {{ top_source.source_name }} ({{ top_source.job_count }} jobs, {{ top_source.pct }}%)
{%- endif %}
- **{{ emerging_count }} emerging skills** detected (≥{{ growth_threshold }}% growth)

---

## Coverage

### Jobs Analyzed
- **Total jobs collected:** {{ jobs_collected }}
{%- if coverage %}
- **Inserted:** {{ coverage.total_jobs }}
- **Deduplicated:** {{ jobs_collected - coverage.total_jobs }}
{%- endif %}

{%- if coverage and coverage.remote_breakdown %}

### Remote Type Distribution
{%- for remote_type, count in coverage.remote_breakdown.items() %}
- **{{ remote_type }}:** {{ count }} jobs
{%- endfor %}

![Remote Split](remote_split.png)
{%- endif %}

{%- if coverage and coverage.sources_breakdown %}

### Jobs by Source
| Source | Jobs | % of Total |
|--------|------|------------|
{%- for source in coverage.sources_breakdown %}
| {{ source.source_name }} | {{ source.job_count }} | {{ source.pct }}% |
{%- endfor %}

![Sources Breakdown](sources_breakdown.png)
{%- endif %}

{%- if coverage and coverage.countries_breakdown %}

### Top Locations
{%- for location in coverage.countries_breakdown[:5] %}
- **{{ location.country }}:** {{ location.job_count }} jobs ({{ location.pct }}%)
{%- endfor %}

![Top Locations](top_locations.png)
{%- endif %}

{%- if coverage and coverage.companies_breakdown %}

### Top Hiring Companies
{%- for company in coverage.companies_breakdown[:5] %}
- **{{ company.company }}:** {{ company.job_count }} jobs
{%- endfor %}

![Top Companies](top_companies.png)
{%- endif %}

---

## Top Skills This Week

| Rank | Skill | Category | Frequency | WoW Growth | Signal |
|------|-------|----------|-----------|------------|--------|
{%- for m in top_skills %}
| {{ loop.index }} | {{ m.skill_name | title }} | {{ m.category }} | {{ m.frequency }} | {{ '%+.1f' | format(m.growth_percentage) }}% | {% if m.emerging_flag %}🚀{% elif m.declining_flag %}📉{% else %}—{% endif %} |
{%- endfor %}

![Top Skills](top_skills.png)

---

## Top Movers

### By Absolute Growth
*Skills with biggest job count increase*

| Rank | Skill | Delta | Current Frequency | Growth % |
|------|-------|-------|-------------------|----------|
{%- for m in movers_by_delta[:10] %}
| {{ loop.index }} | {{ m.skill_name | title }} | {{ '%+d' | format(m.absolute_delta) }} | {{ m.frequency }} | {{ '%+.1f' | format(m.growth_percentage) }}% |
{%- endfor %}

![Movers by Delta](movers_delta.png)

### By Weighted Score
*Penalizes low-base spikes using: delta × log(1 + frequency)*

| Rank | Skill | Mover Score | Delta | Frequency |
|------|-------|-------------|-------|-----------|
{%- for m in movers_by_score[:10] %}
| {{ loop.index }} | {{ m.skill_name | title }} | {{ m.mover_score }} | {{ '%+d' | format(m.absolute_delta) }} | {{ m.frequency }} |
{%- endfor %}

![Movers by Score](movers_score.png)

---

## Fastest Growing Skills

| Rank | Skill | Category | Growth | Frequency |
|------|-------|----------|--------|-----------|
{%- for m in growth_skills %}
| {{ loop.index }} | {{ m.skill_name | title }} | {{ m.category }} | {{ '%+.1f' | format(m.growth_percentage) }}% | {{ m.frequency }} |
{%- endfor %}

![Growth Skills](growth_skills.png)

---

## Emerging Signals 🚀

{%- set emerging = metrics | selectattr('emerging_flag') | list %}
{%- if emerging %}
Skills newly surging this week (frequency ≥ {{ min_freq }}, growth ≥ {{ growth_threshold }}%):

{%- for m in emerging %}
- **{{ m.skill_name | title }}** — {{ m.frequency }} mentions, {{ '%+.1f' | format(m.growth_percentage) }}% growth *({{ m.category }})*
{%- endfor %}
{%- else %}
No new emerging skill signals detected this week.
{%- endif %}

---

{%- if title_stats %}

## Job Title Insights 📋

{%- set top_titles = title_stats.get('top_titles', []) %}
{%- if top_titles %}

### Most Common Titles
{%- for title in top_titles[:10] %}
{{ loop.index }}. **{{ title.title }}** — {{ title.job_count }} jobs
   {%- if title.top_skills %}
   - Top skills: {{ title.top_skills | join(', ') }}
   {%- endif %}
{%- endfor %}

{%- set title_trends = title_stats.get('title_trends', []) %}
{%- if title_trends %}

### Trending Titles
{%- for trend in title_trends[:5] %}
- **{{ trend.title }}**: {{ '%+d' | format(trend.delta) }} jobs ({{ '%+.1f' | format(trend.growth_pct) }}%)
{%- endfor %}
{%- endif %}

{%- endif %}

---

{%- endif %}

{%- if trend_stats %}

## Skill Trends 📈

{%- set skill_trends = trend_stats.get('skill_trends', []) %}
{%- if skill_trends %}

### Momentum Analysis
{%- set accelerating = trend_stats.get('accelerating_count', 0) %}
{%- set decelerating = trend_stats.get('decelerating_count', 0) %}
{%- set stable = trend_stats.get('stable_count', 0) %}

- **Accelerating:** {{ accelerating }} skills showing increasing growth
- **Decelerating:** {{ decelerating }} skills slowing down  
- **Stable:** {{ stable }} skills maintaining steady demand

### Top Skills by Velocity
{%- for trend in skill_trends[:5] %}
- **{{ trend.skill_name | title }}**: {{ '%+.1f' | format(trend.velocity) }} jobs/week · {{ trend.momentum }}
{%- endfor %}

![Skill Trends](skill_trends.png)

{%- endif %}

---

{%- endif %}

{%- if category_stats %}

## Category Breakdown 🏷️

{%- set categories = category_stats.get('category_breakdown', []) %}
{%- if categories %}

### Skills by Category
| Category | Unique Skills | Total Mentions | % of Market |
|----------|---------------|----------------|-------------|
{%- for cat in categories %}
| {{ cat.category }} | {{ cat.skill_count }} | {{ cat.total_mentions }} | {{ cat.pct }}% |
{%- endfor %}

**Dominant Category:** {{ category_stats.get('dominant_category', 'N/A') }}

![Categories](categories.png)

{%- endif %}

---

{%- endif %}

{%- if skill_pairs %}

## Skill Combinations 🔗

### Most Common Skill Pairs
{%- for pair in skill_pairs[:10] %}
{{ loop.index }}. **{{ pair.skill_a | title }}** + **{{ pair.skill_b | title }}** — {{ pair.co_occurrence_count }} jobs
{%- endfor %}

---

{%- endif %}

## Remote Hiring Trend

**{{ remote_pct }}%** of job postings in {{ market_name }} were fully remote this week.

{%- if remote_pct > 50 %}
Remote-first continues to dominate hiring for this market.
{%- elif remote_pct > 25 %}
Remote roles remain significant but hybrid and on-site are equally present.
{%- else %}
On-site and hybrid arrangements dominate this week's postings.
{%- endif %}

---

## Methodology & Caveats

Data collected from: {{ sources | join(', ') }}. Analysis covers jobs ingested during the week of {{ week_start }}. Skill detection uses regex matching against a curated taxonomy of {{ taxonomy_size }}+ terms. Growth figures compare current week frequency to {{ lookback_weeks }} week{{ 's' if lookback_weeks != 1 else '' }} prior. Emerging threshold: frequency ≥ {{ min_freq }} AND growth ≥ {{ growth_threshold }}%. Duplicate listings are deduplicated using URL and content fingerprinting.

---

## Data Sources

{%- for source in sources %}
- {{ source }}
{%- endfor %}

*Generated by Job Market Intelligence Engine · {{ week_start }}*
"""


def generate_markdown_report(
    market: dict,
    week_start: date,
    metrics: list[WeeklyMetric],
    output_dir: Path,
    remote_ratio: float,
    sources_used: list[str],
    jobs_collected: int,
    coverage_data: dict = None,
    title_stats: dict = None,
    trend_stats: dict = None,
    category_stats: dict = None,
    skill_pairs: list[dict] = None,
    taxonomy_size: int = 80,
    lookback_weeks: int = 4,
) -> Path:
    """
    Render report.md from template and write to output_dir.
    Returns the path to the written file.
    """
    top_skills = sorted(metrics, key=lambda m: m.frequency, reverse=True)[:TOP_SKILLS_LIMIT]
    growth_skills = sorted(
        [m for m in metrics if m.growth_percentage > 0],
        key=lambda m: m.growth_percentage,
        reverse=True,
    )[:GROWTH_SKILLS_LIMIT]

    # Top movers by absolute delta (positive only)
    movers_by_delta = sorted(
        [m for m in metrics if m.absolute_delta > 0],
        key=lambda m: m.absolute_delta,
        reverse=True,
    )

    # Top movers by weighted score (positive only)
    movers_by_score = sorted(
        [m for m in metrics if m.mover_score > 0],
        key=lambda m: m.mover_score,
        reverse=True,
    )

    env = Environment(loader=BaseLoader())
    env.filters["selectattr"] = _selectattr_filter
    template = env.from_string(_REPORT_TEMPLATE)

    rendered = template.render(
        market_name=market.get("display_name", market["market_id"]),
        week_number=week_start.isocalendar()[1],
        week_start=week_start.isoformat(),
        top_skills=top_skills,
        growth_skills=growth_skills,
        movers_by_delta=movers_by_delta,
        movers_by_score=movers_by_score,
        metrics=metrics,
        remote_ratio=remote_ratio,
        sources=sources_used,
        sources_count=len(sources_used),
        jobs_collected=jobs_collected,
        coverage=coverage_data,
        title_stats=title_stats,
        trend_stats=trend_stats,
        category_stats=category_stats,
        skill_pairs=skill_pairs,
        min_freq=MIN_FREQ,
        growth_threshold=GROWTH_THRESHOLD,
        taxonomy_size=taxonomy_size,
        lookback_weeks=lookback_weeks,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.md"
    path.write_text(rendered, encoding="utf-8")
    logger.info("[markdown_report] report.md written → %s", path)
    return path


# ─── Jinja2 helper filters ────────────────────────────────────────────────────

def _selectattr_filter(iterable, attr):
    """Simple selectattr that filters items where attr is truthy."""
    return [item for item in iterable if getattr(item, attr, False)]
