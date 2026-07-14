import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def test_should_process_chunk_true_when_idle_and_nothing_else_running():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=400)
    assert should_process_chunk(last_request, other_run_active=False, now=now) is True


def test_should_process_chunk_false_when_recent_activity():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=60)
    assert should_process_chunk(last_request, other_run_active=False, now=now) is False


def test_should_process_chunk_false_when_other_run_active():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=999)
    assert should_process_chunk(last_request, other_run_active=True, now=now) is False


def test_should_process_chunk_true_when_no_requests_seen_yet():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert should_process_chunk(None, other_run_active=False, now=now) is True


def test_should_process_chunk_respects_custom_threshold():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=120)
    assert should_process_chunk(last_request, other_run_active=False, now=now, idle_seconds_threshold=60) is True
    assert should_process_chunk(last_request, other_run_active=False, now=now, idle_seconds_threshold=180) is False


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    # get_config() (src.pipeline_monitor) opens its own connection via
    # src.storage.db.get_connection() rather than reusing the conn passed
    # into run_scheduler_tick - in production both resolve to the same
    # config.settings.DB_PATH file, so this is transparent. Redirect that
    # module-level DB_PATH here so tests see the same isolated tmp file
    # instead of silently reading/depending on the real dev database.
    monkeypatch.setattr("src.storage.db.DB_PATH", db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, raw_description TEXT DEFAULT '',
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
    c.execute("INSERT INTO jobs (job_id, title) VALUES (1, 'Software Engineer')")
    c.commit()
    return c


def test_tick_launches_local_incremental_when_pending_work_exists(conn):
    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # recent activity - should NOT block local_incremental

    row = conn.execute("SELECT field_classification_attempted_at FROM jobs WHERE job_id = 1").fetchone()
    assert row["field_classification_attempted_at"] is not None

    run = conn.execute("SELECT run_type, trigger, status FROM classification_runs WHERE run_type = 'local_incremental'").fetchone()
    assert run["trigger"] == "schedule"
    assert run["status"] == "success"


def test_local_incremental_is_capped_to_chunk_size_not_unbounded(conn):
    # Regression test: on a fresh deploy every existing job has
    # field_classification_attempted_at IS NULL - an uncapped call here would
    # be one long-held transaction over the whole backlog. With chunk_size=1
    # and 3 pending jobs, one tick must process exactly 1, leaving 2 pending
    # (and the completed run must still mark itself 'success', not hang).
    conn.execute("INSERT INTO jobs (job_id, title) VALUES (2, 'Data Analyst')")
    conn.execute("INSERT INTO jobs (job_id, title) VALUES (3, 'Product Manager')")
    conn.execute("INSERT INTO pipeline_config (key, value, updated_at) VALUES ('classification_local_chunk_size', '1', datetime('now'))")
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)

    attempted_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE field_classification_attempted_at IS NOT NULL").fetchone()[0]
    assert attempted_count == 1  # only one chunk processed, not all 3

    run = conn.execute("SELECT status FROM classification_runs WHERE run_type = 'local_incremental'").fetchone()
    assert run["status"] == "success"  # each chunk's run completes on its own, no cursor/continuation needed


def test_tick_does_not_launch_groq_backlog_when_recent_activity(conn):
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # zero idle time

    runs = conn.execute("SELECT run_type FROM classification_runs WHERE run_type = 'groq_backlog'").fetchall()
    assert len(runs) == 0


