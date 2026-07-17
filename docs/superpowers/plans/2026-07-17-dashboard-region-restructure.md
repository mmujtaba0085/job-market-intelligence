# Dashboard Region Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the dashboard's page-level Region toggle (every general widget goes back to worldwide-scoped, status-filtered-only); replace Multi-Location Jobs with a "Top IT Jobs" + "Top Hiring IT Companies" pair governed by one shared, page-session-only local Region selector, each with a "See all" deep-link.

**Architecture:** Four existing dashboard routes (`dashboard_kpis`, `dashboard_top_skills`, `dashboard_sources`, `dashboard_companies`) drop their `_region_scope_clause()` call. `dashboard_location_diversity()` is deleted outright. Two new routes (`dashboard_top_it_jobs`, `dashboard_top_it_companies`) are added with their own local `region` query param (not the removed dashboard-wide default), always filtered to `field_category_id LIKE 'it.%'`. Frontend: `templates/dashboard.html` drops the Region `<select>` and the two already-removed-in-a-prior-plan KPI cards, gains the two new widgets plus their shared local selector; `static/js/dashboard.js`'s `dashboardApi()` stops sending `region`, and two new load functions read the new local selector directly.

**Tech Stack:** Flask, SQLite, vanilla JS, Chart.js (unaffected by this plan — no chart widgets change).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every pytest invocation must use `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`.
- Any new test fixture that touches DB paths must patch ALL of `src.storage.db._SERVING_A_PATH`, `_SERVING_B_PATH`, `_BUFFER_DB_PATH`, `_OPERATIONAL_DB_PATH`, `_POINTER_PATH` together when it calls `db.run_migrations()` or otherwise exercises real connection resolution.
- `_region_scope_clause()` and `_default_region()` are NOT deleted anywhere in this plan — `jobs_list()` (`/jobs`'s own Region toggle) still depends on both, completely unaffected throughout.
- The two new routes' `region` param is **not** the same thing as `_default_region()`'s cookie-backed sitewide default — it's a plain `request.args.get("region", "pk")` read, page-session-only, no `jmi_region` cookie interaction. Do not wire the new local selector to read or write that cookie.
- This plan does **not** touch `/companies/intelligence` or `/jobs`'s not-yet-built Category toggle — the "See all" links point at both, with inert query params on the receiving end for now. That's confirmed, separate, deferred work.
- After Task 2, a manual browser verification step is required — this session's established practice for UI changes. Do not consider Task 2 done on passing automated tests alone.

---

### Task 1: Backend — region-scope removal + two new IT-scoped routes

**Files:**
- Modify: `web_viewer.py` — `dashboard_kpis()` (~line 717), `dashboard_top_skills()` (~line 857, fallback path only), `dashboard_sources()` (~line 961), `dashboard_companies()` (~line 1045): remove region reading/scoping from each.
- Modify: `web_viewer.py` — delete `dashboard_location_diversity()` (~line 1070) and its route decorator entirely.
- Modify: `web_viewer.py` — `_PUBLIC_API_READS` (~line 136): remove `"/api/dashboard/location-diversity"`, add `"/api/dashboard/top-it-jobs"` and `"/api/dashboard/top-it-companies"`.
- Modify: `web_viewer.py` — add two new routes: `dashboard_top_it_jobs()`, `dashboard_top_it_companies()`, placed immediately after `dashboard_companies()` (where `dashboard_location_diversity()` used to be).
- Modify: `tests/test_region_scope_filter.py` — 7 existing tests currently use `/api/dashboard/kpis` as their region-behavior test vehicle; repoint all 7 to `/jobs` (the only route left with region behavior after this task), matching the exact assertion style `test_jobs_list_defaults_to_pakistan_scope`/`test_jobs_list_region_all_shows_every_country` already use.
- Test: `tests/test_dashboard_top_it_widgets.py` (new).

**Interfaces:**
- Produces: `GET /api/dashboard/top-it-jobs` — query params `status` (default `"all"`), `region` (default `"pk"`, values `"pk"`/`"all"`). Returns a JSON array of `{job_id, title, company, location, country, remote_type}` objects, up to 7, ordered by recency.
- Produces: `GET /api/dashboard/top-it-companies` — same query params. Returns a JSON array of `{company, count}` objects, up to 10, ordered by count descending.

- [ ] **Step 1: Read the current exact code before editing**

Read `web_viewer.py` lines 700-1090 in full to confirm the five routes' exact current line numbers and code haven't drifted since this plan was written.

- [ ] **Step 2: Write the failing tests for the two new routes**

Create `tests/test_dashboard_top_it_widgets.py`:

```python
"""
tests/test_dashboard_top_it_widgets.py
──────────────────────────────────────────
Regression coverage for /api/dashboard/top-it-jobs and
/api/dashboard/top-it-companies - see
docs/superpowers/specs/2026-07-17-dashboard-region-restructure-design.md.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def it_widgets_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT,
            field_category_id TEXT, source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE weekly_metrics (market_id TEXT, week_start_date TEXT, week_number INTEGER, skill_name TEXT, category TEXT, frequency INTEGER, growth_percentage REAL, absolute_delta INTEGER, mover_score REAL, emerging_flag INTEGER, declining_flag INTEGER)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

    today = datetime.now().strftime("%Y-%m-%d")
    with conn:
        # Pakistan IT jobs (2)
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('PK IT Job 1', 'Devsinc', 'Pakistan', 'it.software', ?)", (today,))
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('PK IT Job 2', 'Devsinc', 'Pakistan', 'it.data', ?)", (today,))
        # Non-Pakistan IT job (1) - only visible under region=all
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('US IT Job', 'NVIDIA', 'United States', 'it.software', ?)", (today,))
        # Pakistan non-IT job (1) - must never appear under either region value
        conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('PK Nurse Job', 'Shaukat Khanum', 'Pakistan', 'healthcare.clinical', ?)", (today,))
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


def test_top_it_jobs_defaults_to_pakistan_scope(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-jobs")
    assert r.status_code == 200
    titles = [j["title"] for j in r.get_json()]
    assert "PK IT Job 1" in titles
    assert "PK IT Job 2" in titles
    assert "US IT Job" not in titles
    assert "PK Nurse Job" not in titles


def test_top_it_jobs_region_all_still_applies_it_filter(it_widgets_client):
    """region=all broadens the country restriction but must NOT become an
    unfiltered "all jobs" list - the non-IT Pakistan job must still be
    excluded even when region is broadened."""
    r = it_widgets_client.get("/api/dashboard/top-it-jobs?region=all")
    assert r.status_code == 200
    titles = [j["title"] for j in r.get_json()]
    assert "PK IT Job 1" in titles
    assert "PK IT Job 2" in titles
    assert "US IT Job" in titles
    assert "PK Nurse Job" not in titles


def test_top_it_companies_defaults_to_pakistan_scope(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-companies")
    assert r.status_code == 200
    companies = [c["company"] for c in r.get_json()]
    assert "Devsinc" in companies
    assert "NVIDIA" not in companies
    assert "Shaukat Khanum" not in companies


def test_top_it_companies_region_all_includes_worldwide_it_companies(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/top-it-companies?region=all")
    assert r.status_code == 200
    companies = [c["company"] for c in r.get_json()]
    assert "Devsinc" in companies
    assert "NVIDIA" in companies
    assert "Shaukat Khanum" not in companies


def test_top_it_jobs_no_backfill_from_non_it_when_scarce(it_widgets_client):
    """Only 2 Pakistan IT jobs exist in the fixture - the route must return
    exactly those 2, never padding with the non-IT Pakistan job to reach
    a larger count."""
    r = it_widgets_client.get("/api/dashboard/top-it-jobs")
    assert len(r.get_json()) == 2


def test_top_it_jobs_respects_status_window(it_widgets_client):
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (title, company, country, field_category_id, posted_date) VALUES ('Old PK IT Job', 'Contour', 'Pakistan', 'it.software', ?)", (old_date,))
    conn.commit()
    conn.close()

    r_active = it_widgets_client.get("/api/dashboard/top-it-jobs?status=active")
    assert "Old PK IT Job" not in [j["title"] for j in r_active.get_json()]

    r_all = it_widgets_client.get("/api/dashboard/top-it-jobs?status=all")
    assert "Old PK IT Job" in [j["title"] for j in r_all.get_json()]


def test_location_diversity_route_no_longer_exists(it_widgets_client):
    r = it_widgets_client.get("/api/dashboard/location-diversity")
    assert r.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_top_it_widgets.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — the two new routes don't exist yet (404), and `test_location_diversity_route_no_longer_exists` currently PASSES-as-a-false-negative (the route still exists and returns 200) — note this one specifically should currently FAIL too, confirming it's testing real not-yet-applied behavior.

- [ ] **Step 4: Remove region-scoping from the four existing routes**

In `web_viewer.py`, in `dashboard_kpis()`, replace:

```python
    status = request.args.get("status", "all")
    region = _default_region()
    status_clause = _status_window_clause(status)
    region_clause = _region_scope_clause(region)

    # Total jobs
    cursor.execute(f"SELECT COUNT(*) as count FROM active_jobs WHERE 1=1{status_clause}{region_clause}")
    total_jobs = cursor.fetchone()["count"]

    # Total skills - scoped to skills belonging to in-scope (status/window
    # filtered) jobs, via a join back to active_jobs on skills.job_id, so
    # "total skills" respects the same toggle as "total jobs" instead of
    # always counting every skill ever seen regardless of job age/status.
    cursor.execute(f"""
        SELECT COUNT(DISTINCT s.normalized_skill) as count
        FROM skills s
        JOIN active_jobs j ON j.job_id = s.job_id
        WHERE 1=1{_status_window_clause(status, "j.")}{_region_scope_clause(region, "j.")}
    """)
    total_skills = cursor.fetchone()["count"]

    # Active sources
    cursor.execute(f"SELECT COUNT(DISTINCT source_name) as count FROM active_jobs WHERE 1=1{status_clause}{region_clause}")
    active_sources = cursor.fetchone()["count"]

    # Remote percentage
    cursor.execute(f"""
        SELECT
            CAST(SUM(CASE WHEN LOWER(remote_type) = 'remote' THEN 1 ELSE 0 END) AS FLOAT) * 100 / COUNT(*) as pct
        FROM active_jobs
        WHERE 1=1{status_clause}{region_clause}
    """)
    remote_pct = cursor.fetchone()["pct"] or 0
```

with:

```python
    status = request.args.get("status", "all")
    status_clause = _status_window_clause(status)

    # Total jobs
    cursor.execute(f"SELECT COUNT(*) as count FROM active_jobs WHERE 1=1{status_clause}")
    total_jobs = cursor.fetchone()["count"]

    # Total skills - scoped to skills belonging to in-scope (status/window
    # filtered) jobs, via a join back to active_jobs on skills.job_id, so
    # "total skills" respects the same toggle as "total jobs" instead of
    # always counting every skill ever seen regardless of job age/status.
    cursor.execute(f"""
        SELECT COUNT(DISTINCT s.normalized_skill) as count
        FROM skills s
        JOIN active_jobs j ON j.job_id = s.job_id
        WHERE 1=1{_status_window_clause(status, "j.")}
    """)
    total_skills = cursor.fetchone()["count"]

    # Active sources
    cursor.execute(f"SELECT COUNT(DISTINCT source_name) as count FROM active_jobs WHERE 1=1{status_clause}")
    active_sources = cursor.fetchone()["count"]

    # Remote percentage
    cursor.execute(f"""
        SELECT
            CAST(SUM(CASE WHEN LOWER(remote_type) = 'remote' THEN 1 ELSE 0 END) AS FLOAT) * 100 / COUNT(*) as pct
        FROM active_jobs
        WHERE 1=1{status_clause}
    """)
    remote_pct = cursor.fetchone()["pct"] or 0
```

(`total_jobs` and `active_sources` are still computed and returned here even though their KPI cards were removed from the template by an earlier plan — the JSON response keeps both fields; nothing reads them client-side anymore, but trimming the response shape is out of scope for this plan, which is about region-scoping, not response shape.)

In `dashboard_top_skills()`'s fallback block, replace:

```python
    if not skills:
        status = request.args.get("status", "all")
        region = _default_region()
        cursor.execute(f"""
            SELECT s.normalized_skill as skill, COUNT(*) as count, s.category
            FROM skills s
            JOIN active_jobs j ON j.job_id = s.job_id
            WHERE 1=1{_status_window_clause(status, "j.")}{_region_scope_clause(region, "j.")}
            GROUP BY s.normalized_skill, s.category
            ORDER BY count DESC
            LIMIT 10
        """)
        skills = [dict(row) for row in cursor.fetchall()]
```

with:

```python
    if not skills:
        status = request.args.get("status", "all")
        cursor.execute(f"""
            SELECT s.normalized_skill as skill, COUNT(*) as count, s.category
            FROM skills s
            JOIN active_jobs j ON j.job_id = s.job_id
            WHERE 1=1{_status_window_clause(status, "j.")}
            GROUP BY s.normalized_skill, s.category
            ORDER BY count DESC
            LIMIT 10
        """)
        skills = [dict(row) for row in cursor.fetchall()]
```

In `dashboard_sources()`, replace:

```python
    status = request.args.get("status", "all")
    region = _default_region()

    cursor.execute(f"""
        SELECT source_name, COUNT(*) as count
        FROM active_jobs
        WHERE 1=1{_status_window_clause(status)}{_region_scope_clause(region)}
        GROUP BY source_name
        ORDER BY count DESC
    """)
```

with:

```python
    status = request.args.get("status", "all")

    cursor.execute(f"""
        SELECT source_name, COUNT(*) as count
        FROM active_jobs
        WHERE 1=1{_status_window_clause(status)}
        GROUP BY source_name
        ORDER BY count DESC
    """)
```

In `dashboard_companies()`, replace:

```python
    status = request.args.get("status", "all")
    region = _default_region()

    cursor.execute(f"""
        SELECT company, COUNT(*) as count
        FROM active_jobs
        WHERE company IS NOT NULL AND company != ''{_status_window_clause(status)}{_region_scope_clause(region)}
        GROUP BY company
        ORDER BY count DESC
        LIMIT 10
    """)
```

with:

```python
    status = request.args.get("status", "all")

    cursor.execute(f"""
        SELECT company, COUNT(*) as count
        FROM active_jobs
        WHERE company IS NOT NULL AND company != ''{_status_window_clause(status)}
        GROUP BY company
        ORDER BY count DESC
        LIMIT 10
    """)
```

- [ ] **Step 5: Delete `dashboard_location_diversity()` and add the two new routes**

In `web_viewer.py`, delete the entire `dashboard_location_diversity()` function (route decorator, cache decorator, docstring, body — the whole block from `@app.route("/api/dashboard/location-diversity")` through its closing `return jsonify(...)`).

In its place, add:

```python
@app.route("/api/dashboard/top-it-jobs")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_top_it_jobs():
    """
    Recent Pakistan-relevant IT jobs (or worldwide IT jobs when broadened)
    for the dashboard's Top IT Jobs widget - see
    docs/superpowers/specs/2026-07-17-dashboard-region-restructure-design.md.

    Governed by its own local `region` param (pk/all), independent of the
    removed dashboard-wide Region toggle - _default_region()/the jmi_region
    cookie are NOT consulted here, this is a page-session-only control.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    status = request.args.get("status", "all")
    region = request.args.get("region", "pk")
    country_clause = " AND country = 'Pakistan'" if region == "pk" else ""

    cursor.execute(f"""
        SELECT job_id, title, company, location, country, remote_type
        FROM active_jobs
        WHERE field_category_id LIKE 'it.%'{country_clause}{_status_window_clause(status)}
        ORDER BY COALESCE(posted_date, first_seen_at) DESC
        LIMIT 7
    """)
    jobs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(jobs)


@app.route("/api/dashboard/top-it-companies")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_top_it_companies():
    """
    Top companies hiring for IT roles (Pakistan-scoped by default,
    worldwide when broadened) for the dashboard's Top Hiring IT Companies
    widget. Same local `region` param pattern as dashboard_top_it_jobs().
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    status = request.args.get("status", "all")
    region = request.args.get("region", "pk")
    country_clause = " AND country = 'Pakistan'" if region == "pk" else ""

    cursor.execute(f"""
        SELECT company, COUNT(*) as count
        FROM active_jobs
        WHERE company IS NOT NULL AND company != '' AND field_category_id LIKE 'it.%'{country_clause}{_status_window_clause(status)}
        GROUP BY company
        ORDER BY count DESC
        LIMIT 10
    """)
    companies = [{"company": row["company"], "count": row["count"]} for row in cursor.fetchall()]
    conn.close()
    return jsonify(companies)
```

- [ ] **Step 6: Update `_PUBLIC_API_READS`**

In `web_viewer.py`, find:

```python
_PUBLIC_API_READS = {
    "/api/dashboard/kpis", "/api/dashboard/companies", "/api/dashboard/location-diversity",
```

Replace with:

```python
_PUBLIC_API_READS = {
    "/api/dashboard/kpis", "/api/dashboard/companies", "/api/dashboard/top-it-jobs", "/api/dashboard/top-it-companies",
```

(Leave the rest of the set and its trailing comment block unchanged.)

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `pytest tests/test_dashboard_top_it_widgets.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all 7 tests)

- [ ] **Step 8: Fix the 7 now-broken tests in `tests/test_region_scope_filter.py`**

These currently assert region-scoping behavior against `/api/dashboard/kpis`, which no longer applies region at all after Step 4. Replace them with equivalent assertions against `/jobs`, matching the exact style `test_jobs_list_defaults_to_pakistan_scope` (line 90) already uses.

Replace:

```python
def test_dashboard_kpis_total_jobs_defaults_to_pakistan_scope(region_client):
    r = region_client.get("/api/dashboard/kpis")
    assert r.status_code == 200
    data = r.get_json()
    assert data["total_jobs"] == 3  # 2 Pakistan + 1 Global; US and Germany excluded by default


def test_dashboard_kpis_total_jobs_region_all_is_unrestricted(region_client):
    r = region_client.get("/api/dashboard/kpis?region=all")
    assert r.status_code == 200
    data = r.get_json()
    assert data["total_jobs"] == 5  # every seeded job
```

with (deleted — these are now exact duplicates of `test_jobs_list_defaults_to_pakistan_scope` and `test_jobs_list_region_all_shows_every_country` immediately below them in the same file, which already cover this via `/jobs`; keeping both would test the same behavior against a route that no longer has it).

Replace:

```python
def test_status_defaults_to_all_not_active(region_client):
    """The Active/Historical status default flips from 'active' (last
    month only) to 'all' (everything) as of this plan - two restrictive
    defaults (region + age) compounded would show a first-time visitor too
    little. Seed one old Pakistan job (outside the last-month window) and
    confirm it's included by default without any ?status= override."""
    import sqlite3
    from datetime import datetime, timedelta
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (country, posted_date) VALUES ('Pakistan', ?)", (old_date,))
    conn.commit()
    conn.close()

    r = region_client.get("/api/dashboard/kpis")  # no ?status= at all
    assert r.get_json()["total_jobs"] == 4, "old Pakistan job must be included - status defaults to 'all', not 'active'"
```

with:

```python
def test_status_defaults_to_all_not_active(region_client):
    """The Active/Historical status default flips from 'active' (last
    month only) to 'all' (everything) - two restrictive defaults (region +
    age) compounded would show a first-time visitor too little. Seed one
    old Pakistan job (outside the last-month window) and confirm it's
    included by default without any ?status= override. Uses /jobs, not
    /api/dashboard/kpis - the dashboard KPIs route no longer applies
    region scoping at all as of the dashboard-region-restructure plan."""
    import sqlite3
    from datetime import datetime, timedelta
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (country, posted_date) VALUES ('Pakistan', ?)", (old_date,))
    conn.commit()
    conn.close()

    r = region_client.get("/jobs")  # no ?status= at all
    assert "(4)</span>" in r.get_data(as_text=True), "old Pakistan job must be included - status defaults to 'all', not 'active'"
```

Replace:

```python
def test_region_defaults_to_cookie_value_when_no_query_param(region_client):
    region_client.set_cookie("jmi_region", "all")
    r = region_client.get("/api/dashboard/kpis")
    assert r.get_json()["total_jobs"] == 5  # cookie says 'all', no ?region= override


def test_explicit_query_param_overrides_cookie(region_client):
    region_client.set_cookie("jmi_region", "all")
    r = region_client.get("/api/dashboard/kpis?region=pk")
    assert r.get_json()["total_jobs"] == 3  # explicit ?region=pk wins over the 'all' cookie


def test_default_is_pakistan_when_neither_cookie_nor_query_param_present(region_client):
    r = region_client.get("/api/dashboard/kpis")
    assert r.get_json()["total_jobs"] == 3
```

with:

```python
def test_region_defaults_to_cookie_value_when_no_query_param(region_client):
    region_client.set_cookie("jmi_region", "all")
    r = region_client.get("/jobs")
    assert "(5)</span>" in r.get_data(as_text=True)  # cookie says 'all', no ?region= override


def test_explicit_query_param_overrides_cookie(region_client):
    region_client.set_cookie("jmi_region", "all")
    r = region_client.get("/jobs?region=pk")
    assert "(3)</span>" in r.get_data(as_text=True)  # explicit ?region=pk wins over the 'all' cookie


def test_default_is_pakistan_when_neither_cookie_nor_query_param_present(region_client):
    r = region_client.get("/jobs")
    assert "(3)</span>" in r.get_data(as_text=True)
```

Replace:

```python
def test_region_and_status_filters_compose_correctly_when_both_explicit(region_client):
    """Seed one Pakistan job that's old (outside the active window) - with
    status=active EXPLICITLY requested, it must be excluded regardless of
    region, proving the two filters combine with AND, not one silently
    overriding the other. (Default behavior - status defaulting to 'all' -
    is covered separately above.)"""
    import sqlite3
    from datetime import datetime, timedelta
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (country, posted_date) VALUES ('Pakistan', ?)", (old_date,))
    conn.commit()
    conn.close()

    r = region_client.get("/api/dashboard/kpis?status=active")  # region=pk (default), status=active (explicit)
    assert r.get_json()["total_jobs"] == 3  # the old Pakistan job must NOT be included under status=active
```

with:

```python
def test_region_and_status_filters_compose_correctly_when_both_explicit(region_client):
    """Seed one Pakistan job that's old (outside the active window) - with
    status=active EXPLICITLY requested, it must be excluded regardless of
    region, proving the two filters combine with AND, not one silently
    overriding the other. Uses /jobs - see test_status_defaults_to_all_not_active
    above for why /api/dashboard/kpis is no longer the right vehicle for
    this."""
    import sqlite3
    from datetime import datetime, timedelta
    import src.storage.db as db
    conn = sqlite3.connect(db._SERVING_A_PATH)
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO jobs (country, posted_date) VALUES ('Pakistan', ?)", (old_date,))
    conn.commit()
    conn.close()

    r = region_client.get("/jobs?status=active")  # region=pk (default), status=active (explicit)
    assert "(3)</span>" in r.get_data(as_text=True)  # the old Pakistan job must NOT be included under status=active
```

- [ ] **Step 9: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before this task, minus the 2 deleted duplicate tests, plus the 7 new tests, one known pre-existing unrelated failure (`test_login_rejects_external_next_target`). If anything else fails, investigate before continuing — do not assume it's unrelated.

- [ ] **Step 10: Commit**

```bash
git add web_viewer.py tests/test_dashboard_top_it_widgets.py tests/test_region_scope_filter.py
git commit -m "feat: remove dashboard region-scoping, add Top IT Jobs / Top Hiring IT Companies routes"
```

---

### Task 2: Frontend — dashboard.html and dashboard.js

**Files:**
- Modify: `templates/dashboard.html` — remove the Region `<select>` from `.dash-controls`; remove the Total Jobs and Active Sources KPI cards; remove the Multi-Location Jobs widget block; add the shared local Region selector + Top IT Jobs widget + Top Hiring IT Companies widget.
- Modify: `static/js/dashboard.js` — `dashboardApi()` stops sending `region`; remove the `dashboardRegion` change listener; remove `loadLocationDiversity()` and its call in `loadDashboard()`; add `loadTopITJobs()` and `loadTopITCompanies()` plus their call sites and the new selector's change listener.

**Interfaces:**
- Consumes: `GET /api/dashboard/top-it-jobs`, `GET /api/dashboard/top-it-companies` from Task 1.

- [ ] **Step 1: Read the current exact markup before editing**

Read `templates/dashboard.html` in full to confirm exact current line numbers for `.dash-controls`, the KPI cards, and the Multi-Location widget block haven't drifted since this plan was written (Task 1 didn't touch this file, but confirm before editing).

- [ ] **Step 2: Remove the Region selector from `.dash-controls`**

In `templates/dashboard.html`, replace:

```html
        <label for="dashboardStatus" style="color:var(--text-secondary);font-size:12.5px;font-weight:600;">Listings</label>
        <select id="dashboardStatus" class="form-control">
            <option value="active">Active</option>
            <option value="all" selected>Active + historical</option>
            <option value="unverified">Historical / unverified</option>
            <option value="closed">Closed</option>
        </select>
        <label for="dashboardRegion" style="color:var(--text-secondary);font-size:12.5px;font-weight:600;">Region</label>
        <select id="dashboardRegion" class="form-control">
            <option value="pk">Pakistan</option>
            <option value="all">All Countries</option>
        </select>
        <button id="refreshBtn" class="icon-btn secondary">{{ icons.refresh(14) }} Refresh</button>
```

with:

```html
        <label for="dashboardStatus" style="color:var(--text-secondary);font-size:12.5px;font-weight:600;">Listings</label>
        <select id="dashboardStatus" class="form-control">
            <option value="active">Active</option>
            <option value="all" selected>Active + historical</option>
            <option value="unverified">Historical / unverified</option>
            <option value="closed">Closed</option>
        </select>
        <button id="refreshBtn" class="icon-btn secondary">{{ icons.refresh(14) }} Refresh</button>
```

- [ ] **Step 3: Remove the Total Jobs and Active Sources KPI cards**

Replace:

```html
<div class="kpi-grid">
    <div class="kpi-card">
        <div class="kpi-value" id="kpiJobs"><div class="loading-spinner"></div></div>
        <div class="kpi-label">Total jobs <span id="kpiJobsTrend" class="kpi-trend"></span></div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value" id="kpiSkills"><div class="loading-spinner"></div></div>
        <div class="kpi-label">Skills tracked <span id="kpiSkillsTrend" class="kpi-trend"></span></div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value" id="kpiSources"><div class="loading-spinner"></div></div>
        <div class="kpi-label">Active sources</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value" id="kpiRemote"><div class="loading-spinner"></div></div>
        <div class="kpi-label">Remote jobs</div>
    </div>
</div>
```

with:

```html
<div class="kpi-grid">
    <div class="kpi-card">
        <div class="kpi-value" id="kpiSkills"><div class="loading-spinner"></div></div>
        <div class="kpi-label">Skills tracked <span id="kpiSkillsTrend" class="kpi-trend"></span></div>
    </div>
    <div class="kpi-card">
        <div class="kpi-value" id="kpiRemote"><div class="loading-spinner"></div></div>
        <div class="kpi-label">Remote jobs</div>
    </div>
</div>
```

- [ ] **Step 4: Replace the Multi-Location Jobs widget with the shared selector + two new widgets**

Replace:

```html
    <div class="widget" style="grid-column: span 2;">
        <div class="widget-header">
            <h3 class="widget-title"><span class="icon-tile">{{ icons.globe(17) }}</span> Multi-location jobs</h3>
            <span class="widget-subtitle">Companies hiring across the most locations</span>
        </div>
        <table id="locationDiversityTable" class="data-table">
            <thead><tr><th>Rank</th><th>Company</th><th>Max Locations</th><th>Job Postings</th></tr></thead>
            <tbody><tr><td colspan="4" class="loading"><div class="loading-spinner"></div></td></tr></tbody>
        </table>
    </div>
</div>
```

with:

```html
    <div style="grid-column: span 2; display:flex; align-items:center; gap:8px; margin-top:0.5rem;">
        <label for="itRegionSelector" style="color:var(--text-secondary);font-size:12.5px;font-weight:600;">Region</label>
        <select id="itRegionSelector" class="form-control" style="width:auto;">
            <option value="pk">Pakistan</option>
            <option value="all">All Countries</option>
        </select>
    </div>

    <div class="widget">
        <div class="widget-header">
            <h3 class="widget-title"><span class="icon-tile">{{ icons.briefcase(17) }}</span> Top IT Jobs</h3>
            <span class="widget-subtitle">Most recent postings</span>
        </div>
        <div id="topItJobsList" class="loading"><div class="loading-spinner"></div></div>
        <div style="margin-top:0.75rem;text-align:right;">
            <a id="topItJobsSeeAll" href="/jobs?category=it&region=pk" style="font-size:0.85rem;font-weight:600;color:var(--accent);text-decoration:none;">See all IT jobs →</a>
        </div>
    </div>

    <div class="widget">
        <div class="widget-header">
            <h3 class="widget-title"><span class="icon-tile">{{ icons.building(17) }}</span> Top Hiring IT Companies</h3>
            <span class="widget-subtitle">Job count by company</span>
        </div>
        <table id="topItCompaniesTable" class="data-table">
            <thead><tr><th>Rank</th><th>Company</th><th>IT Job Count</th></tr></thead>
            <tbody><tr><td colspan="3" class="loading"><div class="loading-spinner"></div></td></tr></tbody>
        </table>
        <div style="margin-top:0.75rem;text-align:right;">
            <a id="topItCompaniesSeeAll" href="/companies/intelligence?category=it&region=pk" style="font-size:0.85rem;font-weight:600;color:var(--accent);text-decoration:none;">See all IT companies →</a>
        </div>
    </div>
</div>
```

- [ ] **Step 5: Update `dashboardApi()`, remove the old Region listener, remove `loadLocationDiversity()`**

In `static/js/dashboard.js`, replace:

```javascript
    document.getElementById('dashboardStatus').addEventListener('change', function() {
        loadDashboard();
    });
    document.getElementById('dashboardRegion').addEventListener('change', function() {
        document.cookie = `jmi_region=${this.value};path=/;max-age=31536000;SameSite=Lax`;
        loadDashboard();
    });
});

function dashboardApi(path) {
    const status = document.getElementById('dashboardStatus')?.value || 'all';
    const region = document.getElementById('dashboardRegion')?.value || 'pk';
    return `${path}?status=${encodeURIComponent(status)}&region=${encodeURIComponent(region)}`;
}

function loadDashboard() {
    updateTime();
    loadKPIs();
    loadTrendsChart();
    loadTopSkillsChart();
    loadGeoChart();
    loadSourcesChart();
    loadEmergingSkills();
    loadDecliningSkills();
    loadTopCompanies();
    loadLocationDiversity();
}
```

with:

```javascript
    document.getElementById('dashboardStatus').addEventListener('change', function() {
        loadDashboard();
    });
    document.getElementById('itRegionSelector').addEventListener('change', function() {
        loadTopITJobs();
        loadTopITCompanies();
    });
});

function dashboardApi(path) {
    const status = document.getElementById('dashboardStatus')?.value || 'all';
    return `${path}?status=${encodeURIComponent(status)}`;
}

// Same shape as dashboardApi(), but for the two IT widgets specifically -
// reads the local itRegionSelector instead of the (removed) page-level
// Region control. Page-session-only, no cookie.
function localItApi(path) {
    const status = document.getElementById('dashboardStatus')?.value || 'all';
    const region = document.getElementById('itRegionSelector')?.value || 'pk';
    return `${path}?status=${encodeURIComponent(status)}&region=${encodeURIComponent(region)}`;
}

function loadDashboard() {
    updateTime();
    loadKPIs();
    loadTrendsChart();
    loadTopSkillsChart();
    loadGeoChart();
    loadSourcesChart();
    loadEmergingSkills();
    loadDecliningSkills();
    loadTopCompanies();
    loadTopITJobs();
    loadTopITCompanies();
}
```

- [ ] **Step 6: Update `loadKPIs()` to stop writing to the removed KPI cards**

Replace:

```javascript
function loadKPIs() {
    fetch(dashboardApi('/api/dashboard/kpis'))
        .then(response => response.json())
        .then(data => {
            document.getElementById('kpiJobs').textContent = fmtKpi(data.total_jobs);
            setTrend(document.getElementById('kpiJobsTrend'), data.jobs_trend);

            document.getElementById('kpiSkills').textContent = fmtKpi(data.total_skills);
            setTrend(document.getElementById('kpiSkillsTrend'), data.skills_trend);
            
            document.getElementById('kpiSources').textContent = data.active_sources;
            document.getElementById('kpiRemote').textContent = data.remote_pct + '%';
        })
        .catch(error => {
            console.error('Error loading KPIs:', error);
        });
}
```

with:

```javascript
function loadKPIs() {
    fetch(dashboardApi('/api/dashboard/kpis'))
        .then(response => response.json())
        .then(data => {
            document.getElementById('kpiSkills').textContent = fmtKpi(data.total_skills);
            setTrend(document.getElementById('kpiSkillsTrend'), data.skills_trend);

            document.getElementById('kpiRemote').textContent = data.remote_pct + '%';
        })
        .catch(error => {
            console.error('Error loading KPIs:', error);
        });
}
```

- [ ] **Step 7: Replace `loadLocationDiversity()` with `loadTopITJobs()` and `loadTopITCompanies()`**

Replace:

```javascript
function loadLocationDiversity() {
    fetch(dashboardApi('/api/dashboard/location-diversity'))
        .then(response => response.json())
        .then(data => {
            const tbody = document.querySelector('#locationDiversityTable tbody');
            
            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 2rem;">No multi-location postings yet.</td></tr>';
                return;
            }
            
            const pinSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;"><path d="M12 22s7-6.2 7-12A7 7 0 0 0 5 10c0 5.8 7 12 7 12z"></path><circle cx="12" cy="10" r="2.5"></circle></svg>';

            const authed = window.GW_AUTHED;
            const html = data.map((item, index) => `
                <tr${authed ? '' : ' class="gw-row-gate" onclick="gwShowGate()"'}>
                    <td><strong>#${index + 1}</strong></td>
                    <td>${item.company}</td>
                    <td><span style="color: var(--accent); font-weight: 600;">${pinSvg} ${item.max_locations} locations</span></td>
                    <td>${item.job_count} posting${item.job_count > 1 ? 's' : ''}</td>
                </tr>
            `).join('');
            
            tbody.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading location diversity:', error);
            document.querySelector('#locationDiversityTable tbody').innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--danger); padding: 2rem;">Something went wrong loading this — try refreshing.</td></tr>';
        });
}
```

with:

```javascript
function loadTopITJobs() {
    const region = document.getElementById('itRegionSelector')?.value || 'pk';
    document.getElementById('topItJobsSeeAll').href = `/jobs?category=it&region=${encodeURIComponent(region)}`;

    fetch(localItApi('/api/dashboard/top-it-jobs'))
        .then(response => response.json())
        .then(data => {
            const container = document.getElementById('topItJobsList');

            if (!Array.isArray(data) || data.length === 0) {
                container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 2rem;">No IT jobs found for this scope right now.</p>';
                return;
            }

            const authed = window.GW_AUTHED;
            const html = data.map(job => `
                <div style="padding:0.75rem 0;border-bottom:1px solid var(--border-subtle);">
                    ${authed
                        ? `<a href="/jobs/${job.job_id}" style="color:var(--text-primary);text-decoration:none;font-weight:600;">${job.title}</a>`
                        : `<span class="gw-row-gate" onclick="gwShowGate()" style="color:var(--text-primary);font-weight:600;cursor:pointer;">${job.title}</span>`}
                    <div style="font-size:0.8rem;color:var(--text-secondary);margin-top:0.2rem;">${job.company}${job.location ? ' · ' + job.location : (job.country ? ' · ' + job.country : '')}</div>
                </div>
            `).join('');

            container.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading top IT jobs:', error);
            document.getElementById('topItJobsList').innerHTML = '<p style="color: var(--danger); text-align: center; padding: 2rem;">Something went wrong loading this — try refreshing.</p>';
        });
}

function loadTopITCompanies() {
    const region = document.getElementById('itRegionSelector')?.value || 'pk';
    document.getElementById('topItCompaniesSeeAll').href = `/companies/intelligence?category=it&region=${encodeURIComponent(region)}`;

    fetch(localItApi('/api/dashboard/top-it-companies'))
        .then(response => response.json())
        .then(data => {
            const tbody = document.querySelector('#topItCompaniesTable tbody');

            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-secondary); padding: 2rem;">Nothing here yet — check back soon.</td></tr>';
                return;
            }

            const html = data.map((company, index) => `
                <tr>
                    <td><strong>#${index + 1}</strong></td>
                    <td>${company.company}</td>
                    <td><strong>${company.count}</strong> jobs</td>
                </tr>
            `).join('');

            tbody.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading top IT companies:', error);
            document.querySelector('#topItCompaniesTable tbody').innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--danger); padding: 2rem;">Something went wrong loading this — try refreshing.</td></tr>';
        });
}
```

- [ ] **Step 8: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as after Task 1, one known pre-existing unrelated failure. Template/JS changes have no dedicated automated tests of their own beyond what Task 1 already covers at the route level — verified manually next.

- [ ] **Step 9: Manual browser verification**

Start the app locally (`python web_viewer.py`) and confirm in a browser:
- `/dashboard` shows no Region control at top, only "Listings".
- The KPI row shows exactly 2 cards (Skills tracked, Remote jobs), stretched to fill the row.
- Source Performance chart still renders (now showing true, un-region-scoped per-source counts — Adzuna/Arbeitnow/etc. should show their real large counts again, not the near-zero counts seen before this plan).
- Multi-Location Jobs table is gone.
- A "Region" selector (Pakistan/All Countries) appears above the new "Top IT Jobs" and "Top Hiring IT Companies" widgets, which sit directly adjacent to each other.
- Top IT Jobs shows up to 7 recent job cards; switching the local Region selector to "All Countries" changes the results and updates the "See all IT jobs →" link's href to include `region=all`.
- Top Hiring IT Companies shows a ranked company list; same selector-driven behavior and "See all" href update.
- Clicking "See all IT jobs →" navigates to `/jobs?category=it&region=...` (the query params are inert there for now — that's expected, confirmed out of scope).
- Confirm `/jobs`'s own Region toggle (in the filter sidebar) still works exactly as before — unaffected by anything in this plan.

- [ ] **Step 10: Commit**

```bash
git add templates/dashboard.html static/js/dashboard.js
git commit -m "feat: restructure dashboard - remove page-level Region toggle, add Top IT Jobs / Top Hiring IT Companies widgets"
```
