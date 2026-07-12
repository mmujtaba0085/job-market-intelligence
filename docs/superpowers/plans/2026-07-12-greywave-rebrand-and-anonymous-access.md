# GreyWave Rebrand + Anonymous Teaser Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the GreyWave rebrand across every page, make its "Continue with Google" anonymous-teaser gating actually functional (currently dead code, since nothing can reach these pages without a login), fix the two genuinely slow queries this newly-public traffic would otherwise hit, and cache the eight newly-public API endpoints.

**Architecture:** Copy the redesign package's templates/static assets in wholesale (verified safe — see Task 1). Loosen `web_viewer.py`'s existing auth gate for exactly six page endpoints and eight API paths, matched by endpoint name / literal path (not prefix, to avoid collision risk with routes like the admin `/jobs/quality` tool). Replace two expensive on-demand queries with small precomputed summary tables refreshed once per ingestion pipeline run. Reuse the already-built Flask-Caching layer for the eight newly-public API endpoints. Domain migration is a separate, explicitly-gated final task blocked on two manual prerequisites only the user can complete.

**Tech Stack:** Flask, Jinja2, SQLite, Flask-Caching (already installed), pytest.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-12-greywave-rebrand-and-anonymous-access-design.md` — read it for full context/reasoning behind every decision below.
- Redesign package source (already extracted, do not re-extract): `C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/warm-redesign-3/greywave_redesign/`
- Cache TTL for the 8 newly-cached API endpoints: exactly `900` seconds, same `key_prefix=_role_aware_cache_key` and `response_hit_indication=True` already used by the 8 page routes — this is the *same* decorator, not a new one.
- `_PUBLIC_VIEWABLE_ENDPOINTS` is matched against `request.endpoint` (the resolved Flask view-function name). `_PUBLIC_API_READS` is matched against `request.path` (a literal string set, same pattern as the existing `_PUBLIC_PATHS`). Do not use path-prefix matching for either — see Task 2 for why this matters.
- Do not touch `_SCOPE_MAP`, `/admin/*`, or any API-key scope-checking logic. The new public-access check only affects the branch that runs when there is no authenticated user at all (see Task 2's exact placement).
- Exact six endpoint names for `_PUBLIC_VIEWABLE_ENDPOINTS`: `dashboard`, `jobs_list`, `job_detail`, `skills_intelligence`, `companies_intelligence`, `titles_analytics`.
- Exact eight paths for `_PUBLIC_API_READS`: `/api/dashboard/kpis`, `/api/dashboard/companies`, `/api/dashboard/location-diversity`, `/api/skills/search`, `/api/skills/combinations`, `/api/companies/list`, `/api/titles/top`, `/api/filters/skills`.
- Task 6 (domain migration) must not be executed without explicit, separate user confirmation — it touches live DNS/Caddy config on a shared VPS serving other unrelated projects, and depends on two manual prerequisites (DNS A record, Google OAuth Console redirect URI) not yet completed as of this plan.

---

### Task 1: Rebrand rollout

**Files:**
- Create/Replace: `templates/base.html`
- Create: `templates/_brand.html`, `templates/_icons.html`, `templates/_gating.html`
- Replace: `templates/api_docs.html`, `templates/auth/change_password.html`, `templates/auth/login.html`, `templates/auth/my_keys.html`, `templates/companies_intelligence.html`, `templates/dashboard.html`, `templates/index.html`, `templates/job_detail.html`, `templates/jobs_list.html`, `templates/metrics.html`, `templates/skills.html`, `templates/skills_intelligence.html`, `templates/titles_analytics.html`
- Replace: `static/css/filters.css`, `static/js/dashboard.js`
- Create: `static/favicon.svg`
- Do NOT copy: `static/js/filters.js` (confirmed byte-identical to what's already live — copying it would be a no-op)

**Interfaces:** None — this task is pure template/static file replacement. It doesn't depend on any other task and no other task depends on it (Tasks 2-5 are all backend-only `web_viewer.py`/`src/` changes). Task 2's backend gate loosening is what makes this task's `{% if not g.current_user %}` branches reachable — until Task 2 ships, this task's gating UI renders identically to today for every visitor (since every visitor is currently forced to be logged in, `g.current_user` is always truthy, so the "anonymous" branches never trigger) — that's expected and correct; this task alone is not supposed to change any *visible* behavior for currently-logged-in users, only the colors/branding/icons.

- [ ] **Step 1: Copy every file**

Run from the repo root (`D:\vs code\Job Market Intelligence`):

```bash
SRC="C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/warm-redesign-3/greywave_redesign"

cp "$SRC/templates/base.html" templates/base.html
cp "$SRC/templates/_brand.html" templates/_brand.html
cp "$SRC/templates/_icons.html" templates/_icons.html
cp "$SRC/templates/_gating.html" templates/_gating.html
cp "$SRC/templates/api_docs.html" templates/api_docs.html
cp "$SRC/templates/auth/change_password.html" templates/auth/change_password.html
cp "$SRC/templates/auth/login.html" templates/auth/login.html
cp "$SRC/templates/auth/my_keys.html" templates/auth/my_keys.html
cp "$SRC/templates/companies_intelligence.html" templates/companies_intelligence.html
cp "$SRC/templates/dashboard.html" templates/dashboard.html
cp "$SRC/templates/index.html" templates/index.html
cp "$SRC/templates/job_detail.html" templates/job_detail.html
cp "$SRC/templates/jobs_list.html" templates/jobs_list.html
cp "$SRC/templates/metrics.html" templates/metrics.html
cp "$SRC/templates/skills.html" templates/skills.html
cp "$SRC/templates/skills_intelligence.html" templates/skills_intelligence.html
cp "$SRC/templates/titles_analytics.html" templates/titles_analytics.html
cp "$SRC/static/css/filters.css" static/css/filters.css
cp "$SRC/static/js/dashboard.js" static/js/dashboard.js
cp "$SRC/static/favicon.svg" static/favicon.svg
```

- [ ] **Step 2: Verify the app still starts and every copied template renders without a Jinja error**

Run (from repo root, in the venv):
```bash
"./.venv/Scripts/python.exe" -c "
import web_viewer
client = web_viewer.app.test_client()
with client.session_transaction() as sess:
    sess['user_id'] = 1
for path in ['/dashboard', '/jobs', '/skills', '/skills/intelligence', '/companies/intelligence', '/titles/analytics', '/metrics', '/api/docs', '/auth/me/keys']:
    r = client.get(path)
    print(path, r.status_code)
    assert r.status_code in (200, 302), f'{path} returned {r.status_code}'
print('OK - all pages render without a template error')
"
```
Expected: every path prints `200` (or `302` if it redirects somewhere reasonable — none of these should 302 given the session is authenticated), ending with `OK - all pages render without a template error`. A Jinja `TemplateNotFound` or `UndefinedError` here means a copied file references something (a macro import, a route variable) that doesn't line up — stop and investigate rather than proceeding.

Note: `/auth/login` is deliberately not in this list — it needs no session and is checked separately in Step 3.

- [ ] **Step 3: Verify the standalone login page still renders (it doesn't extend base.html)**

Run:
```bash
"./.venv/Scripts/python.exe" -c "
import web_viewer
client = web_viewer.app.test_client()
r = client.get('/auth/login')
print(r.status_code)
assert r.status_code == 200
body = r.get_data(as_text=True)
assert 'GreyWave' in body, 'expected GreyWave branding on the login page'
print('OK')
"
```
Expected: `200` then `OK`.

- [ ] **Step 4: Manual visual check**

Start the dev server (`"./.venv/Scripts/python.exe" web_viewer.py`), log in locally, and open `/dashboard` and `/jobs` in a browser. Confirm: the header shows the GreyWave wordmark (not the old name/emoji), the color palette matches the redesign (warm-neutral background, forest-green accent — not the old cream/tan), the browser tab shows the new favicon. This step has no automated assertion — it's a human visual sanity check before committing, since Steps 2-3 only prove the templates render without *erroring*, not that they render *correctly*.

- [ ] **Step 5: Run the full test suite**

Run: `"./.venv/Scripts/python.exe" -m pytest tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: same baseline as before this change (154 passed, 1 pre-existing unrelated failure in `test_auth_security.py::test_login_rejects_external_next_target`) — this task touches no Python logic, so no test count change is expected.

- [ ] **Step 6: Commit**

```bash
git add templates/ static/css/filters.css static/js/dashboard.js static/favicon.svg
git commit -m "feat: apply GreyWave rebrand (templates, icons, base.html, favicon)"
```

---

### Task 2: Backend access control for six pages

**Files:**
- Modify: `web_viewer.py:106-160` (the `_PUBLIC_PATHS`/`_PUBLIC_PREFIXES` declarations and `global_auth_gate()`)
- Test: `tests/test_public_viewable_routes.py` (new file)

**Interfaces:**
- Produces: `_PUBLIC_VIEWABLE_ENDPOINTS` (a `set[str]` of Flask endpoint names, module-level in `web_viewer.py`) and `_PUBLIC_API_READS` (a `set[str]` of literal path strings, module-level in `web_viewer.py`). Task 4 reads `_PUBLIC_API_READS` to know which 8 routes to add caching to (same list, don't redefine it).

**Why the placement inside `global_auth_gate()` matters — read before writing code:** it is tempting to add the new public-viewable check to the *same* early-return line that handles `_PUBLIC_PATHS` (line 130: `if path in _PUBLIC_PATHS or ...: return`). **Do not do this.** That line bypasses every check in the function, including the API-key scope enforcement further down (lines 156-160). If a request carries a valid API key with insufficient scope (e.g., a `jobs:read`-only key hitting `/api/skills/combinations`, which requires `analytics:read`), it must still be rejected — that's existing, unrelated behavior this task must not weaken. The correct placement is *inside* the `if not user:` branch, which only ever runs for genuinely anonymous requests (no session, no API key at all) — an API-key-authenticated request always has `user` populated (or gets rejected earlier for a bad/missing key), so it never reaches this branch and the scope checks below still apply to it exactly as before.

- [ ] **Step 1: Write the failing test**

Create `tests/test_public_viewable_routes.py`:
```python
"""
tests/test_public_viewable_routes.py
─────────────────────────────────────
Verifies web_viewer.py's global_auth_gate() correctly distinguishes three
cases for the newly-public routes:
  1. An anonymous request (no session, no API key) reaches the six page
     routes and eight API routes without being redirected to /auth/login.
  2. Every other route is still fully gated for anonymous requests (the
     public-viewable change must not leak beyond the named routes).
  3. API-key scope enforcement is NOT bypassed by this change - an API
     key without the required scope still gets 403 on a newly-public API
     path, proving the new check didn't accidentally short-circuit the
     scope-check logic further down in global_auth_gate().
"""
import sqlite3

import pytest


@pytest.fixture()
def anon_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
            remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
            source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
            listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER,
            job_group_id INTEGER
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE skills (job_id INTEGER, raw_detected_skill TEXT, normalized_skill TEXT, category TEXT, confidence_score REAL);
        CREATE TABLE skill_combinations_summary (skill_a TEXT, skill_b TEXT, co_count INTEGER);
        CREATE TABLE top_titles_summary (title TEXT, count INTEGER);
        CREATE TABLE weekly_metrics (week_start_date TEXT, skill_name TEXT, category TEXT, frequency INTEGER, growth_percentage REAL, mover_score REAL, emerging_flag INTEGER);
    """)
    # job_detail() needs a real row to return 200 rather than 404 - job_group_id
    # is left NULL so the view function's job_locations lookup (only reached
    # when job_group_id is truthy) is never exercised by this fixture.
    # This exact schema (including weekly_metrics, needed by dashboard_kpis'
    # week-over-week trend comparison) was empirically verified against all
    # 6 page routes and all 8 API routes together before being written here.
    conn.execute(
        "INSERT INTO jobs (job_id, title, company, listing_status) VALUES (1, 'Test Job', 'Test Co', 'active')"
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    return web_viewer.app.test_client()


def test_anonymous_request_reaches_public_page_routes(anon_client):
    for path in ["/dashboard", "/jobs", "/jobs/1", "/skills/intelligence", "/companies/intelligence", "/titles/analytics"]:
        r = anon_client.get(path)
        assert r.status_code == 200, f"{path} should be reachable anonymously, got {r.status_code}"


def test_anonymous_request_reaches_public_api_routes(anon_client):
    for path in [
        "/api/dashboard/kpis", "/api/dashboard/companies", "/api/dashboard/location-diversity",
        "/api/skills/search", "/api/skills/combinations", "/api/companies/list",
        "/api/titles/top", "/api/filters/skills",
    ]:
        r = anon_client.get(path)
        assert r.status_code == 200, f"{path} should be reachable anonymously, got {r.status_code}"


def test_anonymous_request_still_blocked_from_non_public_routes(anon_client):
    r = anon_client.get("/metrics")
    assert r.status_code == 302
    assert "/auth/login" in r.headers["Location"]

    r = anon_client.get("/api/dashboard/trends")
    assert r.status_code == 401


def test_anonymous_request_still_blocked_from_admin_routes(anon_client):
    r = anon_client.get("/admin/auth/users")
    assert r.status_code in (302, 403)


def test_api_key_scope_enforcement_not_bypassed_on_public_api_route(anon_client, monkeypatch):
    import web_viewer
    from src.auth import middleware as auth_middleware

    fake_user = {"id": 99, "role": "viewer", "active": 1}
    monkeypatch.setattr(
        auth_middleware, "_load_user_from_request",
        lambda: (fake_user, "api_key", 1),
    )
    monkeypatch.setattr(auth_middleware, "api_key_has_scope", lambda user, scope: False)

    r = anon_client.get("/api/skills/combinations", headers={"X-API-Key": "jmi_fake"})
    assert r.status_code == 403, "an API key lacking the required scope must still be rejected on a public-viewable API path"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_public_viewable_routes.py -v`
Expected: `test_anonymous_request_reaches_public_page_routes` and `test_anonymous_request_reaches_public_api_routes` FAIL (currently redirect/401 for anonymous requests). The other three tests should already PASS (they describe today's correct behavior, which this task must not change).

- [ ] **Step 3: Update `web_viewer.py`**

Find (`web_viewer.py:106-107`):
```python
_PUBLIC_PATHS = {"/healthz", "/auth/login", "/auth/logout", "/auth/google", "/auth/google/callback"}
_PUBLIC_PREFIXES = ("/static/",)
```

Replace with:
```python
_PUBLIC_PATHS = {"/healthz", "/auth/login", "/auth/logout", "/auth/google", "/auth/google/callback"}
_PUBLIC_PREFIXES = ("/static/",)

# Reachable without a login, but NOT a full bypass like _PUBLIC_PATHS above -
# g.current_user still populates normally from an existing session/API key,
# and (critically) API-key scope enforcement further down in
# global_auth_gate() still applies. See that function for exactly where
# this is consulted and why the placement matters.
_PUBLIC_VIEWABLE_ENDPOINTS = {
    "dashboard", "jobs_list", "job_detail",
    "skills_intelligence", "companies_intelligence", "titles_analytics",
}
_PUBLIC_API_READS = {
    "/api/dashboard/kpis", "/api/dashboard/companies", "/api/dashboard/location-diversity",
    "/api/skills/search", "/api/skills/combinations",
    "/api/companies/list", "/api/titles/top", "/api/filters/skills",
}
```

Find (`web_viewer.py:125-142`):
```python
@app.before_request
def global_auth_gate():
    from flask import redirect, url_for
    from src.auth.middleware import _is_api_request, api_key_has_scope
    path = request.path
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return

    user = getattr(g, "current_user", None)
    auth_type = getattr(g, "auth_type", None)

    if not user:
        if auth_type == "api_key_rate_limited":
            return jsonify({"error": "Rate limit exceeded"}), 429
        if _is_api_request():
            return jsonify({"error": "Unauthorized",
                            "hint": "Provide X-API-Key or Authorization: Bearer header"}), 401
        return redirect(url_for("auth.login", next=request.url))
```

Replace with:
```python
@app.before_request
def global_auth_gate():
    from flask import redirect, url_for
    from src.auth.middleware import _is_api_request, api_key_has_scope
    path = request.path
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return

    user = getattr(g, "current_user", None)
    auth_type = getattr(g, "auth_type", None)

    is_public_viewable = (
        request.endpoint in _PUBLIC_VIEWABLE_ENDPOINTS or path in _PUBLIC_API_READS
    )

    if not user:
        if auth_type == "api_key_rate_limited":
            return jsonify({"error": "Rate limit exceeded"}), 429
        if is_public_viewable:
            return  # anonymous visitor: g.current_user stays None, template/endpoint decides what to show
        if _is_api_request():
            return jsonify({"error": "Unauthorized",
                            "hint": "Provide X-API-Key or Authorization: Bearer header"}), 401
        return redirect(url_for("auth.login", next=request.url))
```

(The rest of `global_auth_gate()` — the admin-prefix check, the mutation-method check, the API-key scope check — is unchanged. It's only reached when `user` is truthy, i.e. never for the anonymous case this task adds, and always for the API-key case exactly as before.)

- [ ] **Step 4: Run the test again to verify it passes**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_public_viewable_routes.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the full test suite**

Run: `"./.venv/Scripts/python.exe" -m pytest tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: `159 passed` (154 existing + 5 new), same 1 pre-existing unrelated failure.

- [ ] **Step 6: Commit**

```bash
git add web_viewer.py tests/test_public_viewable_routes.py
git commit -m "feat: allow anonymous access to six pages + eight API reads for GreyWave teaser gating"
```

---

### Task 3: Precomputed analytics summaries

**Files:**
- Create: `src/analytics/precomputed_summaries.py`
- Modify: `src/storage/db.py` (new migration, after line 304)
- Modify: `src/orchestrator.py:614-618` (hook the two new recompute calls in)
- Modify: `web_viewer.py` (remove `_role_family`/regexes at lines 1208-1220, add an import, rewrite `skill_combinations()` at lines 1041-1061 and `titles_top()` at lines 1223-1252)
- Test: `tests/test_precomputed_summaries.py` (new file)

**Interfaces:**
- Produces: `recompute_skill_combinations(limit: int = 50) -> int` and `recompute_top_titles(limit: int = 30) -> int`, both in `src/analytics/precomputed_summaries.py`, both taking no required arguments and returning the number of rows written. Also produces `_role_family(title: str) -> str` and its two regex constants (`_SENIORITY_PREFIX_RE`, `_SENIORITY_SUFFIX_RE`) in the same module — `web_viewer.py`'s `title_skills()` (line 1267, unchanged by this task otherwise) imports `_role_family` from here instead of using its own module-level copy.
- Consumes: `get_connection` from `src.storage.db` (existing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_precomputed_summaries.py`:
```python
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
```

- [ ] **Step 2: Run to confirm it fails**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_precomputed_summaries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.analytics.precomputed_summaries'`.

- [ ] **Step 3: Create `src/analytics/precomputed_summaries.py`**

```python
"""
src/analytics/precomputed_summaries.py
────────────────────────────────────────
Precomputed analytics for two endpoints that were too expensive to
compute on every request once they became reachable by anonymous
traffic (see docs/superpowers/specs/2026-07-12-greywave-rebrand-and-anonymous-access-design.md
section 3 for the full empirical investigation).

/api/skills/combinations (top skill co-occurrence pairs): a self-join
across the full `skills` table took ~2.4-3 seconds against real
production data (260K+ rows). Reducing the LIMIT does NOT help - the
join and GROUP BY must fully complete before ORDER BY/LIMIT can even be
applied, since co_count is an aggregate that doesn't exist until then.
Only 192 distinct skills exist, so only ~13,500 pairs ever actually
co-occur - a small, stable output despite the large, growing input.
Precomputing once and reading from a small table is ~2,500-3,500x
faster than any on-the-fly query variant tested (including a covering
index, which only got ~29% faster - still far too slow for a live
request).

/api/titles/top (top job titles grouped by seniority-agnostic role
family): the previous implementation pulled all 73,734 distinct
normalized_title rows into Python and aggregated them via a role_family()
regex transform in a loop, taking ~2.3 seconds total. Titles don't
compress into a small vocabulary the way skills do (71,043 distinct role
families, barely fewer than the raw title count) - but this endpoint
only ever returns the top 30, so the summary table only needs to store
30 rows regardless of how many distinct families exist underneath.

Both are recomputed once per ingestion pipeline run (src/orchestrator.py,
alongside the existing diversity_rank recompute), not per-request.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from src.storage.db import get_connection

logger = logging.getLogger(__name__)

_SENIORITY_PREFIX_RE = re.compile(
    r'^(?:Senior|Junior|Jr\.?|Sr\.?|Associate|Mid[\s-]Level|Entry[\s-]Level)\s+',
    re.IGNORECASE,
)
_SENIORITY_SUFFIX_RE = re.compile(
    r'\s+(?:Intern|Internship)\s*$',
    re.IGNORECASE,
)


def _role_family(title: str) -> str:
    t = _SENIORITY_PREFIX_RE.sub('', title).strip()
    t = _SENIORITY_SUFFIX_RE.sub('', t).strip()
    return t


def recompute_skill_combinations(limit: int = 50) -> int:
    """Recompute the top N skill co-occurrence pairs into
    skill_combinations_summary. Safe to call repeatedly (full replace)."""
    conn = get_connection()
    try:
        return _recompute_skill_combinations(conn, limit=limit)
    finally:
        conn.close()


def _recompute_skill_combinations(conn: sqlite3.Connection, limit: int) -> int:
    conn.execute("DELETE FROM skill_combinations_summary")
    conn.execute("""
        INSERT INTO skill_combinations_summary (skill_a, skill_b, co_count)
        SELECT s1.normalized_skill, s2.normalized_skill, COUNT(*)
        FROM skills s1
        JOIN skills s2 ON s1.job_id = s2.job_id
        WHERE s1.normalized_skill < s2.normalized_skill
        GROUP BY s1.normalized_skill, s2.normalized_skill
        ORDER BY COUNT(*) DESC
        LIMIT ?
    """, (limit,))
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM skill_combinations_summary").fetchone()[0]
    logger.info("[precomputed_summaries] skill_combinations_summary: %d pairs", count)
    return count


def recompute_top_titles(limit: int = 30) -> int:
    """Recompute the top N role families into top_titles_summary. Safe to
    call repeatedly (full replace)."""
    conn = get_connection()
    try:
        return _recompute_top_titles(conn, limit=limit)
    finally:
        conn.close()


def _recompute_top_titles(conn: sqlite3.Connection, limit: int) -> int:
    conn.create_function("role_family", 1, _role_family)
    conn.execute("DELETE FROM top_titles_summary")
    conn.execute("""
        INSERT INTO top_titles_summary (title, count)
        SELECT role_family(normalized_title), SUM(cnt) FROM (
            SELECT normalized_title, COUNT(*) as cnt FROM active_jobs
            WHERE normalized_title IS NOT NULL AND normalized_title != '' AND normalized_title != 'Unknown'
            GROUP BY normalized_title
        )
        GROUP BY role_family(normalized_title)
        ORDER BY SUM(cnt) DESC
        LIMIT ?
    """, (limit,))
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM top_titles_summary").fetchone()[0]
    logger.info("[precomputed_summaries] top_titles_summary: %d role families", count)
    return count
```

- [ ] **Step 4: Run the tests again to verify they pass**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_precomputed_summaries.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Add the migration to `src/storage/db.py`**

Find (`src/storage/db.py:300-306`):
```python
        # Migration 013: salary_period — distinguishes hourly rates (common
        # for internship listings, e.g. "$62/hr") from annual figures, so
        # salary_min/salary_max are never silently misread as one or the
        # other by anything comparing/sorting on them.
        _ensure_column(conn, "jobs", "salary_period", "salary_period TEXT")

    conn.close()
```

Replace with:
```python
        # Migration 013: salary_period — distinguishes hourly rates (common
        # for internship listings, e.g. "$62/hr") from annual figures, so
        # salary_min/salary_max are never silently misread as one or the
        # other by anything comparing/sorting on them.
        _ensure_column(conn, "jobs", "salary_period", "salary_period TEXT")

        # Migration 014: precomputed analytics summaries, refreshed once per
        # ingestion pipeline run (src/analytics/precomputed_summaries.py) -
        # replaces two on-demand queries that became too expensive once
        # reachable by anonymous traffic. Also a covering index on skills
        # that speeds up the periodic recompute itself (~29% faster,
        # verified empirically) even though it no longer runs per-request.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_combinations_summary (
                skill_a TEXT NOT NULL,
                skill_b TEXT NOT NULL,
                co_count INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS top_titles_summary (
                title TEXT NOT NULL,
                count INTEGER NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_skills_job_normalized ON skills(job_id, normalized_skill)"
        )

    conn.close()
```

- [ ] **Step 6: Run migrations locally and verify the tables exist**

Run:
```bash
"./.venv/Scripts/python.exe" -c "
from src.storage.db import run_migrations, get_connection
run_migrations()
conn = get_connection()
tables = {row[0] for row in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()}
assert 'skill_combinations_summary' in tables
assert 'top_titles_summary' in tables
print('OK - both summary tables exist')
conn.close()
"
```
Expected: `OK - both summary tables exist`.

- [ ] **Step 7: Rewrite the two endpoint handlers and remove the now-relocated `_role_family` from `web_viewer.py`**

Find (`web_viewer.py:1041-1061`):
```python
@app.route("/api/skills/combinations")
def skill_combinations():
    """Get top skill pairs/combinations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s1.normalized_skill as skill_a, s2.normalized_skill as skill_b, COUNT(*) as co_count
        FROM skills s1
        JOIN skills s2 ON s1.job_id = s2.job_id
        WHERE s1.normalized_skill < s2.normalized_skill
        GROUP BY s1.normalized_skill, s2.normalized_skill
        ORDER BY co_count DESC
        LIMIT 50
    """)
    
    combinations = [{"skill_a": row["skill_a"], "skill_b": row["skill_b"], "count": row["co_count"]} 
                    for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(combinations)
```

Replace with:
```python
@app.route("/api/skills/combinations")
def skill_combinations():
    """Get top skill pairs/combinations (precomputed - see
    src/analytics/precomputed_summaries.py for why)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    limit = 20 if g.current_user else 5
    cursor.execute(
        "SELECT skill_a, skill_b, co_count FROM skill_combinations_summary ORDER BY co_count DESC LIMIT ?",
        (limit,),
    )

    combinations = [{"skill_a": row["skill_a"], "skill_b": row["skill_b"], "count": row["co_count"]}
                    for row in cursor.fetchall()]
    conn.close()

    return jsonify(combinations)
```

Find (`web_viewer.py:1208-1252`):
```python
_SENIORITY_PREFIX_RE = re.compile(
    r'^(?:Senior|Junior|Jr\.?|Sr\.?|Associate|Mid[\s-]Level|Entry[\s-]Level)\s+',
    re.IGNORECASE,
)
_SENIORITY_SUFFIX_RE = re.compile(
    r'\s+(?:Intern|Internship)\s*$',
    re.IGNORECASE,
)

def _role_family(title: str) -> str:
    t = _SENIORITY_PREFIX_RE.sub('', title).strip()
    t = _SENIORITY_SUFFIX_RE.sub('', t).strip()
    return t


@app.route("/api/titles/top")
def titles_top():
    """Get top job titles grouped by role family (seniority-agnostic)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT normalized_title as title, COUNT(*) as count
        FROM active_jobs
        WHERE normalized_title IS NOT NULL
          AND normalized_title != ''
          AND normalized_title != 'Unknown'
        GROUP BY normalized_title
    """)

    # Aggregate by role family (strip seniority prefix)
    from collections import defaultdict
    families: dict[str, int] = defaultdict(int)
    for row in cursor.fetchall():
        family = _role_family(row["title"])
        families[family] += row["count"]

    conn.close()

    titles = sorted(
        [{"title": fam, "count": cnt} for fam, cnt in families.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:30]
    return jsonify(titles)
```

Replace with:
```python
@app.route("/api/titles/top")
def titles_top():
    """Get top job titles grouped by role family (precomputed - see
    src/analytics/precomputed_summaries.py for why)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT title, count FROM top_titles_summary ORDER BY count DESC LIMIT 30")

    titles = [{"title": row["title"], "count": row["count"]} for row in cursor.fetchall()]
    conn.close()

    return jsonify(titles)
```

Find (`web_viewer.py:42-44`):
```python
# Google Sheets integration
from src.sheets_routes import register_sheets_routes
```

Replace with:
```python
# Google Sheets integration
from src.sheets_routes import register_sheets_routes

from src.analytics.precomputed_summaries import _role_family
```

- [ ] **Step 8: Verify `title_skills()` still works with the relocated `_role_family`**

`web_viewer.py:1255-1288`'s `title_skills()` function calls `_role_family(row["normalized_title"])` — confirm no other change is needed there (it isn't; the import added in Step 7 makes the name resolve the same as before). Run:
```bash
"./.venv/Scripts/python.exe" -c "
import web_viewer
assert web_viewer._role_family('Senior Data Scientist') == 'Data Scientist'
print('OK - _role_family importable and working from web_viewer.py')
"
```
Expected: `OK - _role_family importable and working from web_viewer.py`.

- [ ] **Step 9: Hook the recompute calls into the orchestrator**

Find (`src/orchestrator.py:614-618`):
```python
    if _should_recompute_diversity(args):
        try:
            recompute_diversity_ranks()
        except Exception:
            logger.exception("[diversity_rank] recompute failed; leaving ranks stale until next run")
```

Replace with:
```python
    if _should_recompute_diversity(args):
        try:
            recompute_diversity_ranks()
        except Exception:
            logger.exception("[diversity_rank] recompute failed; leaving ranks stale until next run")
        try:
            recompute_skill_combinations()
            recompute_top_titles()
        except Exception:
            logger.exception("[precomputed_summaries] recompute failed; leaving summaries stale until next run")
```

Find (`src/orchestrator.py:43`):
```python
from src.analytics.diversity_rank import recompute_diversity_ranks
```

Replace with:
```python
from src.analytics.diversity_rank import recompute_diversity_ranks
from src.analytics.precomputed_summaries import recompute_skill_combinations, recompute_top_titles
```

- [ ] **Step 10: Write an integration test proving the endpoint respects role-based row limits**

The unit tests in Step 1 cover `_recompute_skill_combinations()`'s correctness in isolation; this test covers the *endpoint's* behavior end-to-end (real HTTP request through `skill_combinations()`, not calling the recompute function directly) — specifically that an anonymous request gets exactly 5 rows and a signed-in request gets exactly 20, matching the spec's validation requirement.

Create `tests/test_skill_combinations_endpoint.py`:
```python
"""
tests/test_skill_combinations_endpoint.py
─────────────────────────────────────────
Confirms /api/skills/combinations respects the role-based row limit
end-to-end (5 for anonymous, 20 for signed-in) - the summary table
itself stores up to 50 rows for headroom, but neither endpoint response
should ever return more than what's actually displayed.
"""
import sqlite3

import pytest


@pytest.fixture()
def app_client_with_30_pairs(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE skill_combinations_summary (skill_a TEXT, skill_b TEXT, co_count INTEGER)
    """)
    conn.executemany(
        "INSERT INTO skill_combinations_summary VALUES (?,?,?)",
        [(f"skill_a_{i}", f"skill_b_{i}", 100 - i) for i in range(30)],
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    return web_viewer.app.test_client()


def test_anonymous_request_gets_exactly_five_rows(app_client_with_30_pairs):
    r = app_client_with_30_pairs.get("/api/skills/combinations")
    assert r.status_code == 200
    assert len(r.get_json()) == 5


def test_signed_in_request_gets_exactly_twenty_rows(app_client_with_30_pairs):
    with app_client_with_30_pairs.session_transaction() as sess:
        sess["user_id"] = 1
    r = app_client_with_30_pairs.get("/api/skills/combinations")
    assert r.status_code == 200
    assert len(r.get_json()) == 20
```

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_skill_combinations_endpoint.py -v`
Expected: both tests PASS.

- [ ] **Step 11: Run the full test suite**

Run: `"./.venv/Scripts/python.exe" -m pytest tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: `167 passed` (159 from Task 2 + 6 from Step 1 + 2 from Step 10), same 1 pre-existing unrelated failure.

- [ ] **Step 12: One-time backfill so the tables aren't empty**

Run:
```bash
"./.venv/Scripts/python.exe" -c "
from src.analytics.precomputed_summaries import recompute_skill_combinations, recompute_top_titles
n1 = recompute_skill_combinations()
n2 = recompute_top_titles()
print(f'skill_combinations_summary: {n1} rows')
print(f'top_titles_summary: {n2} rows')
"
```
Expected: both counts print as positive integers (exact values depend on current local DB content — on production, expect ~50 and ~30 respectively based on the design's empirical measurements).

- [ ] **Step 13: Commit**

```bash
git add src/analytics/precomputed_summaries.py src/storage/db.py src/orchestrator.py web_viewer.py tests/test_precomputed_summaries.py tests/test_skill_combinations_endpoint.py
git commit -m "feat: precompute skill-combinations and top-titles summaries instead of computing on every request"
```

---

### Task 4: Caching for the eight newly-public API endpoints

**Files:**
- Modify: `web_viewer.py` (8 route decorator additions)

**Interfaces:**
- Consumes: `cache` and `_role_aware_cache_key` (already module-level in `web_viewer.py` since an earlier session's work — no import needed) and `_PUBLIC_API_READS` from Task 2 (informational — confirms this task's 8 routes are exactly that set, not a re-derivation).
- Must run after Task 3, not before: two of these 8 routes (`skill_combinations`, `titles_top`) are rewritten by Task 3. Decorating the *old* on-demand-query versions and then rewriting the function bodies in Task 3 would still work mechanically (decorators wrap whatever the function body is at import time), but reviewing Task 4 against Task 3's already-shipped code is cleaner and less error-prone than reviewing them out of order.

- [ ] **Step 1: Cache `/api/dashboard/kpis`**

Find (`web_viewer.py:537-538`):
```python
@app.route("/api/dashboard/kpis")
def dashboard_kpis():
```

Replace with:
```python
@app.route("/api/dashboard/kpis")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_kpis():
```

- [ ] **Step 2: Cache `/api/dashboard/companies`**

Find (`web_viewer.py:801-802`):
```python
@app.route("/api/dashboard/companies")
def dashboard_companies():
```

Replace with:
```python
@app.route("/api/dashboard/companies")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_companies():
```

- [ ] **Step 3: Cache `/api/dashboard/location-diversity`**

Find (`web_viewer.py:823-824`):
```python
@app.route("/api/dashboard/location-diversity")
def dashboard_location_diversity():
```

Replace with:
```python
@app.route("/api/dashboard/location-diversity")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_location_diversity():
```

- [ ] **Step 4: Cache `/api/skills/search`**

Find (`web_viewer.py:861-862`):
```python
@app.route("/api/skills/search")
def skills_search():
```

Replace with:
```python
@app.route("/api/skills/search")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def skills_search():
```

- [ ] **Step 5: Cache `/api/skills/combinations`**

Find (`web_viewer.py:1041-1042`):
```python
@app.route("/api/skills/combinations")
def skill_combinations():
```

Replace with:
```python
@app.route("/api/skills/combinations")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def skill_combinations():
```

- [ ] **Step 6: Cache `/api/companies/list`**

Find (`web_viewer.py:1071-1072`):
```python
@app.route("/api/companies/list")
def companies_list():
```

Replace with:
```python
@app.route("/api/companies/list")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def companies_list():
```

- [ ] **Step 7: Cache `/api/titles/top`**

Find (`web_viewer.py:1223-1224`):
```python
@app.route("/api/titles/top")
def titles_top():
```

Replace with:
```python
@app.route("/api/titles/top")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def titles_top():
```

- [ ] **Step 8: Cache `/api/filters/skills`**

Find (`web_viewer.py:1291-1292`):
```python
@app.route("/api/filters/skills")
def get_skills_filter():
```

Replace with:
```python
@app.route("/api/filters/skills")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def get_skills_filter():
```

- [ ] **Step 9: Write an integration test proving the cache actually works on one representative route**

Create `tests/test_public_api_caching.py`:
```python
"""
tests/test_public_api_caching.py
───────────────────────────────────
Confirms one of the 8 newly-cached public API endpoints is actually
served from cache on a repeat request (via Flask-Caching's
response_hit_indication mechanism, same verified pattern as
tests/test_route_caching.py), and that an anonymous request and a
signed-in request to the same endpoint don't share a cache entry.
"""
import sqlite3

import pytest


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            listing_status TEXT, company TEXT
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
    """)
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    return web_viewer.app.test_client()


def test_anonymous_repeat_request_is_served_from_cache(app_client):
    r1 = app_client.get("/api/dashboard/companies")
    assert r1.status_code == 200
    assert r1.headers.get("hit_cache") is None

    r2 = app_client.get("/api/dashboard/companies")
    assert r2.status_code == 200
    assert r2.headers.get("hit_cache") == "True"


def test_anonymous_and_signed_in_requests_dont_share_a_cache_entry(app_client):
    app_client.get("/api/dashboard/companies")  # anonymous, populates the anon cache entry

    with app_client.session_transaction() as sess:
        sess["user_id"] = 1
    r = app_client.get("/api/dashboard/companies")  # now signed in, same URL
    assert r.status_code == 200
    assert r.headers.get("hit_cache") is None, "signed-in request must not be served the anonymous visitor's cached response"
```

- [ ] **Step 10: Run the new test and the full suite**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_public_api_caching.py tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: `169 passed` (167 from Task 3 + 2 new), same 1 pre-existing unrelated failure.

- [ ] **Step 11: Commit**

```bash
git add web_viewer.py tests/test_public_api_caching.py
git commit -m "feat: cache the eight newly-public API endpoints"
```

---

### Task 5: robots.txt

**Files:**
- Modify: `web_viewer.py` (new route, add to `_PUBLIC_PATHS`)
- Test: `tests/test_robots_txt.py` (new file)

**Interfaces:** None — standalone addition, no dependency on other tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_robots_txt.py`:
```python
"""tests/test_robots_txt.py — /robots.txt disallows everything, is
reachable without a session (crawlers never authenticate), and is
served at the domain root, not under /static/."""


def test_robots_txt_reachable_without_session():
    import web_viewer
    client = web_viewer.app.test_client()
    r = client.get("/robots.txt")
    assert r.status_code == 200


def test_robots_txt_disallows_everything():
    import web_viewer
    client = web_viewer.app.test_client()
    r = client.get("/robots.txt")
    body = r.get_data(as_text=True)
    assert "User-agent: *" in body
    assert "Disallow: /" in body


def test_robots_txt_is_plain_text():
    import web_viewer
    client = web_viewer.app.test_client()
    r = client.get("/robots.txt")
    assert r.content_type.startswith("text/plain")
```

- [ ] **Step 2: Run to confirm it fails**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_robots_txt.py -v`
Expected: FAIL with 404s (route doesn't exist yet).

- [ ] **Step 3: Add the route to `web_viewer.py`**

Find (`web_viewer.py:106-124`, using the version already updated by Task 2 — if Task 2 hasn't run yet, the `_PUBLIC_VIEWABLE_ENDPOINTS`/`_PUBLIC_API_READS` lines won't be present, match on `_PUBLIC_PATHS` alone in that case):
```python
_PUBLIC_PATHS = {"/healthz", "/auth/login", "/auth/logout", "/auth/google", "/auth/google/callback"}
```

Replace with:
```python
_PUBLIC_PATHS = {"/healthz", "/auth/login", "/auth/logout", "/auth/google", "/auth/google/callback", "/robots.txt"}
```

Then find the `/healthz` route definition (search for `@app.route("/healthz")` in `web_viewer.py`) and add the new route directly after that view function's closing line — matching the pattern of a small, standalone, unauthenticated route living near `/healthz` rather than being scattered elsewhere in the file:

```python
@app.route("/robots.txt")
def robots_txt():
    """Disallow everything for now - not an SEO launch yet."""
    return make_response(("User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}))
```

(Uses `make_response`, already imported at the top of `web_viewer.py` — no new import needed.)

- [ ] **Step 4: Run the test again to verify it passes**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_robots_txt.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Run the full test suite**

Run: `"./.venv/Scripts/python.exe" -m pytest tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: `172 passed` (169 from Task 4 + 3 new), same 1 pre-existing unrelated failure.

- [ ] **Step 6: Commit**

```bash
git add web_viewer.py tests/test_robots_txt.py
git commit -m "feat: add robots.txt disallowing all indexing"
```

---

### Task 6: Domain migration (gated — requires explicit user confirmation before running)

**Do not execute this task as part of automatic plan execution.** Unlike Tasks 1-5, which are self-contained and independently deployable, this task touches live DNS-dependent Caddy configuration on a VPS that also serves other unrelated projects, and depends on two prerequisites only the user can complete:
1. `greywave.dev`'s DNS A record must be pointed at `161.97.163.210` (confirmed not done as of this plan being written).
2. `https://greywave.dev/auth/google/callback` must be added to the Google Cloud Console OAuth app's authorized redirect URIs (cannot be automated from this codebase).

**Files (all on the VPS, none in this repo):**
- Modify: `/opt/Portfolio/Caddyfile` (the shared Caddy container's config — NOT this repo's `deploy/Caddyfile`, which is an unused template)
- Modify: `/opt/jobmarket/.env` (`WEB_VIEWER_URL`)

- [ ] **Step 1: Confirm both prerequisites are actually done**

Ask the user directly (do not assume): has `greywave.dev`'s DNS A record been pointed at `161.97.163.210` yet, and has the Google Cloud Console redirect URI been added? Verify DNS yourself before proceeding: `nslookup greywave.dev` (or equivalent) should resolve to `161.97.163.210`. Do not proceed to Step 2 until both are confirmed.

- [ ] **Step 2: Add the new Caddy block (site not yet cut over)**

On the VPS, edit `/opt/Portfolio/Caddyfile`, adding a new block (do not remove or modify the existing `jobs.mujtaba0085.opior.com` block yet):
```
greywave.dev {
    reverse_proxy jobmarket-web:5000
}
```
Reload Caddy (the exact command depends on how the `portfolio-caddy` container is set up — likely `docker exec portfolio-caddy caddy reload --config /etc/caddy/Caddyfile` or a container restart). Confirm `https://greywave.dev` loads and Caddy successfully provisioned a Let's Encrypt certificate (no TLS warning in a browser, or `curl -v https://greywave.dev/healthz` shows a valid cert).

- [ ] **Step 3: Update `WEB_VIEWER_URL` and verify the OAuth round-trip on the new domain**

On the VPS, update `/opt/jobmarket/.env`'s `WEB_VIEWER_URL` to `https://greywave.dev`, then restart the `jobmarket-web` container so it picks up the new env value. Manually test: visit `https://greywave.dev/auth/login`, click "Continue with Google," confirm the OAuth flow completes and lands back on `https://greywave.dev/dashboard` (not the old domain, not an error page). This is the step that fails if the Google Cloud Console redirect URI (Step 1's second prerequisite) wasn't actually added correctly — if it fails here, stop and fix the Console setting before continuing.

- [ ] **Step 4: Cut the old domain over to a redirect**

Only after Step 3 succeeds: edit `/opt/Portfolio/Caddyfile` again, changing:
```
jobs.mujtaba0085.opior.com {
    reverse_proxy jobmarket-web:5000
}
```
to:
```
jobs.mujtaba0085.opior.com {
    redir https://greywave.dev{uri} permanent
}
```
Reload Caddy again. Confirm `https://jobs.mujtaba0085.opior.com/dashboard` now redirects to `https://greywave.dev/dashboard` (a 301, check via `curl -I`).

- [ ] **Step 5: Final spot-check**

Confirm: `https://greywave.dev/healthz` returns healthy, a real login works end-to-end on the new domain, the old domain redirects correctly for at least one other path (not just `/dashboard`), and `docker compose logs web` (or equivalent) shows no new errors since the cutover.
