"""
src/taxonomy_mapper.py
───────────────────────
Maps raw detected skill text → normalized_skill + category.

Steps:
  1. Apply SKILL_SYNONYMS to resolve aliases (k8s → kubernetes)
  2. Lookup normalized skill in SKILL_TAXONOMY to find category
  3. Return normalized_skill, category (or "other" if unmapped)
"""

from __future__ import annotations

from config.taxonomy import SKILL_SYNONYMS, SKILL_TAXONOMY

# ── Build a reverse lookup: normalized_skill → category ───────────────────────
_SKILL_TO_CATEGORY: dict[str, str] = {}
for _category, _skills in SKILL_TAXONOMY.items():
    if _category.startswith("_"):   # skip disabled categories
        continue
    for _skill in _skills:
        _SKILL_TO_CATEGORY[_skill.lower()] = _category


def resolve_synonym(raw_skill: str) -> str:
    """Resolve an alias to its canonical normalized form."""
    return SKILL_SYNONYMS.get(raw_skill.lower().strip(), raw_skill.lower().strip())


def get_category(normalized_skill: str) -> str:
    """Return the taxonomy category for a normalized skill, or 'other'."""
    return _SKILL_TO_CATEGORY.get(normalized_skill.lower(), "other")


def map_skill(raw_skill: str) -> tuple[str, str]:
    """
    Full mapping pipeline:
    raw_skill → (normalized_skill, category)
    """
    normalized = resolve_synonym(raw_skill)
    category = get_category(normalized)
    return normalized, category
