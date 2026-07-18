# Companies Intelligence Pakistan-First IT Sections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/companies/intelligence` defaults to two ranked sections (IT companies in Pakistan, then worldwide) instead of one flat all-categories grid, with a Category toggle back to today's exact original page — completing the dashboard's already-live "See all IT companies →" link.

**Architecture:** New `GET /api/companies/list-it` route (strict `field_category_id LIKE 'it.%'`, matching yesterday's dashboard IT widgets, not today's NULL-inclusive `/jobs` Category filter) returns both sections in one response. `templates/companies_intelligence.html`'s existing single-list rendering (`applyView()`/`renderGrid()`) is generalized to render into a named target element, called once for the flat All-Categories grid or twice for the two IT sections, with the same search/sort toolbar driving whichever mode is active.

**Tech Stack:** Flask, SQLite, vanilla JS (no new libraries).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every pytest invocation must use `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`.
- Any new test fixture that touches DB paths must patch ALL of `src.storage.db._SERVING_A_PATH`, `_SERVING_B_PATH`, `_BUFFER_DB_PATH`, `_OPERATIONAL_DB_PATH`, `_POINTER_PATH` together when it calls `db.run_migrations()` or otherwise exercises real connection resolution.
- Any hand-rolled `jobs` table test fixture needs a `field_category_id TEXT` column — a fixture missing it causes a hard SQL error the moment a route queries it, not just a wrong result (confirmed twice already this session).
- `companies_list_it()` uses **strict** `field_category_id LIKE 'it.%'` scoping, matching `dashboard_top_it_jobs()`/`dashboard_top_it_companies()`'s reasoning (a curated ranking, precision over recall) — **not** the NULL-inclusive `_category_scope_clause()` helper built today for `/jobs` (a large browse list, recall over precision). Do not reuse that helper here.
- The existing `GET /api/companies/list` route and `companies_list()` function are untouched by this plan — still backs "All Categories" mode exactly as today.
- This page is fully gated for anonymous visitors (`companies_intelligence` is not in `_PUBLIC_VIEWABLE_ENDPOINTS`) — any test hitting it needs a signed-in test-client session (`sess["user_id"] = 1`).
- After Task 2, a manual browser verification step is required — this session's established practice for UI changes. Do not consider Task 2 done on passing tests alone.

---

### Task 1: `GET /api/companies/list-it` backend route

**Files:**
- Modify: `web_viewer.py` (add `companies_list_it()` immediately after `companies_list()`, currently ending at line 1403)
- Test: `tests/test_companies_list_it.py` (new)

**Interfaces:**
- Produces: `GET /api/companies/list-it` — no query params. Returns `{"pakistan": [...], "global": [...]}`, each a list of `{company, job_count, skill_diversity, location_count, remote_pct}` objects, same shape as the existing `/api/companies/list`, but computed within `field_category_id LIKE 'it.%'` scope (strict, no NULL-inclusion) — `pakistan` additionally requires `country = 'Pakistan'`; `global` has no country restriction (still IT-scoped).

- [ ] **Step 1: Read the current exact code before editing**

Read `web_viewer.py` lines 1360-1405 to confirm `companies_intelligence()`/`companies_list()`'s exact current code and end line haven't drifted since this plan was written.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_companies_list_it.py`:

```python
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
    import pytest as _pytest

    # Standalone fixture variant (needs a different seed) - reusing the
    # module fixture's DB isn't possible mid-test, so this test builds its
    # own minimal client inline, following the exact same fixture recipe.
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_companies_list_it.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — `/api/companies/list-it` doesn't exist yet (404 on every request); `test_existing_list_endpoint_is_unaffected` passes already (proves the baseline).

- [ ] **Step 4: Add the route**

In `web_viewer.py`, immediately after `companies_list()` (ends at line 1403, right before `@app.route("/api/companies/<company>/details")`), add:

