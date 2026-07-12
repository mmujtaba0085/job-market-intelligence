# GreyWave Rebrand + Anonymous Teaser Access — Design Spec

## Context

The user commissioned an external redesign package ("Warm redesign of Job
Market Intelligence (3).zip", extracted for review to
`greywave_redesign/` — see its own `README.md` for full detail on the
visual/frontend layer, which is authoritative on colors, copy, icons, and
per-page gating rules). It rebrands the product as **GreyWave**, replaces
all emoji with an inline-SVG icon set, warms up copy in low-risk spots, and
adds a "Continue with Google" teaser experience for six pages that today
require a login to view at all.

The package is explicit that its gating UI is **frontend-only** — it
renders `{% if not g.current_user %}` branches correctly, but nothing in
the current backend ever lets an anonymous request reach these templates,
so as shipped the gating code is dead. The user chose to do the full
thing: apply the redesign *and* make the gating real by loosening the
backend's access control for exactly these six pages and the specific
API endpoints their "always visible" content depends on.

Two more decisions came out of discussion: the product's domain is
changing to **greywave.dev** (already purchased, DNS not yet pointed) with
the current domain becoming a permanent redirect rather than being
retired; and one real gap surfaced during review — `/api/skills/combinations`
sends its *full* result set to every requester today, with only the
frontend deciding how much to display (5 rows for anon, 20 for signed-in).
That's fixed as part of this work, not deferred.

Facts below were verified directly against the current codebase, not
assumed.

## Goals

1. Ship the GreyWave rebrand (name, wordmark, icons, copy, favicon) across
   the 13 templates the package covers.
2. Make six pages (dashboard, jobs list, job detail, skills intelligence,
   companies intelligence, titles analytics) genuinely viewable by
   anonymous visitors, with a teaser experience that funnels them toward
   "Continue with Google" — while signed-in visitors keep seeing exactly
   what they see today, unchanged.
3. Migrate the live domain to `greywave.dev`, keeping the current domain
   working as a redirect rather than breaking existing links/bookmarks.
4. Close the one real data-exposure gap found during review
   (`/api/skills/combinations` over-sending to anonymous requests).

## Explicitly out of scope

- Any rate limiting or bot/abuse protection for the newly-public routes —
  the user chose to accept this risk for now rather than add complexity;
  watching access logs and reacting manually (IP-block via firewall) if
  it becomes a real problem is the accepted fallback.
- Search engine indexing — a `robots.txt` disallowing everything ships as
  part of this work, per the user's choice, so this is *not* an SEO launch.
- Touching the external API-key system (`_SCOPE_MAP`, `/admin/auth/keys`,
  rate-limited API-key auth) in any way. This work adds a new way for
  *browser/session* requests to reach a curated set of routes without
  logging in; it does not change what API keys can do or how they're
  scoped.
- Any change to `_brand.html`/`_icons.html`/`_gating.html`'s own logic —
  they're adopted as shipped in the package. The only backend-adjacent
  template change beyond a straight copy-in is confirming
  `{{ url_for('auth.google_login', next=request.path) }}` resolves
  correctly (it does — verified against `src/auth/routes.py`, no changes
  needed there).
- Rebuilding `base.html` — the package's own README notes it's already
  live with the warm palette from earlier work and isn't part of this
  redesign's 13 pages.

## Design

### 1. Rebrand + redesign rollout

Copy the package's `templates/`, `static/css/filters.css`,
`static/favicon.svg`, `static/js/dashboard.js`, and the three new shared
partials (`_brand.html`, `_icons.html`, `_gating.html`) into the real
app's `templates/` and `static/`, preserving paths exactly as the
package's own README lays out. `static/js/filters.js` is included in the
package but is a byte-for-byte copy of what's already live (per the
package's own notes) — diff before copying to confirm, skip the copy if
identical.

No `web_viewer.py` changes are needed for this section alone — every
template still reads the same route-supplied variables, form field names,
and JS-targeted ids as today (verified against the package's own
file-by-file notes and spot-checked directly for the six gated pages
during design).

### 2. Backend access control for six pages

**Mechanism:** two new endpoint-name sets in `web_viewer.py`, checked
inside the existing `global_auth_gate()` before-request hook, alongside
the current `_PUBLIC_PATHS`/`_PUBLIC_PREFIXES` check:

