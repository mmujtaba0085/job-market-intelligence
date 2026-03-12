"""
tests/test_analytics.py
────────────────────────
Integration tests for analytics/weekly_metrics.py using in-memory SQLite.
"""

import sqlite3
import pytest
from datetime import date, datetime, timezone
from unittest.mock import patch

from src.analytics.emerging_detector import assign_flags
from config.settings import GROWTH_THRESHOLD, DECLINING_THRESHOLD, MIN_FREQ


class TestEmergingDetector:
    def test_emerging_flag_set(self):
        emerging, declining = assign_flags(
            frequency=MIN_FREQ + 1,
            growth_percentage=GROWTH_THRESHOLD + 5,
        )
        assert emerging is True
        assert declining is False

    def test_declining_flag_set(self):
        emerging, declining = assign_flags(
            frequency=MIN_FREQ + 1,
            growth_percentage=DECLINING_THRESHOLD - 5,
        )
        assert emerging is False
        assert declining is True

    def test_both_false_below_min_freq(self):
        emerging, declining = assign_flags(
            frequency=MIN_FREQ - 1,
            growth_percentage=GROWTH_THRESHOLD + 100,
        )
        assert emerging is False
        assert declining is False

    def test_stable_skill(self):
        emerging, declining = assign_flags(frequency=20, growth_percentage=5.0)
        assert emerging is False
        assert declining is False

    def test_new_skill_100_pct_growth(self):
        # New skill = prior_freq was 0, growth set to 100%
        emerging, declining = assign_flags(
            frequency=MIN_FREQ + 2,
            growth_percentage=100.0,
        )
        assert emerging is True


class TestRunManager:
    def test_run_context_initialises(self):
        from src.run_manager import RunContext
        run = RunContext(market_id="ai_ml_global", week="2026-09")
        assert run.run_id != ""
        assert run.started_at != ""
        assert run.jobs_fetched == 0

    def test_record_jobs(self):
        from src.run_manager import RunContext
        run = RunContext(market_id="ai_ml_global", week="2026-09")
        run.record_jobs(fetched=100, inserted=80, deduped=20)
        assert run.jobs_fetched == 100
        assert run.jobs_inserted == 80
        assert run.jobs_deduped == 20

    def test_add_error(self):
        from src.run_manager import RunContext
        run = RunContext(market_id="ai_ml_global", week="2026-09")
        run.add_error("test error")
        assert run.errors_count == 1
        assert "test error" in run.error_samples

    def test_to_dict_keys(self):
        from src.run_manager import RunContext
        run = RunContext(market_id="ai_ml_global", week="2026-09")
        d = run.to_dict()
        expected_core_keys = {
            "run_id", "started_at", "finished_at", "market_id", "week",
            "sources_used", "jobs_fetched", "jobs_inserted", "jobs_deduped",
            "skills_extracted", "metrics_written", "errors_count", "error_samples",
        }
        # Keep backward compatibility for baseline keys while allowing new telemetry fields.
        assert expected_core_keys.issubset(set(d.keys()))
