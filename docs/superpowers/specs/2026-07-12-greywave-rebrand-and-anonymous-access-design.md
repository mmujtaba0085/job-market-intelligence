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

Digging into that gap led to a bigger finding, confirmed empirically
against real production data (not theorized): two of the eight endpoints
being opened to anonymous traffic are genuinely slow —
`/api/skills/combinations` takes **~2.4-3 seconds** per call (a self-join
across the full `skills` table), and `/api/titles/top` takes **~2.3
seconds** (a cheap query followed by an expensive Python-side aggregation
loop over 73,734 rows). Both were previously shielded from most of this
cost by simply requiring a login; opening them to anyone changes the
math. Several query-rewrite options were tested directly against a
scratch copy of production data before settling on the fix — see section
3 for the numbers, including a "smart" rewrite that was tested and
rejected because it made things *worse*.

Facts below were verified directly against the current codebase, not
assumed.

## Goals

1. Ship the GreyWave rebrand (name, wordmark, icons, copy, favicon) across
   the 13 templates the package covers, plus `base.html` (the shared
   layout every page extends — see section 1 for why this is needed
   despite the package's own README saying otherwise).
2. Make six pages (dashboard, jobs list, job detail, skills intelligence,
   companies intelligence, titles analytics) genuinely viewable by
   anonymous visitors, with a teaser experience that funnels them toward
   "Continue with Google" — while signed-in visitors keep seeing exactly
   what they see today, unchanged.
3. Migrate the live domain to `greywave.dev`, keeping the current domain
   working as a redirect rather than breaking existing links/bookmarks.
4. Close the data-exposure gap found during review
   (`/api/skills/combinations` over-sending to anonymous requests) and
   fix the two genuinely slow queries among the eight newly-public
   endpoints, so opening them to anonymous traffic doesn't mean serving
   multi-second responses to the public.
5. Cache all eight newly-public API endpoints using the Flask-Caching
   layer already built for the page routes, so repeat requests (which
   will now come from anyone, not just a small logged-in user base) don't
   recompute from scratch every time.

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

## Design

### 1. Rebrand + redesign rollout

Copy the package's `templates/`, `static/css/filters.css`,
`static/favicon.svg`, `static/js/dashboard.js`, and the three new shared
partials (`_brand.html`, `_icons.html`, `_gating.html`) into the real
app's `templates/` and `static/`, preserving paths exactly as the
package's own README lays out. `static/js/filters.js` is included in the
package but is confirmed byte-for-byte identical to what's already
live (diffed directly) — skip that copy.

**`base.html` also needs replacing, despite the package's own README
claiming otherwise.** The README states it's "already-live... included
for reference/context only, not part of this redesign's 13 pages" — that
claim doesn't hold up under direct verification (likely stale, carried
over from an earlier round of this same redesign effort; this is package
revision 3). Diffed directly: the reference copy has a completely
different color palette (`--bg-base: #FBFBF9`, neutral off-white with a
forest-green accent) from what's actually live (`--bg-base: #FBF6EF`,
warm cream/tan) — and since the 12 other redesigned templates only ever
reference `var(--bg-base)` etc. without redefining them, those variables
have to change in `base.html` itself for the rebrand to have any visual
effect at all on colors. The header/nav wordmark (`{{ brand.wordmark(19)
}}`, imported via `{% import "_brand.html" as brand %}`) and the favicon
link also only exist in `base.html`, not in any child template — without
updating it, the shared header would still read "Job Market Intelligence"
with no wordmark on every single page, redesigned or not.

Verified safe to replace wholesale (not just patch a few lines):
extracted every Jinja construct (`{% ... %}` and `{{ ... }}`) from both
versions and diffed the two sets directly. Everything in the live
version exists in the reference version — nothing is lost. The reference
version adds exactly three new constructs on top: the `_brand.html`
import, the `brand.wordmark(19)` call, and the favicon `url_for()` call.
Every block child templates override, every variable reference
(`g.current_user`, `dark_mode_locked`, `csrf_token`, etc.) is preserved
identically. Safe to copy in the same way as the other 13 templates.

No `web_viewer.py` changes are needed for this section — every template
still reads the same route-supplied variables, form field names, and
JS-targeted ids as today (verified against the package's own
file-by-file notes, spot-checked directly for the six gated pages during
design, and confirmed structurally for `base.html` via the Jinja-construct
diff above).

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

### 3. Precomputed analytics summaries — fixes the exposure gap *and* the performance problem

**What was tested, and why a query rewrite alone isn't the fix.** Against
a scratch copy of the real production `skills` table (260,213 rows, 192
distinct skill values), four things were measured directly, not assumed:

