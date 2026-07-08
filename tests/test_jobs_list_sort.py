"""
tests/test_jobs_list_sort.py
──────────────────────────────
Tests for the /jobs page's diversity-vs-recency sort behavior.
"""

import sqlite3

import pytest


@pytest.fixture()
def jobs_app(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
            remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
            source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
            listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
    """)
    # Source A: 3 jobs, all recent. Source B: 1 job, older. Without diversity,
    # A's 3 jobs would all outrank B's single job on a plain posted_date sort.
    conn.executemany(
        "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status, diversity_rank) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "Job A1", "Co", "2026-01-06", "2026-01-06T00:00:00", "A", "m1", "active", 1),
            (2, "Job A2", "Co", "2026-01-05", "2026-01-05T00:00:00", "A", "m1", "active", 2),
            (3, "Job A3", "Co", "2026-01-04", "2026-01-04T00:00:00", "A", "m1", "active", 3),
            (4, "Job B1", "Co", "2026-01-01", "2026-01-01T00:00:00", "B", "m1", "active", 1),
        ],
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    return client


class TestDiversitySortDefault:
    def test_baseline_state_uses_diversity_rank_order(self, jobs_app):
        response = jobs_app.get("/jobs")
        html = response.get_data(as_text=True)
        # Diversity order: A1(rank1), B1(rank1), A2(rank2), A3(rank3)
        # B1 should appear before A2/A3 despite being the oldest job overall.
        pos_b1 = html.index("Job B1")
        pos_a2 = html.index("Job A2")
        pos_a3 = html.index("Job A3")
        assert pos_b1 < pos_a2 < pos_a3

    def test_explicit_sort_recent_uses_plain_date_order(self, jobs_app):
        response = jobs_app.get("/jobs?sort=recent")
        html = response.get_data(as_text=True)
        # Plain posted_date DESC: A1, A2, A3, B1 (B1 last, it's the oldest)
        pos_a3 = html.index("Job A3")
        pos_b1 = html.index("Job B1")
        assert pos_a3 < pos_b1

    def test_any_filter_forces_plain_date_order(self, jobs_app):
        response = jobs_app.get("/jobs?company=Co")
        html = response.get_data(as_text=True)
        pos_a3 = html.index("Job A3")
        pos_b1 = html.index("Job B1")
        assert pos_a3 < pos_b1

    def test_non_active_status_forces_plain_date_order(self, jobs_app):
        response = jobs_app.get("/jobs?status=all")
        html = response.get_data(as_text=True)
        pos_a3 = html.index("Job A3")
        pos_b1 = html.index("Job B1")
        assert pos_a3 < pos_b1


class TestUnrankedJobsInDiversityView:
    @pytest.fixture()
    def jobs_app_with_unranked(self, tmp_path, monkeypatch):
        db_path = tmp_path / "jobs.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE jobs (
                job_id INTEGER PRIMARY KEY,
                title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
                remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
                source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
                listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER
            );
            CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        """)
        conn.executemany(
            "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status, diversity_rank) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (1, "Job Ranked", "Co", "2026-01-01", "2026-01-01T00:00:00", "A", "m1", "active", 1),
                # Inserted after the last recompute — no rank assigned yet, but still
                # posted more recently than the ranked job above.
                (2, "Job Unranked New", "Co", "2026-01-10", "2026-01-10T00:00:00", "A", "m1", "active", None),
            ],
        )
        conn.commit()
        conn.close()

        import web_viewer
        monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
        web_viewer.app.config.update(TESTING=True)
        client = web_viewer.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        return client

    def test_unranked_job_appears_and_sorts_after_ranked(self, jobs_app_with_unranked):
        response = jobs_app_with_unranked.get("/jobs")
        html = response.get_data(as_text=True)
        assert "Job Unranked New" in html  # visible immediately, not hidden pending recompute
        pos_ranked = html.index("Job Ranked")
        pos_unranked = html.index("Job Unranked New")
        assert pos_ranked < pos_unranked  # ranked job (even though older) sorts first


class TestSortToggleVisibility:
    def test_toggle_shown_in_baseline_state(self, jobs_app):
        response = jobs_app.get("/jobs")
        html = response.get_data(as_text=True)
        assert 'href="/jobs?sort=recent"' in html
        assert 'href="/jobs?sort=diverse"' in html

    def test_toggle_hidden_when_filter_active(self, jobs_app):
        response = jobs_app.get("/jobs?company=Co")
        html = response.get_data(as_text=True)
        assert 'href="/jobs?sort=recent"' not in html
        assert 'href="/jobs?sort=diverse"' not in html

    def test_toggle_hidden_when_status_not_active(self, jobs_app):
        response = jobs_app.get("/jobs?status=all")
        html = response.get_data(as_text=True)
        assert 'href="/jobs?sort=recent"' not in html
