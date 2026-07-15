# Admin Notification Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admins post site-wide or page-targeted announcement bars (maintenance notices, etc.), visible to every visitor including anonymous ones, individually dismissible, optionally auto-expiring, manageable from a new `/admin/notifications` page.

**Architecture:** One new table (`notifications`) in the existing non-rotating `operational.sqlite`. A `before_request` hook filters active, page-matching, not-yet-dismissed notifications into `g.active_notifications`; `base.html` renders one full-width bar per entry. Dismissal is a pure client-side cookie write (no new endpoint). A new admin page provides create/list/remove.

**Tech Stack:** Flask, Jinja2, SQLite (stdlib `sqlite3`), vanilla JS — no new dependencies.

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every `pytest` invocation uses `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp` (Windows temp-dir `PermissionError` workaround).
- Any test fixture that calls `db.run_migrations()` or otherwise exercises real connection resolution must patch ALL of `src.storage.db._SERVING_A_PATH`, `_SERVING_B_PATH`, `_BUFFER_DB_PATH`, `_OPERATIONAL_DB_PATH`, `_POINTER_PATH` together — patching only `DB_PATH` is a known-broken no-op for `get_connection()`'s resolution (caused two real regressions earlier this session). The `notifications` table lives in `operational.sqlite`, so `_OPERATIONAL_DB_PATH` in particular must be patched for any test that touches it.
- Audience is always everyone (no per-role targeting) — this is fixed, not a config option.
- The 7 page keys are fixed: `dashboard`, `jobs`, `skills`, `companies`, `titles`, `metrics`, `api_docs`. No others exist; `/admin/*` and `/auth/*` are never targetable.
- Work directly on branch `main` (no worktree).

---

### Task 1: Data model + pure filtering logic

**Files:**
- Modify: `src/storage/db.py` (`_run_operational_migrations_impl()`, currently at line ~270)
- Create: `src/notifications.py`
- Test: `tests/test_notifications.py`