```python
@app.route("/api/companies/list-it")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def companies_list_it():
    """
    Two ranked IT-company lists (Pakistan, worldwide) for the Companies
    Intelligence page's new default mode - see
    docs/superpowers/specs/2026-07-18-companies-intelligence-it-first-design.md.
    Strict field_category_id LIKE 'it.%' throughout, matching the
    dashboard's Top Hiring IT Companies widget - a curated ranking, not
    a broad browse list, so precision over recall (no NULL-inclusion).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    def _fetch(country_clause):
        cursor.execute(f"""
            SELECT
                j.company,
                COUNT(DISTINCT j.job_id) as job_count,
                COUNT(DISTINCT s.normalized_skill) as skill_diversity,
                COUNT(DISTINCT j.country) as location_count,
                SUM(CASE WHEN LOWER(j.remote_type) = 'remote' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as remote_pct
            FROM active_jobs j
            LEFT JOIN skills s ON j.job_id = s.job_id
            WHERE j.company IS NOT NULL AND j.company != '' AND j.field_category_id LIKE 'it.%'{country_clause}
            GROUP BY j.company
            HAVING job_count >= 2
            ORDER BY job_count DESC
            LIMIT 100
        """)
        return [{"company": row["company"], "job_count": row["job_count"],
                 "skill_diversity": row["skill_diversity"], "location_count": row["location_count"],
                 "remote_pct": round(row["remote_pct"], 1)}
                for row in cursor.fetchall()]

    pakistan = _fetch(" AND j.country = 'Pakistan'")
    global_ = _fetch("")
    conn.close()
    return jsonify({"pakistan": pakistan, "global": global_})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_companies_list_it.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all 6 tests)

- [ ] **Step 6: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before plus the new tests, one known pre-existing unrelated failure (`test_login_rejects_external_next_target`). If anything else fails, investigate before continuing.

- [ ] **Step 7: Commit**

```bash
git add web_viewer.py tests/test_companies_list_it.py
git commit -m "feat: add /api/companies/list-it route for Pakistan/worldwide IT company rankings"
```

---

### Task 2: Category toggle + two-section rendering in `companies_intelligence.html`

**Files:**
- Modify: `templates/companies_intelligence.html` (toolbar, subtitle, two new grid sections, generalized rendering JS)

**Interfaces:**
- Consumes: `GET /api/companies/list-it` from Task 1.

- [ ] **Step 1: Read the current exact markup and JS before editing**

Read `templates/companies_intelligence.html` in full to confirm its exact current structure (the `.page-top-row`/subtitle block, `.co-toolbar`, `#coGrid`, and the full inline `<script>` block — `applyView()`, `renderGrid()`, `openCompanyPanel()`) hasn't drifted since this plan was written.

- [ ] **Step 2: Update the subtitle and toolbar markup**

Replace:

```html
<div class="page-top-row">
    <div>
        <div class="page-title">Companies</div>
        <div class="page-sub">Every employer we've aggregated, blended across all connected sources</div>
    </div>
</div>

<div class="co-toolbar">
    <div class="co-search">
        {{ icons.search(14) }}
        <input type="text" id="coSearchInput" placeholder="Search companies…">
    </div>
    <select id="coSortSelect" class="co-sort">
        <option value="jobs">Sort: Most jobs</option>
        <option value="skills">Sort: Most skills</option>
        <option value="remote">Sort: Most remote</option>
        <option value="countries">Sort: Most countries</option>
    </select>
</div>
```

with:

```html
<div class="page-top-row">
    <div>
        <div class="page-title">Companies</div>
        <div class="page-sub" id="coSubtitle">Companies hiring for IT roles, Pakistan first.</div>
    </div>
</div>

<div class="co-toolbar">
    <div class="co-search">
        {{ icons.search(14) }}
        <input type="text" id="coSearchInput" placeholder="Search companies…">
    </div>
    <select id="coCategorySelect" class="co-sort">
        <option value="it">IT Jobs</option>
        <option value="all">All Categories</option>
    </select>
    <select id="coSortSelect" class="co-sort">
        <option value="jobs">Sort: Most jobs</option>
        <option value="skills">Sort: Most skills</option>
        <option value="remote">Sort: Most remote</option>
        <option value="countries">Sort: Most countries</option>
    </select>
</div>
```

