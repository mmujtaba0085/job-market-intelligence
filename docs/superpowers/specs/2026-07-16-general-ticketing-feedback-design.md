# General Ticketing / Feedback System — Design Spec

## Goal

Let any visitor submit general feedback about the site — bugs, feature ideas, suggestions ("add this source," "add email alerts when a certain kind of job posts"), or anything else — separate from the per-job "Report this listing" feature (`docs/superpowers/specs/2026-07-16-job-report-feature-design.md`), which is scoped to problems with one specific posting. This is the site's general suggestion box.

**This spec is about the ticketing system itself — the container.** The two example ideas the owner gave ("add this site for daily collection," "email alerts for specific job types") are illustrations of what a feature-request ticket looks like in practice, not features to design or build here. They'd be *submitted through* this system once it exists, then separately brainstormed and spec'd on their own merits if/when prioritized.

## Non-goals

- Building either of the two illustrative feature ideas (new-source suggestions, email alerts) — out of scope, see above.
- A "my tickets" status-tracking view for submitters. Anonymous submitters have no durable identity to attach one to anyway, and nothing in the request asks for it — a reasonable future enhancement if the signed-in user base grows, not part of this version.
- Automated ticket triage/categorization — every ticket goes to manual admin review, same as job reports.

## Deliberately mirrors the job-report feature — same pattern family, not a one-off

This is the second "user submits something, admin reviews a queue" feature built today, and it should read as one consistent pattern, not two unrelated mechanisms:
- Same predefined-categories + required-custom-text-for-"other" input shape.
- Same "anyone can submit, no sign-in wall" policy, for the same reason (zero friction for community input; this app already gates plenty of things behind sign-in, but contributing feedback that improves the site for everyone shouldn't be one of them).
- Same `operational.sqlite` placement, same CSRF-via-`X-CSRF-Token`-header convention, same rate-limiting approach (per-IP count check against the table itself), same `/admin/<feature>` review-page shape modeled on `templates/admin_notifications.html`.

## Kept as a separate table from job_reports, not unified — the call, and why

Considered a single generic `feedback` table with a `type` column ('job_report' | 'ticket') and nullable job-specific columns. Decided against it: job reports carry fields a general ticket has no use for (`job_url`, `job_title` — a ticket isn't about any one job), and a ticket carries a `subject` line a job report doesn't need (a job report's "subject" is just the job itself). Forcing both shapes into one table means every row carries columns that are meaningless for its own type, and the admin review pages want different information at a glance for each (a reports admin is thinking "which listing, what's wrong with it"; a tickets admin is thinking "what's the idea, how big is it"). Two small, focused tables and two focused admin pages, matching how `notifications`, and now `job_reports`, already each get their own clean table rather than being folded into a shared "misc admin stuff" table.

## Categories

Four options, deliberately parallel in shape to the job-report feature's four-plus-custom list:

1. **Bug** — something on the site is broken or not working as expected.
2. **Feature request** — a new idea or enhancement (covers both the owner's named examples — a new source to collect from, email alerts for specific job criteria — and anything else a user wants added or changed).
3. **General feedback** — doesn't fit cleanly as a bug or a specific feature ask (e.g. "this page is confusing," general praise/criticism).
4. **Other** — free text, required when chosen (same rule as the report feature).

## Data model

New table in `operational.sqlite`, same non-rotating placement as `notifications` and `job_reports` (this is admin/operational state, not job data):

```sql
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    category          TEXT NOT NULL,               -- 'bug' | 'feature' | 'feedback' | 'other'
    subject           TEXT NOT NULL,                -- short one-line summary, shown in the admin list
    details           TEXT NOT NULL,                -- the actual free-text content
    submitter_user_id INTEGER,                      -- NULL if anonymous
    submitter_email   TEXT,                         -- optional, only meaningful when submitter_user_id IS NULL
    submitter_ip      TEXT NOT NULL,                -- same rate-limiting/abuse-signal role as job_reports.reporter_ip
    status            TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'in_progress' | 'resolved' | 'dismissed'
    admin_notes       TEXT,
    created_at        TEXT NOT NULL,
    resolved_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
```

One deliberate difference from `job_reports`: a fourth status value, `in_progress`. A data-correction report is fundamentally binary (fixed or not); a feature idea or general feedback item reasonably sits "under consideration" for a while before a final resolve/dismiss — worth being able to signal that distinction to anyone who later checks the admin queue.

Migration added to `_run_operational_migrations_impl()` in `src/storage/db.py`, same idempotent pattern as the other three operational tables.

## Where the submission action lives

A persistent, low-key "Feedback" or "Suggest / Report an Issue" link in the site footer (visible on every page, signed-in or anonymous) — general feedback isn't tied to any one page's content the way a job report is tied to a job, so it belongs somewhere globally reachable rather than embedded in a specific page's layout. Opens the same kind of small inline form used for job reports (category dropdown + subject + details + optional email for anonymous submitters), submitted via `fetch()` + CSRF header, no page navigation.

## Admin review

New `/admin/tickets` page, structurally identical to `/admin/reports` (list, newest first, status filter, per-row admin-notes + status-change actions) with the fields that make sense for a ticket instead of a job report (category, subject, details, submitter, status including the extra `in_progress` state). New nav card on `/admin` alongside the other three admin sections.

## Definition of done

Any visitor can submit a categorized (or custom) piece of feedback from anywhere on the site without signing in; it's saved with submitter context where available; an admin can see, triage (including marking something "in progress"), and resolve or dismiss tickets from a dedicated page, structurally consistent with how job reports and notifications are already managed.
