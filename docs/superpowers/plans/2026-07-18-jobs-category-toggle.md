# /jobs Category Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Category toggle (IT / All Categories) to `/jobs`, defaulting to IT with NULL-inclusive scoping — mirrors the already-shipped Region toggle exactly, as the second independent filter dimension Part 2 of the IT-priority-launch-readiness spec called for.

**Architecture:** New `_category_scope_clause()` and `_default_category()` helpers in `web_viewer.py`, same shape as `_region_scope_clause()`/`_default_region()`, wired into `jobs_list()` alongside the existing region/status clauses. New `jmi_category` cookie, same write pattern as `jmi_region`. New Category `<select>` in `jobs_list.html`'s filter sidebar, next to the existing Region control.

**Tech Stack:** Flask, SQLite, vanilla JS/Jinja (no new libraries).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every pytest invocation must use `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`.
- Any new test fixture that touches DB paths must patch ALL of `src.storage.db._SERVING_A_PATH`, `_SERVING_B_PATH`, `_BUFFER_DB_PATH`, `_OPERATIONAL_DB_PATH`, `_POINTER_PATH` together when it calls `db.run_migrations()` or otherwise exercises real connection resolution.
- Any hand-rolled `jobs` table test fixture needs a `field_category_id TEXT` column — a fixture missing it causes a hard SQL error once a route queries it, not just a wrong result (hit for real yesterday).
- The category clause is **NULL-inclusive**, not strict — this is deliberately different from the dashboard's `dashboard_top_it_jobs()`/`dashboard_top_it_companies()` routes (built yesterday, strict `field_category_id LIKE 'it.%'` only). Do not copy that version here. `category == "it"` returns `f" AND ({alias}field_category_id IS NULL OR {alias}field_category_id LIKE 'it.%')"` — shows everything except jobs *confidently* tagged as a real non-IT category.
- `category` is **not** reset by the anonymous-filters-ignored block in `jobs_list()` — same reasoning as `region`: this is a no-sign-in-required default, not a gated filter.
- After Task 2, a manual browser verification step is required — this session's established practice for UI changes. Do not consider Task 2 done on passing tests alone.

---

### Task 1: `_category_scope_clause()` + `_default_category()` + wiring into `jobs_list()`

**Files:**
- Modify: `web_viewer.py:354` (add both new helpers immediately after `_default_region()`, which currently ends at line 354)
- Modify: `web_viewer.py` — `jobs_list()` (currently starts at line 1652): read `category = _default_category()`, append `_category_scope_clause(category, "j.")` to the `base` query alongside the existing region/status clauses, pass `current_category=category` into the `render_template(...)` call, do not add `category` to the anonymous-reset block.
- Test: `tests/test_category_scope_filter.py` (new)

**Interfaces:**
- Produces: `_category_scope_clause(category: str, alias: str = "") -> str` — returns `f" AND ({alias}field_category_id IS NULL OR {alias}field_category_id LIKE 'it.%')"` when `category == "it"`, else `""`. Same signature shape as `_region_scope_clause`.
- Produces: `_default_category() -> str` — `request.args.get("category") or request.cookies.get("jmi_category", "it")`. Same shape as `_default_region`.

- [ ] **Step 1: Read the current exact code before editing**

Read `web_viewer.py` lines 320-360 (to confirm `_region_scope_clause`/`_default_region`'s exact current code and end line) and lines 1652-1720 (`jobs_list()`'s filter-reading and `base` query section) to confirm line numbers haven't drifted since this plan was written.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_category_scope_filter.py`:

