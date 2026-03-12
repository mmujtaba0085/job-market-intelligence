"""
src/storage/models.py
─────────────────────
Typed data contracts (dataclasses) shared across all pipeline modules.
These are the stable interfaces between collectors, normalizer,
extractor, storage, analytics, and report generator.

Import order of dependencies:
  JobRaw → JobNormalized → SkillSignal → WeeklyMetric
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Raw collection output
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class JobRaw:
    """
    Output of a collector. Raw, unprocessed job data.
    No normalization happens at this stage.
    """
    source_id: str              # e.g. "remotive"
    source_name: str            # human-readable e.g. "Remotive"
    url: str                    # canonical listing URL
    fetched_at: datetime        # UTC timestamp of collection

    # One of these will be populated, not both
    raw_html: Optional[str] = None
    raw_json: Optional[dict] = None

    # Optionally pre-parsed fields from API responses
    parsed_fields: Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Normalized job (output of normalizer + deduplicator)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class JobNormalized:
    """
    Output of normalizer.py. Clean, structured job record ready for storage.
    Hashes are computed before deduplication check.
    """
    # ── Hashes (dedupe layer) ─────────────────────────────────────────────────
    url_hash: str               # sha256(source_id + url)
    canonical_hash: str         # sha256(title + company + desc[:200]) - NO location
    description_hash: str       # sha256(raw_description)
    job_group_id: str           # First 16 chars of canonical_hash (for grouping multi-location)

    # ── Market + source ───────────────────────────────────────────────────────
    market_id: str
    source_name: str

    # ── Core fields ───────────────────────────────────────────────────────────
    title: str                  # Original title from source (preserved)
    normalized_title: str       # Canonical title for analytics
    normalization_confidence: float  # Confidence score 0.0-1.0 for normalization
    company: str
    country: str
    location: str               # Primary location (first or only)
    remote_type: str            # "remote" | "hybrid" | "on-site" | "unknown"

    # ── Dates ─────────────────────────────────────────────────────────────────
    posted_date: Optional[date]

    # ── Salary ────────────────────────────────────────────────────────────────
    salary_min: Optional[float]
    salary_max: Optional[float]
    currency: Optional[str]

    # ── Content ───────────────────────────────────────────────────────────────
    description_text: str       # required for skill extraction
    url: str
    
    # ── Multi-location support ────────────────────────────────────────────────
    all_locations: Optional[list[str]] = None  # All locations if job has multiple


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: Skill signal (output of extractor + taxonomy mapper)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkillSignal:
    """
    One detected skill from one job description.
    Multiple SkillSignals can be produced per job.
    """
    job_id: int                 # FK → jobs.job_id
    market_id: str

    raw_detected_skill: str     # exactly as found in text
    normalized_skill: str       # after synonym resolution
    category: str               # from SKILL_TAXONOMY

    confidence_score: Optional[float] = None   # for future LLM-based extraction
    extraction_method: str = "regex_taxonomy"  # "regex_taxonomy" | "llm"


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4: Weekly aggregated metric (output of analytics engine)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WeeklyMetric:
    """
    One row of aggregated analytics for a skill in a market in a given week.
    Written to weekly_metrics table by analytics/weekly_metrics.py.
    """
    market_id: str
    week_start_date: date       # always a Monday (ISO)
    week_number: int            # ISO week number

    skill_name: str             # normalized skill
    category: str

    frequency: int              # count of jobs mentioning this skill this week
    growth_percentage: float    # WoW growth vs EMERGING_LOOKBACK_WEEKS ago
    
    # Enhanced growth metrics
    absolute_delta: int = 0     # this_week_freq - prior_week_freq
    mover_score: float = 0.0    # delta * log1p(frequency) - penalizes low-base spikes

    emerging_flag: bool = False  # freq >= MIN_FREQ AND growth >= GROWTH_THRESHOLD
    declining_flag: bool = False # freq >= MIN_FREQ AND growth <= DECLINING_THRESHOLD
