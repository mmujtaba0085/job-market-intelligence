"""
tests/test_companies_list_it.py
───────────────────────────────────
Regression coverage for /api/companies/list-it - see
docs/superpowers/specs/2026-07-18-companies-intelligence-it-first-design.md.
"""
import sqlite3

import pytest


@pytest.fixture()
def companies_it_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT, location TEXT DEFAULT '', country TEXT,
            field_category_id TEXT, remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            source_name TEXT DEFAULT 'TestSource', normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

    with conn:
        # Devsinc: 2 IT jobs in Pakistan - should appear in both sections
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Devsinc', 'Pakistan', 'it.software')")
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Devsinc', 'Pakistan', 'it.data')")
        # NVIDIA: 2 IT jobs in the US - global section only, not Pakistan
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('NVIDIA', 'United States', 'it.software')")
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('NVIDIA', 'United States', 'it.infrastructure')")
        # Shaukat Khanum Hospital: 1 stray IT-tagged job, 5 non-IT jobs, all Pakistan.
        # Its IT-scoped stats must reflect only the 1 IT job, not all 6.
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Shaukat Khanum Hospital', 'Pakistan', 'it.software')")
        for _ in range(5):
            conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Shaukat Khanum Hospital', 'Pakistan', 'healthcare.clinical')")
        # A Pakistan Jobs Bank parsing bug leaks a bare location into the
        # company field instead of a real employer name - see
        # _LOCATION_LEAKED_AS_COMPANY in web_viewer.py.
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Pakistan', 'Pakistan', 'it.software')")
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Pakistan', 'Pakistan', 'it.data')")
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    return client


def test_list_it_returns_pakistan_and_global_keys(companies_it_client):
    r = companies_it_client.get("/api/companies/list-it")
    assert r.status_code == 200
    data = r.get_json()
    assert "pakistan" in data
    assert "global" in data


def test_pakistan_section_excludes_non_pakistan_companies(companies_it_client):
    r = companies_it_client.get("/api/companies/list-it")
    pakistan_names = {c["company"] for c in r.get_json()["pakistan"]}
    assert "Devsinc" in pakistan_names
    assert "NVIDIA" not in pakistan_names


def test_global_section_includes_worldwide_it_companies(companies_it_client):
    r = companies_it_client.get("/api/companies/list-it")
    global_names = {c["company"] for c in r.get_json()["global"]}
    assert "Devsinc" in global_names
    assert "NVIDIA" in global_names


def test_neither_section_includes_non_it_only_companies(companies_it_client):
    """Shaukat Khanum's IT-tagged job count (1) is below the HAVING
    job_count >= 2 floor, so it must not appear in either section at all -
    proving the floor applies to the IT-scoped count, not the company's
    overall job count (6)."""
    r = companies_it_client.get("/api/companies/list-it")
    data = r.get_json()
    all_names = {c["company"] for c in data["pakistan"]} | {c["company"] for c in data["global"]}
    assert "Shaukat Khanum Hospital" not in all_names


def test_stray_it_job_company_shows_it_scoped_stats_not_blended():
    """Same Shaukat Khanum scenario, but with a second IT-tagged job added
    so it clears the >= 2 floor - its job_count must reflect only its 2
    IT-tagged jobs, not all 7 (6 non-IT + this one)."""
    import tempfile
    from pathlib import Path

    tmp_path = Path(tempfile.mkdtemp())
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT, location TEXT DEFAULT '', country TEXT,
            field_category_id TEXT, remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            source_name TEXT DEFAULT 'TestSource', normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    with conn:
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Shaukat Khanum Hospital', 'Pakistan', 'it.software')")
        conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Shaukat Khanum Hospital', 'Pakistan', 'it.data')")
        for _ in range(5):
            conn.execute("INSERT INTO jobs (company, country, field_category_id) VALUES ('Shaukat Khanum Hospital', 'Pakistan', 'healthcare.clinical')")
    conn.close()

    import web_viewer
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    mp.setattr(web_viewer, "DB_PATH", db_path)
    mp.setattr("src.storage.db._SERVING_A_PATH", db_path)
    mp.setattr("src.storage.db._SERVING_B_PATH", db_path)
    mp.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    mp.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    mp.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1

    r = client.get("/api/companies/list-it")
    pakistan = {c["company"]: c for c in r.get_json()["pakistan"]}
    assert pakistan["Shaukat Khanum Hospital"]["job_count"] == 2

    mp.undo()


def test_existing_list_endpoint_is_unaffected(companies_it_client):
    """/api/companies/list must still return every company (IT and non-IT),
    same shape as before this plan - proving list-it is additive, not a
    modification of the existing route."""
    r = companies_it_client.get("/api/companies/list")
    assert r.status_code == 200
    names = {c["company"] for c in r.get_json()}
    assert "Devsinc" in names
    assert "NVIDIA" in names
    assert "Shaukat Khanum Hospital" in names  # 6 total jobs, clears the >= 2 floor on its own


def test_list_it_excludes_location_leaked_as_company(companies_it_client):
    """The IT-scoped route excludes company='Pakistan' (a parsing bug),
    but the untouched /api/companies/list still shows it - confirming the
    fix stayed scoped to the new IT-first routes."""
    r = companies_it_client.get("/api/companies/list-it")
    data = r.get_json()
    all_names = {c["company"] for c in data["pakistan"]} | {c["company"] for c in data["global"]}
    assert "Pakistan" not in all_names

    r_unaffected = companies_it_client.get("/api/companies/list")
    assert "Pakistan" in {c["company"] for c in r_unaffected.get_json()}
