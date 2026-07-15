import json
import sqlite3

import pytest


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
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
    conn.execute("""
        INSERT INTO jobs
        (market_id, source_name, url, url_hash, canonical_hash, description_hash,
         title, company, country, location, remote_type, raw_description,
         first_seen_at, last_seen_at, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ('ai_ml_global', 'test_source', 'https://example.com/job/1',
          'hash_url_1', 'hash_canonical_1', 'hash_desc_1',
          'Software Engineer', 'Acme', 'US', 'San Francisco', 'remote',
          'Test job description',
          '2026-07-01T00:00:00', '2026-07-01T00:00:00', '2026-07-01T00:00:00'))
    conn.commit()
    conn.close()

    import src.storage.db as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # Rotating-DB architecture: get_connection() resolves via serving/operational
    # paths, not DB_PATH. Point every rotation target at this one isolated test
    # file so run_migrations() migrates it in place (single-file emulation) and
    # nothing touches the real data/ directory.
    monkeypatch.setattr(db, "_SERVING_A_PATH", db_path)
    monkeypatch.setattr(db, "_SERVING_B_PATH", db_path)
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", db_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    db.run_migrations()
    return db_path


def test_new_tables_exist(migrated_db):
    conn = sqlite3.connect(migrated_db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"job_categories", "job_category_assignments", "groq_classification_queue", "classification_runs"} <= tables


def test_jobs_market_id_untouched_by_migration(migrated_db):
    conn = sqlite3.connect(migrated_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT market_id FROM jobs WHERE job_id = 1").fetchone()
    assert row["market_id"] == "ai_ml_global"


def test_new_jobs_columns_added(migrated_db):
    conn = sqlite3.connect(migrated_db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert {"field_category_id", "field_classification_confidence", "field_classification_method", "field_classification_attempted_at"} <= columns


def test_job_categories_seeded_from_config(migrated_db):
    conn = sqlite3.connect(migrated_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT name, parent_id, isco, keywords FROM job_categories WHERE category_id = 'it.software'").fetchone()
    assert row["name"] == "Software Engineering"
    assert row["parent_id"] == "it"
    keywords = json.loads(row["keywords"])
    assert "software engineer" in keywords

    count = conn.execute("SELECT COUNT(*) FROM job_categories").fetchone()[0]
    from config.job_markets import JOB_MARKETS
    assert count == len(JOB_MARKETS)


def test_migrations_idempotent_on_second_run(migrated_db):
    import src.storage.db as db
    db.run_migrations()  # must not raise
    conn = sqlite3.connect(migrated_db)
    count = conn.execute("SELECT COUNT(*) FROM job_categories").fetchone()[0]
    from config.job_markets import JOB_MARKETS
    assert count == len(JOB_MARKETS)  # re-seed didn't duplicate rows (category_id is PRIMARY KEY + INSERT OR REPLACE)
