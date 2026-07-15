"""
tests/test_dashboard_geo_endpoint.py
──────────────────────────────────────
Regression coverage for GET /api/dashboard/geo: every active job must land
in exactly one bucket of the response - NULL/blank/'unknown' country values
are grouped into a visible "Unknown" bucket and 'global' into a visible
"Remote / Global" bucket, instead of being silently excluded from the
response (the old WHERE clause dropped them entirely) or truncated off the
end by a LIMIT (the old LIMIT 15 silently dropped the long tail once there
were more than 15 distinct country values - which, historically, included
raw US state-code leakage like "MA" fragmenting "United States" into dozens
of extra buckets).

Follows the fixture pattern established in
tests/test_skill_combinations_endpoint.py: a minimal hand-rolled sqlite
schema monkeypatched onto every rotating-DB target, get_db_connection()'s
"SELECT 1 FROM active_jobs LIMIT 1" health check satisfied by a real
active_jobs view, and a signed-in session - this endpoint is gated (it is
not in web_viewer.py's _PUBLIC_API_READS, unlike /api/dashboard/kpis), so
an anonymous request would 401 before ever reaching the query under test.
"""
import sqlite3

import pytest


@pytest.fixture()
def geo_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    # Minimal schema: dashboard_geo() only ever touches `country`, and
    # get_db_connection()'s health check only needs active_jobs to exist
    # and be queryable - see test_skill_combinations_endpoint.py for the
    # same minimal-schema precedent against a different dashboard endpoint.
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            country TEXT,
            listing_status TEXT DEFAULT 'active'
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")

    # Deliberately messy mix covering every shape the old query used to
    # mishandle: real countries, NULL, blank, 'unknown'/'Unknown' case
    # variants, 'global'/'Global' case variants, historical raw state-code
    # leakage ("MA" - not this endpoint's job to fix; that's the source-side
    # collector fix + scripts/backfill_us_state_country_codes.py), and one
    # hidden (non-active) row that must be excluded from the total entirely.
    active_rows = [
        "United States", "United States", "Germany",
        "Unknown", None, "", "unknown",
        "Global", "global",
        "MA",
    ]
    with conn:
        for country in active_rows:
            conn.execute(
                "INSERT INTO jobs (country, listing_status) VALUES (?, 'active')", (country,)
            )
        conn.execute("INSERT INTO jobs (country, listing_status) VALUES ('Hidden Co', 'hidden')")
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    # Rotating-DB architecture: web_viewer.get_db_connection() reads serving_db_path().
    # Point every rotation target at this one isolated test file so the request
    # under test always reads the seeded data, never the real data/ directory.
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    return web_viewer.app.test_client()


def test_anonymous_request_is_blocked(geo_client):
    r = geo_client.get("/api/dashboard/geo")
    assert r.status_code == 401


def test_bucket_total_equals_active_job_count_no_silent_drops(geo_client):
    """The core regression: 10 active jobs were seeded (the 11th is
    'hidden' and must not count) - summing `count` across every bucket in
    the response must recover exactly 10, proving nothing was dropped by
    either the old WHERE-clause exclusion or the old LIMIT 15 truncation."""
    with geo_client.session_transaction() as sess:
        sess["user_id"] = 1

    r = geo_client.get("/api/dashboard/geo")
    assert r.status_code == 200
    geo = r.get_json()

    total = sum(row["count"] for row in geo)
    assert total == 10, f"bucket total {total} != 10 active jobs seeded; buckets={geo}"


def test_unknown_and_global_are_visible_buckets_not_dropped(geo_client):
    """NULL / '' / 'Unknown' / 'unknown' collapse into one visible "Unknown"
    bucket; 'Global' / 'global' collapse into one visible "Remote / Global"
    bucket - distinct from each other, and neither silently excluded the
    way the old WHERE clause excluded them."""
    with geo_client.session_transaction() as sess:
        sess["user_id"] = 1

    r = geo_client.get("/api/dashboard/geo")
    assert r.status_code == 200
    geo = {row["country"]: row["count"] for row in r.get_json()}

    # Unknown, None, '', 'unknown' = 4 rows collapsed into one bucket.
    assert geo.get("Unknown") == 4, f"expected Unknown=4, got buckets={geo}"
    # Global, global = 2 rows collapsed into one bucket, labeled distinctly
    # from Unknown so the frontend can tell "known-remote" apart from
    # "we don't know where this is".
    assert geo.get("Remote / Global") == 2, f"expected 'Remote / Global'=2, got buckets={geo}"
    assert "Global" not in geo, "'Global' should have been relabeled to 'Remote / Global', not left as-is"

    assert geo.get("United States") == 2
    assert geo.get("Germany") == 1

    # The hidden (non-active) row's country must never appear at all.
    assert "Hidden Co" not in geo


def test_hidden_job_excluded_from_total(geo_client):
    """listing_status='hidden' rows are excluded by active_jobs itself -
    confirms the bucket total tracks *active* jobs, not every job row."""
    with geo_client.session_transaction() as sess:
        sess["user_id"] = 1

    r = geo_client.get("/api/dashboard/geo")
    geo = r.get_json()
    total = sum(row["count"] for row in geo)
    assert total == 10, "hidden row must not be counted in the active-job bucket total"
