import sqlite3
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, raw_description TEXT DEFAULT '', market_id TEXT,
            field_category_id TEXT, field_classification_confidence REAL,
            field_classification_method TEXT, field_classification_attempted_at TEXT
        );
        CREATE TABLE job_categories (category_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, isco TEXT, keywords TEXT);
        CREATE TABLE job_category_assignments (
            job_id INTEGER, category_id TEXT, assignment_type TEXT, confidence REAL,
            method TEXT, evidence_json TEXT, assigned_at TEXT,
            PRIMARY KEY (job_id, category_id, assignment_type)
        );
        CREATE TABLE groq_classification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT DEFAULT 'pending',
            prompt_sent TEXT, response_received TEXT, attempt_count INTEGER DEFAULT 0,
            last_attempted_at TEXT, created_at TEXT, UNIQUE(job_id)
        );
        CREATE TABLE classification_runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, trigger TEXT, status TEXT DEFAULT 'running',
            started_at TEXT, finished_at TEXT, cursor_job_id INTEGER,
            jobs_processed INTEGER DEFAULT 0, jobs_classified INTEGER DEFAULT 0,
            jobs_queued_groq INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    c.execute(
        "INSERT INTO jobs (job_id, title, raw_description) VALUES (1, 'Senior Software Engineer', 'Python backend role')"
    )
    c.execute(
        "INSERT INTO jobs (job_id, title, raw_description) VALUES (2, 'Xyzzy Widget Wrangler', 'totally unclassifiable made-up title')"
    )
    c.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run1', 'local_incremental', 'schedule', datetime('now'))"
    )
    c.commit()
    return c


def test_above_threshold_job_classified_directly(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    row = conn.execute("SELECT field_category_id, field_classification_method, field_classification_attempted_at FROM jobs WHERE job_id = 1").fetchone()
    assert row["field_category_id"] == "it.software"
    assert row["field_classification_method"] == "local_hybrid_v1"
    assert row["field_classification_attempted_at"] is not None

    assignment = conn.execute("SELECT * FROM job_category_assignments WHERE job_id = 1 AND assignment_type = 'primary'").fetchone()
    assert assignment["category_id"] == "it.software"


def test_below_threshold_job_queued_for_groq(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    row = conn.execute("SELECT field_category_id, field_classification_attempted_at FROM jobs WHERE job_id = 2").fetchone()
    assert row["field_category_id"] is None
    assert row["field_classification_attempted_at"] is not None  # attempted, just unclassified

    queued = conn.execute("SELECT status FROM groq_classification_queue WHERE job_id = 2").fetchone()
    assert queued["status"] == "pending"


def test_run_stats_updated(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    run = conn.execute("SELECT jobs_processed, jobs_classified, jobs_queued_groq FROM classification_runs WHERE run_id = 'run1'").fetchone()
    assert run["jobs_processed"] == 2
    assert run["jobs_classified"] == 1
    assert run["jobs_queued_groq"] == 1


def test_already_attempted_jobs_skipped_by_incremental(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    conn.execute("INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run2', 'local_incremental', 'schedule', datetime('now'))")
    result = classify_pending_jobs(conn, run_id="run2")
    assert result["processed"] == 0  # both jobs already attempted


def test_reclassify_all_reprocesses_everything(conn):
    from src.classification.local_stage import classify_pending_jobs, reclassify_all
    classify_pending_jobs(conn, run_id="run1")

    conn.execute("INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run2', 'local_full_backfill', 'manual', datetime('now'))")
    result = reclassify_all(conn, run_id="run2")
    assert result["processed"] == 2  # reprocesses job 1 even though already attempted


def test_limit_caps_batch_size_and_sets_cursor(conn):
    from src.classification.local_stage import classify_pending_jobs
    result = classify_pending_jobs(conn, run_id="run1", limit=1)
    assert result["processed"] == 1

    run = conn.execute("SELECT cursor_job_id FROM classification_runs WHERE run_id = 'run1'").fetchone()
    assert run["cursor_job_id"] == 1


def test_reclassify_clears_stale_state_on_downgrade(conn, monkeypatch):
    # Simulate job 1 having been classified in a prior run (as if by an
    # earlier, now-superseded taxonomy/threshold), then reclassify_all()
    # runs against a classifier that no longer matches it - the job must
    # end up genuinely unclassified, not stuck showing a stale category
    # while also sitting in the Groq queue.
    conn.execute(
        "UPDATE jobs SET field_category_id = 'it.software', field_classification_confidence = 0.9, "
        "field_classification_method = 'local_hybrid_v1', field_classification_attempted_at = datetime('now') WHERE job_id = 1"
    )
    conn.execute(
        "INSERT INTO job_category_assignments (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at) "
        "VALUES (1, 'it.software', 'primary', 0.9, 'local_hybrid_v1', '[]', datetime('now'))"
    )
    conn.commit()

    # Force classify_job to always return no match, regardless of title, so
    # this test proves the downgrade path without depending on the real
    # classifier's keyword list happening to miss "Senior Software Engineer".
    from src.classification import local_stage
    from src.market_classifier import MarketMatch
    monkeypatch.setattr(local_stage, "classify_job", lambda title, description: MarketMatch(None, 0.0, (), "local_hybrid_v1", ()))

    from src.classification.local_stage import reclassify_all
    conn.execute("INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run2', 'local_full_backfill', 'manual', datetime('now'))")
    reclassify_all(conn, run_id="run2")

    row = conn.execute("SELECT field_category_id, field_classification_confidence, field_classification_method FROM jobs WHERE job_id = 1").fetchone()
    assert row["field_category_id"] is None
    assert row["field_classification_confidence"] is None
    assert row["field_classification_method"] is None

    assignments = conn.execute("SELECT COUNT(*) FROM job_category_assignments WHERE job_id = 1").fetchone()[0]
    assert assignments == 0  # stale primary row removed, not left behind

    queued = conn.execute("SELECT status FROM groq_classification_queue WHERE job_id = 1").fetchone()
    assert queued["status"] == "pending"