- [ ] **Step 3: Add the two IT-mode grid sections alongside the existing flat grid**

Replace:

```html
<div class="co-grid" id="coGrid">
    <div class="co-empty"><div class="loading-spinner"></div></div>
</div>
{{ gating.bar() }}
```

with:

```html
<div id="coItSections">
    <h3 class="card-title" style="margin-bottom:12px; font-size:15px;">IT Companies in Pakistan</h3>
    <div class="co-grid" id="coGridPakistan">
        <div class="co-empty"><div class="loading-spinner"></div></div>
    </div>
    <h3 class="card-title" style="margin: 1.5rem 0 12px; font-size:15px;">IT Companies Worldwide</h3>
    <div class="co-grid" id="coGridGlobal">
        <div class="co-empty"><div class="loading-spinner"></div></div>
    </div>
</div>
<div class="co-grid" id="coGrid" style="display:none;">
    <div class="co-empty"><div class="loading-spinner"></div></div>
</div>
{{ gating.bar() }}
```

- [ ] **Step 4: Generalize the loading/rendering JS for two-target support**

Replace:

```javascript
let allCompanies = [];
let currentSort = 'jobs';
let currentSearch = '';

function initials(name) {
    const words = name.replace(/[^\w\s]/g, '').split(/\s+/).filter(Boolean);
    if (words.length === 0) return '?';
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[1][0]).toUpperCase();
}

document.addEventListener('DOMContentLoaded', function () {
    fetch('/api/companies/list')
        .then(r => r.json())
        .then(data => {
            allCompanies = data;
            applyView();
        })
        .catch(() => {
            document.getElementById('coGrid').innerHTML =
                '<div class="co-empty">Something went wrong loading companies — try refreshing.</div>';
        });

    document.getElementById('coSearchInput').addEventListener('input', function (e) {
        currentSearch = e.target.value.trim().toLowerCase();
        applyView();
    });
    document.getElementById('coSortSelect').addEventListener('change', function (e) {
        currentSort = e.target.value;
        applyView();
    });
});

function applyView() {
    let list = allCompanies.filter(c => c.company.toLowerCase().includes(currentSearch));
    const sortKey = { jobs: 'job_count', skills: 'skill_diversity', remote: 'remote_pct', countries: 'location_count' }[currentSort];
    list = list.slice().sort((a, b) => (b[sortKey] || 0) - (a[sortKey] || 0));
    renderGrid(list);
}

function renderGrid(list) {
    const grid = document.getElementById('coGrid');
    if (list.length === 0) {
        grid.innerHTML = '<div class="co-empty">No companies match "' + currentSearch + '".</div>';
        return;
    }
    grid.innerHTML = list.map(c => `
        <div class="co-card" data-company="${encodeURIComponent(c.company)}" onclick="openCompanyPanel(this)">
            <div class="co-card-top">
                <div class="co-avatar">${initials(c.company)}</div>
                <div style="min-width:0;flex:1;">
                    <div class="co-name" title="${c.company}">${c.company}</div>
                </div>
            </div>
            <div class="co-meta-row">
                <span>{{ icons.briefcase(12) }} <strong style="color:var(--text-primary);">${c.job_count}</strong> jobs</span>
                <span>{{ icons.tag(12) }} <strong style="color:var(--text-primary);">${c.skill_diversity}</strong> skills</span>
                <span>{{ icons.globe(12) }} <strong style="color:var(--text-primary);">${c.location_count}</strong> countries</span>
                <span class="co-remote-stat"><strong>${c.remote_pct}%</strong> remote</span>
            </div>
        </div>
    `).join('');

    // keep the panel's selected-card highlight in sync after a re-render
    const openCompany = document.getElementById('coPanel').dataset.company;
    if (openCompany) {
        const match = grid.querySelector(`[data-company="${CSS.escape(openCompany)}"]`);
        if (match) match.classList.add('selected');
    }
}
```

