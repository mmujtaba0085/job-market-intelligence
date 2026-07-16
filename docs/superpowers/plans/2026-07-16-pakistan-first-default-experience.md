# Pakistan-First Default Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/jobs` and the general-purpose dashboard widgets default to showing only `country IN ('Pakistan', 'Global')` jobs, with a sticky, visitor-facing "Region" toggle to broaden to every country. Also flips the existing Active/Historical status default from "Active" (last month only) to "Active + Historical" (everything) — with two restrictive filters, only one should narrow the default view, or a first-time visitor sees too little. The region filter is the one that matters for the mission; the age filter defaults open.

**Architecture:** A new `_region_scope_clause(region, alias)` helper in `web_viewer.py`, composed alongside the existing `_status_window_clause()` at each relevant route, following the exact same "AND-fragment or empty string" shape. A new `region` query param (default `pk`) drives it, with a `jmi_region` cookie (same write pattern as the existing `jmi_theme` cookie) making the visitor's choice persist across visits.

**Tech Stack:** Flask, SQLite, vanilla JS (no new dependencies).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every pytest invocation must use `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`.
- Any new test fixture that touches DB paths and calls `run_migrations()` or exercises real connection resolution must patch ALL of `src.storage.db._SERVING_A_PATH`, `_SERVING_B_PATH`, `_BUFFER_DB_PATH`, `_OPERATIONAL_DB_PATH`, `_POINTER_PATH` together — patching only `DB_PATH` is a known-broken pattern.
- `country IN ('Pakistan', 'Global')` is the exact, final scope definition (per the spec) — not `country = 'Pakistan'` alone, not including arbitrary `remote_type='remote'` jobs regardless of country.
- **`dashboard_geo()` (`/api/dashboard/geo`) is deliberately EXCLUDED from the region filter** — this is a considered deviation from the spec's literal "every general-purpose widget" language, made during planning after reading the actual route: `dashboard_geo()`'s entire purpose is showing the country breakdown of active jobs. Since the region filter restricts the same `country` column the widget groups by, applying it would make the widget degenerate (only ever showing "Pakistan" and/or "Remote / Global" as bars whenever region=pk, which is most of the time) instead of the genuinely useful "where are jobs located" view it provides today. The geo chart always shows the true, unrestricted country breakdown regardless of the Region toggle. **Confirmed correct directly by the owner** ("the main idea of showing this distribution was showing variety, which we will show") — not just this plan's own inference, an explicit requirement.
- **The Active/Historical `status` default changes from `"active"` to `"all"`** at every one of the same call sites Task 1 touches (`jobs_list()` and every dashboard route in scope) — explicit owner instruction, given alongside the region-scope request: with the new Pakistan-region default ALSO narrowing what's shown, the age/status filter should default open (everything) rather than compounding two restrictive defaults into an overly sparse first-visit experience. This is a real behavior change to the already-shipped Active/Historical feature, not new-to-this-plan scope creep — bundled into Task 1 because it touches the identical call sites already being edited for the region clause.

---

### Task 1: `_region_scope_clause()` helper + backend wiring