**Interfaces:**
- Produces: `src.notifications.PAGE_KEYS: tuple[str, ...]` = `("dashboard", "jobs", "skills", "companies", "titles", "metrics", "api_docs")`
- Produces: `src.notifications.page_key_for_path(path: str) -> str | None` — maps a request path to one of `PAGE_KEYS`, or `None` if the path isn't in any targetable section.
- Produces: `src.notifications.filter_active_notifications(rows: list[sqlite3.Row], path: str, dismissed_ids: set[int], now: datetime) -> list[sqlite3.Row]` — pure, no I/O.
- Produces: `notifications` table schema (see Step 3) in `operational.sqlite`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notifications.py`:

```python
"""
tests/test_notifications.py
─────────────────────────────
Pure-function tests for src/notifications.py's page-matching, expiry, and
dismissed-id filtering logic - no Flask, no request context, matching the
same separation-of-concerns already used by
src.classification.scheduling.should_process_chunk().
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.notifications import PAGE_KEYS, filter_active_notifications, page_key_for_path


def _row(id=1, target_pages="all", expires_at=None):
    """Minimal stand-in for a sqlite3.Row - a dict works since
    filter_active_notifications() only ever does dict-style key access."""
    return {"id": id, "target_pages": target_pages, "expires_at": expires_at}


class TestPageKeyForPath:
    def test_dashboard_root(self):
        assert page_key_for_path("/") == "dashboard"

    def test_dashboard_prefix(self):
        assert page_key_for_path("/dashboard") == "dashboard"

    def test_jobs_list(self):
        assert page_key_for_path("/jobs") == "jobs"

    def test_jobs_detail(self):
        assert page_key_for_path("/jobs/12345") == "jobs"

    def test_skills(self):
        assert page_key_for_path("/skills/intelligence") == "skills"

    def test_companies(self):
        assert page_key_for_path("/companies/intelligence") == "companies"

    def test_titles(self):
        assert page_key_for_path("/titles/analytics") == "titles"

    def test_metrics(self):
        assert page_key_for_path("/metrics") == "metrics"

    def test_api_docs(self):
        assert page_key_for_path("/api/docs") == "api_docs"

    def test_admin_path_is_not_targetable(self):
        assert page_key_for_path("/admin/pipeline") is None

    def test_auth_path_is_not_targetable(self):
        assert page_key_for_path("/auth/login") is None

    def test_unrelated_path_is_none(self):
        assert page_key_for_path("/healthz") is None

    def test_all_page_keys_have_at_least_one_matching_path(self):
        # Guards against a future PAGE_KEYS edit that adds a key with no
        # matching branch in page_key_for_path().
        sample_paths = {
            "dashboard": "/dashboard", "jobs": "/jobs", "skills": "/skills/intelligence",
            "companies": "/companies/intelligence", "titles": "/titles/analytics",
            "metrics": "/metrics", "api_docs": "/api/docs",
        }
        for key in PAGE_KEYS:
            assert page_key_for_path(sample_paths[key]) == key


class TestFilterActiveNotifications:
    def test_all_pages_notification_matches_any_path(self):
        rows = [_row(id=1, target_pages="all")]
        result = filter_active_notifications(rows, "/jobs", set(), datetime.now(timezone.utc))
        assert len(result) == 1

    def test_specific_page_matches_only_that_page(self):
        rows = [_row(id=1, target_pages="jobs")]
        now = datetime.now(timezone.utc)
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 1
        assert len(filter_active_notifications(rows, "/dashboard", set(), now)) == 0

    def test_multi_page_target_list(self):
        rows = [_row(id=1, target_pages="jobs,dashboard")]
        now = datetime.now(timezone.utc)
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 1
        assert len(filter_active_notifications(rows, "/dashboard", set(), now)) == 1
        assert len(filter_active_notifications(rows, "/skills/intelligence", set(), now)) == 0

    def test_no_expiry_never_filtered_out_by_time(self):
        rows = [_row(id=1, target_pages="all", expires_at=None)]
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        assert len(filter_active_notifications(rows, "/jobs", set(), far_future)) == 1

    def test_future_expiry_still_active(self):
        now = datetime.now(timezone.utc)
        future = (now + timedelta(hours=1)).isoformat()
        rows = [_row(id=1, target_pages="all", expires_at=future)]
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 1

    def test_past_expiry_filtered_out(self):
        now = datetime.now(timezone.utc)
        past = (now - timedelta(hours=1)).isoformat()
        rows = [_row(id=1, target_pages="all", expires_at=past)]
        assert len(filter_active_notifications(rows, "/jobs", set(), now)) == 0

    def test_dismissed_id_filtered_out(self):
        rows = [_row(id=7, target_pages="all")]
        now = datetime.now(timezone.utc)
        assert len(filter_active_notifications(rows, "/jobs", {7}, now)) == 0
        assert len(filter_active_notifications(rows, "/jobs", {8}, now)) == 1

    def test_empty_rows_returns_empty(self):
        assert filter_active_notifications([], "/jobs", set(), datetime.now(timezone.utc)) == []

    def test_multiple_notifications_all_returned_when_all_match(self):
        rows = [_row(id=1, target_pages="all"), _row(id=2, target_pages="jobs")]
        now = datetime.now(timezone.utc)
        result = filter_active_notifications(rows, "/jobs", set(), now)
        assert {r["id"] for r in result} == {1, 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notifications.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.notifications'`.

- [ ] **Step 3: Add the `notifications` table to the operational migration**

In `src/storage/db.py`, inside `_run_operational_migrations_impl()` (the `conn.executescript("""...""")` call starting at line ~271), add a new `CREATE TABLE` block after the existing `pipeline_config` table definition, still inside the same executescript string:

```sql
        CREATE TABLE IF NOT EXISTS notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            heading      TEXT NOT NULL,
            body         TEXT NOT NULL,
            severity     TEXT NOT NULL DEFAULT 'info',
            target_pages TEXT NOT NULL DEFAULT 'all',
            created_at   TEXT NOT NULL,
            expires_at   TEXT,
            removed_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_active ON notifications(removed_at, expires_at);
```

(Read the current exact content of `_run_operational_migrations_impl()` first — it already has `pipeline_runs` and `pipeline_config` `CREATE TABLE` statements plus a `defaults` seeding loop inside one `conn.executescript()` call. Insert the new `CREATE TABLE`/`CREATE INDEX` lines inside that same script string, after `pipeline_config`'s closing `);` and before the script string's closing `"""`. Do not touch the `defaults` loop below it — `notifications` has no config defaults to seed.)

- [ ] **Step 4: Create `src/notifications.py`**

```python
"""
src/notifications.py
──────────────────────
Admin-authored announcement bars, shown to every visitor (including
anonymous ones) on some or all pages. Storage lives in operational.sqlite
(src.storage.db.get_operational_connection()) alongside pipeline_config/
pipeline_runs - this is admin/operational state, not job data.

page_key_for_path() and filter_active_notifications() are pure functions -
no Flask, no I/O - so the filtering logic is testable without a request
context, matching the same separation already used by
src.classification.scheduling.should_process_chunk().
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

PAGE_KEYS: tuple[str, ...] = (
    "dashboard", "jobs", "skills", "companies", "titles", "metrics", "api_docs",
)

_PATH_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/dashboard", "dashboard"),
    ("/jobs", "jobs"),
    ("/skills", "skills"),
    ("/companies", "companies"),
    ("/titles", "titles"),
    ("/metrics", "metrics"),
    ("/api/docs", "api_docs"),
)


def page_key_for_path(path: str) -> str | None:
    """Maps a request path to one of PAGE_KEYS, or None if the path isn't
    in any targetable section (e.g. /admin/*, /auth/*, /healthz)."""
    if path == "/":
        return "dashboard"
    for prefix, key in _PATH_PREFIXES:
        if path.startswith(prefix):
            return key
    return None


def filter_active_notifications(
    rows: list,
    path: str,
    dismissed_ids: set[int],
    now: datetime,
) -> list:
    """rows: notifications table rows already filtered to removed_at IS NULL
    by the caller's SQL query (see load_active_notifications() below) - this
    function only handles page-matching, expiry, and dismissal, the parts
    that need `path`/`now`/`dismissed_ids` rather than a plain WHERE clause."""
    page_key = page_key_for_path(path)
    result = []
    for row in rows:
        if row["id"] in dismissed_ids:
            continue
        if row["expires_at"]:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                from datetime import timezone as _tz
                expires_at = expires_at.replace(tzinfo=_tz.utc)
            if now >= expires_at:
                continue
        targets = row["target_pages"]
        if targets == "all":
            result.append(row)
        elif page_key and page_key in targets.split(","):
            result.append(row)
    return result


def load_active_notifications(path: str, dismissed_ids: set[int], now: datetime) -> list:
    """Query + filter in one call - the function web_viewer.py's
    before_request hook actually calls."""
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    try:
        rows = conn.execute(
            "SELECT id, heading, body, severity, target_pages, expires_at FROM notifications WHERE removed_at IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return filter_active_notifications(rows, path, dismissed_ids, now)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_notifications.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (20 tests).

- [ ] **Step 6: Add a migration test**

Add to `tests/test_notifications.py` (reuse the same `isolated_paths`-style fixture already established in `tests/test_db_rotation_paths.py` — read that file's fixture first and copy its shape exactly, since this test needs `db.run_migrations()` to actually create the table):

```python
import src.storage.db as db


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    db.run_migrations()
    return tmp_path


def test_notifications_table_created_in_operational_db(isolated_paths):
    conn = db.get_operational_connection()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "notifications" in tables


def test_load_active_notifications_reads_from_operational_db(isolated_paths):
    from datetime import datetime, timezone
    from src.notifications import load_active_notifications

    conn = db.get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at) VALUES (?,?,?,?,?)",
        ("Maintenance", "Site will be down briefly.", "warning", "all", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    result = load_active_notifications("/jobs", set(), datetime.now(timezone.utc))
    assert len(result) == 1
    assert result[0]["heading"] == "Maintenance"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_notifications.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (22 tests).

- [ ] **Step 8: Commit**

```bash
git add src/storage/db.py src/notifications.py tests/test_notifications.py
git commit -m "feat: add notifications table and page-matching/expiry/dismissal filtering logic"
```

---

### Task 2: Rendering — before_request hook, template partial, base.html wiring, dismissal

**Files:**
- Modify: `web_viewer.py` (new `before_request` hook near `_track_last_request_at` at line ~204; imports for `page_key_for_path`/`load_active_notifications` from Task 1)
- Modify: `templates/base.html` (new CSS block, new include point before `<header>`)
- Create: `templates/_notifications.html`
- Test: `tests/test_notifications_rendering.py`

**Interfaces:**
- Consumes: `src.notifications.load_active_notifications(path, dismissed_ids, now) -> list` (Task 1).
- Produces: `g.active_notifications` — populated once per request by the new `before_request` hook, a list of dict-like rows with keys `id`, `heading`, `body`, `severity`, `target_pages`, `expires_at`.
- Produces: `dismissNotification(id)` — global JS function, sets/reads the `jmi_dismissed` cookie.

- [ ] **Step 1: Write the failing test**

Create `tests/test_notifications_rendering.py` (reuse the `isolated_paths` fixture pattern from `tests/test_db_rotation_paths.py` again, plus a minimal Flask test client — read `tests/test_public_viewable_routes.py`'s `anon_client` fixture first for the exact `jobs`/`active_jobs` schema a real page route needs to render without erroring, and copy that shape):

```python
"""
tests/test_notifications_rendering.py
────────────────────────────────────────
End-to-end (via Flask test client) proof that an admin-created notification
actually appears on a targeted page's rendered HTML, does not appear on an
untargeted page, and is excluded once its id is in the jmi_dismissed cookie.
"""
import sqlite3
from datetime import datetime, timezone

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
    """)
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", db_path)
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", db_path)
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", db_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()

    import src.storage.db as db
    db.run_migrations()

    return web_viewer.app.test_client()


def _seed_notification(heading="Maintenance tonight", target_pages="all", expires_at=None):
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (heading, "Details here.", "warning", target_pages, datetime.now(timezone.utc).isoformat(), expires_at),
    )
    conn.commit()
    row_id = conn.execute("SELECT id FROM notifications WHERE heading = ?", (heading,)).fetchone()[0]
    conn.close()
    return row_id


def test_all_pages_notification_appears_on_jobs_page(anon_client):
    _seed_notification(target_pages="all")
    resp = anon_client.get("/jobs")
    assert resp.status_code == 200
    assert b"Maintenance tonight" in resp.data


def test_page_specific_notification_does_not_appear_on_untargeted_page(anon_client):
    _seed_notification(heading="Jobs-only notice", target_pages="jobs")
    resp = anon_client.get("/dashboard")
    assert b"Jobs-only notice" not in resp.data


def test_page_specific_notification_appears_on_targeted_page(anon_client):
    _seed_notification(heading="Jobs-only notice", target_pages="jobs")
    resp = anon_client.get("/jobs")
    assert b"Jobs-only notice" in resp.data


def test_dismissed_notification_does_not_appear(anon_client):
    row_id = _seed_notification(heading="Dismiss me")
    anon_client.set_cookie("jmi_dismissed", str(row_id))
    resp = anon_client.get("/jobs")
    assert b"Dismiss me" not in resp.data


def test_notification_without_dismiss_cookie_appears(anon_client):
    _seed_notification(heading="Not dismissed")
    resp = anon_client.get("/jobs")
    assert b"Not dismissed" in resp.data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notifications_rendering.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — `b"Maintenance tonight" not in resp.data` (nothing renders it yet).

- [ ] **Step 3: Add the `before_request` hook in `web_viewer.py`**

Read the existing `_track_last_request_at()` hook first (around line 204-211) for the exact decorator/registration shape. Add a new hook directly after it, before the `init_auth_db()` call:

```python
@app.before_request
def _load_active_notifications():
    if request.path == "/healthz" or request.path.startswith("/static/"):
        g.active_notifications = []
        return
    from datetime import datetime, timezone
    from src.notifications import load_active_notifications

    dismissed_raw = request.cookies.get("jmi_dismissed", "")
    dismissed_ids = {int(x) for x in dismissed_raw.split(",") if x.strip().isdigit()}
    g.active_notifications = load_active_notifications(
        request.path, dismissed_ids, datetime.now(timezone.utc)
    )
```

- [ ] **Step 4: Create `templates/_notifications.html`**

```html
{% for n in g.active_notifications %}
<div class="notification-bar notification-{{ n.severity }}" data-notification-id="{{ n.id }}">
  <div class="container notification-bar-inner">
    <div class="notification-text"><strong>{{ n.heading }}</strong> {{ n.body }}</div>
    <button class="notification-close" onclick="dismissNotification({{ n.id }})" aria-label="Dismiss">&times;</button>
  </div>
</div>
{% endfor %}
```

- [ ] **Step 5: Wire the partial into `base.html`**

In `templates/base.html`, add the include immediately before the `<header>` opening tag (currently right after `{% block filter_sidebar %}{% endblock %}`):

```html
    {% block filter_sidebar %}{% endblock %}

    {% include "_notifications.html" %}

    <header>
```

Add the CSS block inside `base.html`'s existing `<style>` section (anywhere after the `:root`/`[data-theme="dark"]` custom-property blocks, e.g. right after the `/* ─── Header ─── */` section):

```css
        /* ─── Notification bar ───────────────────────────────────────── */
        .notification-bar { padding: 10px 0; }
        .notification-bar-inner {
            display: flex; align-items: center; justify-content: space-between;
            gap: 12px; font-size: 13px;
        }
        .notification-text { flex: 1; }
        .notification-info    { background: var(--accent-bg);  color: var(--accent);  }
        .notification-warning { background: var(--warning-bg); color: var(--warning); }
        .notification-urgent  { background: var(--danger-bg);  color: var(--danger);  }
        .notification-close {
            background: none; border: none; cursor: pointer;
            font-size: 18px; line-height: 1; color: inherit; padding: 0 4px;
        }
```

Add the dismissal JS in `base.html`'s existing final `<script>` block (right after the `window.toggleTheme` function):

```html
    <script>
    window.dismissNotification = function(id){
        var el = document.querySelector('[data-notification-id="' + id + '"]');
        if (el) el.remove();
        var match = document.cookie.match(/jmi_dismissed=([^;]*)/);
        var ids = match ? match[1].split(',').filter(Boolean) : [];
        if (ids.indexOf(String(id)) === -1) ids.push(String(id));
        document.cookie = 'jmi_dismissed=' + ids.join(',') + ';path=/;max-age=2592000;SameSite=Lax';
    };
    </script>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_notifications_rendering.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (5 tests).

- [ ] **Step 7: Run the full suite to confirm no regressions**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp --ignore=tests/test_auth_security.py`
Expected: all pass (the one pre-existing unrelated `test_auth_security.py::test_login_rejects_external_next_target` failure is a documented baseline, not a regression).

- [ ] **Step 8: Commit**

```bash
git add web_viewer.py templates/base.html templates/_notifications.html tests/test_notifications_rendering.py
git commit -m "feat: render active notifications as dismissible bars on every page"
```

---

### Task 3: Admin UI — create/list/remove

**Files:**
- Modify: `web_viewer.py` (3 new routes; nav card added to `admin_dashboard()`'s template context is not needed — the card is static HTML in the template)
- Modify: `templates/admin_dashboard.html` (new nav card, matching the "Pipeline Monitor"/"Classification Pipeline" cards' exact style)
- Create: `templates/admin_notifications.html`
- Test: `tests/test_admin_notifications_routes.py`

**Interfaces:**
- Consumes: `src.notifications.PAGE_KEYS` (Task 1), `src.storage.db.get_operational_connection()` (pre-existing).
- No new interfaces produced — this is the top-level management layer.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_notifications_routes.py` (copy the `admin_client` fixture shape from `tests/test_admin_classification_routes.py` exactly — same rotation-path monkeypatches, same auth-session setup — but this fixture only needs the `notifications` table plus auth, so run `db.run_migrations()` against the isolated paths rather than hand-rolling a minimal schema):

```python
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    import src.storage.db as db

    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    db.run_migrations()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", tmp_path / "serving_a.sqlite")
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()

    import src.auth.models as models
    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()

    client = web_viewer.app.test_client()
    admin_id = next(u["id"] for u in models.list_users() if u["username"] == "admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["_csrf_token"] = "test-csrf"
    return client


def test_dashboard_requires_admin(tmp_path, monkeypatch):
    import src.storage.db as db
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    db.run_migrations()

    import web_viewer
    web_viewer.app.config.update(TESTING=True)
    client = web_viewer.app.test_client()
    r = client.get("/admin/notifications", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_dashboard_renders(admin_client):
    r = admin_client.get("/admin/notifications")
    assert r.status_code == 200


def test_create_inserts_a_row(admin_client):
    r = admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Scheduled maintenance",
        "body": "The site will be briefly unavailable at 2am UTC.",
        "severity": "warning",
        "target_pages": "all",
    })
    assert r.status_code in (200, 302)

    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT heading, severity, target_pages, removed_at FROM notifications").fetchone()
    conn.close()
    assert row["heading"] == "Scheduled maintenance"
    assert row["severity"] == "warning"
    assert row["target_pages"] == "all"
    assert row["removed_at"] is None


def test_create_with_specific_pages_joins_them_with_commas(admin_client):
    admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Jobs page notice",
        "body": "Body text.",
        "severity": "info",
        "target_pages": "jobs",
        "pages": ["jobs", "dashboard"],
    })
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT target_pages FROM notifications WHERE heading = ?", ("Jobs page notice",)).fetchone()
    conn.close()
    assert set(row["target_pages"].split(",")) == {"jobs", "dashboard"}


def test_create_with_expiry_hours_sets_absolute_expires_at(admin_client):
    admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Temporary notice",
        "body": "Body text.",
        "severity": "info",
        "target_pages": "all",
        "expires_in_hours": "2",
    })
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT expires_at FROM notifications WHERE heading = ?", ("Temporary notice",)).fetchone()
    conn.close()
    assert row["expires_at"] is not None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert now + timedelta(hours=1, minutes=55) < expires_at < now + timedelta(hours=2, minutes=5)


def test_create_without_expiry_leaves_expires_at_null(admin_client):
    admin_client.post("/admin/notifications/create", data={
        "csrf_token": "test-csrf",
        "heading": "Permanent-ish notice",
        "body": "Body text.",
        "severity": "info",
        "target_pages": "all",
    })
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    row = conn.execute("SELECT expires_at FROM notifications WHERE heading = ?", ("Permanent-ish notice",)).fetchone()
    conn.close()
    assert row["expires_at"] is None


def test_remove_sets_removed_at(admin_client):
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at) VALUES (?,?,?,?,?)",
        ("To be removed", "Body.", "info", "all", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    row_id = conn.execute("SELECT id FROM notifications WHERE heading = ?", ("To be removed",)).fetchone()["id"]
    conn.close()

    r = admin_client.post(f"/admin/notifications/{row_id}/remove", data={"csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    conn = get_operational_connection()
    row = conn.execute("SELECT removed_at FROM notifications WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    assert row["removed_at"] is not None


def test_removed_notification_stops_appearing_in_active_list(admin_client):
    from src.notifications import load_active_notifications
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at) VALUES (?,?,?,?,?)",
        ("Active then removed", "Body.", "info", "all", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    row_id = conn.execute("SELECT id FROM notifications WHERE heading = ?", ("Active then removed",)).fetchone()["id"]
    conn.close()

    admin_client.post(f"/admin/notifications/{row_id}/remove", data={"csrf_token": "test-csrf"})

    result = load_active_notifications("/jobs", set(), datetime.now(timezone.utc))
    assert row_id not in {r["id"] for r in result}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_notifications_routes.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — 404s (routes don't exist yet).

- [ ] **Step 3: Add the three routes in `web_viewer.py`**

Add these routes right after the existing `/admin/pipeline*` routes (or any other natural location among the admin routes — read the surrounding admin route organization first and place them in a clearly-labeled `# ═══ ADMIN: NOTIFICATIONS ═══` section, matching the `# ═══ ADMIN: PIPELINE MONITOR ═══` / `# ═══ ADMIN: CLASSIFICATION PIPELINE ═══` section-comment convention already used in this file):

```python
@app.route("/admin/notifications")
@require_admin
def admin_notifications():
    from src.notifications import PAGE_KEYS
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    rows = conn.execute(
        "SELECT * FROM notifications ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("admin_notifications.html", notifications=rows, page_keys=PAGE_KEYS)


@app.route("/admin/notifications/create", methods=["POST"])
@require_admin
def admin_notifications_create():
    from datetime import datetime, timedelta, timezone
    from src.notifications import PAGE_KEYS
    from src.storage.db import get_operational_connection

    heading = request.form.get("heading", "").strip()
    body = request.form.get("body", "").strip()
    severity = request.form.get("severity", "info")
    if severity not in ("info", "warning", "urgent"):
        severity = "info"

    all_pages = request.form.get("target_pages") == "all"
    if all_pages:
        target_pages = "all"
    else:
        selected = [p for p in request.form.getlist("pages") if p in PAGE_KEYS]
        target_pages = ",".join(selected) if selected else "all"

    expires_at = None
    hours_raw = request.form.get("expires_in_hours", "").strip()
    if hours_raw:
        try:
            hours = float(hours_raw)
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        except ValueError:
            pass

    if not heading or not body:
        return jsonify({"error": "heading and body are required"}), 400

    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (heading, body, severity, target_pages, datetime.now(timezone.utc).isoformat(), expires_at),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_notifications"))


@app.route("/admin/notifications/<int:notification_id>/remove", methods=["POST"])
@require_admin
def admin_notifications_remove(notification_id: int):
    from datetime import datetime, timezone
    from src.storage.db import get_operational_connection

    conn = get_operational_connection()
    conn.execute(
        "UPDATE notifications SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), notification_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_notifications"))
```

Note: `test_create_with_specific_pages_joins_them_with_commas` posts BOTH `target_pages: "jobs"` (a leftover single value, simulating a form that always sends the radio/checkbox state) AND `pages: ["jobs", "dashboard"]` (the actual multi-select) — the route reads `request.form.get("target_pages") == "all"` to decide the ALL-vs-SPECIFIC branch, and only reads `request.form.getlist("pages")` in the specific-pages branch, so this combination correctly exercises the specific-pages path. The real form (Step 5) never sends `target_pages=all` when specific checkboxes are checked, since the "All pages" checkbox and the per-page checkboxes are mutually exclusive in the UI — the JS in Step 5 handles this.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_notifications_routes.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (9 tests).

- [ ] **Step 5: Create `templates/admin_notifications.html`**

Read `templates/admin_pipeline.html` in full first and match its exact card/table/button/JS conventions (the `csrf` JS constant, the `fetch()` + `FormData` pattern, the `.card`/`.run-btn`-equivalent styling). Then write:

```html
{% extends "base.html" %}
{% block title %}Notifications - Admin{% endblock %}
{% block content %}
<div class="container" style="max-width:1100px">

  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
    <div>
      <h1 style="margin:0">Notifications</h1>
      <p style="color:#6b7280;margin:0.25rem 0 0">Site-wide or page-targeted announcement bars</p>
    </div>
    <a href="/admin" class="btn" style="background:#f3f4f6;color:#374151;text-decoration:none">← Admin</a>
  </div>

  <div class="card" style="margin-bottom:1.5rem">
    <h3 style="margin:0 0 1rem">New Notification</h3>
    <form id="create-form">
      <div class="form-group">
        <label>Heading</label>
        <input class="form-control" name="heading" required maxlength="120">
      </div>
      <div class="form-group">
        <label>Body</label>
        <textarea class="form-control" name="body" required rows="2" maxlength="500"></textarea>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">
        <div class="form-group" style="margin-bottom:0">
          <label>Severity</label>
          <select class="form-control" name="severity">
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="urgent">Urgent</option>
          </select>
        </div>
        <div class="form-group" style="margin-bottom:0">
          <label>Expires in (hours, optional)</label>
          <input class="form-control" type="number" name="expires_in_hours" min="1" placeholder="Leave blank for no expiry">
        </div>
      </div>
      <div class="form-group">
        <label style="display:flex;align-items:center;gap:0.5rem">
          <input type="checkbox" id="all-pages-toggle" checked> All pages
        </label>
        <div id="page-checkboxes" style="display:none;margin-top:0.5rem;display:grid;grid-template-columns:repeat(4,1fr);gap:0.5rem">
          {% for key in page_keys %}
          <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.85rem">
            <input type="checkbox" name="pages" value="{{ key }}"> {{ key }}
          </label>
          {% endfor %}
        </div>
      </div>
      <button type="submit" class="btn btn-primary">Create</button>
      <span id="create-msg" style="font-size:0.8rem;margin-left:0.5rem"></span>
    </form>
  </div>

  <div class="card">
    <h3 style="margin:0 0 1rem">All Notifications</h3>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Heading</th><th>Severity</th><th>Target</th><th>Created</th><th>Expires</th><th>Status</th><th></th>
        </tr></thead>
        <tbody>
        {% for n in notifications %}
        <tr>
          <td>{{ n.heading }}</td>
          <td><span class="badge badge-{{ 'danger' if n.severity == 'urgent' else n.severity if n.severity == 'warning' else 'info' }}">{{ n.severity }}</span></td>
          <td>{{ n.target_pages }}</td>
          <td><time data-utc="{{ n.created_at }}">{{ n.created_at }}</time></td>
          <td>{% if n.expires_at %}<time data-utc="{{ n.expires_at }}">{{ n.expires_at }}</time>{% else %}—{% endif %}</td>
          <td>
            {% if n.removed_at %}<span class="badge badge-danger">removed</span>
            {% else %}<span class="badge badge-success">active</span>{% endif %}
          </td>
          <td>
            {% if not n.removed_at %}
            <button class="btn btn-secondary btn-sm remove-btn" data-id="{{ n.id }}">Remove now</button>
            {% endif %}
          </td>
        </tr>
        {% else %}
        <tr><td colspan="7" style="padding:2rem;text-align:center;color:#9ca3af">No notifications yet.</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
const csrf = "{{ csrf_token() }}";

document.getElementById('all-pages-toggle').addEventListener('change', (e) => {
  document.getElementById('page-checkboxes').style.display = e.target.checked ? 'none' : 'grid';
});

document.getElementById('create-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  fd.append('csrf_token', csrf);
  fd.append('target_pages', document.getElementById('all-pages-toggle').checked ? 'all' : 'specific');
  const msg = document.getElementById('create-msg');
  const r = await fetch('/admin/notifications/create', { method: 'POST', body: fd });
  if (r.ok) {
    location.reload();
  } else {
    msg.style.color = '#dc2626';
    msg.textContent = 'Failed to create notification';
  }
});

document.querySelectorAll('.remove-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const fd = new FormData();
    fd.append('csrf_token', csrf);
    const r = await fetch(`/admin/notifications/${btn.dataset.id}/remove`, { method: 'POST', body: fd });
    if (r.ok) location.reload();
  });
});
</script>
{% endblock %}
```

- [ ] **Step 6: Add the nav card to `templates/admin_dashboard.html`**

Add a new card matching the exact style of the existing "Pipeline Monitor" card (right after the "Classification Pipeline" card, before "Display Settings"):

```html
        <!-- Notifications -->
        <div class="card" style="cursor: pointer; transition: transform 0.2s;" onclick="window.location.href='/admin/notifications'">
            <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;">
                <div style="font-size: 3rem;">📢</div>
                <div>
                    <h2 style="margin: 0; color: #1f2937;">Notifications</h2>
                    <p style="color: #6b7280; margin: 0.5rem 0 0 0; font-size: 0.875rem;">
                        Post site-wide or page-targeted announcement bars
                    </p>
                </div>
            </div>
            <div style="margin-top: 1.5rem;">
                <a href="/admin/notifications" class="btn" style="display: inline-block; width: 100%; text-align: center; text-decoration: none;">
                    Open Notifications →
                </a>
            </div>
        </div>
```

- [ ] **Step 7: Manually verify the page renders**

Start the dev server (check `docs/SETUP_AND_OPERATIONS.md` or run `python web_viewer.py` if that's the established local entrypoint), sign in as admin, open `/admin/notifications`, confirm:
- The create form renders with all fields.
- Unchecking "All pages" reveals the 7 page checkboxes.
- Creating a notification adds it to the table below and it appears as a bar when visiting `/jobs`.
- Clicking "Remove now" removes it from the bar on next page load and marks it "removed" in the table.
- Clicking the × on a rendered bar hides it immediately and it stays hidden on a page refresh (dismissal cookie).

- [ ] **Step 8: Run the full suite one final time**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp --ignore=tests/test_auth_security.py`
Expected: all pass (same one documented pre-existing baseline failure aside).

- [ ] **Step 9: Commit**

```bash
git add web_viewer.py templates/admin_notifications.html templates/admin_dashboard.html tests/test_admin_notifications_routes.py
git commit -m "feat: add admin notifications management page (create/list/remove)"
```
