"""
tests/test_orchestrator.py
────────────────────────────
Tests for the diversity-rank recompute hook in src/orchestrator.py's main().
"""

import argparse

import pytest

import src.storage.db as db


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


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    """Same rotating-DB path isolation used by tests/test_db_rotation_paths.py
    and tests/test_scheduler_tick_isolation.py - patching only DB_PATH (and
    not the other four) is a known-broken pattern, so all five path
    constants plus both lock paths are patched together here too."""
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    db.run_migrations()
    return tmp_path


class TestRecomputeRunsAfterRotation:
    """
    End-to-end regression coverage for the ordering bug: recompute_diversity_ranks()
    / recompute_skill_combinations() / recompute_top_titles() used to run BEFORE
    the ingest-only rotate() block, so their writes (via get_connection(), which
    resolves to whatever is Serving AT CALL TIME) landed on the file that was
    about to be demoted - and then db_rotation.py's _refresh_demoted_file()
    clobbered that file with the new Serving's (recompute-less) content anyway.
    The file that actually went live never received the fresh writes.

    Fixed by moving all three recompute calls to after the ingest-only
    rotate() block. This test drives orchestrator.main() with everything
    EXCEPT the DB/rotation layer mocked out - _run(), _setup_logging(), and
    pipeline_monitor are stubbed (irrelevant to this bug), but
    get_buffer_connection(), rotate(), and all three recompute functions run
    for real against isolated tmp files, so the assertions below prove real
    data survives on whichever file is actually Serving after rotation -
    not just that the functions were called.
    """

    def test_ingest_only_recompute_lands_on_post_rotation_serving_file(self, isolated_paths, monkeypatch):
        import src.orchestrator as orchestrator
        from src.storage.models import JobNormalized, SkillSignal

        # Fresh bootstrap out of run_migrations() always starts pointer at "a".
        assert db._read_pointer() == "a"

        # Seed Buffer with one new job + two skills, the same way real
        # ingest-only ingestion writes into Buffer (upsert_job + insert_skills
        # inside use_buffer_connection()), so this cycle has data that will
        # change skill_combinations_summary / top_titles_summary / diversity_rank
        # once merged and recomputed.
        job = JobNormalized(
            url_hash="catchup-h1", canonical_hash="catchup-c1",
            description_hash="catchup-d1", job_group_id="catchup-c1",
            market_id="software-engineering", source_name="test-source",
            title="Data Scientist", normalized_title="Data Scientist",
            normalization_confidence=1.0, company="Acme", country="Canada",
            location="Remote", remote_type="remote", posted_date=None,
            salary_min=None, salary_max=None, currency=None,
            description_text="Great job", url="https://example.com/job1",
        )
        with db.use_buffer_connection():
            job_id, status = db.upsert_job(job)
            assert status == "inserted"
            db.insert_skills([
                SkillSignal(job_id=job_id, market_id="software-engineering",
                            raw_detected_skill="Python", normalized_skill="python",
                            category="language"),
                SkillSignal(job_id=job_id, market_id="software-engineering",
                            raw_detected_skill="SQL", normalized_skill="sql",
                            category="language"),
            ])

        monkeypatch.setattr(orchestrator, "_parse_args", lambda: _args(mode="ingest-only"))
        monkeypatch.setattr(orchestrator, "_run", lambda args, week_start: {})
        monkeypatch.setattr(orchestrator, "_setup_logging", lambda run_id="", week="": None)
        monkeypatch.setattr("src.pipeline_monitor.start_run", lambda mode, trigger="schedule": "test-run-id")
        monkeypatch.setattr("src.pipeline_monitor.finish_run", lambda run_id, **kwargs: None)
        # run_migrations() runs for real too (unmocked) - already applied by
        # the isolated_paths fixture, so this is just an idempotent no-op
        # repeat, exactly as it is in production on every run.

        orchestrator.main()

        # Buffer had a job, so rotate() must have actually flipped the pointer.
        assert db._read_pointer() == "b"

        # get_connection() now resolves to serving_b - whatever is live RIGHT
        # NOW after rotation. It must carry this cycle's fresh recompute.
        live_conn = db.get_connection()
        try:
            combo_row = live_conn.execute(
                "SELECT co_count FROM skill_combinations_summary WHERE skill_a = ? AND skill_b = ?",
                ("python", "sql"),
            ).fetchone()
            assert combo_row is not None and combo_row["co_count"] == 1

            title_row = live_conn.execute(
                "SELECT count FROM top_titles_summary WHERE title = ?", ("Data Scientist",)
            ).fetchone()
            assert title_row is not None and title_row["count"] == 1

            rank_row = live_conn.execute(
                "SELECT diversity_rank FROM jobs WHERE url_hash = 'catchup-h1'"
            ).fetchone()
            assert rank_row is not None and rank_row["diversity_rank"] is not None
        finally:
            live_conn.close()

        # The demoted file (serving_a, now Free) received the merged job data
        # via _refresh_demoted_file() (it's a byte-copy of serving_b taken
        # mid-rotation), but must NOT show this cycle's recompute - proving
        # the fresh summary/rank writes only ever touched the file that's
        # actually live, not the one that just stopped being live. This is
        # the exact contrast the pre-fix ordering bug got backwards (fresh
        # data landed on the about-to-be-demoted file and was then
        # overwritten, while the newly-live file never received it at all).
        free_conn = db.get_free_connection()
        try:
            free_job_row = free_conn.execute(
                "SELECT job_id FROM jobs WHERE url_hash = 'catchup-h1'"
            ).fetchone()
            assert free_job_row is not None, "sanity check: merged job must exist on the demoted file too"

            stale_combo_count = free_conn.execute(
                "SELECT COUNT(*) AS n FROM skill_combinations_summary"
            ).fetchone()["n"]
            assert stale_combo_count == 0
        finally:
            free_conn.close()