```python
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

`_PUBLIC_VIEWABLE_ENDPOINTS` is checked against `request.endpoint` (the
resolved Flask view-function name), **not** the raw path — this is
deliberate: `/jobs/<int:job_id>` has infinitely many literal paths, and
critically, a path-prefix check like `"/jobs/"` would also match the
existing `/jobs/quality` admin data-quality tool, which must **not**
become anonymously reachable. Flask resolves `request.endpoint` during
routing, before `before_request` hooks run, so it's already correctly
disambiguated (`"job_detail"` vs. the quality-review endpoint) by the
time the gate checks it — matching by endpoint name sidesteps the whole
class of prefix-collision risk.

`_PUBLIC_API_READS` uses plain path-string matching (a set, same pattern
as `_PUBLIC_PATHS` today) since all eight of these API paths are literal
strings with no URL parameters.

**Why this is safe for signed-in visitors too:** `g.current_user` is
already populated by a *separate*, unconditional `before_request` hook
(`load_logged_in_user`, registered ahead of `global_auth_gate`) that runs
on every request regardless of path — it doesn't consult
`_PUBLIC_PATHS` at all. So a logged-in user hitting `/dashboard` still
gets `g.current_user` populated from their session exactly as today; only
a request with no valid session sees `g.current_user is None`, which is
exactly the signal every `{% if not g.current_user %}` branch in the
redesign's templates already keys off. No template logic needs to change
to account for this — it already does the right thing once these routes
stop being force-redirected.

**Every other `/api/*` endpoint is untouched** — still fully gated,
exactly as today. The redesign's own JS already skips calling them for
anonymous visitors (verified directly against the shipped
`skills_intelligence.html`, `companies_intelligence.html`,
`titles_analytics.html`, `dashboard.js`: every deep-dive/detail fetch is
wrapped in `if (!window.GW_AUTHED) { ...show locked state...; return; }`
*before* the fetch call, so the request is never made at all for
anonymous visitors). No backend change is needed to enforce this — it's
already enforced by the existing gate simply not being touched for these
paths.

### 3. Close the `/api/skills/combinations` exposure gap

`skill_combinations()` (`web_viewer.py:1042`) currently runs a fixed
`LIMIT 50` SQL query and returns the full result to every caller — the
frontend alone decides how much to render (20 rows for signed-in
visitors, 5 for anonymous, per `skills_intelligence.html`'s
`const limit = window.GW_AUTHED ? 20 : 5`). An anonymous visitor reading
the raw JSON response (not just the rendered page) sees all 50 today.

Fix: when `g.current_user` is falsy, cap the query to `LIMIT 5` instead
of `LIMIT 50`. Signed-in behavior is completely unchanged (still up to
50 returned, frontend still trims display to 20). This makes the teaser
genuinely enforced server-side rather than a display-only convention,
without changing what any signed-in user sees or receives.

### 4. Domain migration

The live site is *not* served by a per-app Caddyfile as
`deploy/Caddyfile`'s template comments suggest — this VPS runs a single
shared Caddy container (`portfolio-caddy`) routing multiple unrelated
projects by domain, configured at `/opt/Portfolio/Caddyfile` on the VPS
(verified directly, not assumed from the repo's own deploy docs, which
describe the standalone-Caddy pattern this VPS doesn't actually use).
Current live block:

```
jobs.mujtaba0085.opior.com {
    reverse_proxy jobmarket-web:5000
}
```

Changes to that same file:

```
greywave.dev {
    reverse_proxy jobmarket-web:5000
}

jobs.mujtaba0085.opior.com {
    redir https://greywave.dev{uri} permanent
}
```

Plus updating `/opt/jobmarket/.env`'s `WEB_VIEWER_URL` to
`https://greywave.dev` (this value is used to construct the Google OAuth
callback redirect URI in `src/auth/routes.py:google_login()` — it must
match the domain the request actually arrived on for the OAuth round
trip to complete correctly).

**Two manual prerequisites only the user can do, called out explicitly
so this isn't silently blocked mid-rollout:**
1. Point `greywave.dev`'s DNS A record at `161.97.163.210` — confirmed
   not yet done as of this design. Caddy auto-provisions a Let's Encrypt
   certificate on first request to a new domain, but only once DNS
   actually resolves there.
2. Add `https://greywave.dev/auth/google/callback` to the Google Cloud
   Console OAuth app's authorized redirect URIs. Without this, Google
   will reject the OAuth callback with a redirect-URI-mismatch error for
   anyone signing in via the new domain — outside this codebase, cannot
   be automated here.

The domain-facing changes (Caddy config, `.env`) are written and ready
but the actual cutover — flipping the old domain to a redirect — happens
only after DNS is confirmed pointed, so the site is never left
unreachable on both domains at once.

### 5. SEO: robots.txt

Crawlers only ever check `/robots.txt` at the domain root — a file
dropped into `static/` would only be reachable at `/static/robots.txt`,
which no crawler looks for. This needs a dedicated route:

```python
@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")
```

`/robots.txt` is added to `_PUBLIC_PATHS` so crawlers (which never hold a
session) can actually reach it — same treatment as `/healthz` today.

Disallows everything, per the user's choice — not an SEO launch yet, easy
to loosen later once the rollout has settled.

## Testing / validation

1. Local: start the dev server, verify the six pages load without any
   session cookie present and show the teaser state (blurred charts,
   lock badges, KPI cards with real numbers) rather than a redirect to
   `/auth/login`. Verify a logged-in session still sees the full,
   unchanged experience on the same six pages.
2. Local: confirm the eight opened API endpoints return real data
   without a session; confirm every other `/api/*` endpoint still
   redirects/401s without one.
3. Local: confirm `/api/skills/combinations` returns exactly 5 rows
   without a session and up to 50 with one (existing signed-in behavior
   unchanged).
4. Local: run the existing pytest suite — no regressions expected, but
   confirm directly rather than assume (this touches the global auth
   gate, which several existing tests exercise).
5. Deploy to the VPS on the current domain first (no domain cutover yet)
   and spot-check the same things against the live site.
6. Once DNS for `greywave.dev` is confirmed pointed (manual, by the
   user): add the new Caddy block, confirm Let's Encrypt provisions
   correctly, confirm the OAuth round-trip works end-to-end on the new
   domain (requires the Google Cloud Console step above to already be
   done), *then* flip the old domain to a redirect.
