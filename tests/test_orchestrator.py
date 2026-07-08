"""
tests/test_orchestrator.py
────────────────────────────
Tests for the diversity-rank recompute hook in src/orchestrator.py's main().
"""

import argparse

import pytest


def _args(mode=None, backfill=False):
    return argparse.Namespace(mode=mode, backfill=backfill, start=None, end=None, html=False, max_runtime=None, run_id=None)


class TestShouldRecomputeDiversity:
    def test_true_for_ingest_only(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="ingest-only")) is True

    def test_true_for_weekly(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="weekly")) is True

    def test_true_for_crawl(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="crawl")) is True

    def test_false_for_report_only(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="report-only")) is False

    def test_false_for_backfill(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode=None, backfill=True)) is False


class TestMainCallsRecompute:
    def test_recompute_called_after_ingest_only(self, monkeypatch):
        import src.orchestrator as orchestrator

        monkeypatch.setattr(orchestrator, "_parse_args", lambda: _args(mode="ingest-only"))
        monkeypatch.setattr(orchestrator, "run_migrations", lambda: None)
        monkeypatch.setattr(orchestrator, "_run", lambda args, week_start: {})
        monkeypatch.setattr(orchestrator, "_setup_logging", lambda run_id="", week="": None)
        monkeypatch.setattr("src.pipeline_monitor.start_run", lambda mode, trigger="schedule": "test-run-id")
        monkeypatch.setattr("src.pipeline_monitor.finish_run", lambda run_id, **kwargs: None)

        called = {"count": 0}
        monkeypatch.setattr(orchestrator, "recompute_diversity_ranks", lambda: called.__setitem__("count", called["count"] + 1))

        orchestrator.main()

        assert called["count"] == 1

    def test_recompute_not_called_after_report_only(self, monkeypatch):
        import src.orchestrator as orchestrator

        monkeypatch.setattr(orchestrator, "_parse_args", lambda: _args(mode="report-only"))
        monkeypatch.setattr(orchestrator, "run_migrations", lambda: None)
        monkeypatch.setattr(orchestrator, "_run", lambda args, week_start: {})
        monkeypatch.setattr(orchestrator, "_setup_logging", lambda run_id="", week="": None)
        monkeypatch.setattr("src.pipeline_monitor.start_run", lambda mode, trigger="schedule": "test-run-id")
        monkeypatch.setattr("src.pipeline_monitor.finish_run", lambda run_id, **kwargs: None)

        called = {"count": 0}
        monkeypatch.setattr(orchestrator, "recompute_diversity_ranks", lambda: called.__setitem__("count", called["count"] + 1))

        orchestrator.main()

        assert called["count"] == 0

    def test_recompute_failure_does_not_fail_the_run(self, monkeypatch):
        import src.orchestrator as orchestrator

        monkeypatch.setattr(orchestrator, "_parse_args", lambda: _args(mode="ingest-only"))
        monkeypatch.setattr(orchestrator, "run_migrations", lambda: None)
        monkeypatch.setattr(orchestrator, "_run", lambda args, week_start: {})
        monkeypatch.setattr(orchestrator, "_setup_logging", lambda run_id="", week="": None)
        monkeypatch.setattr("src.pipeline_monitor.start_run", lambda mode, trigger="schedule": "test-run-id")

        finish_calls = []
        monkeypatch.setattr(
            "src.pipeline_monitor.finish_run",
            lambda run_id, **kwargs: finish_calls.append(kwargs),
        )

        def _raise():
            raise RuntimeError("simulated recompute failure")

        monkeypatch.setattr(orchestrator, "recompute_diversity_ranks", _raise)

        orchestrator.main()  # must not raise, despite the recompute failure

        assert len(finish_calls) == 1
        assert finish_calls[0]["status"] == "success"
