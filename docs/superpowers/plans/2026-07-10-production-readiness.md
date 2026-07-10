# Production Readiness for Public Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the app use its 4 CPU cores instead of serializing every request through one gunicorn worker, cache recurring read-only page requests, and add a SQLite safety net — all in preparation for a public launch in a few days.

**Architecture:** Reconfigure gunicorn to run 4 worker processes × 4 threads each (`gthread`); add `Flask-Caching` with a filesystem backend, keyed by request path+query string *and* admin/non-admin role so cached responses never cross-contaminate between the two; add `PRAGMA busy_timeout` to the one connection helper that's missing it. Verify with the existing test suite plus a new lightweight load-test script.

**Tech Stack:** Flask, gunicorn (`gthread` worker class), Flask-Caching 2.4+ (`FileSystemCache`), SQLite (WAL mode, already enabled), Python's `concurrent.futures` for the load test (no new dependency needed for that part).

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-10-production-readiness-design.md` — read it for full context/reasoning behind every decision below.
- Cache TTL: exactly 900 seconds (15 minutes) flat across every cached route — copied verbatim from the spec, don't invent a different number.
- Cache directory: `data/cache/` (already gitignored — confirmed via `.gitignore:37`).
- gunicorn: `worker_class = "gthread"`, `workers = 4`, `threads = 4`. All other existing settings (`bind`, `timeout`, `graceful_timeout`, `keepalive`, logging) stay unchanged.
- SQLite busy_timeout: exactly `5000` (5 seconds) in `src/storage/db.py`'s `get_connection()`. Note: `web_viewer.py`'s own `get_db_connection()` helper (line 137) already has `PRAGMA busy_timeout = 30000` set — do NOT touch that function, it's already correct. Only `src/storage/db.py:get_connection()` is missing the setting.
- Routes to cache (exact function names verified against current `web_viewer.py`, do not cache anything else): `dashboard()` (`/dashboard`, line 488), `jobs_list()` (`/jobs`, line 1378), `job_detail()` (`/jobs/<int:job_id>`, line 1857), `skills_overview()` (`/skills`, line 1940), `skills_intelligence()` (`/skills/intelligence`, line 824), `companies_intelligence()` (`/companies/intelligence`, line 1033), `titles_analytics()` (`/titles/analytics`, line 1163), `metrics_overview()` (`/metrics`, line 1980).
- Never touch caching-wise: anything under `/admin/*`, `/auth/*`, `/sheets/track`, `/sheets/track_job`, `/healthz`, or any `/api/*` route — all explicitly out of scope per the spec.
- **Critical correctness requirement, verified empirically (not from documentation alone) before this plan was written:** Flask-Caching's `@cache.cached()` decorator does **not** include the query string in its cache key by default (`query_string` parameter defaults to `False` — confirmed by reading `flask_caching.Cache.cached`'s source directly). Every cached route in this plan uses a custom `key_prefix` callable (not the string default, not `query_string=True` alone) that combines `request.full_path` (path + query string) with the caller's admin/non-admin role. This was verified working correctly with a real Flask test app before being written into this plan — see Task 2.

---

### Task 1: Gunicorn concurrency config + SQLite busy_timeout

**Files:**
- Modify: `gunicorn.conf.py` (full file, currently 10 lines)
- Modify: `src/storage/db.py:45-51` (`get_connection()`)

**Interfaces:** None — both are standalone config changes with no dependency on other tasks.

- [ ] **Step 1: Update gunicorn.conf.py**

Current content:
```python
bind = "0.0.0.0:5000"
workers = 1
threads = 2
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True
loglevel = "info"
```

Replace with:
```python
bind = "0.0.0.0:5000"
worker_class = "gthread"
workers = 4
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True
loglevel = "info"
```

(Only change: `worker_class = "gthread"` added, `workers` changed from `1` to `4`, `threads` changed from `2` to `4`. Everything else identical.)

- [ ] **Step 2: Verify gunicorn actually starts with 4 worker processes**

Run (from repo root, in the venv):
```bash
"./.venv/Scripts/python.exe" -m gunicorn -c gunicorn.conf.py web_viewer:app --check-config
```
Expected: no errors (this validates the config file loads correctly without actually binding the port).

Then start it for real, in the background, and confirm worker count:
```bash
"./.venv/Scripts/python.exe" -m gunicorn -c gunicorn.conf.py web_viewer:app &
sleep 3
ps aux | grep "gunicorn: worker" | grep -v grep | wc -l
kill %1
```
Expected: the worker-count line prints `4`. Stop the background process after confirming (the `kill %1` above, or `pkill -f "gunicorn.*web_viewer"` if needed).

- [ ] **Step 3: Add busy_timeout to src/storage/db.py**

Find (`src/storage/db.py:45-51`):
```python
def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # better concurrent read perf
    return conn
```

Replace with:
```python
def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # better concurrent read perf
    conn.execute("PRAGMA busy_timeout = 5000")  # wait up to 5s on a write-lock collision instead of failing immediately
    return conn
```

- [ ] **Step 4: Verify busy_timeout is actually set on a fresh connection**

Run:
```bash
"./.venv/Scripts/python.exe" -c "
from src.storage.db import get_connection
conn = get_connection()
result = conn.execute('PRAGMA busy_timeout').fetchone()
print('busy_timeout:', result[0])
assert result[0] == 5000, f'expected 5000, got {result[0]}'
print('OK')
conn.close()
"
```
Expected output:
```
busy_timeout: 5000
OK
```

- [ ] **Step 5: Run the existing test suite to confirm no regressions**

Run: `"./.venv/Scripts/python.exe" -m pytest tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: `149 passed`, 1 pre-existing failure in `tests/test_auth_security.py::test_login_rejects_external_next_target` (unrelated to this change, do not attempt to fix it here).

- [ ] **Step 6: Commit**

```bash
git add gunicorn.conf.py src/storage/db.py
git commit -m "feat: enable multi-worker gunicorn concurrency + SQLite busy_timeout"
```

---

### Task 2: Flask-Caching setup with role-aware cache key

**Files:**
- Modify: `requirements.txt`
- Modify: `web_viewer.py` (imports + app config, near the top where `app = Flask(__name__)` is set up)
- Test: `tests/test_cache_key.py` (new file)

**Interfaces:**
- Produces: `cache` (a `flask_caching.Cache` instance, module-level in `web_viewer.py`, importable as `from web_viewer import cache` if a later task needs it — Task 3 does not need to import it since it edits the same file) and `_role_aware_cache_key()` (a zero-argument callable in `web_viewer.py`, used as the `key_prefix=` argument to `@cache.cached(...)` in Task 3).

- [ ] **Step 1: Add Flask-Caching to requirements.txt**

Find (`requirements.txt`, the "Web viewer" section):
```
# Web viewer
flask>=3.0
```

Replace with:
```
# Web viewer
flask>=3.0
Flask-Caching>=2.4
```

- [ ] **Step 2: Install it**

Run: `"./.venv/Scripts/python.exe" -m pip install Flask-Caching>=2.4`
Expected: install succeeds (it may already be installed from verification done while writing this plan — that's fine, pip will report "already satisfied").

- [ ] **Step 3: Write the failing test for the role-aware cache key**

Create `tests/test_cache_key.py`:
```python
"""
Verifies web_viewer.py's cache setup: Flask-Caching's @cache.cached()
does NOT include the query string in its default cache key (confirmed by
reading flask_caching.Cache.cached's source - query_string defaults to
False), so every cached route must use the custom key_prefix callable
tested here instead of relying on defaults. This test would have caught
the bug where two different /jobs?market=... filter combinations
incorrectly share one cached response.
"""
from flask import Flask, request, g
from flask_caching import Cache

from web_viewer import _role_aware_cache_key


def _build_test_app():
    app = Flask(__name__)
    app.config["CACHE_TYPE"] = "SimpleCache"
    cache = Cache(app)
    call_count = {"n": 0}

    @app.before_request
    def _set_role():
        g.current_user = {"role": "admin"} if request.headers.get("X-Test-Admin") == "1" else {"role": "viewer"}

    @app.route("/thing")
    @cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
    def thing():
        call_count["n"] += 1
        return f"call {call_count['n']}"

    return app, call_count


def test_different_query_strings_get_separate_cache_entries():
    app, call_count = _build_test_app()
    with app.test_client() as c:
        c.get("/thing?market=ai_ml_global")
        c.get("/thing?market=ai_ml_global")  # repeat -> cache hit
        c.get("/thing?market=swe_backend_global")  # different query -> fresh call
    assert call_count["n"] == 2


def test_admin_and_viewer_get_separate_cache_entries_for_same_url():
    app, call_count = _build_test_app()
    with app.test_client() as c:
        c.get("/thing")  # viewer
        c.get("/thing")  # viewer repeat -> cache hit
        c.get("/thing", headers={"X-Test-Admin": "1"})  # admin, same URL -> fresh call
        c.get("/thing", headers={"X-Test-Admin": "1"})  # admin repeat -> cache hit
    assert call_count["n"] == 2
```

- [ ] **Step 4: Run it to confirm it fails (the function doesn't exist yet)**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_cache_key.py -v`
Expected: FAIL with `ImportError: cannot import name '_role_aware_cache_key' from 'web_viewer'`

- [ ] **Step 5: Add the cache setup to web_viewer.py**

Find (`web_viewer.py:44-52`):
```python
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True   # slide the 2h window on every request
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
DB_PATH = SETTINGS_DB_PATH
logger = logging.getLogger(__name__)
```

Replace with:
```python
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True   # slide the 2h window on every request
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["CACHE_TYPE"] = "FileSystemCache"
app.config["CACHE_DIR"] = "data/cache"
app.config["CACHE_DEFAULT_TIMEOUT"] = 900  # 15 minutes
DB_PATH = SETTINGS_DB_PATH
logger = logging.getLogger(__name__)

from flask_caching import Cache
cache = Cache(app)


def _role_aware_cache_key() -> str:
    """
    Cache key for @cache.cached(key_prefix=_role_aware_cache_key).

    Flask-Caching's default key does NOT include the query string
    (query_string defaults to False - confirmed by reading the library's
    source) - request.full_path is used explicitly here so that e.g.
    /jobs?market=ai_ml_global and /jobs?market=swe_backend_global get
    separate cache entries instead of colliding into one. The role prefix
    (admin vs. everyone else) keeps an admin session's cached response
    (which includes extra UI like the "Data Quality Review" link) from
    ever being served to a regular viewer, or vice versa.
    """
    role = "admin" if (g.current_user and g.current_user.get("role") == "admin") else "viewer"
    return f"{role}:{request.full_path}"
```

- [ ] **Step 6: Run the test again to verify it passes**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_cache_key.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Run the full test suite to confirm nothing else broke**

Run: `"./.venv/Scripts/python.exe" -m pytest tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: `151 passed` (149 existing + 2 new), same 1 pre-existing unrelated failure.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt web_viewer.py tests/test_cache_key.py
git commit -m "feat: add Flask-Caching with role-aware, query-string-aware cache key"
```

---

### Task 3: Apply caching to the 8 read-only page routes

**Files:**
- Modify: `web_viewer.py` (8 route functions — exact locations below)

**Interfaces:**
- Consumes: `cache` and `_role_aware_cache_key` from Task 2 (both already module-level in this same file, no import needed).

- [ ] **Step 1: Cache `/dashboard`**

Find (`web_viewer.py:487-490`):
```python
@app.route("/dashboard")
def dashboard():
    """BI Dashboard with interactive widgets."""
    return render_template("dashboard.html")
```

Replace with:
```python
@app.route("/dashboard")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard():
    """BI Dashboard with interactive widgets."""
    return render_template("dashboard.html")
```

- [ ] **Step 2: Cache `/jobs`**

Find (`web_viewer.py:1377-1379`):
```python
@app.route("/jobs")
def jobs_list():
    """List jobs with filters, status selector, and pagination."""
```

Replace with:
```python
@app.route("/jobs")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def jobs_list():
    """List jobs with filters, status selector, and pagination."""
```

- [ ] **Step 3: Cache `/jobs/<int:job_id>`**

Find (`web_viewer.py:1856-1858`):
```python
@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    """Show full job details including description and all locations."""
```

Replace with:
```python
@app.route("/jobs/<int:job_id>")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def job_detail(job_id):
    """Show full job details including description and all locations."""
```

- [ ] **Step 4: Cache `/skills`**

Find (`web_viewer.py:1939-1941`):
```python
@app.route("/skills")
def skills_overview():
    """Overview of all detected skills."""
```

Replace with:
```python
@app.route("/skills")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def skills_overview():
    """Overview of all detected skills."""
```

- [ ] **Step 5: Cache `/skills/intelligence`**

Find (`web_viewer.py:823-826`):
```python
@app.route("/skills/intelligence")
def skills_intelligence():
    """Skills Intelligence Page with detailed analytics."""
    return render_template("skills_intelligence.html")
```

Replace with:
```python
@app.route("/skills/intelligence")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def skills_intelligence():
    """Skills Intelligence Page with detailed analytics."""
    return render_template("skills_intelligence.html")
```

- [ ] **Step 6: Cache `/companies/intelligence`**

Find (`web_viewer.py:1032-1035`):
```python
@app.route("/companies/intelligence")
def companies_intelligence():
    """Company Intelligence Page."""
    return render_template("companies_intelligence.html")
```

Replace with:
```python
@app.route("/companies/intelligence")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def companies_intelligence():
    """Company Intelligence Page."""
    return render_template("companies_intelligence.html")
```

- [ ] **Step 7: Cache `/titles/analytics`**

Find (`web_viewer.py:1162-1165`):
```python
@app.route("/titles/analytics")
def titles_analytics():
    """Job Titles Analytics Page."""
    return render_template("titles_analytics.html")
```

Replace with:
```python
@app.route("/titles/analytics")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def titles_analytics():
    """Job Titles Analytics Page."""
    return render_template("titles_analytics.html")
```

- [ ] **Step 8: Cache `/metrics`**

Find (`web_viewer.py:1979-1981`):
```python
@app.route("/metrics")
def metrics_overview():
    """Weekly metrics and trends."""
```

Replace with:
```python
@app.route("/metrics")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def metrics_overview():
    """Weekly metrics and trends."""
```

- [ ] **Step 9: Write an integration test proving caching actually works on a real route**

Uses Flask-Caching's built-in `response_hit_indication=True` mechanism
(added to every route's decorator in Steps 1-8 above): it adds a
`hit_cache: True` response header only when a request was served from
cache, never on the first (real) render. Verified directly against the
`flask_caching` library before being written here — introspecting its
internal cache-store keys directly is backend-specific and not a safe
thing to assert on, but this header is a stable, documented, public
signal meant exactly for this kind of check.

The fixture's schema below was verified empirically against the real
`dashboard()` and `jobs_list()` view functions before being written here
(a first attempt using a 1-column `active_jobs` view with zero rows ran
`/dashboard` fine — it does no DB access at all — but crashed `/jobs` with
`sqlite3.OperationalError: no such column: source_name`, since
`jobs_list()` unconditionally runs `SELECT DISTINCT source_name FROM
active_jobs ...` before any filtering happens). The schema below carries
every column `jobs_list()` touches for the two request shapes this test
makes (no `skills` filter is used, so the conditional `skills` table join
in that function is never reached and doesn't need a table here).

Create `tests/test_route_caching.py`:
```python
"""
tests/test_route_caching.py
──────────────────────────────
Integration test: hits a real cached route through the actual running app
(not a synthetic test app like tests/test_cache_key.py's unit tests) and
confirms the second identical request is served from cache, via
Flask-Caching's response_hit_indication mechanism (a `hit_cache: True`
response header, present only on cache hits).
"""
import sqlite3

import pytest


@pytest.fixture()
def cached_app(tmp_path, monkeypatch):
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
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    web_viewer.cache.clear()
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1  # matches the established pattern in tests/test_jobs_list_sort.py
    return client


def test_dashboard_second_request_is_served_from_cache(cached_app):
    r1 = cached_app.get("/dashboard")
    assert r1.status_code == 200
    assert r1.headers.get("hit_cache") is None  # first hit - real render, not cached yet

    r2 = cached_app.get("/dashboard")
    assert r2.status_code == 200
    assert r2.headers.get("hit_cache") == "True"  # second hit - served from cache


def test_differently_filtered_jobs_request_is_not_served_from_unfiltered_cache(cached_app):
    r1 = cached_app.get("/jobs")
    r2 = cached_app.get("/jobs?market=ai_ml_global")
    assert r1.status_code == 200 and r2.status_code == 200
    # Different query string -> must NOT be a cache hit against the
    # unfiltered /jobs response from r1, even though both hit the same
    # view function moments apart.
    assert r2.headers.get("hit_cache") is None
```

- [ ] **Step 10: Run the new test and the full suite**

Run: `"./.venv/Scripts/python.exe" -m pytest tests/test_route_caching.py tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: new test(s) pass, same 149 (or 151, if Task 2's tests are counted) + pre-existing baseline, no new failures.

- [ ] **Step 11: Commit**

```bash
git add web_viewer.py tests/test_route_caching.py
git commit -m "feat: cache the 8 read-only page routes (dashboard, jobs, skills, companies, titles, metrics)"
```

---

### Task 4: Load test + deployment validation

**Files:**
- Create: `scripts/load_test.py`

**Interfaces:** None — this is the final validation step, consumes nothing from earlier tasks except the running application itself.

- [ ] **Step 1: Write the load test script**

Create `scripts/load_test.py`:
```python
"""
Lightweight concurrent-load test for the production-readiness work
(docs/superpowers/specs/2026-07-10-production-readiness-design.md).
No new dependency - uses concurrent.futures + requests (already a
project dependency) rather than pulling in locust for a one-off script.

Simulates a burst of concurrent users hitting a mix of cached and
cache-excluded routes, and reports: error count, response time
percentiles, and - the specific thing this validates, not just "did it
not crash" - whether repeat requests to the same URL get measurably
faster after the first (proof the cache is actually being hit, not
silently bypassed).

Usage:
    python scripts/load_test.py --base-url http://localhost:5000 --users 75 --cookie "session=<value>"

The --cookie value must be a valid logged-in session cookie (copy it from
your browser's dev tools after logging in locally) - every route under
test requires authentication, there is no anonymous path to hit.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

_ROUTES = [
    "/dashboard",
    "/jobs",
    "/jobs?market=ai_ml_global",
    "/jobs?market=swe_backend_global&remote_type=remote",
    "/skills",
    "/skills/intelligence",
    "/companies/intelligence",
    "/titles/analytics",
    "/metrics",
]


def _fetch(base_url: str, path: str, cookie: str) -> tuple[str, int, float]:
    start = time.monotonic()
    try:
        resp = requests.get(f"{base_url}{path}", headers={"Cookie": cookie}, timeout=15)
        elapsed = time.monotonic() - start
        return path, resp.status_code, elapsed
    except requests.RequestException as exc:
        elapsed = time.monotonic() - start
        print(f"  !! request to {path} failed: {exc}", file=sys.stderr)
        return path, 0, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--users", type=int, default=75, help="concurrent simulated users")
    parser.add_argument("--cookie", required=True, help="logged-in session cookie, e.g. 'session=eyJ...'")
    args = parser.parse_args()

    print(f"Warming cache with one request per route...")
    for path in _ROUTES:
        _fetch(args.base_url, path, args.cookie)

    print(f"\nCold (first-hit-per-route) timings just captured above are the baseline.")
    print(f"Now firing {args.users} concurrent requests across {len(_ROUTES)} routes...\n")

    tasks = [(_ROUTES[i % len(_ROUTES)]) for i in range(args.users)]
    results: list[tuple[str, int, float]] = []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [pool.submit(_fetch, args.base_url, path, args.cookie) for path in tasks]
        for f in as_completed(futures):
            results.append(f.result())
    total_wall_time = time.monotonic() - start

    errors = [r for r in results if r[1] != 200]
    times = sorted(r[2] for r in results)

    print(f"Total wall time for {args.users} concurrent requests: {total_wall_time:.2f}s")
    print(f"Errors (non-200): {len(errors)} / {len(results)}")
    for path, status, _ in errors[:10]:
        print(f"  {status} {path}")

    if times:
        print(f"Response time - min: {times[0]*1000:.0f}ms, "
              f"median: {statistics.median(times)*1000:.0f}ms, "
              f"p95: {times[int(len(times)*0.95)]*1000:.0f}ms, "
              f"max: {times[-1]*1000:.0f}ms")

    print("\nCache-hit verification: re-fetching each route once more, timing should be low (cached)...")
    for path in _ROUTES:
        _, status, elapsed = _fetch(args.base_url, path, args.cookie)
        flag = "OK" if status == 200 else f"FAILED ({status})"
        print(f"  {path:55} {elapsed*1000:6.1f}ms  {flag}")

    if errors:
        print(f"\nFAILED: {len(errors)} requests did not return 200.")
        sys.exit(1)
    print("\nPASSED: no errors under load.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the local dev server**

First, start the app locally with the new gunicorn config in one terminal:
```bash
"./.venv/Scripts/python.exe" -m gunicorn -c gunicorn.conf.py web_viewer:app
```

In a browser, log in at `http://localhost:5000/auth/login`, open dev tools → Application/Storage → Cookies, copy the `session` cookie value.

In a second terminal, run:
```bash
"./.venv/Scripts/python.exe" scripts/load_test.py --base-url http://localhost:5000 --users 75 --cookie "session=<paste-the-value-here>"
```

Expected: `PASSED: no errors under load.` printed at the end, and the final "Cache-hit verification" section shows noticeably lower response times than whatever the very first cold-cache request took (the warm-up section printed at the start) - confirming caching is actually reducing load, not just present in the code.

- [ ] **Step 3: Run the full test suite one final time**

Run: `"./.venv/Scripts/python.exe" -m pytest tests -q --basetemp="C:/Users/moham/AppData/Local/Temp/pytest_basetemp"`
Expected: same passing baseline as Task 3's Step 10, no new failures introduced by the load test script (it's a standalone script, shouldn't affect anything, this is a final sanity check).

- [ ] **Step 4: Commit**

```bash
git add scripts/load_test.py
git commit -m "feat: add concurrent load-test script for production-readiness validation"
```

- [ ] **Step 5: VPS deployment (requires explicit user confirmation before running — this touches the live production site)**

This step is intentionally **not** automated as part of task execution. Once Tasks 1-4 are committed and merged, deploying to the VPS means:
1. Pushing the commits to whatever remote/branch the VPS pulls from.
2. Rebuilding and restarting the `jobmarket-web` container (`docker compose build && docker compose up -d web`, or equivalent per `deploy/VPS_DEPLOY.md`).
3. Spot-check: `curl -fsS http://localhost:5000/healthz` on the VPS returns healthy.
4. Spot-check: log in via a browser against the real public URL, confirm `/dashboard` and `/jobs` load correctly.
5. Confirm worker count: `docker exec jobmarket-web ps aux | grep "gunicorn: worker" | grep -v grep | wc -l` should print `4`.

**Do not perform this step without explicit go-ahead** — it's a production deployment, not a local change, and should be confirmed at the time rather than assumed as part of this plan's execution.
