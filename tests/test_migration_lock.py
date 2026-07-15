"""
tests/test_migration_lock.py
──────────────────────────────
Regression test for a real production incident: run_migrations() used to
have no protection against concurrent execution. gunicorn's 4 worker
processes each import web_viewer.py independently at startup, and each one
calls run_migrations() - on a deploy carrying real schema changes, all four
raced to write the same DDL to the same SQLite file simultaneously, raising
"database is locked" in every worker. gunicorn treated that as "worker
failed to boot" and shut the entire master process down; only Docker's
restart policy (pure luck - the second attempt found nothing left to
migrate) brought the site back.

Fixed with an fcntl file lock (Unix only - a no-op on Windows, where local
dev/tests run single-process and there's no concurrent-writer race to
guard against).
"""
import sqlite3

import pytest

import src.storage.db as db


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    # Matches migration 001_init.sql's real jobs table (same fixture shape
    # already verified against the full migration chain in
    # test_classification_schema.py) - a minimal ad-hoc schema fails partway
    # through with "no such column" once later migrations reference columns
    # like market_id that a trimmed-down fixture wouldn't have.
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            url TEXT NOT NULL,
            url_hash TEXT NOT NULL UNIQUE,
            canonical_hash TEXT NOT NULL,
            description_hash TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            remote_type TEXT NOT NULL DEFAULT 'unknown',
            posted_date TEXT,
            salary_min REAL,
            salary_max REAL,
            currency TEXT,
            raw_description TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    # Rotation-DB paths (same shape as tests/test_db_rotation_paths.py's
    # fixture) so bootstrap + the split migrations operate entirely inside
    # tmp_path and never touch the real data/ directory. After bootstrap the
    # real migrations run against serving_a (a copy of the legacy db_path),
    # so post-run assertions check serving_a, not the untouched legacy file.
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    return db_path


def test_run_migrations_still_works_with_locking_wrapper(isolated_db):
    db.run_migrations()  # must not raise
    conn = sqlite3.connect(db._SERVING_A_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "job_categories" in tables  # a real migration genuinely ran


def test_run_migrations_is_idempotent_under_the_lock(isolated_db):
    db.run_migrations()
    db.run_migrations()  # second call must not raise or duplicate seed rows
    conn = sqlite3.connect(db._SERVING_A_PATH)
    count = conn.execute("SELECT COUNT(*) FROM job_categories").fetchone()[0]
    from config.job_markets import JOB_MARKETS
    assert count == len(JOB_MARKETS)


def test_lock_acquired_before_and_released_after_migration_work(isolated_db, monkeypatch):
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
    monkeypatch.setattr(db, "_run_all_migrations", lambda: call_order.append("migrate"))

    db.run_migrations()

    assert call_order == ["lock", "migrate", "unlock"]


def test_no_op_lock_path_still_runs_migrations_on_windows(isolated_db, monkeypatch):
    # Directly exercises the fcntl-unavailable branch regardless of the
    # platform actually running this test.
    monkeypatch.setattr(db, "fcntl", None)
    db.run_migrations()
    conn = sqlite3.connect(db._SERVING_A_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "job_categories" in tables
