"""
src/skill_extractor.py
───────────────────────
Extracts skills from a job description using regex + taxonomy matching.

Pipeline per job:
  1. Lowercase + sanitize description
  2. Slide over all known skills in SKILL_TAXONOMY (including synonyms)
  3. Use word-boundary regex to avoid partial matches ("r" inside "pytorch")
  4. Map raw detected text → normalized_skill + category via taxonomy_mapper
  5. Deduplicate per-job (one SkillSignal per unique normalized_skill)

Returns: list[SkillSignal]
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from config.taxonomy import SKILL_SYNONYMS, SKILL_TAXONOMY
from src.storage.models import SkillSignal
from src.taxonomy_mapper import map_skill

logger = logging.getLogger(__name__)

# ── Build complete search term list from taxonomy + synonyms ──────────────────
# Longer phrases must be checked before shorter ones to avoid partial matches.
_ALL_TERMS: list[str] = []

for _skills in SKILL_TAXONOMY.values():
    _ALL_TERMS.extend(_skills)
_ALL_TERMS.extend(SKILL_SYNONYMS.keys())

# Sort longest-first so "machine learning" matches before "learning"
_ALL_TERMS = sorted(set(t.lower() for t in _ALL_TERMS), key=len, reverse=True)

# Pre-compile one regex per term for performance
_TERM_PATTERNS: list[tuple[str, re.Pattern]] = [
    (term, re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE))
    for term in _ALL_TERMS
]


def extract_skills(
    job_id: int,
    market_id: str,
    description: str,
) -> list[SkillSignal]:
    """
    Extract all detectable skills from a job description.
    Returns one SkillSignal per unique normalized_skill (deduped per job).
    """
    if not description:
        return []

    seen_normalized: set[str] = set()
    signals: list[SkillSignal] = []

    for raw_term, pattern in _TERM_PATTERNS:
        if pattern.search(description):
            normalized, category = map_skill(raw_term)

            # Skip if we've already recorded this normalized skill for this job
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)

            signals.append(
                SkillSignal(
                    job_id=job_id,
                    market_id=market_id,
                    raw_detected_skill=raw_term,
                    normalized_skill=normalized,
                    category=category,
                    confidence_score=None,
                    extraction_method="regex_taxonomy",
                )
            )

    return signals


def extract_skills_batch(
    job_ids_and_descriptions: list[tuple[int, str]],
    market_id: str,
) -> list[SkillSignal]:
    """
    Batch extraction for multiple jobs.
    Input: list of (job_id, description_text) tuples.
    """
    all_signals: list[SkillSignal] = []
    for job_id, description in job_ids_and_descriptions:
        signals = extract_skills(job_id, market_id, description)
        all_signals.extend(signals)
        logger.debug("[skill_extractor] job_id=%d → %d skills", job_id, len(signals))
    return all_signals