**Files:**
- Modify: `web_viewer.py:277` (add helper immediately after `_status_window_clause`, which ends around line 320 — confirm the exact end line yourself by reading the function, it returns `""` for the `all`/unrecognized case as its last line)
- Modify: `web_viewer.py` at each of these existing `_status_window_clause(...)` call sites — add a `_region_scope_clause(region, ...)` call composed alongside it, same alias argument, reading `region = request.args.get("region", "pk")` the same way `status` is already read in each function:
  - `jobs_list()` — line 1647 area (`base += _status_window_clause(current_status, "j.")`)
  - `dashboard_kpis()` — lines 682-712 (four separate queries: total_jobs, total_skills join, active_sources, remote_pct — all four need the region clause added, matching how all four already get the status clause)
  - `dashboard_top_skills()` — line 855 (the fallback-path query only, matching the existing status-filter boundary documented in that function's own docstring — the primary `weekly_metrics` path stays untouched, same reasoning as status)
  - `dashboard_sources()` — line 927
  - `dashboard_companies()` — line 1010
  - `dashboard_location_diversity()` — line 1037
- **Do NOT modify** `dashboard_geo()` (line 866 area) — see Global Constraints above.
- Test: `tests/test_region_scope_filter.py` (new)

**Interfaces:**
- Produces: `_region_scope_clause(region: str, alias: str = "") -> str` — returns `" AND {alias}country IN ('Pakistan', 'Global')"` when `region == "pk"`, else `""`. Same signature shape as `_status_window_clause`.

**Also in scope for this task:** change every `request.args.get("status", "active")` at these same 6 call sites (plus `jobs_list()`'s own `current_status = request.args.get("status", "active")` line) to `request.args.get("status", "all")` — the Active/Historical default flips from "Active" (last month) to "Active + Historical" (everything), per the owner's explicit instruction. `_status_window_clause()` itself is unchanged (still supports both values identically) — only the *default* passed to `request.args.get(...)` changes, sitewide, everywhere `status` is read.

- [ ] **Step 1: Read the current exact code before editing**

Read `web_viewer.py` lines 270-330 to see `_status_window_clause()` in full and confirm its exact end line, and re-confirm each of the 6 call-site line numbers listed above are still accurate (this file has been edited several times today — line numbers may have drifted by the time this task runs).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_region_scope_filter.py`:

```python
"""
tests/test_region_scope_filter.py
─────────────────────────────────────
Regression coverage for the Pakistan-first default region scope - see
docs/superpowers/specs/2026-07-16-pakistan-first-default-experience-design.md.

Follows the fixture pattern established in tests/test_dashboard_geo_endpoint.py:
a minimal hand-rolled sqlite schema monkeypatched onto every rotating-DB
target, a signed-in session where a route requires one.
"""
import sqlite3

import pytest


@pytest.fixture()
def region_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '', company TEXT DEFAULT '', country TEXT,
            source_name TEXT DEFAULT 'TestSource', remote_type TEXT DEFAULT 'unknown',
            listing_status TEXT DEFAULT 'active', posted_date TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            market_id TEXT DEFAULT 'm1', location_count INTEGER DEFAULT 1,
            normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE weekly_metrics (market_id TEXT, week_start_date TEXT, week_number INTEGER, skill_name TEXT, category TEXT, frequency INTEGER, growth_percentage REAL, absolute_delta INTEGER, mover_score REAL, emerging_flag INTEGER, declining_flag INTEGER)")
    conn.execute("CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

    with conn:
        for country in ("Pakistan", "Pakistan", "Global", "United States", "Germany"):
            conn.execute("INSERT INTO jobs (country) VALUES (?)", (country,))
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


def test_region_scope_clause_pk_restricts_to_pakistan_and_global():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("pk") == " AND country IN ('Pakistan', 'Global')"


def test_region_scope_clause_all_is_unrestricted():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("all") == ""


def test_region_scope_clause_unrecognized_value_is_unrestricted():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("bogus") == ""


def test_region_scope_clause_applies_alias_prefix():
    from web_viewer import _region_scope_clause
    assert _region_scope_clause("pk", "j.") == " AND j.country IN ('Pakistan', 'Global')"


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


def test_jobs_list_defaults_to_pakistan_scope(region_client):
    r = region_client.get("/jobs")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "(3)</span>" in body  # 2 Pakistan + 1 Global


def test_jobs_list_region_all_shows_every_country(region_client):
    r = region_client.get("/jobs?region=all")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "(5)</span>" in body


def test_dashboard_geo_is_not_affected_by_region_scope(region_client):
    """dashboard_geo() is deliberately excluded from the region filter - it
    must always show the true, unrestricted country breakdown, since
    restricting the same column it groups by would make the widget
    degenerate. Confirmed via the bucket-sum-equals-total invariant already
    established in test_dashboard_geo_endpoint.py - here, specifically,
    proving region=pk does NOT shrink the total the way it does for kpis."""
    r_default = region_client.get("/api/dashboard/geo")
    r_all = region_client.get("/api/dashboard/geo?region=all")
    total_default = sum(row["count"] for row in r_default.get_json())
    total_all = sum(row["count"] for row in r_all.get_json())
    assert total_default == 5, f"geo must show all 5 jobs regardless of region, got {total_default}"
    assert total_all == 5


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

Note on the last test: remove the unused `db_path = region_client.application.config.get(...)` line if it doesn't resolve cleanly during implementation — the intent is just "open the same sqlite file the fixture already pointed the app at and insert one more row directly," adjust the exact mechanics to whatever works cleanly against the fixture as actually written.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_region_scope_filter.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — `_region_scope_clause` doesn't exist yet, and no route reads `region` yet.

- [ ] **Step 3: Add the helper**

In `web_viewer.py`, immediately after `_status_window_clause()`'s closing (find the exact line — it's the function whose last line is `return ""  # 'all' (or any unrecognized value) → no filter`):

```python
def _region_scope_clause(region: str, alias: str = "") -> str:
    """
    SQL AND-clause fragment restricting to Pakistan-relevant jobs by
    default - see
    docs/superpowers/specs/2026-07-16-pakistan-first-default-experience-design.md.

    'pk' (the default): country IN ('Pakistan', 'Global') - jobs physically
    in Pakistan, plus jobs explicitly marked open to remote applicants
    anywhere (sources like Himalayas set country='Global' specifically for
    genuinely worldwide-open roles). A specific non-Pakistan country value
    on a remote job (e.g. country='United States') is deliberately NOT
    included - it's a signal the role is likely restricted to that
    country in practice, not genuinely open to a Pakistan-based applicant.
    'all' (or any unrecognized value): no restriction - every job,
    regardless of country.
    """
    if region == "pk":
        return f" AND {alias}country IN ('Pakistan', 'Global')"
    return ""
```

- [ ] **Step 4: Wire it into each call site — add the region param AND flip the status default**

For each of the 6 routes listed in this task's Files section (plus `jobs_list()`'s `current_status` line), change the existing `status = request.args.get("status", "active")` line's default from `"active"` to `"all"`, add a new `region = request.args.get("region", "pk")` line next to it, and append `_region_scope_clause(region, <same alias the status clause uses at that call site>)` immediately after each `_status_window_clause(...)` call in an f-string (concatenate the two clause fragments — order doesn't matter, both are `AND ...` fragments). Example for `dashboard_sources()`:

