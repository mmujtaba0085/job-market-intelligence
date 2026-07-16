"""
tests/test_tickets.py
─────────────────────────
Regression coverage for src/tickets.py's pure logic - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.tickets import CATEGORIES, is_rate_limited, validate_ticket_input


def test_categories_are_the_four_agreed_values():
    assert CATEGORIES == ("bug", "feature", "feedback", "other")


def test_validate_accepts_predefined_category_with_subject_and_details():
    assert validate_ticket_input("bug", "Login button broken on Safari", "Clicking it does nothing, console shows a 404 for /auth/login") is None


def test_validate_rejects_unrecognized_category():
    err = validate_ticket_input("not_a_real_category", "Subject", "Details")
    assert err is not None


def test_validate_requires_non_blank_subject():
    assert validate_ticket_input("bug", "", "Some details here") is not None
    assert validate_ticket_input("bug", "   ", "Some details here") is not None


def test_validate_requires_non_blank_details_for_every_category():
    for category in CATEGORIES:
        err = validate_ticket_input(category, "A real subject line", "")
        assert err is not None, f"expected {category} to require details"
        err_whitespace = validate_ticket_input(category, "A real subject line", "   ")
        assert err_whitespace is not None


def test_validate_accepts_other_category_with_subject_and_details():
    assert validate_ticket_input("other", "Dark mode contrast", "The muted text is hard to read in dark mode on the jobs list page") is None


@pytest.fixture()
def tickets_conn(tmp_path, monkeypatch):
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


def test_create_ticket_inserts_and_returns_id(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    ticket_id = create_ticket(
        tickets_conn, category="feature", subject="Add LinkedIn as a source",
        details="Would love to see LinkedIn job postings included alongside the existing sources",
        submitter_user_id=None, submitter_email="test@example.com", submitter_ip="127.0.0.1", now=now,
    )
    assert isinstance(ticket_id, int)
    row = tickets_conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    assert row["subject"] == "Add LinkedIn as a source"
    assert row["status"] == "open"
    assert row["submitter_email"] == "test@example.com"


def test_is_rate_limited_false_under_threshold(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    for i in range(4):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(tickets_conn, "9.9.9.9", now) is False


def test_is_rate_limited_true_at_threshold(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    for i in range(5):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(tickets_conn, "9.9.9.9", now) is True


def test_is_rate_limited_ignores_tickets_older_than_an_hour(tickets_conn):
    from src.tickets import create_ticket
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    now = datetime.now(timezone.utc)
    for i in range(5):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="9.9.9.9", now=old,
        )
    assert is_rate_limited(tickets_conn, "9.9.9.9", now) is False


def test_is_rate_limited_scoped_per_ip(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    for i in range(5):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="1.1.1.1", now=now,
        )
    assert is_rate_limited(tickets_conn, "2.2.2.2", now) is False