with:

```javascript
let allCompanies = [];
let pakistanCompanies = [];
let globalCompanies = [];
let currentSort = 'jobs';
let currentSearch = '';
let currentCategory = 'it';

function initials(name) {
    const words = name.replace(/[^\w\s]/g, '').split(/\s+/).filter(Boolean);
    if (words.length === 0) return '?';
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[1][0]).toUpperCase();
}

function getInitialCategory() {
    const params = new URLSearchParams(window.location.search);
    return params.get('category') === 'all' ? 'all' : 'it';
}

document.addEventListener('DOMContentLoaded', function () {
    currentCategory = getInitialCategory();
    document.getElementById('coCategorySelect').value = currentCategory;
    applyCategoryMode();

    document.getElementById('coSearchInput').addEventListener('input', function (e) {
        currentSearch = e.target.value.trim().toLowerCase();
        applyView();
    });
    document.getElementById('coSortSelect').addEventListener('change', function (e) {
        currentSort = e.target.value;
        applyView();
    });
    document.getElementById('coCategorySelect').addEventListener('change', function (e) {
        currentCategory = e.target.value;
        applyCategoryMode();
    });
});

function applyCategoryMode() {
    const itSections = document.getElementById('coItSections');
    const allGrid = document.getElementById('coGrid');
    const subtitle = document.getElementById('coSubtitle');

    if (currentCategory === 'it') {
        itSections.style.display = '';
        allGrid.style.display = 'none';
        subtitle.textContent = 'Companies hiring for IT roles, Pakistan first.';
        if (pakistanCompanies.length === 0 && globalCompanies.length === 0) {
            fetch('/api/companies/list-it')
                .then(r => r.json())
                .then(data => {
                    pakistanCompanies = data.pakistan;
                    globalCompanies = data.global;
                    applyView();
                })
                .catch(() => {
                    document.getElementById('coGridPakistan').innerHTML =
                        '<div class="co-empty">Something went wrong loading companies — try refreshing.</div>';
                });
        } else {
            applyView();
        }
    } else {
        itSections.style.display = 'none';
        allGrid.style.display = '';
        subtitle.textContent = "Every employer we've aggregated, blended across all connected sources";
        if (allCompanies.length === 0) {
            fetch('/api/companies/list')
                .then(r => r.json())
                .then(data => {
                    allCompanies = data;
                    applyView();
                })
                .catch(() => {
                    document.getElementById('coGrid').innerHTML =
                        '<div class="co-empty">Something went wrong loading companies — try refreshing.</div>';
                });
        } else {
            applyView();
        }
    }
}

function filterAndSort(list) {
    let filtered = list.filter(c => c.company.toLowerCase().includes(currentSearch));
    const sortKey = { jobs: 'job_count', skills: 'skill_diversity', remote: 'remote_pct', countries: 'location_count' }[currentSort];
    return filtered.slice().sort((a, b) => (b[sortKey] || 0) - (a[sortKey] || 0));
}

function applyView() {
    if (currentCategory === 'it') {
        renderGrid(filterAndSort(pakistanCompanies), 'coGridPakistan');
        renderGrid(filterAndSort(globalCompanies), 'coGridGlobal');
    } else {
        renderGrid(filterAndSort(allCompanies), 'coGrid');
    }
}

function renderGrid(list, targetId) {
    const grid = document.getElementById(targetId);
    if (list.length === 0) {
        grid.innerHTML = '<div class="co-empty">No companies match "' + currentSearch + '".</div>';
        return;
    }
    grid.innerHTML = list.map(c => `
        <div class="co-card" data-company="${encodeURIComponent(c.company)}" onclick="openCompanyPanel(this)">
            <div class="co-card-top">
                <div class="co-avatar">${initials(c.company)}</div>
                <div style="min-width:0;flex:1;">
                    <div class="co-name" title="${c.company}">${c.company}</div>
                </div>
            </div>
            <div class="co-meta-row">
                <span>{{ icons.briefcase(12) }} <strong style="color:var(--text-primary);">${c.job_count}</strong> jobs</span>
                <span>{{ icons.tag(12) }} <strong style="color:var(--text-primary);">${c.skill_diversity}</strong> skills</span>
                <span>{{ icons.globe(12) }} <strong style="color:var(--text-primary);">${c.location_count}</strong> countries</span>
                <span class="co-remote-stat"><strong>${c.remote_pct}%</strong> remote</span>
            </div>
        </div>
    `).join('');

    // keep the panel's selected-card highlight in sync after a re-render
    const openCompany = document.getElementById('coPanel').dataset.company;
    if (openCompany) {
        const match = grid.querySelector(`[data-company="${CSS.escape(openCompany)}"]`);
        if (match) match.classList.add('selected');
    }
}
```