| Approach | Time | Verdict |
|---|---|---|
| Current query, `LIMIT 50` | 2444ms | baseline |
| Same query, `LIMIT 5` instead of 50 | 2474ms | **no improvement** — `co_count` is an aggregate that doesn't exist until `GROUP BY` finishes, so `LIMIT` can't be applied until after the expensive part is already done |
| Add a covering index `(job_id, normalized_skill)` | 1740ms | real, ~29% faster — `EXPLAIN QUERY PLAN` confirms both sides of the join become index-only scans — but still far too slow for a live request |
| Pre-filter to only "common enough" skills before joining | 4144ms | **worse** — only 192 distinct skills exist total, so the extra subquery cost outweighed any reduction in join size |
| Precompute once, read from a small summary table | 0.1-0.7ms | **~2,500-3,500x faster than any on-the-fly variant** |

The reason precomputation wins so decisively: only 192 distinct skills
exist, so only 13,532 pairs ever actually co-occur (confirmed by
materializing the full result) — a small, stable output size, even
though the *input* (raw skill-instance rows) is large and keeps growing.
Computing the full join is expensive; storing its result is cheap to
read back.

The same investigation was repeated for `/api/titles/top`
(`titles_top()`, `web_viewer.py:1224`), which is architecturally
different: it pulls all distinct `normalized_title` rows (**73,734** on
production) into Python and aggregates them into role families
(`_role_family()`, a two-regex seniority-prefix/suffix strip) via a
`defaultdict` loop, taking **2280ms total** end to end. Pushing
`_role_family()` into SQL as a registered function
(`conn.create_function("role_family", 1, _role_family)`) and grouping
there instead of in Python dropped this to **1019ms** — verified to
produce an identical top-30 result. That's a real ~2.2x improvement, but
notably smaller than skill-combinations' win: titles have 71,043 distinct
role families (barely fewer than the 73,734 raw titles — the seniority
strip doesn't collapse much), so the "vocabulary" here isn't small and
bounded the same way skills are. The fix is still precomputation — not
because the *output* is small in the same sense, but because this
endpoint only ever returns the top 30, so the summary table only needs to
store 30 rows regardless of how many distinct role families exist
underneath.

**The fix — two new small tables, refreshed once per ingestion run, not
computed per-request:**

New module `src/analytics/precomputed_summaries.py` (same pattern as the
existing `src/analytics/diversity_rank.py`), with `_role_family` and its
two regex constants moved here from `web_viewer.py` (so both this module
and `title_skills()`'s existing per-family lookup share one definition
instead of duplicating the regex logic):

```python
def recompute_skill_combinations(limit: int = 50) -> int:
    """Recompute the top N skill co-occurrence pairs into
    skill_combinations_summary. Full replace (DELETE + INSERT), not
    incremental — cheap enough at this scale and avoids drift."""
    conn = get_connection()
    try:
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
        return conn.execute("SELECT COUNT(*) FROM skill_combinations_summary").fetchone()[0]
    finally:
        conn.close()


def recompute_top_titles(limit: int = 30) -> int:
    """Recompute the top N role families into top_titles_summary, using
    the role_family() SQL UDF (verified ~2.2x faster than the previous
    Python-side aggregation over the full, high-cardinality title list)."""
    conn = get_connection()
    conn.create_function("role_family", 1, _role_family)
    try:
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
        return conn.execute("SELECT COUNT(*) FROM top_titles_summary").fetchone()[0]
    finally:
        conn.close()
```

New migration in `src/storage/db.py` (same inline-migration pattern as
011-013): `CREATE TABLE skill_combinations_summary (skill_a TEXT, skill_b
TEXT, co_count INTEGER)` and `CREATE TABLE top_titles_summary (title
TEXT, count INTEGER)`, plus the covering index
`CREATE INDEX idx_skills_job_normalized ON skills(job_id, normalized_skill)`
on the source table (the ~29% win from the table above — cheap to add,
speeds up the periodic recompute itself even though it no longer runs
per-request).

**Wired into the existing pipeline hook**, `src/orchestrator.py:614-618`,
right after the existing `recompute_diversity_ranks()` call, same
try/except-and-log pattern (a precompute failure must not crash the
whole ingestion run):

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

**Endpoint handlers simplify to plain reads**, and the exposure gap closes
as a side effect of the rewrite (not a separate patch):

```python
@app.route("/api/skills/combinations")
def skill_combinations():
    conn = get_db_connection()
    limit = 20 if g.current_user else 5
    rows = conn.execute(
        "SELECT skill_a, skill_b, co_count FROM skill_combinations_summary ORDER BY co_count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return jsonify([{"skill_a": r["skill_a"], "skill_b": r["skill_b"], "count": r["co_count"]} for r in rows])


@app.route("/api/titles/top")
def titles_top():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT title, count FROM top_titles_summary ORDER BY count DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return jsonify([{"title": r["title"], "count": r["count"]} for r in rows])
```

The visible skill-pair rows are unchanged for everyone (signed-in
visitors still see 20 rows, anonymous visitors still see 5) — but what's
actually sent over the wire changes for *both*: signed-in requests now
receive exactly 20 rows (not 50, since the frontend never displayed more
than 20 anyway — folding in the earlier "pull only what's shown" fix),
and anonymous requests now genuinely receive only 5 server-side, not 50
with only 5 rendered client-side. The summary table itself stores up to
50 for headroom (cheap — it's 50 rows either way), but neither endpoint
response ever needs to.

**Correction, found during final review (not caught when this was
originally written):** one *other* piece of rendered output does change
for anonymous visitors as a direct consequence of this trim.
`skills_intelligence.html`'s "N more combinations — continue with Google"
teaser row computed its count from `combinations.length - limit` — that
only ever showed anything when the backend returned more than `limit`
rows. Once the backend returns *exactly* `limit`, that comparison is
always false and the teaser row silently stopped rendering for anonymous
visitors. Fixed by showing the row unconditionally for anonymous
visitors with generic copy ("More combinations — continue with Google")
instead of a now-unavailable exact count — see
`templates/skills_intelligence.html`'s `loadSkillCombinations()`. The
page's other gating elements (the persistent bottom bar, the hard
overlay, the locked skill-detail panel) were unaffected and continued to
funnel anonymous visitors toward sign-in throughout.

**One-time backfill:** both summary tables are empty until the next
scheduled pipeline run. Run `recompute_skill_combinations()` and
`recompute_top_titles()` once manually as part of deploying this (same
one-shot-seed pattern already used elsewhere in this project), so the
tables aren't empty when this ships.

### 4. Caching the eight newly-public API endpoints

None of the `/api/*` endpoints are cached today — the Flask-Caching layer
built earlier only covers the eight outer page routes. That was a
reasonable call when every one of these endpoints required a login;
it stops being reasonable once anyone can call them with no account.
Since anonymous visitors already share one cache key (the existing
role/user-aware key's `"anon"` fallback, previously dead code, now live),
extending the same `@cache.cached(timeout=900, key_prefix=_role_aware_cache_key,
response_hit_indication=True)` decorator to all eight
`_PUBLIC_API_READS` endpoints is a direct reuse of already-tested
infrastructure — not a new caching mechanism.

This is complementary to section 3, not a substitute for it: caching
means a repeat request within 15 minutes costs nothing, but the *first*
request after a cache expiry still pays whatever the underlying query
costs — which is why the two slow queries needed fixing at the source,
not just caching over. For `/api/skills/combinations` and
`/api/titles/top` specifically, section 3's rewrite already makes even a
cold cache miss sub-millisecond, so caching them adds little — it's
applied anyway for consistency (all eight `_PUBLIC_API_READS` endpoints
get the same treatment, so there's one rule to remember, not a
special-cased exception) and because it's free: the same decorator, no
extra code path.

### 5. Domain migration

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

### 6. SEO: robots.txt

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
   without a session and exactly 20 with one (the rendered result is
   unchanged from today; the wire payload now matches what's actually
   displayed instead of over-fetching to 50).
4. Local: run `recompute_skill_combinations()` and `recompute_top_titles()`
   once manually against the local DB, confirm both summary tables
   populate, confirm `/api/skills/combinations` and `/api/titles/top`
   return the same results as the pre-rewrite queries did (correctness,
   not just speed).
5. Local: confirm the eight `_PUBLIC_API_READS` endpoints show `hit_cache:
   True` on a second identical request within the cache TTL (same
   verification pattern already proven for the page routes), and confirm
   an anonymous request and a signed-in request to the same endpoint
   don't share a cache entry.
6. Local: run the existing pytest suite — no regressions expected, but
   confirm directly rather than assume (this touches the global auth
   gate, which several existing tests exercise).
7. Deploy to the VPS on the current domain first (no domain cutover yet),
   run the one-time summary backfill there too, and spot-check the same
   things against the live site — including timing `/api/skills/combinations`
   and `/api/titles/top` directly to confirm they're now sub-millisecond
   reads, not the 2-3 second queries measured during design.
8. Once DNS for `greywave.dev` is confirmed pointed (manual, by the
   user): add the new Caddy block, confirm Let's Encrypt provisions
   correctly, confirm the OAuth round-trip works end-to-end on the new
   domain (requires the Google Cloud Console step above to already be
   done), *then* flip the old domain to a redirect.