```python
@app.route("/api/dashboard/sources")
def dashboard_sources():
    """Get source performance breakdown."""
    conn = get_db_connection()
    cursor = conn.cursor()
    status = request.args.get("status", "all")
    region = request.args.get("region", "pk")

    cursor.execute(f"""
        SELECT source_name, COUNT(*) as count
        FROM active_jobs
        WHERE 1=1{_status_window_clause(status)}{_region_scope_clause(region)}
        GROUP BY source_name
        ORDER BY count DESC
    """)
```

Apply the same shape (flip the status default to `"all"`, read `region`, append `_region_scope_clause(region, alias)` right after each `_status_window_clause(...)` call) to `jobs_list()`, `dashboard_kpis()` (all four queries), `dashboard_top_skills()`'s fallback query, `dashboard_companies()`, and `dashboard_location_diversity()` — matching each site's existing alias usage exactly (some use `"j."`, some use `""`, per the Files section above).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_region_scope_filter.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before plus the new tests, with exactly one known pre-existing unrelated failure (`test_login_rejects_external_next_target` in `test_auth_security.py`) — if anything else fails, investigate before continuing.

- [ ] **Step 7: Commit**

```bash
git add web_viewer.py tests/test_region_scope_filter.py
git commit -m "feat: add Pakistan-first region scope filter to jobs list and dashboard"
```

---

### Task 2: Region toggle UI + sticky cookie

**Files:**
- Modify: `templates/dashboard.html` (add a "Region" dropdown next to the existing "Listings" dropdown)
- Modify: `templates/jobs_list.html` (add a "Region" filter next to the existing "Listing Status" filter)
- Modify: `static/js/dashboard.js` (`dashboardApi()` must also append `&region=...` from the new control, matching how it already appends `status`)
- Modify: `web_viewer.py` (`jobs_list()` and every dashboard route from Task 1 must read the region default from the `jmi_region` cookie when no explicit `?region=` query param is given, not just hardcode `"pk"`)
- Test: extend `tests/test_region_scope_filter.py` with cookie-default tests

**Interfaces:**
- Consumes: `_region_scope_clause(region, alias)` from Task 1.
- Produces: nothing new consumed by later tasks — this is the last task in this plan.

- [ ] **Step 1: Read the current exact markup before editing**

Read `templates/dashboard.html`'s existing `id="dashboardStatus"` select block in full (was around line 194-200 as of earlier today), and `templates/jobs_list.html`'s existing "Listing Status" filter-section block (was around line 111-118), to match styling/structure exactly. Read `static/js/dashboard.js`'s `dashboardApi(path)` function in full (was around line 29-32).

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_region_scope_filter.py`:

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

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_region_scope_filter.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — routes still hardcode `"pk"` regardless of the cookie.

- [ ] **Step 4: Add a shared default-resolution helper and use it everywhere Task 1 added `region = request.args.get("region", "pk")`**

In `web_viewer.py`, near `_region_scope_clause`:

```python
def _default_region() -> str:
    """Resolves the region default with query-param > cookie > hardcoded
    'pk' priority - an explicit ?region= always wins (so the toggle's own
    reload works), falling back to the visitor's remembered jmi_region
    cookie, falling back to Pakistan-first for a first-time visitor."""
    return request.args.get("region") or request.cookies.get("jmi_region", "pk")
