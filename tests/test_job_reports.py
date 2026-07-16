"""
tests/test_job_reports.py
─────────────────────────────
Regression coverage for src/job_reports.py's pure logic - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.job_reports import REASON_CATEGORIES, is_rate_limited, validate_report_input


def test_reason_categories_are_the_five_agreed_values():
    assert REASON_CATEGORIES == ("incorrect_info", "wrong_category", "broken_link", "spam", "other")


def test_validate_accepts_predefined_category_with_no_details():
    assert validate_report_input("incorrect_info", "") is None


def test_validate_rejects_unrecognized_category():
    err = validate_report_input("not_a_real_category", "")
    assert err is not None


def test_validate_requires_details_for_other_category():
    err = validate_report_input("other", "")
    assert err is not None
    assert validate_report_input("other", "   ") is not None  # whitespace-only also rejected


def test_validate_accepts_other_category_with_real_details():
    assert validate_report_input("other", "The salary listed doesn't match the linked posting") is None


@pytest.fixture()
def reports_conn(tmp_path, monkeypatch):
    monkeypatch.setattr("src.storage.db._DATA_DIR", tmp_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    from src.storage.db import get_operational_connection, run_migrations
    run_migrations()
    conn = get_operational_connection()
    yield conn
    conn.close()


def test_create_report_inserts_and_returns_id(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    report_id = create_report(
        reports_conn, job_id=42, job_url="https://example.com/job/42", job_title="Backend Engineer",
        reason_category="incorrect_info", details="Salary is wrong", reporter_user_id=None,
        reporter_email="test@example.com", reporter_ip="127.0.0.1", now=now,
    )
    assert isinstance(report_id, int)
    row = reports_conn.execute("SELECT * FROM job_reports WHERE report_id = ?", (report_id,)).fetchone()
    assert row["job_title"] == "Backend Engineer"
    assert row["status"] == "open"
    assert row["reporter_email"] == "test@example.com"


def test_is_rate_limited_false_under_threshold(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    for _ in range(4):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(reports_conn, "9.9.9.9", now) is False


def test_is_rate_limited_true_at_threshold(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    for _ in range(5):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(reports_conn, "9.9.9.9", now) is True


def test_is_rate_limited_ignores_reports_older_than_an_hour(reports_conn):
    from src.job_reports import create_report
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    now = datetime.now(timezone.utc)
    for _ in range(5):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="9.9.9.9", now=old,
        )
    assert is_rate_limited(reports_conn, "9.9.9.9", now) is False


def test_is_rate_limited_scoped_per_ip(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    for _ in range(5):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="1.1.1.1", now=now,
        )
    assert is_rate_limited(reports_conn, "2.2.2.2", now) is False  # different IP, unaffected
