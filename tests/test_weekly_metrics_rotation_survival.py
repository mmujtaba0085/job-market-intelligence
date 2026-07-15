"""
tests/test_weekly_metrics_rotation_survival.py
─────────────────────────────────────────────────
Regression coverage for a real production bug: upsert_weekly_metric() used
to write via get_connection() only - whichever physical file the pointer
currently calls "Serving". The weekly/report-only pipeline mode that calls
it runs on a separate, much less frequent schedule than the ingest-only
cycle that rotates the pointer every 12h, so no ordering fix could help
here (unlike the recompute_diversity_ranks/recompute_skill_combinations/
recompute_top_titles bug fixed earlier via reordering in orchestrator.py) -
sooner or later some later rotation's _refresh_demoted_file() was
guaranteed to overwrite whichever single file the weekly write landed on.
Confirmed in production: weekly_metrics was found completely empty despite
the weekly timer having run successfully days earlier.

Fixed by making upsert_weekly_metric() write to both serving-slot files, so
the row survives no matter which one rotation later promotes to "Serving".
"""
from datetime import date

import pytest

import src.storage.db as db
from src.storage.models import WeeklyMetric


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    """Same rotating-DB path isolation used by tests/test_db_rotation_paths.py,
    tests/test_scheduler_tick_isolation.py, and tests/test_orchestrator.py -
    patching only DB_PATH (and not the other four) is a known-broken
    pattern, so all five path constants plus both lock paths are patched
    together here too."""
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


def _sample_metric() -> WeeklyMetric:
    return WeeklyMetric(
        market_id="software-engineering",
        week_start_date=date(2026, 7, 13),
        week_number=29,
        skill_name="python",
        category="language",
        frequency=42,
        growth_percentage=15.5,
        absolute_delta=6,
        mover_score=12.3,
        emerging_flag=True,
        declining_flag=False,
    )


def _read_metric(conn, skill_name="python"):
    return conn.execute(
        "SELECT frequency, emerging_flag FROM weekly_metrics WHERE skill_name = ?",
        (skill_name,),
    ).fetchone()


class TestUpsertWeeklyMetricDualWrite:
    def test_write_lands_on_both_serving_slot_files_immediately(self, isolated_paths):
        db.upsert_weekly_metric(_sample_metric())

        live_conn = db.get_connection()
        try:
            row = _read_metric(live_conn)
            assert row is not None and row["frequency"] == 42 and row["emerging_flag"] == 1
        finally:
            live_conn.close()

        free_conn = db.get_free_connection()
        try:
            row = _read_metric(free_conn)
            assert row is not None and row["frequency"] == 42 and row["emerging_flag"] == 1
        finally:
            free_conn.close()

    def test_metric_survives_rotation(self, isolated_paths):
        from src.db_rotation import rotate

        assert db._read_pointer() == "a"
        db.upsert_weekly_metric(_sample_metric())

        rotate()  # flips the pointer even with an empty buffer

        assert db._read_pointer() == "b"

        live_conn = db.get_connection()
        try:
            row = _read_metric(live_conn)
            assert row is not None and row["frequency"] == 42, (
                "weekly_metrics row must survive rotation - this is exactly "
                "what was silently destroyed in production before the fix"
            )
        finally:
            live_conn.close()

    def test_update_to_existing_metric_also_lands_on_both_files(self, isolated_paths):
        db.upsert_weekly_metric(_sample_metric())

        updated = _sample_metric()
        updated.frequency = 99
        updated.emerging_flag = False
        db.upsert_weekly_metric(updated)

        for conn_getter in (db.get_connection, db.get_free_connection):
            conn = conn_getter()
            try:
                row = _read_metric(conn)
                assert row is not None and row["frequency"] == 99 and row["emerging_flag"] == 0
            finally:
                conn.close()
