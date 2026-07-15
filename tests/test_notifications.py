"""
tests/test_notifications.py
─────────────────────────────
Pure-function tests for src/notifications.py's page-matching, expiry, and
dismissed-id filtering logic - no Flask, no request context, matching the
same separation-of-concerns already used by
src.classification.scheduling.should_process_chunk().
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.notifications import PAGE_KEYS, filter_active_notifications, page_key_for_path


def _row(id=1, target_pages="all", expires_at=None):
    """Minimal stand-in for a sqlite3.Row - a dict works since
    filter_active_notifications() only ever does dict-style key access."""
    return {"id": id, "target_pages": target_pages, "expires_at": expires_at}


class TestPageKeyForPath:
    def test_dashboard_root(self):
        assert page_key_for_path("/") == "dashboard"

    def test_dashboard_prefix(self):
        assert page_key_for_path("/dashboard") == "dashboard"

    def test_jobs_list(self):
        assert page_key_for_path("/jobs") == "jobs"

    def test_jobs_detail(self):
        assert page_key_for_path("/jobs/12345") == "jobs"

    def test_skills(self):
        assert page_key_for_path("/skills/intelligence") == "skills"

    def test_companies(self):
        assert page_key_for_path("/companies/intelligence") == "companies"

    def test_titles(self):
        assert page_key_for_path("/titles/analytics") == "titles"

    def test_metrics(self):
        assert page_key_for_path("/metrics") == "metrics"

    def test_api_docs(self):
        assert page_key_for_path("/api/docs") == "api_docs"

    def test_admin_path_is_not_targetable(self):
        assert page_key_for_path("/admin/pipeline") is None

    def test_auth_path_is_not_targetable(self):
        assert page_key_for_path("/auth/login") is None

    def test_unrelated_path_is_none(self):
        assert page_key_for_path("/healthz") is None

    def test_all_page_keys_have_at_least_one_matching_path(self):
        # Guards against a future PAGE_KEYS edit that adds a key with no
        # matching branch in page_key_for_path().
        sample_paths = {
            "dashboard": "/dashboard", "jobs": "/jobs", "skills": "/skills/intelligence",
            "companies": "/companies/intelligence", "titles": "/titles/analytics",
            "metrics": "/metrics", "api_docs": "/api/docs",
        }
        for key in PAGE_KEYS:
            assert page_key_for_path(sample_paths[key]) == key


class TestFilterActiveNotifications:
    def test_all_pages_notification_matches_any_path(self):
        rows = [_row(id=1, target_pages="all")]
        result = filter_active_notifications(rows, "/jobs", set(), datetime.now(timezone.utc))
        assert len(result) == 1

    def test_specific_page_matches_only_that_page(self):
        rows = [_row(id=1, target_pages="jobs")]
        now = datetime.now(timezone.utc)
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 1
        assert len(filter_active_notifications(rows, "/dashboard", set(), now)) == 0

    def test_multi_page_target_list(self):
        rows = [_row(id=1, target_pages="jobs,dashboard")]
        now = datetime.now(timezone.utc)
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 1
        assert len(filter_active_notifications(rows, "/dashboard", set(), now)) == 1
        assert len(filter_active_notifications(rows, "/skills/intelligence", set(), now)) == 0

    def test_no_expiry_never_filtered_out_by_time(self):
        rows = [_row(id=1, target_pages="all", expires_at=None)]
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        assert len(filter_active_notifications(rows, "/jobs", set(), far_future)) == 1

    def test_future_expiry_still_active(self):
        now = datetime.now(timezone.utc)
        future = (now + timedelta(hours=1)).isoformat()
        rows = [_row(id=1, target_pages="all", expires_at=future)]
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 1

    def test_past_expiry_filtered_out(self):
        now = datetime.now(timezone.utc)
        past = (now - timedelta(hours=1)).isoformat()
        rows = [_row(id=1, target_pages="all", expires_at=past)]
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 0

    def test_dismissed_id_filtered_out(self):
        rows = [_row(id=7, target_pages="all")]
        now = datetime.now(timezone.utc)
        assert len(filter_active_notifications(rows, "/jobs", {7}, now)) == 0
        assert len(filter_active_notifications(rows, "/jobs", {8}, now)) == 1

    def test_empty_rows_returns_empty(self):
        assert filter_active_notifications([], "/jobs", set(), datetime.now(timezone.utc)) == []

    def test_multiple_notifications_all_returned_when_all_match(self):
        rows = [_row(id=1, target_pages="all"), _row(id=2, target_pages="jobs")]
        now = datetime.now(timezone.utc)
        result = filter_active_notifications(rows, "/jobs", set(), now)
        assert {r["id"] for r in result} == {1, 2}


import src.storage.db as db


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
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


def test_notifications_table_created_in_operational_db(isolated_paths):
    conn = db.get_operational_connection()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "notifications" in tables


def test_load_active_notifications_reads_from_operational_db(isolated_paths):
    from datetime import datetime, timezone
    from src.notifications import load_active_notifications

    conn = db.get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at) VALUES (?,?,?,?,?)",
        ("Maintenance", "Site will be down briefly.", "warning", "all", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    result = load_active_notifications("/jobs", set(), datetime.now(timezone.utc))
    assert len(result) == 1
    assert result[0]["heading"] == "Maintenance"
