"""
tests/test_precomputed_summaries.py
─────────────────────────────────────
Unit tests for src/analytics/precomputed_summaries.py using an in-memory
SQLite DB, same pattern as tests/test_diversity_rank.py.

Empirically verified during design (against a scratch copy of real
production data, not theorized): the on-demand self-join this replaces
took ~2.4-3 seconds per call; reading from the precomputed table it
writes here takes ~0.1-0.7ms. These tests check correctness of what gets
written, not speed - the speed claim is already proven, re-proving it
here would just be a slow, flaky timing-based test for no added value.
"""
import sqlite3

import pytest


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE skills (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            normalized_skill TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            listing_status TEXT,
            normalized_title TEXT
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skill_combinations_summary (skill_a TEXT, skill_b TEXT, co_count INTEGER)")
    conn.execute("CREATE TABLE top_titles_summary (title TEXT, count INTEGER)")
    return conn


class TestRecomputeSkillCombinations:
    def test_counts_pairs_correctly_and_orders_by_frequency(self):
        from src.analytics.precomputed_summaries import _recompute_skill_combinations

        conn = _make_conn()
        # job 1: python+sql, job 2: python+sql, job 3: python+go
        # -> (python,sql) count=2, (go,python) count=1
        conn.executemany(
            "INSERT INTO skills (job_id, normalized_skill) VALUES (?,?)",
            [
                (1, "python"), (1, "sql"),
                (2, "python"), (2, "sql"),
                (3, "go"), (3, "python"),
            ],
        )
        conn.commit()

        written = _recompute_skill_combinations(conn, limit=50)

        rows = conn.execute("SELECT skill_a, skill_b, co_count FROM skill_combinations_summary ORDER BY co_count DESC").fetchall()
        assert written == 2
        assert (rows[0]["skill_a"], rows[0]["skill_b"], rows[0]["co_count"]) == ("python", "sql", 2)
        assert (rows[1]["skill_a"], rows[1]["skill_b"], rows[1]["co_count"]) == ("go", "python", 1)

    def test_respects_limit(self):
        from src.analytics.precomputed_summaries import _recompute_skill_combinations

        conn = _make_conn()
        # Three jobs each with a unique pair of skills -> 3 distinct pairs
        conn.executemany(
            "INSERT INTO skills (job_id, normalized_skill) VALUES (?,?)",
            [(1, "a"), (1, "b"), (2, "c"), (2, "d"), (3, "e"), (3, "f")],
        )
        conn.commit()

        written = _recompute_skill_combinations(conn, limit=2)
        assert written == 2

    def test_full_replace_clears_stale_rows(self):
        from src.analytics.precomputed_summaries import _recompute_skill_combinations

        conn = _make_conn()
        conn.execute("INSERT INTO skill_combinations_summary VALUES ('stale_a', 'stale_b', 999)")
        conn.commit()
        conn.executemany(
            "INSERT INTO skills (job_id, normalized_skill) VALUES (?,?)",
            [(1, "python"), (1, "sql")],
        )
        conn.commit()

        _recompute_skill_combinations(conn, limit=50)

        rows = conn.execute("SELECT skill_a FROM skill_combinations_summary WHERE skill_a = 'stale_a'").fetchall()
        assert rows == []


class TestRecomputeTopTitles:
    def test_groups_by_role_family_stripping_seniority(self):
        from src.analytics.precomputed_summaries import _recompute_top_titles

        conn = _make_conn()
        conn.executemany(
            "INSERT INTO jobs (job_id, listing_status, normalized_title) VALUES (?,?,?)",
            [
                (1, "active", "Senior Software Engineer"),
                (2, "active", "Software Engineer"),
                (3, "active", "Junior Software Engineer"),
                (4, "active", "Product Manager"),
            ],
        )
        conn.commit()

        written = _recompute_top_titles(conn, limit=30)

        rows = {r["title"]: r["count"] for r in conn.execute("SELECT title, count FROM top_titles_summary")}
        assert written == 2
        assert rows["Software Engineer"] == 3
        assert rows["Product Manager"] == 1

    def test_excludes_hidden_jobs_null_and_unknown_titles(self):
        from src.analytics.precomputed_summaries import _recompute_top_titles

        conn = _make_conn()
        conn.executemany(
            "INSERT INTO jobs (job_id, listing_status, normalized_title) VALUES (?,?,?)",
            [
                (1, "active", "Data Scientist"),
                (2, "hidden", "Data Scientist"),
                (3, "active", "Unknown"),
                (4, "active", None),
            ],
        )
        conn.commit()

        _recompute_top_titles(conn, limit=30)

        rows = {r["title"]: r["count"] for r in conn.execute("SELECT title, count FROM top_titles_summary")}
        assert rows == {"Data Scientist": 1}


class TestRoleFamily:
    def test_strips_seniority_prefix_and_suffix(self):
        from src.analytics.precomputed_summaries import _role_family

        assert _role_family("Senior Software Engineer") == "Software Engineer"
        assert _role_family("Junior Data Analyst") == "Data Analyst"
        assert _role_family("Marketing Intern") == "Marketing"
        assert _role_family("Product Manager") == "Product Manager"