```python
"""
tests/test_category_scope_filter.py
───────────────────────────────────────
Regression coverage for the /jobs Category toggle (IT/All Categories) -
see docs/superpowers/specs/2026-07-17-it-priority-launch-readiness-design.md
Part 2. Deliberately NULL-inclusive, unlike the dashboard's strict IT
widgets - see tests/test_dashboard_top_it_widgets.py for that contrast.
"""
import sqlite3

import pytest


@pytest.fixture()
def category_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT DEFAULT 'Pakistan',
            field_category_id TEXT,
            source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
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

    with conn:
        # IT-tagged (2)
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed IT Job 1', 'it.software')")
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed IT Job 2', 'it.data')")
        # Untagged/NULL (1) - must still show under category=it (NULL-inclusive)
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Unclassified Job', NULL)")
        # Confidently non-IT (1) - must be excluded under category=it even though it's not NULL-inclusion-excluded
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed Nurse Job', 'healthcare.clinical')")
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


def test_category_scope_clause_it_is_null_inclusive():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("it") == " AND (field_category_id IS NULL OR field_category_id LIKE 'it.%')"


def test_category_scope_clause_all_is_unrestricted():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("all") == ""


def test_category_scope_clause_unrecognized_value_is_unrestricted():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("bogus") == ""


def test_category_scope_clause_applies_alias_prefix():
    from web_viewer import _category_scope_clause
    assert _category_scope_clause("it", "j.") == " AND (j.field_category_id IS NULL OR j.field_category_id LIKE 'it.%')"


def test_jobs_list_defaults_to_it_scope(category_client):
    r = category_client.get("/jobs")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Confirmed IT Job 1" in body
    assert "Confirmed IT Job 2" in body
    assert "Confirmed Nurse Job" not in body


def test_jobs_list_it_scope_includes_unclassified_jobs(category_client):
    """The whole point of NULL-inclusion - a real job the classifier
    hasn't tagged yet must not be hidden by the IT default."""
    r = category_client.get("/jobs")
    assert "Unclassified Job" in r.get_data(as_text=True)


def test_jobs_list_category_all_shows_every_category(category_client):
    r = category_client.get("/jobs?category=all")
    body = r.get_data(as_text=True)
    assert "Confirmed IT Job 1" in body
    assert "Confirmed IT Job 2" in body
    assert "Unclassified Job" in body
    assert "Confirmed Nurse Job" in body


def test_category_defaults_to_cookie_value_when_no_query_param(category_client):
    category_client.set_cookie("jmi_category", "all")
    r = category_client.get("/jobs")
    assert "Confirmed Nurse Job" in r.get_data(as_text=True)  # cookie says 'all', no ?category= override


def test_category_explicit_query_param_overrides_cookie(category_client):
    category_client.set_cookie("jmi_category", "all")
    r = category_client.get("/jobs?category=it")
    assert "Confirmed Nurse Job" not in r.get_data(as_text=True)  # explicit ?category=it wins over the 'all' cookie


def test_category_not_reset_for_anonymous_visitor(tmp_path, monkeypatch):
    """Same no-sign-in-required treatment as Region - an anonymous visitor
    typing ?category=all directly must still see it honored, not silently
    reset like the signed-in-only filters (market/remote/search/etc)."""
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', country TEXT DEFAULT 'Pakistan',
            field_category_id TEXT,
            source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')), ingested_at TEXT,
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    with conn:
        conn.execute("INSERT INTO jobs (title, field_category_id) VALUES ('Confirmed Nurse Job', 'healthcare.clinical')")
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    client = web_viewer.app.test_client()  # no session - anonymous

    r = client.get("/jobs?category=all")
    assert "Confirmed Nurse Job" in r.get_data(as_text=True)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_category_scope_filter.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — `_category_scope_clause`/`_default_category` don't exist yet; the route-level tests fail with a "no such column: field_category_id" error or simply show every job regardless of category (since nothing filters yet).

- [ ] **Step 4: Add the two new helpers**

In `web_viewer.py`, immediately after `_default_region()` (ends at line 354), add:

```python
def _category_scope_clause(category: str, alias: str = "") -> str:
    """
    SQL AND-clause fragment restricting to IT-relevant jobs by default -
    see docs/superpowers/specs/2026-07-17-it-priority-launch-readiness-design.md
    Part 2.

    'it' (the default): field_category_id IS NULL OR field_category_id
    LIKE 'it.%' - shows everything except jobs *confidently* tagged as a
    real non-IT category. Deliberately NULL-inclusive rather than strict
    'it.%'-only: a strict filter would hide the majority of jobs by
    default, including real postings from IT-only companies the
    classifier simply hasn't tagged yet (confirmed against real coverage
    numbers during that spec's brainstorm). This differs from the
    dashboard's Top IT Jobs / Top Hiring IT Companies widgets
    (dashboard_top_it_jobs/dashboard_top_it_companies), which stay
    strict 'it.%'-only on purpose - those are small curated rankings
    where precision matters more than recall; this is a large,
    self-evaluated browse list where hiding untagged-but-real postings
    is the bigger cost.
    'all' (or any unrecognized value): no restriction - every job,
    regardless of category.
    """
    if category == "it":
        return f" AND ({alias}field_category_id IS NULL OR {alias}field_category_id LIKE 'it.%')"
    return ""


def _default_category() -> str:
    """
    Resolves the category default with query-param > cookie > hardcoded
    'it' priority - same shape as _default_region().
    """
    return request.args.get("category") or request.cookies.get("jmi_category", "it")
```

- [ ] **Step 5: Wire it into `jobs_list()`**

In `web_viewer.py`'s `jobs_list()`, replace:

```python
    current_status = request.args.get("status", "all")
    region         = _default_region()
    sort_param     = request.args.get("sort", "diverse")
```

with:

```python
    current_status = request.args.get("status", "all")
    region         = _default_region()
    category       = _default_category()
    sort_param     = request.args.get("sort", "diverse")
```

Replace:

```python
    # Status + active-window filter (see _status_window_clause)
    base += _status_window_clause(current_status, "j.")
    # Region scope filter (see _region_scope_clause)
    base += _region_scope_clause(region, "j.")
