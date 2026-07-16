# General Ticketing / Feedback System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any visitor, signed-in or anonymous, can submit general site feedback (bug/feature/feedback/other) from a persistent footer link on every page; submissions save to a new `tickets` table; an admin triages and resolves/dismisses/marks-in-progress from a new `/admin/tickets` page.

**Architecture:** Deliberately mirrors `docs/superpowers/plans/2026-07-16-job-report-feature.md`'s shape one-for-one: a pure Python module for validation/rate-limit/insert (`src/tickets.py`, parallel to `src/job_reports.py`), a `tickets` table in `operational.sqlite`, a fetch()-based submission route, and an admin review page structurally identical to `/admin/reports`. The one structural difference: submission lives in the global footer (`templates/base.html`), not a per-job page, and the status lifecycle has an extra non-terminal `in_progress` state.

**Tech Stack:** Flask, SQLite, vanilla JS (fetch for submission, matching the job-report feature's pattern — not the admin-notifications traditional-form pattern, since this is a fetch-based inline form like job reports).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every pytest invocation must use `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`.
- Any new test fixture that touches DB paths must patch ALL of `src.storage.db._SERVING_A_PATH`, `_SERVING_B_PATH`, `_BUFFER_DB_PATH`, `_OPERATIONAL_DB_PATH`, `_POINTER_PATH` together when it calls `db.run_migrations()` or otherwise exercises real connection resolution.
- `tickets` lives in `operational.sqlite` (`get_operational_connection()`), same non-rotating placement as `notifications` and `job_reports`.
- CSRF: `validate_csrf()` (`src/auth/middleware.py`) accepts either the `X-CSRF-Token` header OR a `_csrf_token` form field. The submission route uses the header (sent via `fetch()`, matching the job-report feature's pattern); the admin resolve/dismiss/in-progress actions use plain form POSTs with a hidden `_csrf_token` field, matching `/admin/reports`.
- Category values are exactly: `bug`, `feature`, `feedback`, `other`.
- `subject` and `details` are **both always required** (non-empty after `.strip()`), regardless of category — this differs from the job-report feature, where `details` was optional except for the `other` category. The `tickets` table's `details` column is `NOT NULL` for every row (a ticket with no content is useless regardless of category), so validation enforces this unconditionally rather than conditionally like `job_reports` does.
- Status values are exactly: `open`, `in_progress`, `resolved`, `dismissed` — one more than `job_reports`' three, reflecting that a feature idea or general feedback item reasonably sits "under consideration" before a final resolution.
- Rate limit: reject a new ticket if the same `submitter_ip` has submitted 5 or more tickets in the last hour (same threshold as `job_reports`, same reasoning: generous for genuine use, low enough to blunt casual spam).
- Mobile responsiveness: both the footer submission form and the admin tickets table must remain usable on narrow (~360-400px) viewports — inputs/selects/textareas at `width:100%` with `box-sizing:border-box`, the admin table wrapped in a horizontally-scrollable container (`<div style="overflow-x:auto;">`), no fixed pixel widths that overflow. This app has no dedicated mobile stylesheet, so keep these templates' styling self-contained (inline/scoped), matching the approach already used for the job-report feature's UI.

---

### Task 1: `tickets` table + pure submission logic

**Files:**
- Modify: `src/storage/db.py` (`_run_operational_migrations_impl()` — add the `tickets` table migration)
- Create: `src/tickets.py`
- Test: `tests/test_tickets.py`

**Interfaces:**
- Produces: `CATEGORIES: tuple[str, ...]` = `("bug", "feature", "feedback", "other")`.
- Produces: `validate_ticket_input(category: str, subject: str, details: str) -> str | None` — pure function. Returns an error message if `category` is unrecognized, or if `subject` or `details` is blank after `.strip()` (both always required — see Global Constraints), else `None`.
- Produces: `is_rate_limited(conn, submitter_ip: str, now: datetime) -> bool` — same shape as `src.job_reports.is_rate_limited`: `True` if `submitter_ip` has 5+ rows in `tickets` with `created_at` within the last hour of `now`.
- Produces: `create_ticket(conn, *, category: str, subject: str, details: str, submitter_user_id: int | None, submitter_email: str | None, submitter_ip: str, now: datetime) -> int` — inserts a row with `status='open'`, returns the new `ticket_id`. Does not itself call `validate_ticket_input` or `is_rate_limited` — same caller-checks-first contract as `create_report`.

- [ ] **Step 1: Confirm the current migration pattern**

Read `src/storage/db.py`'s `_run_operational_migrations_impl()`, specifically wherever the `job_reports` table migration was added (per `docs/superpowers/plans/2026-07-16-job-report-feature.md` Task 1 Step 4 — if that plan has already been executed, `job_reports` will already be present; either way, match its exact `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` style).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_tickets.py`:

```python
"""
tests/test_tickets.py
─────────────────────────
Regression coverage for src/tickets.py's pure logic - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.tickets import CATEGORIES, is_rate_limited, validate_ticket_input


def test_categories_are_the_four_agreed_values():
    assert CATEGORIES == ("bug", "feature", "feedback", "other")


def test_validate_accepts_predefined_category_with_subject_and_details():
    assert validate_ticket_input("bug", "Login button broken on Safari", "Clicking it does nothing, console shows a 404 for /auth/login") is None


def test_validate_rejects_unrecognized_category():
    err = validate_ticket_input("not_a_real_category", "Subject", "Details")
    assert err is not None


def test_validate_requires_non_blank_subject():
    assert validate_ticket_input("bug", "", "Some details here") is not None
    assert validate_ticket_input("bug", "   ", "Some details here") is not None


def test_validate_requires_non_blank_details_for_every_category():
    for category in CATEGORIES:
        err = validate_ticket_input(category, "A real subject line", "")
        assert err is not None, f"expected {category} to require details"
        err_whitespace = validate_ticket_input(category, "A real subject line", "   ")
        assert err_whitespace is not None


def test_validate_accepts_other_category_with_subject_and_details():
    assert validate_ticket_input("other", "Dark mode contrast", "The muted text is hard to read in dark mode on the jobs list page") is None


@pytest.fixture()
def tickets_conn(tmp_path, monkeypatch):
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


def test_create_ticket_inserts_and_returns_id(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    ticket_id = create_ticket(
        tickets_conn, category="feature", subject="Add LinkedIn as a source",
        details="Would love to see LinkedIn job postings included alongside the existing sources",
        submitter_user_id=None, submitter_email="test@example.com", submitter_ip="127.0.0.1", now=now,
    )
    assert isinstance(ticket_id, int)
    row = tickets_conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    assert row["subject"] == "Add LinkedIn as a source"
    assert row["status"] == "open"
    assert row["submitter_email"] == "test@example.com"


def test_is_rate_limited_false_under_threshold(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    for i in range(4):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(tickets_conn, "9.9.9.9", now) is False


def test_is_rate_limited_true_at_threshold(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    for i in range(5):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="9.9.9.9", now=now,
        )
    assert is_rate_limited(tickets_conn, "9.9.9.9", now) is True


def test_is_rate_limited_ignores_tickets_older_than_an_hour(tickets_conn):
    from src.tickets import create_ticket
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    now = datetime.now(timezone.utc)
    for i in range(5):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="9.9.9.9", now=old,
        )
    assert is_rate_limited(tickets_conn, "9.9.9.9", now) is False


def test_is_rate_limited_scoped_per_ip(tickets_conn):
    from src.tickets import create_ticket
    now = datetime.now(timezone.utc)
    for i in range(5):
        create_ticket(
            tickets_conn, category="bug", subject=f"Bug {i}", details="Details",
            submitter_user_id=None, submitter_email=None, submitter_ip="1.1.1.1", now=now,
        )
    assert is_rate_limited(tickets_conn, "2.2.2.2", now) is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_tickets.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — `src.tickets` doesn't exist yet.

- [ ] **Step 4: Add the migration**

In `src/storage/db.py`'s `_run_operational_migrations_impl()`, add:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            category          TEXT NOT NULL,
            subject           TEXT NOT NULL,
            details           TEXT NOT NULL,
            submitter_user_id INTEGER,
            submitter_email   TEXT,
            submitter_ip      TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'open',
            admin_notes       TEXT,
            created_at        TEXT NOT NULL,
            resolved_at       TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
```

- [ ] **Step 5: Create `src/tickets.py`**

```python
"""
src/tickets.py
──────────────────
General site feedback/ticketing - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.
Deliberately mirrors src/job_reports.py's shape (same separation of pure
validation/rate-limit checks from the create_ticket() insert, same
operational.sqlite placement) - this is the second feature in that pattern
family, not a one-off.

Unlike job_reports, subject and details are BOTH always required regardless
of category (the tickets table's details column is NOT NULL for every row -
a ticket with no content is useless no matter which category it's filed
under, unlike a job report where a predefined category like "spam" is
self-explanatory without extra text).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

CATEGORIES: tuple[str, ...] = ("bug", "feature", "feedback", "other")


def validate_ticket_input(category: str, subject: str, details: str) -> str | None:
    """Returns an error message if invalid, else None."""
    if category not in CATEGORIES:
        return f"Unrecognized category: {category!r}"
    if not subject.strip():
        return "Please provide a short subject line"
    if not details.strip():
        return "Please provide some details"
    return None


def is_rate_limited(conn: sqlite3.Connection, submitter_ip: str, now: datetime) -> bool:
    cutoff = (now - timedelta(hours=1)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM tickets WHERE submitter_ip = ? AND created_at >= ?",
        (submitter_ip, cutoff),
    ).fetchone()
    return row["n"] >= 5


def create_ticket(
    conn: sqlite3.Connection, *, category: str, subject: str, details: str,
    submitter_user_id: int | None, submitter_email: str | None, submitter_ip: str, now: datetime,
) -> int:
    cursor = conn.execute(
        """INSERT INTO tickets
           (category, subject, details, submitter_user_id, submitter_email,
            submitter_ip, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
        (category, subject.strip(), details.strip(), submitter_user_id,
         submitter_email, submitter_ip, now.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_tickets.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add src/storage/db.py src/tickets.py tests/test_tickets.py
git commit -m "feat: add tickets table and pure submission/validation logic"
```

---

### Task 2: Submission route + footer UI

**Files:**
- Modify: `web_viewer.py` (new `POST /tickets` route)
- Modify: `templates/base.html` (footer feedback link + inline form + JS)
- Test: `tests/test_ticket_submission.py`

**Interfaces:**
- Consumes: `CATEGORIES`, `validate_ticket_input`, `is_rate_limited`, `create_ticket` from Task 1 (`src.tickets`).

- [ ] **Step 1: Re-confirm the footer's current exact markup**

Read `templates/base.html` lines 505-515 (the `<footer>` block) to confirm the insertion point is still accurate before editing — `<footer><div class="container"><p>&copy; 2026 GreyWave...</p></div></footer>`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ticket_submission.py`:

```python
"""
tests/test_ticket_submission.py
───────────────────────────────────
End-to-end coverage for POST /tickets - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.
"""
import sqlite3

import pytest


@pytest.fixture()
def ticket_client(tmp_path, monkeypatch):
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
    return web_viewer.app.test_client()


def _csrf_client(client):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    return client


def test_anonymous_visitor_can_submit_a_ticket(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "feature", "subject": "Add LinkedIn as a source", "details": "Would be great to include LinkedIn postings"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 200
    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT * FROM tickets WHERE subject = 'Add LinkedIn as a source'").fetchone()
    conn.close()
    assert row is not None
    assert row["submitter_user_id"] is None
    assert row["category"] == "feature"


def test_missing_csrf_token_is_rejected(ticket_client):
    r = ticket_client.post(
        "/tickets",
        data={"category": "bug", "subject": "Subject", "details": "Details"},
    )
    assert r.status_code == 400


def test_blank_details_is_rejected(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "bug", "subject": "Subject", "details": ""},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_blank_subject_is_rejected(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "bug", "subject": "", "details": "Some details"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_unrecognized_category_is_rejected(ticket_client):
    _csrf_client(ticket_client)
    r = ticket_client.post(
        "/tickets",
        data={"category": "not_a_category", "subject": "Subject", "details": "Details"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 400


def test_sixth_ticket_from_same_ip_within_an_hour_is_rate_limited(ticket_client):
    _csrf_client(ticket_client)
    for i in range(5):
        r = ticket_client.post(
            "/tickets",
            data={"category": "feedback", "subject": f"Subject {i}", "details": "Details"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert r.status_code == 200
    r = ticket_client.post(
        "/tickets",
        data={"category": "feedback", "subject": "One too many", "details": "Details"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert r.status_code == 429
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_ticket_submission.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — the route doesn't exist yet (404 on every request).

- [ ] **Step 4: Add the submission route**

In `web_viewer.py`, add (near the job-reports submission route if it exists, otherwise near the other public POST routes):

```python
@app.route("/tickets", methods=["POST"])
def submit_ticket():
    from src.auth.middleware import validate_csrf
    from src.tickets import create_ticket, is_rate_limited, validate_ticket_input
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    category = request.form.get("category", "")
    subject = request.form.get("subject", "").strip()
    details = request.form.get("details", "").strip()
    validation_error = validate_ticket_input(category, subject, details)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    submitter_ip = request.remote_addr or "unknown"
    conn = get_operational_connection()
    try:
        if is_rate_limited(conn, submitter_ip, datetime.now(timezone.utc)):
            return jsonify({"error": "Too many submissions from this IP recently - please try again later"}), 429

        submitter_user_id = g.current_user["id"] if g.current_user else None
        submitter_email = None if g.current_user else (request.form.get("email", "").strip() or None)

        create_ticket(
            conn, category=category, subject=subject, details=details,
            submitter_user_id=submitter_user_id, submitter_email=submitter_email,
            submitter_ip=submitter_ip, now=datetime.now(timezone.utc),
        )
    finally:
        conn.close()

    return jsonify({"status": "ok"})
```

Confirm `datetime`/`timezone`/`jsonify`/`g` are already imported at module level in `web_viewer.py` (they are, used elsewhere including the job-reports submission route if built) — no new top-level import needed.

**Also add `"submit_ticket"` to `web_viewer.py`'s `_PUBLIC_VIEWABLE_ENDPOINTS` set** (near `_PUBLIC_PATHS`, around line 116) — `global_auth_gate()`'s `before_request` hook redirects anonymous requests to `/auth/login` by default unless the endpoint is explicitly allowlisted there. This was discovered the hard way building the job-report feature's identical route: the endpoint existing and passing CSRF isn't enough, since this hook runs first and doesn't know the route is meant to be public. Match `submit_job_report`'s entry there (added when the job-report feature was built) for the comment style.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ticket_submission.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 6: Add the footer UI to base.html**

In `templates/base.html`, replace the existing `<footer>` block:

```html
    <footer>
        <div class="container">
            <p>&copy; 2026 GreyWave &mdash; job market intelligence, refreshed daily.</p>
        </div>
    </footer>
```

with:

```html
    <footer>
        <div class="container">
            <p>&copy; 2026 GreyWave &mdash; job market intelligence, refreshed daily.
                &mdash; <a href="#" onclick="document.getElementById('ticketForm').style.display='block'; this.style.display='none'; return false;" style="color:inherit;text-decoration:underline;">Suggest a feature / report an issue</a>
            </p>
            <div id="ticketForm" style="display:none;margin-top:0.75rem;padding:1rem;border:1px solid var(--border-color);border-radius:8px;max-width:480px;box-sizing:border-box;text-align:left;">
                <label style="display:block;font-size:0.85rem;font-weight:600;margin-bottom:0.4rem;">What's this about?</label>
                <select id="ticketCategory" style="width:100%;margin-bottom:0.6rem;box-sizing:border-box;">
                    <option value="bug">Bug &mdash; something's broken</option>
                    <option value="feature">Feature request &mdash; add or change something</option>
                    <option value="feedback">General feedback</option>
                    <option value="other">Other</option>
                </select>
                <input type="text" id="ticketSubject" placeholder="Short summary" style="width:100%;margin-bottom:0.6rem;box-sizing:border-box;">
                <textarea id="ticketDetails" placeholder="Details" style="width:100%;margin-bottom:0.6rem;min-height:70px;box-sizing:border-box;"></textarea>
                {% if not g.current_user %}
                <input type="email" id="ticketEmail" placeholder="Your email (optional, if you'd like a response)" style="width:100%;margin-bottom:0.6rem;box-sizing:border-box;">
                {% endif %}
                <div id="ticketMsg" style="font-size:0.8rem;margin-bottom:0.5rem;"></div>
                <button type="button" class="btn" onclick="submitTicket()">Submit</button>
            </div>
        </div>
    </footer>
    <script>
    function submitTicket() {
        var category = document.getElementById('ticketCategory').value;
        var subject = document.getElementById('ticketSubject').value;
        var details = document.getElementById('ticketDetails').value;
        var emailEl = document.getElementById('ticketEmail');
        var body = new URLSearchParams({category: category, subject: subject, details: details});
        if (emailEl) body.append('email', emailEl.value);
        fetch('/tickets', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': '{{ csrf_token() }}'},
            body: body,
        })
        .then(function(r) { return r.json().then(function(data) { return {ok: r.ok, data: data}; }); })
        .then(function(result) {
            var msg = document.getElementById('ticketMsg');
            if (result.ok) {
                msg.textContent = "Thanks - we'll take a look.";
                msg.style.color = 'var(--accent-color, green)';
                document.getElementById('ticketSubject').value = '';
                document.getElementById('ticketDetails').value = '';
            } else {
                msg.textContent = result.data.error || 'Something went wrong.';
                msg.style.color = 'red';
            }
        });
    }
    </script>
```

This form is global (rendered on every page via `base.html`), so it must not assume anything page-specific — it only reads its own inputs and posts to a fixed URL, matching the job-report form's self-contained JS shape.

- [ ] **Step 7: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before plus the new tests, one known pre-existing failure (`test_login_rejects_external_next_target`).

- [ ] **Step 8: Commit**

```bash
git add web_viewer.py templates/base.html tests/test_ticket_submission.py
git commit -m "feat: add ticket submission route and footer feedback UI"
```

---

### Task 3: Admin review page

**Files:**
- Modify: `web_viewer.py` (`GET /admin/tickets`, `POST /admin/tickets/<id>/resolve`, `POST /admin/tickets/<id>/dismiss`, `POST /admin/tickets/<id>/in-progress`)
- Create: `templates/admin_tickets.html`
- Modify: `templates/admin_dashboard.html` (new nav card)
- Test: `tests/test_admin_tickets_routes.py`

**Interfaces:**
- Consumes: the `tickets` table from Task 1.

- [ ] **Step 1: Read the current nav-card section of admin_dashboard.html**

Read `templates/admin_dashboard.html` to find whichever nav card is currently last in that section (Job Reports, if `docs/superpowers/plans/2026-07-16-job-report-feature.md` has already been built; otherwise Notifications) — the new Tickets card goes immediately after it, matching its exact markup pattern (icon emoji + heading + description + full-width button).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_admin_tickets_routes.py`:

```python
"""
tests/test_admin_tickets_routes.py
──────────────────────────────────────
Regression coverage for /admin/tickets - see
docs/superpowers/specs/2026-07-16-general-ticketing-feedback-design.md.
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
    from src.tickets import create_ticket
    op_conn = get_operational_connection()
    ticket_id = create_ticket(
        op_conn, category="feature", subject="Add LinkedIn as a source",
        details="Would be great to include LinkedIn postings", submitter_user_id=None,
        submitter_email=None, submitter_ip="1.2.3.4", now=datetime.now(timezone.utc),
    )
    op_conn.close()

    import src.auth.models as models
    from pathlib import Path
    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()

    client = web_viewer.app.test_client()
    client._seeded_ticket_id = ticket_id
    return client


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def test_tickets_page_requires_admin(admin_client):
    r = admin_client.get("/admin/tickets")
    assert r.status_code in (302, 401, 403)


def test_tickets_page_lists_open_tickets_by_default(admin_client):
    _login_admin(admin_client)
    r = admin_client.get("/admin/tickets")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Add LinkedIn as a source" in body


def test_tickets_page_status_all_shows_everything(admin_client):
    _login_admin(admin_client)
    import src.storage.db as db
    from src.tickets import create_ticket
    from datetime import datetime, timezone
    op_conn = db.get_operational_connection()
    dismissed_id = create_ticket(
        op_conn, category="bug", subject="Dismissed bug report", details="Not reproducible",
        submitter_user_id=None, submitter_email=None, submitter_ip="5.5.5.5", now=datetime.now(timezone.utc),
    )
    op_conn.execute("UPDATE tickets SET status = 'dismissed' WHERE ticket_id = ?", (dismissed_id,))
    op_conn.commit()
    op_conn.close()

    r = admin_client.get("/admin/tickets")
    assert "Dismissed bug report" not in r.get_data(as_text=True)

    r_all = admin_client.get("/admin/tickets?status=all")
    body_all = r_all.get_data(as_text=True)
    assert "Dismissed bug report" in body_all
    assert "Add LinkedIn as a source" in body_all


def test_mark_in_progress_updates_status(admin_client):
    _login_admin(admin_client)
    ticket_id = admin_client._seeded_ticket_id
    r = admin_client.post(f"/admin/tickets/{ticket_id}/in-progress", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status, resolved_at FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "in_progress"
    assert row["resolved_at"] is None  # non-terminal, not resolved


def test_resolve_ticket_updates_status(admin_client):
    _login_admin(admin_client)
    ticket_id = admin_client._seeded_ticket_id
    r = admin_client.post(f"/admin/tickets/{ticket_id}/resolve", data={"_csrf_token": "test-csrf", "admin_notes": "Added in v2"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status, admin_notes FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "resolved"
    assert row["admin_notes"] == "Added in v2"


def test_dismiss_ticket_updates_status(admin_client):
    _login_admin(admin_client)
    ticket_id = admin_client._seeded_ticket_id
    r = admin_client.post(f"/admin/tickets/{ticket_id}/dismiss", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "dismissed"


def test_in_progress_ticket_can_still_be_resolved(admin_client):
    _login_admin(admin_client)
    ticket_id = admin_client._seeded_ticket_id
    admin_client.post(f"/admin/tickets/{ticket_id}/in-progress", data={"_csrf_token": "test-csrf"})
    r = admin_client.post(f"/admin/tickets/{ticket_id}/resolve", data={"_csrf_token": "test-csrf"})
    assert r.status_code in (200, 302)

    import src.storage.db as db
    conn = db.get_operational_connection()
    row = conn.execute("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    assert row["status"] == "resolved"
```

Note: as with the job-report admin tests, if this codebase's real `@require_admin` session mechanics differ from the simple `session["user_id"] = 1` shortcut sketched here, check `tests/test_admin_notifications_routes.py` or `tests/test_admin_classification_routes.py` (or `tests/test_admin_reports_routes.py` if Task 3 of the job-report plan has already been built) for the actual established fixture and copy its exact mechanics.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_admin_tickets_routes.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL — routes don't exist yet.

- [ ] **Step 4: Add the admin routes**

In `web_viewer.py`:

```python
@app.route("/admin/tickets")
@require_admin
def admin_tickets():
    from src.storage.db import get_operational_connection
    status = request.args.get("status", "open")
    conn = get_operational_connection()
    if status == "all":
        rows = conn.execute("SELECT * FROM tickets ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    conn.close()
    return render_template("admin_tickets.html", tickets=rows, current_status=status)


@app.route("/admin/tickets/<int:ticket_id>/in-progress", methods=["POST"])
@require_admin
def admin_tickets_in_progress(ticket_id):
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    conn = get_operational_connection()
    conn.execute(
        "UPDATE tickets SET status = 'in_progress' WHERE ticket_id = ? AND status IN ('open', 'in_progress')",
        (ticket_id,),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_tickets"))


@app.route("/admin/tickets/<int:ticket_id>/resolve", methods=["POST"])
@require_admin
def admin_tickets_resolve(ticket_id):
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    admin_notes = request.form.get("admin_notes", "").strip()
    conn = get_operational_connection()
    conn.execute(
        "UPDATE tickets SET status = 'resolved', admin_notes = ?, resolved_at = ? WHERE ticket_id = ? AND status IN ('open', 'in_progress')",
        (admin_notes or None, datetime.now(timezone.utc).isoformat(), ticket_id),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_tickets"))


@app.route("/admin/tickets/<int:ticket_id>/dismiss", methods=["POST"])
@require_admin
def admin_tickets_dismiss(ticket_id):
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    conn = get_operational_connection()
    conn.execute(
        "UPDATE tickets SET status = 'dismissed', resolved_at = ? WHERE ticket_id = ? AND status IN ('open', 'in_progress')",
        (datetime.now(timezone.utc).isoformat(), ticket_id),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_tickets"))
```

Note the `in-progress` route deliberately does NOT set `resolved_at` (it's a non-terminal state, per Global Constraints); `resolve` and `dismiss` both set it (terminal states). All three transitions are allowed from either `open` or `in_progress` (so a ticket already in progress can still be directly resolved or dismissed, and — for `in-progress` itself — re-posting is a harmless no-op).

- [ ] **Step 5: Create `templates/admin_tickets.html`**

Copy `templates/admin_reports.html`'s structure (per `docs/superpowers/plans/2026-07-16-job-report-feature.md` Task 3 Step 5 — itself copied from `admin_notifications.html`) and adapt:
- Table columns: Category, Subject, Details, Submitter (username if `submitter_user_id` else "Anonymous" + email if present), Submitted, Status.
- Status filter tabs above the table: Open (default, `/admin/tickets`), In Progress (`/admin/tickets?status=in_progress`), Resolved (`/admin/tickets?status=resolved`), Dismissed (`/admin/tickets?status=dismissed`), All (`/admin/tickets?status=all`) — highlighting whichever matches `current_status`.
- Per-row actions: an `admin_notes` text input + three buttons — "Mark In Progress" (`POST /admin/tickets/<id>/in-progress`), "Resolve" (`POST /admin/tickets/<id>/resolve`), "Dismiss" (`POST /admin/tickets/<id>/dismiss`) — each its own `<form>` with a hidden `<input type="hidden" name="_csrf_token" value="{{ session.get('_csrf_token', '') }}">`, matching the reports page's plain-form-POST convention (not fetch, since there's no fetch involved in the admin actions).
- Mobile: wrap the table in `<div style="overflow-x:auto;">`, same as `admin_reports.html`.

- [ ] **Step 6: Add the nav card**

In `templates/admin_dashboard.html`, immediately after whichever nav card is currently last in that section (found in Step 1), add:

```html
        <!-- Tickets & Feedback -->
        <div class="card" style="cursor: pointer; transition: transform 0.2s;" onclick="window.location.href='/admin/tickets'">
            <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;">
                <div style="font-size: 3rem;">💬</div>
                <div>
                    <h2 style="margin: 0; color: #1f2937;">Tickets & Feedback</h2>
                    <p style="color: #6b7280; margin: 0.5rem 0 0 0; font-size: 0.875rem;">
                        Review bug reports, feature requests, and general feedback from visitors
                    </p>
                </div>
            </div>
            <div style="margin-top: 1.5rem;">
                <a href="/admin/tickets" class="btn" style="display: inline-block; width: 100%; text-align: center; text-decoration: none;">
                    Open Tickets →
                </a>
            </div>
        </div>
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_admin_tickets_routes.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (all tests)

- [ ] **Step 8: Run the full suite**

Run: `pytest tests -q --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: same pass count as before plus the new tests, one known pre-existing failure.

- [ ] **Step 9: Commit**

```bash
git add web_viewer.py templates/admin_tickets.html templates/admin_dashboard.html tests/test_admin_tickets_routes.py
git commit -m "feat: add admin tickets review page with in-progress/resolve/dismiss actions"
```