- [ ] **Step 5: Update `openCompanyPanel()` to look up data across the right source(s)**

Replace:

```javascript
function openCompanyPanel(cardEl) {
    document.querySelectorAll('.co-card').forEach(c => c.classList.remove('selected'));
    cardEl.classList.add('selected');

    const encoded = cardEl.dataset.company;
    const company = decodeURIComponent(encoded);
    const data = allCompanies.find(c => c.company === company);
    if (!data) return;
```

with:

```javascript
function openCompanyPanel(cardEl) {
    document.querySelectorAll('.co-card').forEach(c => c.classList.remove('selected'));
    cardEl.classList.add('selected');

    const encoded = cardEl.dataset.company;
    const company = decodeURIComponent(encoded);
    // A company can appear in both pakistanCompanies and globalCompanies
    // (global is a superset) - check Pakistan first since that's the
    // more specific/relevant match when both exist.
    const data = (currentCategory === 'it')
        ? (pakistanCompanies.find(c => c.company === company) || globalCompanies.find(c => c.company === company))
        : allCompanies.find(c => c.company === company);
    if (!data) return;
```

(The rest of `openCompanyPanel()` — stats grid, skills fetch, gating check — is unchanged; it already only reads from the local `data` variable this lookup produces, not from `allCompanies` directly.)

- [ ] **Step 6: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as after Task 1, one known pre-existing unrelated failure. No new automated tests here — this is a template-only change, verified manually next.

- [ ] **Step 7: Manual browser verification**

Start the app locally (`python web_viewer.py` — **restart it if already running from an earlier edit**, since Jinja caches compiled templates in-process, hit for real yesterday) and clear the local Flask response cache (`rm -f data/cache/flask/*`). Sign in (this page is fully gated), then confirm in a browser:
- `/companies/intelligence` defaults to "IT Jobs" selected, subtitle reads "Companies hiring for IT roles, Pakistan first.", and shows two headed sections — "IT Companies in Pakistan" and "IT Companies Worldwide" — each populated.
- A company appearing in both sections (e.g. a well-known Pakistani company with enough IT postings) is not confused between them — clicking either instance opens the same correct drill-down panel.
- Search box and sort dropdown filter/re-sort both sections simultaneously.
- Switching Category to "All Categories" hides both IT sections, shows the original flat grid, reverts the subtitle to "Every employer we've aggregated, blended across all connected sources", and the toolbar's search/sort still work against it.
- Switching back to "IT Jobs" restores the two-section view without a page reload (data was already fetched once, cached in the JS variables).
- The drill-down panel (click any card) still shows correct stats and top skills exactly as before this plan, regardless of which mode/section the card was clicked from.
- Visiting `/companies/intelligence?category=all` directly lands straight in All Categories mode (dashboard's future link target for that mode, and general shareability).
- The dashboard's existing "See all IT companies →" link (built yesterday) now lands in a fully working, correctly-scoped view instead of an inert one.

- [ ] **Step 8: Commit**

```bash
git add templates/companies_intelligence.html
git commit -m "feat: default Companies Intelligence to Pakistan-first IT sections with a Category toggle"
```
