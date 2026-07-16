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
    # Rotating-DB architecture: get_connection()/get_operational_connection()
    # resolve via serving/operational paths, not DB_PATH. Point every rotation
    # target at this one isolated test file so the scheduler tick and get_config()
    # both read/write it (single-file emulation), never the real data/ directory.
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr("src.storage.db._CLASSIFICATION_SCHEDULER_LOCK_PATH", tmp_path / ".classification_scheduler.lock")
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


def test_groq_backlog_failure_is_caught_and_marked_failed_immediately(conn, monkeypatch):
    # Regression test for a real production incident: this block had no
    # try/except at all, unlike local_incremental and groq_retry right next
    # to it. When process_groq_queue() raised, the exception propagated
    # uncaught past this function entirely, got swallowed by a broader
    # catch-all in web_viewer.py's _scheduler_tick_once(), and the run's DB
    # row just sat as 'running' - with no error message ever recorded -
    # until the (unrelated) 30-minute staleness timeout eventually cleaned
    # it up. Confirmed in production: three separate groq_backlog runs each
    # "took" ~30 minutes and left an empty error column, which is exactly
    # what staleness-timeout cleanup of a silently-crashed run looks like.
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification import groq_stage

    def _boom(conn, run_id, statuses, **kwargs):
        raise RuntimeError("simulated Groq API failure")

    monkeypatch.setattr(groq_stage, "process_groq_queue", _boom)

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # must not raise - caught internally

    run = conn.execute("SELECT status FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()
    assert run is not None
    assert run["status"] == "failed"  # marked failed immediately, not left 'running' for staleness to catch later


def test_groq_backlog_runs_without_idle_gating(conn):
    # Free is never serving live traffic, so groq_backlog no longer defers to
    # should_process_chunk()'s idle check - it must run even with zero idle
    # time (last_request_at == now), which would have blocked it before this
    # task (see the removed test_tick_does_not_launch_groq_backlog_when_recent_activity).
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from datetime import datetime, timezone
    from unittest.mock import patch
    from src.classification.scheduling import run_scheduler_tick

    now = datetime.now(timezone.utc)
    with patch("src.classification.groq_stage.process_groq_queue", return_value={"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0}) as mock_process:
        run_scheduler_tick(conn, last_request_at=now, now=now)
    assert mock_process.called

    run = conn.execute("SELECT run_type, trigger FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()
    assert run is not None
    assert run["trigger"] == "backfill_idle"


def test_local_full_backfill_runs_without_idle_gating(conn):
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES ('r1', 'local_full_backfill', 'manual', 'running', datetime('now'))"
    )
    conn.commit()

    from datetime import datetime, timezone
    from unittest.mock import patch
    from src.classification.scheduling import run_scheduler_tick

    now = datetime.now(timezone.utc)
    with patch("src.classification.local_stage.reclassify_all", return_value={"processed": 0, "classified": 0, "queued_groq": 0}) as mock_reclassify:
        run_scheduler_tick(conn, last_request_at=now, now=now)
    assert mock_reclassify.called


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


def test_groq_retry_is_bounded_to_chunk_size_not_unbounded(conn, monkeypatch):
    # Regression test for a real production incident: process_groq_queue()
    # defaults to limit=None (unbounded) unless a caller passes one
    # explicitly - groq_backlog's call site already does (limit=groq_chunk_size),
    # but groq_retry's call site never did, so a large failed_technical queue
    # got processed entirely in one run. Confirmed in production: a groq_retry
    # run held the cross-process scheduler lock (see the fcntl lock tests
    # above) for over an hour with zero progress reported, blocking every
    # other classification run - not a hang, just unboundedly slow.
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, attempt_count, created_at) VALUES (1, 'failed_technical', 1, datetime('now'))")
    conn.execute("INSERT INTO pipeline_config (key, value, updated_at) VALUES ('classification_groq_chunk_size', '10', datetime('now'))")
    conn.commit()

    captured_kwargs = {}

    def _fake_process_groq_queue(conn, run_id, statuses, **kwargs):
        captured_kwargs.update(kwargs)
        return {"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0}

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", _fake_process_groq_queue)

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)

    assert captured_kwargs.get("limit") == 10, (
        f"groq_retry must pass the configured groq_chunk_size as limit, not leave it "
        f"unbounded (limit=None) - got kwargs {captured_kwargs!r}"
    )


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


# ── Cross-worker locking + stale-run recovery ─────────────────────────────
#
# Regression coverage for a real production incident: gunicorn's 4 worker
# processes each start their own copy of the auto-scheduler thread (see
# web_viewer.py's module-level `_scheduler_thread = Thread(...)`), so up to
# 4 independent ticks could call run_scheduler_tick() at once with no
# coordination. Two of them raced to start the same local_incremental run
# within the same millisecond, both got stuck at 0 progress, and since
# _any_run_active() has no way to tell a genuinely-running run from an
# abandoned one, that permanently blocked every future tick - only 5
# local_incremental runs ever executed in the table's whole history before
# it wedged. Fixed with (1) an fcntl file lock around the whole tick, same
# pattern already used for db.run_migrations(), so only one worker's tick
# can execute at a time, and (2) a staleness timeout that supersedes any
# 'running' row older than a generous threshold, so a crash/redeploy that
# manages to slip past the lock can never wedge things permanently again.

def test_lock_acquired_before_and_released_after_tick_work(conn, monkeypatch):
    import src.storage.db as db
    if db.fcntl is None:
        pytest.skip("fcntl is Unix-only; this platform uses the no-op path (covered separately)")

    call_order = []
    real_flock = db.fcntl.flock

    def tracking_flock(fd, operation):
        if operation == db.fcntl.LOCK_EX:
            call_order.append("lock")
        elif operation == db.fcntl.LOCK_UN:
            call_order.append("unlock")
        return real_flock(fd, operation)

    monkeypatch.setattr(db.fcntl, "flock", tracking_flock)
    monkeypatch.setattr("src.classification.scheduling._run_scheduler_tick_impl", lambda *a, **kw: call_order.append("tick"))

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)

    assert call_order == ["lock", "tick", "unlock"]


def test_no_op_lock_path_still_runs_tick_on_windows(conn, monkeypatch):
    # Directly exercises the fcntl-unavailable branch regardless of the
    # platform actually running this test - mirrors
    # test_no_op_lock_path_still_runs_migrations_on_windows in
    # tests/test_migration_lock.py.
    import src.storage.db as db
    monkeypatch.setattr(db, "fcntl", None)

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # must not raise

    row = conn.execute("SELECT field_classification_attempted_at FROM jobs WHERE job_id = 1").fetchone()
    assert row["field_classification_attempted_at"] is not None  # tick still did real work


def test_stale_running_row_is_superseded_not_left_wedged_forever(conn):
    now = datetime.now(timezone.utc)
    abandoned_start = now - timedelta(hours=2)  # long past any real chunk's duration
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES ('zombie', 'local_incremental', 'schedule', 'running', ?)",
        (abandoned_start.isoformat(),),
    )
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    run_scheduler_tick(conn, last_request_at=now, now=now)

    zombie = conn.execute("SELECT status FROM classification_runs WHERE run_id = 'zombie'").fetchone()
    assert zombie["status"] == "failed"  # superseded, not left running forever

    # And because it was cleared, a fresh local_incremental run for the real
    # pending job must actually have been allowed to start and complete.
    fresh = conn.execute(
        "SELECT status FROM classification_runs WHERE run_type = 'local_incremental' AND run_id != 'zombie'"
    ).fetchone()
    assert fresh is not None and fresh["status"] == "success"


def test_recently_started_running_row_is_not_touched_by_staleness_check(conn):
    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(minutes=2)  # well within a real chunk's expected duration
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES ('genuinely-active', 'local_incremental', 'schedule', 'running', ?)",
        (recent_start.isoformat(),),
    )
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    run_scheduler_tick(conn, last_request_at=now, now=now)

    still_running = conn.execute("SELECT status FROM classification_runs WHERE run_id = 'genuinely-active'").fetchone()
    assert still_running["status"] == "running"  # not stale yet - must not be touched

    # And the tick must have correctly deferred to it as still active - no
    # second local_incremental run started alongside it.
    count = conn.execute("SELECT COUNT(*) FROM classification_runs WHERE run_type = 'local_incremental'").fetchone()[0]
    assert count == 1
