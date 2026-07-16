# "Report This Listing" Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any visitor, signed-in or anonymous, can report a problem with a specific job from its detail page; reports save to a new `job_reports` table; an admin reviews and resolves/dismisses them from a new `/admin/reports` page.

**Architecture:** Follows the exact shape already established by the admin notification bar feature — a small operational table (`job_reports` in `operational.sqlite`), a pure Python module for validation/insert logic (mirroring `src/notifications.py`'s separation of pure logic from Flask I/O), a public submission route, and admin CRUD-style routes matching `/admin/notifications`'s exact structure (form POST + redirect, `validate_csrf()`, `cache.clear()` on mutate).

**Tech Stack:** Flask, SQLite, vanilla JS (fetch for the submission form only — the admin actions use plain form POSTs, matching the notifications precedent).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every pytest invocation must use `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`.
- Any new test fixture that touches DB paths must patch ALL of `src.storage.db._SERVING_A_PATH`, `_SERVING_B_PATH`, `_BUFFER_DB_PATH`, `_OPERATIONAL_DB_PATH`, `_POINTER_PATH` together when it calls `db.run_migrations()` or otherwise exercises real connection resolution.
- `job_reports` lives in `operational.sqlite` (`get_operational_connection()`), never the rotating Serving/Free files — it's admin/operational state about a job, not job data itself.
- CSRF: `validate_csrf()` (`src/auth/middleware.py`) accepts either the `X-CSRF-Token` header OR a `_csrf_token` form field (exact name, with the leading underscore) — never a plain `csrf_token` field, that mismatch broke CSRF entirely on a different admin page earlier this session.
- Reason categories are exactly these five string values: `incorrect_info`, `wrong_category`, `broken_link`, `spam`, `other`. `details` is required (non-empty after `.strip()`) when `reason_category == "other"`, optional otherwise.
- Rate limit: reject a new report if the same `reporter_ip` has submitted 5 or more reports in the last hour.
- Mobile responsiveness: both the report form (job_detail.html) and the admin reports table (admin_reports.html) must remain usable on narrow (~360-400px) viewports — no fixed pixel widths that overflow, inputs/selects at `width:100%` with `box-sizing:border-box`, and any table wrapped in a horizontally-scrollable container rather than left to overflow the page. This app has no dedicated mobile stylesheet today, so these two templates should stay self-contained (inline/scoped styles) and not assume one exists.

---

### Task 1: `job_reports` table + pure submission logic

**Files:**
- Modify: `src/storage/db.py` (`_run_operational_migrations_impl()` — add the `job_reports` table migration, same idempotent pattern as `notifications`)
- Create: `src/job_reports.py` (pure validation logic + the actual insert, mirroring `src/notifications.py`'s shape)
- Test: `tests/test_job_reports.py`

**Interfaces:**
- Produces: `REASON_CATEGORIES: tuple[str, ...]` = `("incorrect_info", "wrong_category", "broken_link", "spam", "other")`.
- Produces: `validate_report_input(reason_category: str, details: str) -> str | None` — pure function, no I/O. Returns an error message string if invalid (unrecognized category, or `details` blank when category is `"other"`), else `None`.
- Produces: `is_rate_limited(conn, reporter_ip: str, now: datetime) -> bool` — queries `job_reports` for the given connection, returns `True` if `reporter_ip` has 5+ rows with `created_at` within the last hour of `now`.
- Produces: `create_report(conn, *, job_id: int | None, job_url: str, job_title: str, reason_category: str, details: str, reporter_user_id: int | None, reporter_email: str | None, reporter_ip: str, now: datetime) -> int` — inserts a row, returns the new `report_id`. Does NOT itself call `validate_report_input` or `is_rate_limited` — those are the caller's (the route's, in Task 2) responsibility to check first; this function just performs the insert once the caller has already decided it's valid.

- [ ] **Step 1: Read the exact current migration pattern first**

Read `src/storage/db.py`'s `_run_operational_migrations_impl()` in full, specifically the `notifications` table's `CREATE TABLE IF NOT EXISTS` block (added 2026-07-15), to match its exact style (column formatting, index creation) for the new table.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_job_reports.py`:

```python
"""
tests/test_job_reports.py
─────────────────────────────
Regression coverage for src/job_reports.py's pure logic - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.job_reports import REASON_CATEGORIES, is_rate_limited, validate_report_input


def test_reason_categories_are_the_five_agreed_values():
    assert REASON_CATEGORIES == ("incorrect_info", "wrong_category", "broken_link", "spam", "other")


def test_validate_accepts_predefined_category_with_no_details():
    assert validate_report_input("incorrect_info", "") is None


def test_validate_rejects_unrecognized_category():
    err = validate_report_input("not_a_real_category", "")
    assert err is not None


def test_validate_requires_details_for_other_category():
    err = validate_report_input("other", "")
    assert err is not None
    assert validate_report_input("other", "   ") is not None  # whitespace-only also rejected


def test_validate_accepts_other_category_with_real_details():
    assert validate_report_input("other", "The salary listed doesn't match the linked posting") is None


@pytest.fixture()
def reports_conn(tmp_path, monkeypatch):
    monkeypatch.setattr("src.storage.db._DATA_DIR", tmp_path)
    monkeypatch.setattr("src.storage.db._OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr("src.storage.db._SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr("src.storage.db._SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr("src.storage.db._BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr("src.storage.db._POINTER_PATH", tmp_path / "serving_pointer.txt")
    from src.storage.db import get_operational_connection, run_migrations
    run_migrations()
    conn = get_operational_connection()
    yield conn
    conn.close()


def test_create_report_inserts_and_returns_id(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    report_id = create_report(
        reports_conn, job_id=42, job_url="https://example.com/job/42", job_title="Backend Engineer",
        reason_category="incorrect_info", details="Salary is wrong", reporter_user_id=None,
        reporter_email="test@example.com", reporter_ip="127.0.0.1", now=now,
    )
    assert isinstance(report_id, int)
    row = reports_conn.execute("SELECT * FROM job_reports WHERE report_id = ?", (report_id,)).fetchone()
    assert row["job_title"] == "Backend Engineer"
    assert row["status"] == "open"
    assert row["reporter_email"] == "test@example.com"


def test_is_rate_limited_false_under_threshold(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    for _ in range(4):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(reports_conn, "9.9.9.9", now) is False


def test_is_rate_limited_true_at_threshold(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    for _ in range(5):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(reports_conn, "9.9.9.9", now) is True


def test_is_rate_limited_ignores_reports_older_than_an_hour(reports_conn):
    from src.job_reports import create_report
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    now = datetime.now(timezone.utc)
    for _ in range(5):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="9.9.9.9", now=old,
        )
    assert is_rate_limited(reports_conn, "9.9.9.9", now) is False


def test_is_rate_limited_scoped_per_ip(reports_conn):
    from src.job_reports import create_report
    now = datetime.now(timezone.utc)
    for _ in range(5):
        create_report(
            reports_conn, job_id=1, job_url="https://x/1", job_title="T",
            reason_category="spam", details="", reporter_user_id=None,
            reporter_email=None, reporter_ip="1.1.1.1", now=now,
        )
    assert is_rate_limited(reports_conn, "2.2.2.2", now) is False  # different IP, unaffected
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_job_reports.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — `src.job_reports` doesn't exist yet.

- [ ] **Step 4: Add the migration**

In `src/storage/db.py`'s `_run_operational_migrations_impl()`, add (matching the exact idempotent style already used for `notifications`):

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_reports (
            report_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id           INTEGER,
            job_url          TEXT NOT NULL,
            job_title        TEXT NOT NULL,
            reason_category  TEXT NOT NULL,
            details          TEXT,
            reporter_user_id INTEGER,
            reporter_email   TEXT,
            reporter_ip      TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'open',
            admin_notes      TEXT,
            created_at       TEXT NOT NULL,
            resolved_at      TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_reports_status ON job_reports(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_reports_job_url ON job_reports(job_url)")
```

- [ ] **Step 5: Create `src/job_reports.py`**

```python
"""
src/job_reports.py
─────────────────────
Per-job "Report this listing" feature - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.
Storage lives in operational.sqlite (src.storage.db.get_operational_connection()),
same reasoning already applied to notifications - this is admin/operational
state about a job, not job data itself.

validate_report_input() and is_rate_limited() are pure/query-only checks the
caller (the Flask route) runs BEFORE calling create_report() - this module
doesn't enforce them itself, matching the same separation of pure logic
from I/O already used by src.notifications and
src.classification.scheduling.should_process_chunk().
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

REASON_CATEGORIES: tuple[str, ...] = (
    "incorrect_info", "wrong_category", "broken_link", "spam", "other",
)


def validate_report_input(reason_category: str, details: str) -> str | None:
    """Returns an error message if invalid, else None."""
    if reason_category not in REASON_CATEGORIES:
        return f"Unrecognized reason category: {reason_category!r}"
    if reason_category == "other" and not details.strip():
        return "Please provide details when selecting 'Other'"
    return None


def is_rate_limited(conn: sqlite3.Connection, reporter_ip: str, now: datetime) -> bool:
    cutoff = (now - timedelta(hours=1)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM job_reports WHERE reporter_ip = ? AND created_at >= ?",
        (reporter_ip, cutoff),
    ).fetchone()
    return row["n"] >= 5


def create_report(
    conn: sqlite3.Connection, *, job_id: int | None, job_url: str, job_title: str,
    reason_category: str, details: str, reporter_user_id: int | None,
    reporter_email: str | None, reporter_ip: str, now: datetime,
) -> int:
    cursor = conn.execute(
        """INSERT INTO job_reports
           (job_id, job_url, job_title, reason_category, details,
            reporter_user_id, reporter_email, reporter_ip, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (job_id, job_url, job_title, reason_category, details or None,
         reporter_user_id, reporter_email, reporter_ip, now.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_job_reports.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add src/storage/db.py src/job_reports.py tests/test_job_reports.py
git commit -m "feat: add job_reports table and pure submission/validation logic"
```

---

### Task 2: Submission route + job detail page UI

**Files:**
- Modify: `web_viewer.py` (new `POST /jobs/<int:job_id>/report` route)
- Modify: `templates/job_detail.html` (report link + inline form)
- Test: `tests/test_job_report_submission.py`

**Interfaces:**
- Consumes: `REASON_CATEGORIES`, `validate_report_input`, `is_rate_limited`, `create_report` from Task 1 (`src.job_reports`).

- [ ] **Step 1: Read the current exact job_detail.html structure**

Read `templates/job_detail.html` lines 100-125 (the `.job-header` block, ending with the `View original posting` link) to confirm the exact insertion point is still accurate.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_job_report_submission.py`:

```python
"""
tests/test_job_report_submission.py
───────────────────────────────────────
End-to-end coverage for POST /jobs/<job_id>/report - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.
"""
import sqlite3

import pytest


@pytest.fixture()
def report_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, url TEXT,
            listing_status TEXT DEFAULT 'active'
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.execute("INSERT INTO jobs (job_id, title, url) VALUES (1, 'Backend Engineer', 'https://example.com/job/1')")
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
    return web_viewer.app.test_client()


def _csrf_client(client):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    return client


def test_anonymous_visitor_can_submit_a_report(report_client):
    _csrf_client(report_client)
    r = report_client.post(
        "/jobs/1/report",
        data={"reason_category": "incorrect_info", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 200
    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT * FROM job_reports WHERE job_id = 1").fetchone()
    conn.close()
    assert row is not None
    assert row["reporter_user_id"] is None
    assert row["job_title"] == "Backend Engineer"


def test_missing_csrf_token_is_rejected(report_client):
    r = report_client.post("/jobs/1/report", data={"reason_category": "incorrect_info", "details": ""})
    assert r.status_code == 400


def test_other_category_without_details_is_rejected(report_client):
    _csrf_client(report_client)
    r = report_client.post(
        "/jobs/1/report",
        data={"reason_category": "other", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_unknown_job_id_returns_404(report_client):
    _csrf_client(report_client)
    r = report_client.post(
        "/jobs/9999/report",
        data={"reason_category": "spam", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 404


def test_sixth_report_from_same_ip_within_an_hour_is_rate_limited(report_client):
    _csrf_client(report_client)
    for _ in range(5):
        r = report_client.post(
            "/jobs/1/report",
            data={"reason_category": "spam", "details": ""},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert r.status_code == 200
    r = report_client.post(
        "/jobs/1/report",
        data={"reason_category": "spam", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 429
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_job_report_submission.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — the route doesn't exist yet (404 on every request).

- [ ] **Step 4: Add the submission route**

In `web_viewer.py`, add (near the other `/jobs/...` routes):

```python
@app.route("/jobs/<int:job_id>/report", methods=["POST"])
def submit_job_report(job_id):
    from src.auth.middleware import validate_csrf
    from src.job_reports import create_report, is_rate_limited, validate_report_input
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    conn = get_db_connection()
    job = conn.execute("SELECT job_id, title, url FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    if job is None:
        return jsonify({"error": "Job not found"}), 404

    reason_category = request.form.get("reason_category", "")
    details = request.form.get("details", "").strip()
    validation_error = validate_report_input(reason_category, details)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    reporter_ip = request.remote_addr or "unknown"
    op_conn = get_operational_connection()
    try:
        if is_rate_limited(op_conn, reporter_ip, datetime.now(timezone.utc)):
            return jsonify({"error": "Too many reports from this IP recently - please try again later"}), 429

        reporter_user_id = g.current_user["id"] if g.current_user else None
        reporter_email = None if g.current_user else (request.form.get("email", "").strip() or None)

        create_report(
            op_conn, job_id=job["job_id"], job_url=job["url"], job_title=job["title"],
            reason_category=reason_category, details=details,
            reporter_user_id=reporter_user_id, reporter_email=reporter_email,
            reporter_ip=reporter_ip, now=datetime.now(timezone.utc),
        )
    finally:
        op_conn.close()

    return jsonify({"status": "ok"})
```

Confirm `datetime`/`timezone` are already imported at module level in `web_viewer.py` (they are, used elsewhere) — no new top-level import needed for those two.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_job_report_submission.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 6: Add the UI to job_detail.html**

In `templates/job_detail.html`, immediately after the `{% if job.url %}...View original posting...{% endif %}` block (still inside `.job-header`), add:

```html
    <button type="button" class="btn-link" style="margin-top:0.5rem;font-size:0.85rem;color:var(--text-secondary);" onclick="document.getElementById('reportForm').style.display='block'; this.style.display='none';">
        {{ icons.flag(14) if icons.flag is defined else '' }} Report this listing
    </button>
    <div id="reportForm" style="display:none;margin-top:0.75rem;padding:1rem;border:1px solid var(--border-color);border-radius:8px;max-width:420px;box-sizing:border-box;">
        <label style="display:block;font-size:0.85rem;font-weight:600;margin-bottom:0.4rem;">Why are you reporting this listing?</label>
        <select id="reportReason" style="width:100%;margin-bottom:0.6rem;box-sizing:border-box;">
            <option value="incorrect_info">Incorrect information</option>
            <option value="wrong_category">Wrong category</option>
            <option value="broken_link">Broken or dead link</option>
            <option value="spam">Spam or not a real job</option>
            <option value="other">Other</option>
        </select>
        <textarea id="reportDetails" placeholder="Details (required for 'Other')" style="width:100%;margin-bottom:0.6rem;min-height:60px;box-sizing:border-box;"></textarea>
        {% if not g.current_user %}
        <input type="email" id="reportEmail" placeholder="Your email (optional, if you'd like a response)" style="width:100%;margin-bottom:0.6rem;box-sizing:border-box;">
        {% endif %}
        <div id="reportMsg" style="font-size:0.8rem;margin-bottom:0.5rem;"></div>
        <button type="button" class="btn" onclick="submitJobReport({{ job.job_id }})">Submit Report</button>
    </div>
    <script>
    function submitJobReport(jobId) {
        var reason = document.getElementById('reportReason').value;
        var details = document.getElementById('reportDetails').value;
        var emailEl = document.getElementById('reportEmail');
        var body = new URLSearchParams({reason_category: reason, details: details});
        if (emailEl) body.append('email', emailEl.value);
        fetch('/jobs/' + jobId + '/report', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': '{{ session.get("_csrf_token", "") }}'},
            body: body,
        })
        .then(function(r) { return r.json().then(function(data) { return {ok: r.ok, data: data}; }); })
        .then(function(result) {
            var msg = document.getElementById('reportMsg');
            if (result.ok) {
                msg.textContent = "Thanks - we'll review this.";
                msg.style.color = 'var(--accent-color, green)';
            } else {
                msg.textContent = result.data.error || 'Something went wrong.';
                msg.style.color = 'red';
            }
        });
    }
    </script>
```

If `icons.flag` doesn't exist in `templates/_icons.html`, drop the icon call entirely (`{{ icons.flag(14) if icons.flag is defined else '' }}` already guards against this safely, but check `templates/_icons.html` and add a simple flag icon macro there if it's easy to match the existing icon style — otherwise the guarded fallback (empty string, just the text label) is a perfectly fine outcome and no icon file change is required).

- [ ] **Step 7: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before plus the new tests, one known pre-existing failure (`test_login_rejects_external_next_target`).

- [ ] **Step 8: Commit**

```bash
git add web_viewer.py templates/job_detail.html tests/test_job_report_submission.py
git commit -m "feat: add job report submission route and job-detail-page UI"
```

---

### Task 3: Admin review page

**Files:**
- Modify: `web_viewer.py` (`GET /admin/reports`, `POST /admin/reports/<id>/resolve`, `POST /admin/reports/<id>/dismiss`)
- Create: `templates/admin_reports.html`
- Modify: `templates/admin_dashboard.html` (new nav card)
- Test: `tests/test_admin_reports_routes.py`

**Interfaces:**
- Consumes: the `job_reports` table from Task 1.

- [ ] **Step 1: Read the current exact admin_notifications.html and its nav card**

Read `templates/admin_notifications.html` in full and `templates/admin_dashboard.html`'s Notifications nav-card block (around line 263-279) to match styling exactly.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_admin_reports_routes.py`:

```python
"""
tests/test_admin_reports_routes.py
──────────────────────────────────────
Regression coverage for /admin/reports - see
docs/superpowers/specs/2026-07-16-job-report-feature-design.md.
"""
import sqlite3

import pytest


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (job_id INTEGER PRIMARY KEY, title TEXT, listing_status TEXT DEFAULT 'active')")
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
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

    from src.storage.db import get_operational_connection, run_migrations
    run_migrations()
    from datetime import datetime, timezone
    from src.job_reports import create_report
    op_conn = get_operational_connection()
    report_id = create_report(
        op_conn, job_id=1, job_url="https://example.com/1", job_title="Backend Engineer",
        reason_category="incorrect_info", details="Salary wrong", reporter_user_id=None,
        reporter_email=None, reporter_ip="1.2.3.4", now=datetime.now(timezone.utc),
    )
    op_conn.close()

    import src.auth.models as models
    from pathlib import Path
    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()

    client = web_viewer.app.test_client()
    client._seeded_report_id = report_id
    return client


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def test_reports_page_requires_admin(admin_client):
    r = admin_client.get("/admin/reports")
    assert r.status_code in (302, 401, 403)


def test_reports_page_lists_open_reports(admin_client):
    _login_admin(admin_client)
    r = admin_client.get("/admin/reports")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Backend Engineer" in body
    assert "Salary wrong" in body


def test_reports_page_defaults_to_open_only(admin_client):
    _login_admin(admin_client)
    import src.storage.db as db
    from src.job_reports import create_report
    from datetime import datetime, timezone
    op_conn = db.get_operational_connection()
    dismissed_id = create_report(
        op_conn, job_id=2, job_url="https://example.com/2", job_title="Frontend Engineer",
        reason_category="spam", details="", reporter_user_id=None,
        reporter_email=None, reporter_ip="5.5.5.5", now=datetime.now(timezone.utc),
    )
    op_conn.execute("UPDATE job_reports SET status = 'dismissed' WHERE report_id = ?", (dismissed_id,))
    op_conn.commit()
    op_conn.close()

    r = admin_client.get("/admin/reports")
    body = r.get_data(as_text=True)
    assert "Backend Engineer" in body  # the open, seeded report
    assert "Frontend Engineer" not in body  # dismissed, excluded by default


def test_reports_page_status_all_shows_everything(admin_client):
    _login_admin(admin_client)
    import src.storage.db as db
    from src.job_reports import create_report
    from datetime import datetime, timezone
    op_conn = db.get_operational_connection()
    dismissed_id = create_report(
        op_conn, job_id=2, job_url="https://example.com/2", job_title="Frontend Engineer",
        reason_category="spam", details="", reporter_user_id=None,
        reporter_email=None, reporter_ip="5.5.5.5", now=datetime.now(timezone.utc),
    )
    op_conn.execute("UPDATE job_reports SET status = 'dismissed' WHERE report_id = ?", (dismissed_id,))
    op_conn.commit()
    op_conn.close()

    r = admin_client.get("/admin/reports?status=all")
    body = r.get_data(as_text=True)
    assert "Backend Engineer" in body
    assert "Frontend Engineer" in body


def test_resolve_report_updates_status(admin_client):
    _login_admin(admin_client)
    report_id = admin_client._seeded_report_id
    r = admin_client.post(f"/admin/reports/{report_id}/resolve", data={"_csrf_token": "test-csrf", "admin_notes": "Fixed the salary"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status, admin_notes FROM job_reports WHERE report_id = ?", (report_id,)).fetchone()
    conn.close()
    assert row["status"] == "resolved"
    assert row["admin_notes"] == "Fixed the salary"


def test_dismiss_report_updates_status(admin_client):
    _login_admin(admin_client)
    report_id = admin_client._seeded_report_id
    r = admin_client.post(f"/admin/reports/{report_id}/dismiss", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status FROM job_reports WHERE report_id = ?", (report_id,)).fetchone()
    conn.close()
    assert row["status"] == "dismissed"
```

Note: adjust the admin-login fixture mechanics (`_login_admin`) to match whatever this codebase's actual established admin-session pattern is in `tests/test_admin_notifications_routes.py` or `tests/test_admin_classification_routes.py` if the simple `session["user_id"] = 1` shortcut used above doesn't actually satisfy `@require_admin` in practice — check one of those existing files' real login fixture and copy its exact mechanics if different from what's sketched here.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_admin_reports_routes.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — routes don't exist yet.

- [ ] **Step 4: Add the admin routes**

In `web_viewer.py`, matching `admin_notifications`/`admin_notifications_create`/`admin_notifications_remove`'s exact structure:

```python
@app.route("/admin/reports")
@require_admin
def admin_reports():
    from src.storage.db import get_operational_connection
    status = request.args.get("status", "open")
    conn = get_operational_connection()
    if status == "all":
        rows = conn.execute("SELECT * FROM job_reports ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM job_reports WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    conn.close()
    return render_template("admin_reports.html", reports=rows, current_status=status)


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@require_admin
def admin_reports_resolve(report_id):
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    admin_notes = request.form.get("admin_notes", "").strip()
    conn = get_operational_connection()
    conn.execute(
        "UPDATE job_reports SET status = 'resolved', admin_notes = ?, resolved_at = ? WHERE report_id = ? AND status = 'open'",
        (admin_notes or None, datetime.now(timezone.utc).isoformat(), report_id),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_reports"))


@app.route("/admin/reports/<int:report_id>/dismiss", methods=["POST"])
@require_admin
def admin_reports_dismiss(report_id):
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    conn = get_operational_connection()
    conn.execute(
        "UPDATE job_reports SET status = 'dismissed', resolved_at = ? WHERE report_id = ? AND status = 'open'",
        (datetime.now(timezone.utc).isoformat(), report_id),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_reports"))
```

- [ ] **Step 5: Create `templates/admin_reports.html`**

Copy `templates/admin_notifications.html`'s overall structure (extends the same base admin layout, same card/table conventions) and adapt the table columns to: Job (title, linked to `job_url`), Reason, Details, Reporter (username if `reporter_user_id` else "Anonymous" + email if present), Submitted, Status, and a per-row form with an `admin_notes` text input + "Resolve" button (`POST /admin/reports/<id>/resolve`) and a "Dismiss" button (`POST /admin/reports/<id>/dismiss`), each form including a hidden `<input type="hidden" name="_csrf_token" value="{{ session.get('_csrf_token', '') }}">` matching the CSRF-via-form-field convention `validate_csrf()` supports (the admin actions here use plain form POSTs, not fetch, matching the notifications precedent — don't introduce a header-based CSRF flow for these two routes since there's no fetch involved).

Add a simple status filter above the table: four links/tabs — Open (default, `/admin/reports`), Resolved (`/admin/reports?status=resolved`), Dismissed (`/admin/reports?status=dismissed`), All (`/admin/reports?status=all`) — highlighting whichever matches `current_status` (passed in from the route). Mobile: wrap the table in `<div style="overflow-x:auto;">` so it scrolls horizontally on narrow viewports instead of squeezing columns or breaking page layout — check whether `templates/admin_notifications.html` or `templates/admin_dashboard.html` already establish a wrapper/utility class for this (this codebase has scrollable tables elsewhere, e.g. the jobs list) and reuse that class if one exists rather than inventing a new pattern.

- [ ] **Step 6: Add the nav card**

In `templates/admin_dashboard.html`, immediately after the Notifications card's closing `</div>` (around line 279), add:

```html
        <!-- Job Reports -->
        <div class="card" style="cursor: pointer; transition: transform 0.2s;" onclick="window.location.href='/admin/reports'">
            <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;">
                <div style="font-size: 3rem;">🚩</div>
                <div>
                    <h2 style="margin: 0; color: #1f2937;">Job Reports</h2>
                    <p style="color: #6b7280; margin: 0.5rem 0 0 0; font-size: 0.875rem;">
                        Review listings flagged by visitors as incorrect, miscategorized, or broken
                    </p>
                </div>
            </div>
            <div style="margin-top: 1.5rem;">
                <a href="/admin/reports" class="btn" style="display: inline-block; width: 100%; text-align: center; text-decoration: none;">
                    Open Job Reports →
                </a>
            </div>
        </div>
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_admin_reports_routes.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 8: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before plus the new tests, one known pre-existing failure.

- [ ] **Step 9: Commit**

```bash
git add web_viewer.py templates/admin_reports.html templates/admin_dashboard.html tests/test_admin_reports_routes.py
git commit -m "feat: add admin job-reports review page"
```