def test_tick_respects_configured_idle_threshold_override(conn, monkeypatch):
    conn.execute("INSERT INTO pipeline_config (key, value, updated_at) VALUES ('classification_idle_seconds', '10', datetime('now'))")
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    idle_60s = now - timedelta(seconds=60)  # below the 300s default, above the configured 10s
    run_scheduler_tick(conn, last_request_at=idle_60s, now=now)

    run = conn.execute("SELECT run_type FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()
    assert run is not None  # only starts if the 10s config override was actually read, not the 300s default


def test_tick_launches_groq_backlog_when_idle_and_pending_rows_exist(conn, monkeypatch):
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    long_idle = now - timedelta(seconds=400)
    run_scheduler_tick(conn, last_request_at=long_idle, now=now)

    run = conn.execute("SELECT run_type, trigger FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()
    assert run is not None
    assert run["trigger"] == "backfill_idle"


def test_tick_does_not_start_second_groq_backlog_if_one_already_running(conn):
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES ('existing', 'groq_backlog', 'backfill_idle', 'running', datetime('now'))"
    )
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    long_idle = now - timedelta(seconds=400)
    run_scheduler_tick(conn, last_request_at=long_idle, now=now)

    count = conn.execute("SELECT COUNT(*) FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()[0]
    assert count == 1  # still just the pre-existing one, no duplicate


def test_local_full_backfill_advances_across_ticks_not_stuck_on_first_chunk(conn):
    # Regression test: reclassify_all() has no natural "already done" filter
    # (unlike classify_pending_jobs), so without after_job_id wired through
    # correctly, two ticks would both reprocess the same first job forever.
    conn.execute("INSERT INTO jobs (job_id, title) VALUES (2, 'Data Analyst')")
    conn.execute("INSERT INTO jobs (job_id, title) VALUES (3, 'Product Manager')")
    conn.execute("INSERT INTO pipeline_config (key, value, updated_at) VALUES ('classification_local_chunk_size', '1', datetime('now'))")
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES ('backfill1', 'local_full_backfill', 'manual', 'running', datetime('now'))"
    )
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    long_idle = now - timedelta(seconds=400)

    run_scheduler_tick(conn, last_request_at=long_idle, now=now)
    after_tick_1 = conn.execute("SELECT cursor_job_id FROM classification_runs WHERE run_id = 'backfill1'").fetchone()
    assert after_tick_1["cursor_job_id"] == 1

    run_scheduler_tick(conn, last_request_at=long_idle, now=now)
    after_tick_2 = conn.execute("SELECT cursor_job_id, status FROM classification_runs WHERE run_id = 'backfill1'").fetchone()
    assert after_tick_2["cursor_job_id"] == 2  # advanced past job 1, not stuck reprocessing it

    run_scheduler_tick(conn, last_request_at=long_idle, now=now)
    after_tick_3 = conn.execute("SELECT cursor_job_id, status FROM classification_runs WHERE run_id = 'backfill1'").fetchone()
    assert after_tick_3["cursor_job_id"] == 3
    assert after_tick_3["status"] == "success"  # all 3 jobs processed, run completed


def test_tick_launches_groq_retry_when_never_run_before(conn, monkeypatch):
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, attempt_count, created_at) VALUES (1, 'failed_technical', 1, datetime('now'))")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # recent activity - must NOT block groq_retry

    run = conn.execute("SELECT trigger, status FROM classification_runs WHERE run_type = 'groq_retry'").fetchone()
    assert run is not None
    assert run["trigger"] == "schedule"
    assert run["status"] == "success"


def test_tick_skips_groq_retry_when_recently_run(conn, monkeypatch):
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=10)
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at, finished_at) VALUES ('prev-retry', 'groq_retry', 'schedule', 'success', ?, ?)",
        (recent.isoformat(), recent.isoformat()),
    )
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.commit()

    from src.classification import groq_stage
    mock_called = {"count": 0}
    def _mock_process(conn, run_id, statuses, **kw):
        mock_called["count"] += 1
        return {"processed": 0, "succeeded": 0, "failed_technical": 0, "no_match": 0}
    monkeypatch.setattr(groq_stage, "process_groq_queue", _mock_process)

    from src.classification.scheduling import run_scheduler_tick
    run_scheduler_tick(conn, last_request_at=now, now=now)

    count = conn.execute("SELECT COUNT(*) FROM classification_runs WHERE run_type = 'groq_retry'").fetchone()[0]
    assert count == 1  # still just the pre-existing one from 10 minutes ago - not due yet (< 1 hour)


def test_tick_launches_groq_retry_after_interval_elapsed(conn, monkeypatch):
    now = datetime.now(timezone.utc)
    long_ago = now - timedelta(hours=2)
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at, finished_at) VALUES ('prev-retry', 'groq_retry', 'schedule', 'success', ?, ?)",
        (long_ago.isoformat(), long_ago.isoformat()),
    )
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 0, "succeeded": 0, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    run_scheduler_tick(conn, last_request_at=now, now=now)

    count = conn.execute("SELECT COUNT(*) FROM classification_runs WHERE run_type = 'groq_retry'").fetchone()[0]
    assert count == 2  # the 2-hour-old one plus a new one just launched