```

with:

```python
    # Status + active-window filter (see _status_window_clause)
    base += _status_window_clause(current_status, "j.")
    # Region scope filter (see _region_scope_clause)
    base += _region_scope_clause(region, "j.")
    # Category scope filter (see _category_scope_clause)
    base += _category_scope_clause(category, "j.")
```

Replace:

```python
        current_status=current_status,
        current_region=region,
        search_query=search_query,
```

with:

```python
        current_status=current_status,
        current_region=region,
        current_category=category,
        search_query=search_query,
```

Do **not** add `category` to the `if not g.current_user:` reset block — matches how `region` is deliberately excluded from it.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_category_scope_filter.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before plus the new tests, one known pre-existing unrelated failure (`test_login_rejects_external_next_target`). If anything else fails, investigate before continuing.

- [ ] **Step 8: Commit**

```bash
git add web_viewer.py tests/test_category_scope_filter.py
git commit -m "feat: add Category (IT/All Categories) scope filter to /jobs"
```

---

### Task 2: Category `<select>` UI + Active Filters badge

**Files:**
- Modify: `templates/jobs_list.html` — add the Category filter-section next to the existing Region one; add the Active Filters badge condition/badge line.

**Interfaces:**
- Consumes: `current_category` from Task 1's `render_template(...)` call.

- [ ] **Step 1: Read the current exact markup before editing**

Read `templates/jobs_list.html` lines 105-215 to confirm the Region filter-section and Active Filters block's exact current line numbers/markup haven't drifted since this plan was written.

- [ ] **Step 2: Add the Category filter-section**

In `templates/jobs_list.html`, replace:

```html
        <div class="filter-section">
            <label>Region</label>
            <select name="region" onchange="document.cookie='jmi_region='+this.value+';path=/;max-age=31536000;SameSite=Lax'; this.form.submit();">
                <option value="pk" {% if current_region == 'pk' %}selected{% endif %}>Pakistan</option>
                <option value="all" {% if current_region == 'all' %}selected{% endif %}>All Countries</option>
            </select>
        </div>
```

with:

```html
        <div class="filter-section">
            <label>Region</label>
            <select name="region" onchange="document.cookie='jmi_region='+this.value+';path=/;max-age=31536000;SameSite=Lax'; this.form.submit();">
                <option value="pk" {% if current_region == 'pk' %}selected{% endif %}>Pakistan</option>
                <option value="all" {% if current_region == 'all' %}selected{% endif %}>All Countries</option>
            </select>
        </div>

        <div class="filter-section">
            <label>Category</label>
            <select name="category" onchange="document.cookie='jmi_category='+this.value+';path=/;max-age=31536000;SameSite=Lax'; this.form.submit();">
                <option value="it" {% if current_category == 'it' %}selected{% endif %}>IT Jobs</option>
                <option value="all" {% if current_category == 'all' %}selected{% endif %}>All Categories</option>
            </select>
        </div>
```

- [ ] **Step 3: Add the Active Filters badge**

Replace:

```html
{% if search_query or current_market or current_remote or current_country or current_source or current_company or skills_filter or date_from or date_to or current_status != 'all' or current_region != 'pk' %}
```

with:

```html
{% if search_query or current_market or current_remote or current_country or current_source or current_company or skills_filter or date_from or date_to or current_status != 'all' or current_region != 'pk' or current_category != 'it' %}
```

Replace:

```html
    {% if current_region != 'pk' %}<span class="filter-badge">Region: All Countries</span>{% endif %}
```

with:

```html
    {% if current_region != 'pk' %}<span class="filter-badge">Region: All Countries</span>{% endif %}
    {% if current_category != 'it' %}<span class="filter-badge">Category: All Categories</span>{% endif %}
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as after Task 1, one known pre-existing unrelated failure. No new automated tests here — this is a template-only change, verified manually next.

- [ ] **Step 5: Manual browser verification**

Start the app locally (`python web_viewer.py`) — **restart it if it was already running from an earlier edit**, since Jinja caches compiled templates in-process and won't pick up template changes from a stale running process (hit for real yesterday) — and clear the local Flask response cache (`rm -f data/cache/flask/*`) before checking. Confirm in a browser:
- `/jobs`'s filter sidebar shows a "Category" control (IT Jobs / All Categories) next to "Region".
- Default page load has "IT Jobs" selected, and the job list only shows IT-scoped results.
- Switching to "All Categories" reloads the page, shows every category, and persists the choice on a subsequent page load (cookie set).
- Switching back to "IT Jobs" persists too.
- The "Active Filters" bar shows a "Category: All Categories" badge only when broadened, not by default.
- Confirm the dashboard's "Recent IT Jobs" and "Top Hiring IT Companies" widgets (shipped yesterday) are unaffected — they use their own local selector and strict scoping, untouched by this plan.

- [ ] **Step 6: Commit**

```bash
git add templates/jobs_list.html
git commit -m "feat: add Category toggle UI to /jobs filter sidebar"
```
