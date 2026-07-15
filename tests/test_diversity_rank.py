"""
tests/test_diversity_rank.py
─────────────────────────────
Unit tests for src/analytics/diversity_rank.py using an in-memory SQLite DB.
"""

import sqlite3

import pytest


def _point_rotation_paths_at(db, monkeypatch, db_path, tmp_path):
    """Rotating-DB architecture: get_connection() resolves via serving/operational
    paths, not DB_PATH. Point every rotation target at this one isolated test file
    (single-file emulation) so run_migrations() migrates it in place and nothing
    touches the real data/ directory."""
    monkeypatch.setattr(db, "_SERVING_A_PATH", db_path)
    monkeypatch.setattr(db, "_SERVING_B_PATH", db_path)
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", db_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            source_name TEXT,
            posted_date TEXT,
            ingested_at TEXT,
            listing_status TEXT,
            diversity_rank INTEGER
        )
    """)
    return conn


def _insert(conn, rows):
    conn.executemany(
        "INSERT INTO jobs (job_id, source_name, posted_date, ingested_at, listing_status) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()


class TestRecomputeDiversityRanks:
    def test_ranks_each_source_independently_by_recency(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "active"),
            (3, "A", "2026-01-06", "2026-01-06T00:00:00", "active"),
            (4, "B", "2026-01-04", "2026-01-04T00:00:00", "active"),
            (5, "B", "2026-01-01", "2026-01-01T00:00:00", "active"),
        ])

        _recompute(conn)

        ranks = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}
        # Source A: job 3 (01-06) newest -> rank 1, job 1 (01-05) -> rank 2, job 2 (01-03) -> rank 3
        assert ranks[3] == 1
        assert ranks[1] == 2
        assert ranks[2] == 3
        # Source B: job 4 (01-04) newest -> rank 1, job 5 (01-01) -> rank 2
        assert ranks[4] == 1
        assert ranks[5] == 2

    def test_non_active_jobs_left_unranked(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "closed"),
            (3, "A", "2026-01-04", "2026-01-04T00:00:00", "hidden"),
        ])

        _recompute(conn)

        ranks = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}
        assert ranks[1] == 1
        assert ranks[2] is None
        assert ranks[3] is None

    def test_null_listing_status_counts_as_active(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [(1, "A", "2026-01-05", "2026-01-05T00:00:00", None)])

        _recompute(conn)

        rank = conn.execute("SELECT diversity_rank FROM jobs WHERE job_id = 1").fetchone()["diversity_rank"]
        assert rank == 1

    def test_idempotent(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "active"),
        ])

        _recompute(conn)
        first_pass = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}
        _recompute(conn)
        second_pass = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}

        assert first_pass == second_pass

    def test_returns_count_of_active_rows_updated(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "active"),
            (3, "A", "2026-01-01", "2026-01-01T00:00:00", "closed"),
        ])

        updated = _recompute(conn)

        assert updated == 2


class TestRunMigrationsAddsColumn:
    def test_diversity_rank_column_added(self, tmp_path, monkeypatch):
        import src.storage.db as db

        db_path = tmp_path / "jobs.sqlite"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        _point_rotation_paths_at(db, monkeypatch, db_path, tmp_path)
        db.run_migrations()

        conn = sqlite3.connect(str(db_path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        conn.close()

        assert "diversity_rank" in columns

    def test_running_migrations_twice_does_not_error(self, tmp_path, monkeypatch):
        import src.storage.db as db

        db_path = tmp_path / "jobs.sqlite"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        _point_rotation_paths_at(db, monkeypatch, db_path, tmp_path)
        db.run_migrations()
        db.run_migrations()  # must not raise on a second run

        conn = sqlite3.connect(str(db_path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        conn.close()
        assert "diversity_rank" in columns