```

Replace every `region = request.args.get("region", "pk")` line added in Task 1 with `region = _default_region()`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_region_scope_filter.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests, including the 3 new cookie ones)

- [ ] **Step 6a: Align the dashboard's existing "Listings" dropdown with the new status default**

The existing `id="dashboardStatus"` select in `templates/dashboard.html` is static HTML (no server-side `selected` logic) — the browser currently defaults to its first `<option>` (`value="active"`). Since the Python-side default changed to `"all"` in Task 1, move `selected` to that option so the visible dropdown matches what the page actually loads:

```html
        <select id="dashboardStatus" class="form-control">
            <option value="active">Active</option>
            <option value="all" selected>Active + historical</option>
            <option value="unverified">Historical / unverified</option>
            <option value="closed">Closed</option>
        </select>
```

Also update `dashboardApi()`'s defensive fallback in `static/js/dashboard.js` (Step 7 below) from `|| 'active'` to `|| 'all'`, so it matches even in the edge case where the select element somehow isn't found.

`templates/jobs_list.html`'s "Listing Status" dropdown needs no template change — it already renders `selected` dynamically from `current_status` (`{% if value == current_status %}selected{% endif %}`), so it automatically follows once `current_status`'s Python-side default changes in Task 1.

- [ ] **Step 6: Add the Region control to the dashboard**

In `templates/dashboard.html`, immediately after the existing `</select>` that closes the `id="dashboardStatus"` block, add:

```html
        <label for="dashboardRegion" style="color:var(--text-secondary);font-size:12.5px;font-weight:600;">Region</label>
        <select id="dashboardRegion" class="form-control">
            <option value="pk">Pakistan</option>
            <option value="all">All Countries</option>
        </select>
```

Then, in the same file's `<script>` block (or wherever the page sets the select's initial value from server-rendered state — follow whatever pattern `dashboardStatus` itself already uses, if any, for consistency), ensure `dashboardRegion`'s initial selected value reflects the resolved server-side default. If `dashboardStatus` has no server-side-rendered initial value today (just defaults to its first `<option>` via plain HTML), do the same here for consistency — the cookie read in Task 1/Step 4 already makes the *data* correct on first load regardless of what the dropdown visually shows before any JS runs; keep this simple rather than inventing new state-passing machinery.

- [ ] **Step 7: Wire the Region control into `dashboardApi()` and the change handler**

In `static/js/dashboard.js`, modify `dashboardApi`:

```javascript
function dashboardApi(path) {
    const status = document.getElementById('dashboardStatus')?.value || 'all';
    const region = document.getElementById('dashboardRegion')?.value || 'pk';
    return `${path}?status=${encodeURIComponent(status)}&region=${encodeURIComponent(region)}`;
}
```

In the same file's `DOMContentLoaded` handler, alongside the existing `dashboardStatus` change listener, add:

```javascript
    document.getElementById('dashboardRegion').addEventListener('change', function() {
        document.cookie = `jmi_region=${this.value};path=/;max-age=31536000;SameSite=Lax`;
        loadDashboard();
    });
```

- [ ] **Step 8: Add the Region control to `/jobs`**

In `templates/jobs_list.html`, immediately after the existing "Listing Status" `filter-section` div's closing `</div>`, add a parallel filter section:

```html
        <div class="filter-section">
            <label>Region</label>
            <select name="region" onchange="document.cookie='jmi_region='+this.value+';path=/;max-age=31536000;SameSite=Lax'; this.form.submit();">
                <option value="pk" {% if current_region == 'pk' %}selected{% endif %}>Pakistan</option>
                <option value="all" {% if current_region == 'all' %}selected{% endif %}>All Countries</option>
            </select>
        </div>
```

In `web_viewer.py::jobs_list()`, add `current_region = _default_region()` alongside the existing `current_status = request.args.get(...)` line, and pass `current_region=current_region` into the `render_template(...)` call's kwargs, matching how `current_status` is already passed.

- [ ] **Step 9: Manual verification (no automated test for template rendering itself — covered by Task 1/2's route-level tests)**

Run the app locally (`python web_viewer.py` or the project's usual dev-run command) and confirm in a browser: `/jobs` and `/dashboard` both show a "Region" control defaulting to "Pakistan"; switching to "All Countries" changes the job count/dashboard numbers and persists after a page reload (cookie set); switching back to "Pakistan" also persists. Confirm the geo chart's total does NOT change when toggling region (per this plan's Global Constraints — it's deliberately excluded). Also confirm the existing "Listings" / "Listing Status" dropdown now shows "Active + historical" selected by default on both pages (not "Active"), matching the new status default.

- [ ] **Step 10: Run the full suite one more time**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count plus the new tests, same one known pre-existing failure.

- [ ] **Step 11: Commit**

```bash
git add templates/dashboard.html templates/jobs_list.html static/js/dashboard.js web_viewer.py tests/test_region_scope_filter.py
git commit -m "feat: add sticky Region toggle (Pakistan / All Countries) to jobs list and dashboard"
```
