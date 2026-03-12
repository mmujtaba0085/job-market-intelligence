"""
tests/test_skill_extractor.py
──────────────────────────────
Unit tests for src/skill_extractor.py and src/taxonomy_mapper.py
"""

import pytest
from src.skill_extractor import extract_skills
from src.taxonomy_mapper import resolve_synonym, get_category, map_skill


class TestSkillExtractor:
    def test_detects_known_skill(self):
        signals = extract_skills(1, "ai_ml_global", "Looking for Python expertise")
        names = [s.normalized_skill for s in signals]
        assert "python" in names

    def test_detects_multi_word_skill(self):
        signals = extract_skills(1, "ai_ml_global", "deep learning experience required")
        names = [s.normalized_skill for s in signals]
        assert "deep learning" in names

    def test_no_partial_match(self):
        # "r" should NOT match as skill inside "pytorch"
        signals = extract_skills(2, "ai_ml_global", "Experience with pytorch framework")
        names = [s.normalized_skill for s in signals]
        assert "r" not in names

    def test_deduplication_per_job(self):
        # Python mentioned twice → only one SkillSignal
        signals = extract_skills(3, "ai_ml_global", "python and more python skills needed")
        python_signals = [s for s in signals if s.normalized_skill == "python"]
        assert len(python_signals) == 1

    def test_empty_description(self):
        signals = extract_skills(4, "ai_ml_global", "")
        assert signals == []

    def test_extraction_method_label(self):
        signals = extract_skills(5, "ai_ml_global", "machine learning role")
        assert all(s.extraction_method == "regex_taxonomy" for s in signals)


class TestTaxonomyMapper:
    def test_synonym_resolution(self):
        assert resolve_synonym("k8s") == "kubernetes"
        assert resolve_synonym("sklearn") == "scikit-learn"

    def test_unknown_synonym_passthrough(self):
        assert resolve_synonym("unknownlib") == "unknownlib"

    def test_category_lookup(self):
        assert get_category("python") == "programming"
        assert get_category("pytorch") == "ml_frameworks"
        assert get_category("aws") == "cloud"

    def test_unknown_category_returns_other(self):
        assert get_category("totally_fake_skill_xyz") == "other"

    def test_map_skill_full_pipeline(self):
        normalized, category = map_skill("k8s")
        assert normalized == "kubernetes"
        assert category == "mlops"
