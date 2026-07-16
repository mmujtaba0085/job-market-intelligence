# "Report This Listing" Feature — Design Spec

## Goal

Let any visitor flag a problem with a specific job posting — wrong information, wrong category, a broken link, or anything else — and have that report saved so an admin can review and act on it. Directly serves the platform's trust mission: for an audience relying on this site to find real opportunities, an easy way to flag bad data matters as much as the data itself.

## Non-goals

- A reporter-facing reply/messaging system. The owner's request was to save reports "so we can respond later" — read as "don't lose this feedback," not "build a two-way inbox now." Signed-in reporters' reports are linked to their account (a real response channel exists if ever needed later); anonymous reporters can optionally leave an email if they want a reply, but nothing sends one automatically. A full response mechanism is a future enhancement, not part of this spec.
- Automated report-triggered actions (e.g., auto-hiding a job after N reports). Every report goes to manual admin review; no auto-moderation in this version.
- The general ticketing/feedback system (site-wide bugs/ideas, not tied to a specific job) — a separate, related spec, next in this session's batch. This spec is scoped to per-job reports only, though it establishes the reason-category + custom-input pattern the ticketing spec will likely reuse.

## Who can report

**Everyone, signed-in or anonymous** — no sign-in requirement. Reporting bad data is exactly the kind of low-friction, community-benefit action that shouldn't have an account barrier, and this app already has substantial anonymous teaser traffic who'd otherwise be unable to help. Basic abuse resistance instead of a sign-in wall (see Rate limiting below).

## Reason categories

Five options: four predefined + custom free text, covering the two the owner named explicitly plus the other realistic cases this app's own data-quality work this session has actually run into:

1. **Incorrect information** (wrong salary, location, company, or other details) — the owner's "wrong info."
2. **Wrong category** (tagged as the wrong field/type, e.g. shows as IT but isn't) — the owner's "assigned wrongly."
3. **Broken or dead link** ("View original posting" doesn't work).
4. **Spam or not a real job** (fake posting, scam, duplicate).
5. **Other** — free text, required when this option is chosen (predefined categories can optionally add detail text too, just not required).

## Where the report action lives

- **Job detail page**: a clearly visible "Report this listing" link/button (matches this app's existing understated, warm-palette UI conventions — not a jarring red banner, a normal secondary action near the job's other metadata).
- **Jobs list page**: not on every row (too much visual noise for a list) — the detail page is the natural, sufficient place, since a reporter needs to actually look at the posting to know something's wrong with it.
- Opens a small inline form (reason dropdown + optional/conditional details textarea + optional email field shown only to anonymous visitors) — no separate page navigation needed, submitted via the same `fetch()` + CSRF-header pattern already established for notification dismissal and admin actions.

## Data model

New table in `operational.sqlite` (non-rotating — this is operational/admin state about a job, not job data itself, same reasoning already applied to `notifications`):

```sql
CREATE TABLE IF NOT EXISTS job_reports (
    report_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           INTEGER,               -- best-effort reference; NOT reliable alone across DB rotation (see below)
    job_url          TEXT NOT NULL,          -- stable identifier - always resolvable regardless of rotation
    job_title         TEXT NOT NULL,          -- snapshot at report time, so admin has context even if the job later changes/gets hidden
    reason_category  TEXT NOT NULL,          -- 'incorrect_info' | 'wrong_category' | 'broken_link' | 'spam' | 'other'
    details          TEXT,                   -- free text; required when reason_category = 'other'
    reporter_user_id INTEGER,                -- NULL if anonymous
    reporter_email   TEXT,                   -- optional, only ever set when reporter_user_id IS NULL and they chose to leave one
    reporter_ip      TEXT NOT NULL,           -- same signal already captured for every request via access_logs; used for basic rate limiting
    status           TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'resolved' | 'dismissed'
    admin_notes      TEXT,                   -- internal notes, filled in when resolving/dismissing
    created_at       TEXT NOT NULL,
    resolved_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_reports_status ON job_reports(status);
CREATE INDEX IF NOT EXISTS idx_job_reports_job_url ON job_reports(job_url);
```

**Why `job_url` and not just `job_id`:** a job's `job_id` is a per-file auto-increment primary key in the rotating Serving A/B databases — it is not guaranteed to mean the same row in both files (they can be seeded/merged independently). `job_url` is stable and always resolvable regardless of which file is currently Serving, so it's the field the admin review page actually keys its "look up the live job" lookup on; `job_id` is stored as a best-effort convenience only.

Migration added to `_run_operational_migrations_impl()` in `src/storage/db.py`, following the exact same idempotent `CREATE TABLE IF NOT EXISTS` pattern already used there for `pipeline_config`/`pipeline_runs`/`notifications`.

## Submission flow

- `POST /jobs/<job_id>/report` (or a URL-based lookup if cleaner given the job_id-across-rotation caveat above — implementer's call, both are reasonable, the important part is that whichever route resolves the job it snapshots `job_url`/`job_title` at submission time).
- CSRF-protected via `validate_csrf()`, token sent via the `X-CSRF-Token` header (not a `csrf_token` form field — that mismatch broke CSRF entirely on an earlier admin page this session; the correct convention is already established and must be followed here).
- **Rate limiting**: before inserting, count reports from the same `reporter_ip` in the last hour; reject (clear, friendly error, not a silent failure) past a small threshold (5/hour is a reasonable starting point — generous for genuine use, low enough to blunt casual spam). Simple `COUNT(*) ... WHERE reporter_ip = ? AND created_at >= ?` query against `job_reports` itself — no new rate-limiting mechanism needed, this app's existing per-API-key rate limiter (`src/auth/middleware.py`) is a different, unrelated concern (API consumers, not anonymous form submitters) and doesn't fit this case.
- On success: a brief, clear confirmation ("Thanks — we'll review this."), no page reload needed.

## Admin review

New `/admin/reports` page, following `templates/admin_notifications.html`'s established conventions exactly (card/table layout, same button/badge styling, same JS `fetch()` + `X-CSRF-Token` pattern):

- Lists open reports by default (a status filter to also see resolved/dismissed), newest first.
- Each row: job title (linked to the live job via `job_url` if it still resolves), reason category, details, reporter (signed-in username + link to their account, or "Anonymous" + email if one was left), submitted date.
- Actions per row: **Resolve** (with an optional admin-notes field) and **Dismiss** (for invalid/spam reports) — both simple status-flip actions, CSRF-protected, matching the notification removal route's exact shape.
- New nav card on `/admin` linking here, matching the existing pattern for `/admin/pipeline`, `/admin/classification`, `/admin/notifications`.

## Definition of done

Any visitor, signed-in or not, can report a specific job from its detail page, choosing a reason and optionally adding detail; the report is saved with enough context (job snapshot, reporter identity if available) for an admin to act on later without the original job necessarily still being easy to find; an admin can see, resolve, or dismiss reports from a dedicated page; basic per-IP rate limiting prevents casual spam without requiring sign-in.
