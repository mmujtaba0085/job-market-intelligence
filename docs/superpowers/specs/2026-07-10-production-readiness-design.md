# Production Readiness for Public Launch — Design Spec

## Context

Job Market Intelligence is going public in a few days. The user expects
roughly 300 users over the first week (not concurrently — a trickle, not a
spike) against a VPS with 4 CPU cores, 7.8GB RAM, and a SQLite database
currently holding 100K+ rows. The site is currently gated behind a login
wall for everything except `/healthz` and `/auth/*` — that stays as-is for
this launch; a future (out-of-scope) change will eventually move to
Google-only auth and grant one of the user's own Google accounts admin
access via the admin panel.

Three things prompted this work, checked against the actual deployed
config rather than assumed:

- **Concurrency is effectively serialized today.** `gunicorn.conf.py` sets
  `workers = 1, threads = 2`, but no `worker_class` is set anywhere (not in
  the config, not on the Dockerfile's `CMD`). Gunicorn defaults to the
  `sync` worker class, under which the `threads` setting does nothing —
  the app handles exactly one HTTP request at a time, full stop. Caddy (the
  reverse proxy in front of it, `deploy/Caddyfile`) does no queuing or
  balancing of its own; it's a straight `reverse_proxy 127.0.0.1:5000`.
- **No caching exists anywhere.** Every request re-runs its full DB
  query + Jinja2 render, even for identical repeated requests.
- **SQLite has no `busy_timeout` set.** `src/storage/db.py`'s
  `get_connection()` enables WAL mode but never sets a busy timeout, so a
  write-lock collision fails immediately with "database is locked" instead
  of waiting briefly and retrying.

Two facts, verified directly rather than assumed, materially shaped this
design:

1. **Regular visitors generate zero writes.** The only public write routes
   (`/sheets/track`, `/sheets/track_job` — click tracking, `src/sheets_routes.py`)
   are not linked from any template or static JS (confirmed via
   `grep -rln "sheets/track" templates/ static/` returning nothing) — they're
   only reachable via URLs embedded in separate Google Sheets export
   artifacts, a different channel entirely from the public website.
   Browsing/filtering jobs is pure reads.
2. **Every page is behind login.** `web_viewer.py`'s `_PUBLIC_PATHS` is just
   `{"/healthz", "/auth/login", "/auth/logout", "/auth/google",
   "/auth/google/callback"}` plus the `/static/` prefix — `/dashboard`,
   `/jobs`, `/skills`, etc. all require an authenticated session today, and
   that isn't changing for this launch. Admin sessions see extra UI
   (e.g. jobs_list.html's "🛠️ Open Data Quality Review" link) that regular
   authenticated viewers don't — caching must not cross-contaminate the two.

Given (1), SQLite's single-writer limitation is not a live concern for this
launch — nothing on the public path writes concurrently with anything else.
`busy_timeout` is still added as cheap insurance (admin actions and the
scheduled pipeline do write), not because it's load-bearing at this scale.

## Goals

1. Let the app actually use the 4 CPU cores it has, instead of serializing
   every request through one worker.
2. Cache recurring read requests so repeat/popular views skip the DB query
   and template render entirely.
3. "Load balancing," clarified with the user: properly utilizing the
   existing single VPS, not provisioning multiple servers.

## Explicitly out of scope

- True multi-server horizontal scaling or migrating off SQLite to a
  networked database (Postgres/MySQL) — not warranted at this traffic
  scale (300 users/week, zero public writes), and far too large a change
  for a launch that's days away.
- Removing the `/api/*` surface — the user is deferring this decision,
  not asking for it now.
- Switching from password to Google-only auth, or granting admin access
  via a Google account — explicitly future work per the user.
- Rate limiting / abuse protection beyond what already exists — not asked
  for; can be a fast follow if it becomes a real problem.

## Design

### 1. Concurrency: reconfigure gunicorn

`gunicorn.conf.py` changes from the current dead config to:

```python
worker_class = "gthread"
workers = 4       # one per CPU core - true parallelism across all 4
threads = 4        # per worker, for I/O-bound waiting (DB queries)
```

(`bind`, `timeout`, `graceful_timeout`, `keepalive`, and the logging
settings are unchanged.) This gives ~16 requests genuinely in-flight at
once before anything queues — comfortable headroom for the real traffic
level, and it costs nothing extra: the container is currently using 0.05%
CPU and 83MB of the VPS's 7.8GB RAM.

### 2. SQLite safety net: busy_timeout

`src/storage/db.py`'s `get_connection()` adds one line alongside the
existing `PRAGMA journal_mode = WAL`:

```python
conn.execute("PRAGMA busy_timeout = 5000")  # 5s
```

WAL mode already supports multiple processes/threads safely; this just
means a rare write-lock collision waits up to 5 seconds and retries instead
of failing immediately. Not load-bearing at current traffic (no public
writes), but a correct, cheap thing to have in place before going live —
and it protects the one thing that *does* write on a schedule regardless of
web traffic: the ingest/crawl/weekly pipeline.

### 3. Caching: Flask-Caching + filesystem backend

New dependency: `Flask-Caching`. Configured with `CACHE_TYPE =
"FileSystemCache"`, cache directory `data/cache/` (already gitignored),
`CACHE_DEFAULT_TIMEOUT = 900` (15 minutes).

**Why filesystem, not in-process memory:** with 4 separate gunicorn worker
*processes* now, an in-process cache (`SimpleCache`) would be a different,
mostly-empty cache per worker — a visitor's second request might land on a
different worker with no cached copy at all. Filesystem cache is naturally
shared across all workers since they share the container's disk.

**Why not Redis:** the "more proper" answer for larger scale, but means
standing up and operating a new service days before launch. Documented here
as the natural upgrade path if traffic grows well past this launch's
expected level — not needed now.

**TTL:** 15 minutes flat across every cached route. Simple, and still a
small fraction of the real data-refresh cadence (crawl every 4h, ingest
every 12h) — worst case a page is 15 minutes stale, never more.

**Cache key:** Flask-Caching's default key (request path + query string)
is used, which naturally gives `/jobs` and `/jobs?market=...&skills=...`
separate cache entries — plus an explicit role marker (admin vs.
non-admin, read from `g.current_user`) folded into the key, so an admin's
cached response is never served to a regular viewer or vice versa.

**Routes cached** (all GET, all read-only, all currently behind login) —
verified against the actual route decorators in `web_viewer.py`, not
assumed: `/dashboard`, `/jobs`, `/jobs/<int:job_id>`, `/skills`,
`/skills/intelligence`, `/companies/intelligence`, `/titles/analytics`,
`/metrics`.

**Deliberately deferred: the `/api/dashboard/*`, `/api/skills/*`,
`/api/companies/*`, `/api/titles/*`, `/api/filters/*`, `/api/jobs`
endpoints.** These are the AJAX endpoints the page templates themselves
call client-side to populate charts/tables after the initial page loads
(confirmed via `grep "@app.route" web_viewer.py` — e.g. `/dashboard`
renders a shell, then JS fetches `/api/dashboard/kpis`,
`/api/dashboard/trends`, etc.), and they likely carry the bulk of the real
per-request DB aggregation cost — probably *more* valuable to cache than
the outer page routes. They're deferred here specifically because they sit
on the same scope-checked surface as the external API-key system
(`_SCOPE_MAP` in `web_viewer.py` ties `/api/filters` etc. to API-key
scopes like `analytics:read`) that the user has explicitly said not to
touch yet ("don't remove it yet, I'll let you know"). Caching them
correctly means handling both session-cookie access (from the page's own
JS) and API-key access without the two ever sharing a cache entry, which
is a reasonable next step once the `/api/*` surface's future is decided —
not blocked on it forever, just not bundled into this pass.

**Routes never cached:**
- Everything under `/admin/*` — always wants live data.
- `/auth/*` — the login page embeds a CSRF token; caching it would either
  break CSRF validation or serve one visitor's token to another.
- `/sheets/track`, `/sheets/track_job` — these write on every hit; caching
  would silently stop recording clicks.
- `/healthz` — must always reflect real-time container status.
- Anything not a GET request (caching is applied per-view-function via
  `@cache.cached(...)`, which is naturally GET-only in Flask-Caching).

### 4. Validation plan

1. **Regression check** — run the existing test suite (`pytest tests -q`,
   baseline: 149 passed, 1 pre-existing unrelated failure) after the
   gunicorn/caching/busy_timeout changes to confirm nothing else broke.
2. **Local load test** — simulate realistic concurrent traffic (a modest
   virtual-user count comfortably exceeding the real expected peak, e.g.
   50-100 concurrent simulated users over a few minutes) against a mix of
   cached and cache-excluded routes, hitting both popular unfiltered views
   and a spread of filter combinations. Confirm: no errors, reasonable
   response times under load, and — checked directly, not assumed — that
   repeat requests actually get faster after the first (cache is really
   being hit, not silently bypassed).
3. **VPS spot-check after deploy** — confirm `/healthz` still returns
   healthy, spot-check a few real pages load correctly, confirm the
   gunicorn process shows the expected worker count.

## Testing

Covered by the validation plan above — no new automated test suite is
proposed here (this is an infra/config change, not new application logic),
but the load test in step 2 is a required, concrete verification step
before this is considered done, not an optional nice-to-have.
