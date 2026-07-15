# Admin Notification Bar — Design Spec

## Goal

Let admins post site-wide or page-targeted announcement bars (maintenance notices, outages, etc.) without a code deploy — visible to every visitor including anonymous ones, individually dismissible, optionally auto-expiring, manageable from a new admin page.

## Requirements (confirmed with user)

- Audience: everyone, including anonymous visitors — not gated behind login.
- Dismissal: a visitor can close a notification; it stays hidden for them (tracked client-side, via cookie) but remains live for everyone else until the admin removes it or it expires.
- Page targeting: admin picks "all pages" or specific pages via checkboxes, not free-form URL patterns.
- Severity: admin picks one of `info` / `warning` / `urgent` per notification, mapped to distinct colors.
- Multiple simultaneous notifications on the same page: all stack, each its own bar — no priority/ordering system.
- Placement: one full-width bar per active notification, stacked above the existing `<header>`, scrolls away with the page (not sticky/fixed).
- Expiry: optional — admin can set a duration after which the notification stops showing on its own; otherwise it stays until manually removed.

## Data model

New table `notifications`, created in **`operational.sqlite`** (the existing non-rotating file that already holds `pipeline_config`/`pipeline_runs` — this is admin/operational state, not job data, so it belongs alongside those, not in the rotating serving files). Migration goes in `_run_operational_migrations_impl()` in `src/storage/db.py`, next to the existing `pipeline_runs`/`pipeline_config` table creation.

```sql
CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    heading      TEXT NOT NULL,
    body         TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'info',   -- 'info' | 'warning' | 'urgent'
    target_pages TEXT NOT NULL DEFAULT 'all',    -- 'all' or comma-separated page keys
    created_at   TEXT NOT NULL,
    expires_at   TEXT,                            -- NULL = no auto-expiry
    removed_at   TEXT                              -- NULL = active; set on manual removal
);
CREATE INDEX IF NOT EXISTS idx_notifications_active ON notifications(removed_at, expires_at);
```

**Page keys** (the fixed, checkbox-able set — matches `base.html`'s main nav sections exactly, no finer-grained targeting than that):

| key | matches |
|---|---|
| `dashboard` | `/` or `/dashboard` |
| `jobs` | `/jobs` and everything under it (list + detail pages) |
| `skills` | `/skills` and everything under it |
| `companies` | `/companies` and everything under it |
| `titles` | `/titles` and everything under it |
| `metrics` | `/metrics` |
| `api_docs` | `/api/docs` |

`/admin/*` and `/auth/*` are never targetable — an admin declaring maintenance already knows about it, and login pages aren't where a visitor needs to see a banner.

A notification is **active** when `removed_at IS NULL AND (expires_at IS NULL OR expires_at > current time)`.

## Rendering

`web_viewer.py` gets a new `before_request` hook (same shape as the existing `_track_last_request_at`), populating `g.active_notifications`: every active row whose `target_pages` is `'all'` or contains a key matching the current `request.path`, **minus** any id already present in the visitor's `jmi_dismissed` cookie (comma-separated ids — same cookie-based pattern `base.html` already uses for `jmi_theme`).

The page-matching and dismissed-filtering logic is a pure function — `filter_active_notifications(rows, path, dismissed_ids, now) -> list[Row]` — in a new small module `src/notifications.py`, so it's testable without a request context (mirrors how `should_process_chunk()` in `src/classification/scheduling.py` is kept pure and separate from its Flask call site).

New template partial `templates/_notifications.html` (matching the `_brand.html`/`_gating.html` partial convention already in this codebase), included once in `base.html` right before `<header>`. Loops over `g.active_notifications`, one full-width bar per row:

```html
<div class="notification-bar notification-{{ n.severity }}" data-notification-id="{{ n.id }}">
  <div class="container notification-bar-inner">
    <div><strong>{{ n.heading }}</strong> {{ n.body }}</div>
    <button class="notification-close" onclick="dismissNotification({{ n.id }})" aria-label="Dismiss">&times;</button>
  </div>
</div>
```

New CSS block in `base.html` (`.notification-bar` full-width, edge-to-edge — NOT the existing `.alert` class, which is a small inset box for flash messages; this reuses the same `--accent-bg`/`--warning-bg`/`--danger-bg` + text-color variable pairs for its three severity variants, just in a full-width bar layout instead). `dismissNotification(id)` is plain JS: hides that bar's element immediately and appends `id` to the `jmi_dismissed` cookie (`max-age` matched to a generous window, e.g. 30 days, so a dismissal doesn't quietly "expire" and reappear) — no new endpoint, same `document.cookie` pattern `toggleTheme()` already uses.

## Admin UI

New page `/admin/notifications`, matching the existing `/admin/pipeline` page's layout and conventions exactly (same card/table/button styling, same `@require_admin` pattern):

- **Create form**: heading (text input), body (textarea), severity (dropdown: Info/Warning/Urgent), "All pages" checkbox that disables the per-page checkboxes when checked, the 7 per-page checkboxes, optional "expires in ___ hours" number input (blank = no expiry). Converted to an absolute `expires_at` timestamp server-side at creation time (`now + N hours`), so `expires_at` itself is always an absolute point in time, not a duration.
- **Table**: all notifications (active and past), columns for heading/severity/target/created/expires/status, a "Remove now" button per active row (sets `removed_at`).

New routes in `web_viewer.py`, all `@require_admin`:
- `GET /admin/notifications` — renders the page.
- `POST /admin/notifications/create` — inserts a row.
- `POST /admin/notifications/<id>/remove` — sets `removed_at = now`.

New nav card on the existing `/admin` dashboard page linking to `/admin/notifications`, matching how `/admin/pipeline` and `/admin/classification` are already linked from there.

## Testing

- `src/notifications.py`'s `filter_active_notifications()`: pure-function unit tests — page-match (`all`, exact key, wrong key), expiry (none, future, past), dismissed-id exclusion, and the case of zero active rows.
- Route tests: create requires admin (401/403/redirect for non-admin), create writes a row with correct defaults, remove sets `removed_at` and the notification stops appearing in `g.active_notifications` on a subsequent request, a notification targeted at `jobs` does not appear when requesting `/dashboard` and does appear when requesting `/jobs`.
- A request with a pre-set `jmi_dismissed` cookie containing a given notification's id does not see that notification in `g.active_notifications`, while a request without that cookie does.

## Out of scope

- Rich text / markdown in the body (plain text only).
- Per-role targeting (e.g. "only signed-in users" or "only admins") — audience is always everyone, per the confirmed requirement.
- A dismiss-tracking table server-side (cookie-only, no per-visitor DB rows — keeps this simple and matches the "no account for anonymous visitors" constraint).
