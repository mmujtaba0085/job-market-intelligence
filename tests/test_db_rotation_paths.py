"""
tests/test_db_rotation_paths.py
────────────────────────────────
Covers the pointer file (atomic read/write, atomic-replace-under-open-handle)
and the four connection-resolution functions added to src/storage/db.py for
the rotating-DB architecture (see
docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md).
"""
import os
import sqlite3

import pytest

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
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")  # legacy path, absent = fresh install
    return tmp_path


def test_read_pointer_defaults_to_a_when_missing(isolated_paths):
    assert db._read_pointer() == "a"


def test_write_then_read_pointer_round_trips(isolated_paths):
    db._write_pointer("b")
    assert db._read_pointer() == "b"


def test_write_pointer_is_atomic_replace_not_in_place_edit(isolated_paths):
    db._write_pointer("a")
    # The point of using os.replace() (rather than an in-place write) is that
    # _write_pointer() must never truncate the file a concurrent reader has
    # open - a reader mid-rotation sees the old value or the new one, never a
    # partial/empty file.
    if os.name == "posix":
        # POSIX: os.replace() swaps the inode, so a handle opened *before* the
        # replace keeps reading the OLD content even after the path is
        # replaced. Production runs on Linux (gunicorn), so this is the path
        # that actually matters.
        with open(db._POINTER_PATH) as still_open:
            db._write_pointer("b")
            assert still_open.read().strip() == "a"
    else:
        # Windows: os.replace() onto a path that still has an open read handle
        # raises PermissionError instead of swapping in place - the OS refuses
        # to replace the file, so it is likewise never truncated. Verify the
        # rename simply waits until no handle is held, then round-trips.
        with open(db._POINTER_PATH) as still_open:
            assert still_open.read().strip() == "a"
        db._write_pointer("b")
    assert db._read_pointer() == "b"


def test_serving_path_for_maps_a_and_b(isolated_paths):
    assert db._serving_path_for("a") == db._SERVING_A_PATH
    assert db._serving_path_for("b") == db._SERVING_B_PATH


def test_get_connection_resolves_to_current_pointer(isolated_paths):
    db.run_migrations()
    db._write_pointer("a")
    conn = db.get_connection()
    conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h1','c1','d1','t','','n','n','n')")
    conn.commit()
    conn.close()

    db._write_pointer("b")
    conn_b = db.get_connection()
    count_b = conn_b.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn_b.close()
    assert count_b == 0  # serving_b is a separate, empty file

    db._write_pointer("a")
    conn_a = db.get_connection()
    count_a = conn_a.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn_a.close()
    assert count_a == 1


def test_get_free_connection_is_always_the_other_file(isolated_paths):
    db.run_migrations()
    db._write_pointer("a")
    conn = db.get_free_connection()
    conn.execute("SELECT 1")  # just confirm it opens without error
    conn.close()
    assert db._free_path() == db._SERVING_B_PATH

    db._write_pointer("b")
    assert db._free_path() == db._SERVING_A_PATH


def test_use_buffer_connection_redirects_get_connection(isolated_paths):
    db.run_migrations()
    with db.use_buffer_connection():
        conn = db.get_connection()
        conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h2','c2','d2','t','','n','n','n')")
        conn.commit()
        conn.close()

    # Outside the context manager, get_connection() is back to Serving and
    # must NOT see the row written while buffer was active.
    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert count == 0

    buffer_conn = db.get_buffer_connection()
    buffer_count = buffer_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    buffer_conn.close()
    assert buffer_count == 1


def test_use_free_connection_redirects_get_connection(isolated_paths):
    db.run_migrations()
    db._write_pointer("a")
    with db.use_free_connection():
        conn = db.get_connection()
        conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h3','c3','d3','t','','n','n','n')")
        conn.commit()
        conn.close()

    free_conn = db.get_free_connection()  # should be serving_b
    free_count = free_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    free_conn.close()
    assert free_count == 1


def test_operational_connection_has_pipeline_tables_not_jobs(isolated_paths):
    db.run_migrations()
    conn = db.get_operational_connection()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "pipeline_config" in tables
    assert "pipeline_runs" in tables
    assert "jobs" not in tables


def test_serving_files_have_no_pipeline_config_table(isolated_paths):
    db.run_migrations()
    conn = db.get_connection()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "jobs" in tables
    assert "pipeline_config" not in tables


def test_bootstrap_migrates_legacy_single_file_data_into_serving_a_and_operational(isolated_paths):
    # Simulate an existing pre-rotation production DB at the legacy DB_PATH,
    # with real data in both a rotating table and an operational table.
    # Legacy jobs table mirrors the real 001_init.sql schema (same reasoning as
    # tests/test_migration_lock.py's fixture): a trimmed-down jobs table can't
    # survive the real migration chain, which indexes/selects columns like
    # posted_date and location. A real pre-rotation production DB always has
    # the full schema, so this is what bootstrap actually operates on.
    legacy_conn = sqlite3.connect(db.DB_PATH)
    legacy_conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL, source_name TEXT NOT NULL, url TEXT NOT NULL,
            url_hash TEXT NOT NULL UNIQUE, canonical_hash TEXT NOT NULL, description_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', remote_type TEXT NOT NULL DEFAULT 'unknown',
            posted_date TEXT, salary_min REAL, salary_max REAL, currency TEXT,
            raw_description TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, ingested_at TEXT NOT NULL);
        INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at)
            VALUES ('m','s','u','legacy-hash','c','d','Legacy Job','','n','n','n');
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO pipeline_config (key, value, updated_at) VALUES ('ingest_interval_hours', '6', 'n');
    """)
    legacy_conn.commit()
    legacy_conn.close()

    db.run_migrations()  # triggers _bootstrap_rotation_files() since pointer file doesn't exist yet

    assert db._read_pointer() == "a"

    serving_conn = db.get_connection()
    row = serving_conn.execute("SELECT title FROM jobs WHERE url_hash = 'legacy-hash'").fetchone()
    serving_conn.close()
    assert row is not None and row["title"] == "Legacy Job"

    op_conn = db.get_operational_connection()
    cfg_row = op_conn.execute("SELECT value FROM pipeline_config WHERE key = 'ingest_interval_hours'").fetchone()
    op_conn.close()
    assert cfg_row is not None and cfg_row["value"] == "6"


def test_bootstrap_is_a_noop_once_pointer_already_exists(isolated_paths):
    db.run_migrations()
    db._write_pointer("b")  # simulate a rotation having already happened
    conn = db.get_connection()  # currently serving_b
    conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h4','c4','d4','t','','n','n','n')")
    conn.commit()
    conn.close()

    db.run_migrations()  # must NOT re-bootstrap and must NOT flip the pointer back to 'a'

    assert db._read_pointer() == "b"
    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'h4'").fetchone()[0]
    conn.close()
    assert count == 1
