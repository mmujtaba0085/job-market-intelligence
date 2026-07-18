"""
tests/test_emerging_declining_blended_classification.py
─────────────────────────────────────────────────────────
Regression coverage for a blended-classification bug in
/api/dashboard/emerging, /api/dashboard/declining, and /metrics: a skill
present in multiple markets could be pulled into the declining (or
emerging) list purely because ONE market's stored declining_flag/
emerging_flag was set, even when its displayed blended
AVG(growth_percentage) sat on the opposite side of the threshold.

Confirmed live 2026-07-18: "amazon web services" showed growth=+9.74 in
the declining list (ai_ml_global +78.57%, swe_backend_global -59.09%,
MAX(declining_flag)=1 from the second market alone). Fixed by
recomputing the classification from the blended AVG(growth_percentage)
against GROWTH_THRESHOLD/DECLINING_THRESHOLD directly, instead of
trusting HAVING MAX(emerging_flag)=1 / HAVING MAX(declining_flag)=1.
"""
import sqlite3

import pytest

from config.settings import GROWTH_THRESHOLD, DECLINING_THRESHOLD


@pytest.fixture()
def metrics_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT,
            source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            normalized_title TEXT DEFAULT '', diversity_rank INTEGER, field_category_id TEXT
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("""
        CREATE TABLE weekly_metrics (
            market_id TEXT, week_start_date TEXT, week_number INTEGER, skill_name TEXT,
            category TEXT, frequency INTEGER, growth_percentage REAL, absolute_delta INTEGER,
            mover_score REAL, emerging_flag INTEGER, declining_flag INTEGER
        )
    """)
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

    with conn:
        # "mixed signal" skill: one market strongly declining (flag set),
        # the other strongly emerging - blended average lands POSITIVE
        # and above GROWTH_THRESHOLD (blended: (160 + -40)/2 = 60 >= 50),
        # so it must appear in emerging, not declining, despite one
        # market's declining_flag being 1 - this is the shape of the
        # actual live bug (a positive blended skill still landing in the
        # declining list via HAVING MAX(declining_flag)=1).
        conn.execute(
            "INSERT INTO weekly_metrics (market_id, week_start_date, skill_name, category, frequency, growth_percentage, mover_score, emerging_flag, declining_flag) "
            "VALUES ('ai_ml_global', '2026-07-06', 'mixed signal skill', 'cloud', 25, 160.0, 60.0, 1, 0)"
        )
        conn.execute(
            "INSERT INTO weekly_metrics (market_id, week_start_date, skill_name, category, frequency, growth_percentage, mover_score, emerging_flag, declining_flag) "
            "VALUES ('swe_backend_global', '2026-07-06', 'mixed signal skill', 'cloud', 9, -40.0, -20.0, 0, 1)"
        )
        # "genuinely declining" skill: both markets agree, blended average
        # is well below DECLINING_THRESHOLD - must still appear.
        conn.execute(
            "INSERT INTO weekly_metrics (market_id, week_start_date, skill_name, category, frequency, growth_percentage, mover_score, emerging_flag, declining_flag) "
            "VALUES ('ai_ml_global', '2026-07-06', 'genuinely declining skill', 'programming', 20, -40.0, -20.0, 0, 1)"
        )
        conn.execute(
            "INSERT INTO weekly_metrics (market_id, week_start_date, skill_name, category, frequency, growth_percentage, mover_score, emerging_flag, declining_flag) "
            "VALUES ('swe_backend_global', '2026-07-06', 'genuinely declining skill', 'programming', 20, -35.0, -18.0, 0, 1)"
        )
        # "genuinely emerging" skill: both markets agree, blended average
        # is well above GROWTH_THRESHOLD - must appear in emerging.
        conn.execute(
            "INSERT INTO weekly_metrics (market_id, week_start_date, skill_name, category, frequency, growth_percentage, mover_score, emerging_flag, declining_flag) "
            "VALUES ('ai_ml_global', '2026-07-06', 'genuinely emerging skill', 'ml_core', 20, 90.0, 40.0, 1, 0)"
        )
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


def test_mixed_signal_skill_excluded_from_declining(metrics_client):
    """A skill whose blended growth% is positive must not appear in the
    declining list, even though one of its market rows has
    declining_flag=1 - this was the actual live bug."""
    r = metrics_client.get("/api/dashboard/declining")
    names = {s["skill"] for s in r.get_json()}
    assert "mixed signal skill" not in names


def test_mixed_signal_skill_appears_in_emerging(metrics_client):
    """Same skill's blended growth% (plain AVG per the existing query,
    (160 + -40)/2 = 60) clears GROWTH_THRESHOLD, so it belongs in
    emerging instead."""
    blended_growth = (160.0 + -40.0) / 2
    assert blended_growth >= GROWTH_THRESHOLD, "fixture assumption: blended growth must clear the emerging bar"
    r = metrics_client.get("/api/dashboard/emerging")
    names = {s["skill"] for s in r.get_json()}
    assert "mixed signal skill" in names


def test_genuinely_declining_skill_still_appears(metrics_client):
    r = metrics_client.get("/api/dashboard/declining")
    names = {s["skill"] for s in r.get_json()}
    assert "genuinely declining skill" in names


def test_genuinely_emerging_skill_still_appears(metrics_client):
    r = metrics_client.get("/api/dashboard/emerging")
    names = {s["skill"] for s in r.get_json()}
    assert "genuinely emerging skill" in names


def test_displayed_growth_is_never_contradictory(metrics_client):
    """For every skill in the declining list, its displayed growth% must
    actually be <= DECLINING_THRESHOLD - the exact invariant the live bug
    violated (a skill showing +9.74% growth listed as declining)."""
    r = metrics_client.get("/api/dashboard/declining")
    for skill in r.get_json():
        assert skill["growth"] <= DECLINING_THRESHOLD, (
            f"{skill['skill']} shown in declining with growth={skill['growth']}, "
            f"which is not <= DECLINING_THRESHOLD ({DECLINING_THRESHOLD})"
        )

    r = metrics_client.get("/api/dashboard/emerging")
    for skill in r.get_json():
        assert skill["growth"] >= GROWTH_THRESHOLD, (
            f"{skill['skill']} shown in emerging with growth={skill['growth']}, "
            f"which is not >= GROWTH_THRESHOLD ({GROWTH_THRESHOLD})"
        )


def test_metrics_page_excludes_mixed_signal_skill_from_declining(metrics_client, monkeypatch):
    """/metrics (the legacy metrics_overview() page) has the same
    blended-classification query duplicated - must be fixed identically.
    Captures the actual render_template() context passed by
    metrics_overview() rather than parsing HTML, since the template's
    markup structure isn't otherwise under test here."""
    import web_viewer

    captured = {}
    real_render_template = web_viewer.render_template

    def capturing_render_template(name, **kwargs):
        if name == "metrics.html":
            captured.update(kwargs)
        return real_render_template(name, **kwargs)

    monkeypatch.setattr(web_viewer, "render_template", capturing_render_template)

    r = metrics_client.get("/metrics")
    assert r.status_code == 200

    declining_names = {row["skill_name"] for row in captured["declining"]}
    emerging_names = {row["skill_name"] for row in captured["emerging"]}
    assert "mixed signal skill" not in declining_names
    assert "mixed signal skill" in emerging_names
    assert "genuinely declining skill" in declining_names
