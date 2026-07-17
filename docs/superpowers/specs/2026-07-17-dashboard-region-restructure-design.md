# Dashboard Region Restructure — Design Spec

## Goal

Remove the dashboard's page-level Region toggle entirely — every general widget shows worldwide ("entirety") data, filtered only by the Listings (Active/Historical/All/Unverified) control. Region becomes a narrower, local concept that only applies to the two IT-specific widgets at the bottom of the page (Top IT Jobs, Top Hiring IT Companies), which get their own shared selector, plus a "see more" deep-link into `/jobs` carrying the selected filters forward.

## Supersedes

- **`docs/superpowers/specs/2026-07-17-dashboard-deops-redesign-design.md`'s removal of Source Performance.** That widget is kept after all, not deleted — it just loses its region-scoping (which happens automatically once Part A below removes the dashboard's only source of a `region` value). Total Jobs and Active Sources KPI removals from that spec are unaffected and still happening.
- **The dashboard-side half of `docs/superpowers/plans/2026-07-16-pakistan-first-default-experience.md`.** That plan's Task 1/2 built a page-level Region selector for both `/jobs` and the dashboard. This spec removes the dashboard's copy of that control and stops applying `_region_scope_clause()` in `dashboard_kpis()`, `dashboard_top_skills()` (fallback path), and `dashboard_companies()`. **`/jobs`'s own Region toggle is completely unaffected** — same helper functions, same cookie, same behavior, untouched.
- **`docs/superpowers/specs/2026-07-17-it-priority-launch-readiness-design.md`'s Part 1** (the two IT widgets) — their selection query (`field_category_id LIKE 'it.%'`) is unchanged, but they no longer read the (now-removed) dashboard-wide region default. They get their own dedicated, shared local selector instead (Part C below).

## Non-goals

- `/jobs`'s own Region toggle and the not-yet-built Category toggle from the IT-priority spec — both stay exactly as already speced/shipped, nothing here touches them.
- Opening `/companies/intelligence` to anonymous visitors — stays gated, confirmed explicitly. The Top Hiring IT Companies "see more" link goes there anyway; an anonymous visitor clicking it hits the sign-in wall, same as any other click on Skills/Companies/Titles today (not a new regression, not specially fixed either).
- Restructuring `/companies/intelligence` itself (a "Pakistan IT companies, then global IT companies" sectioned view) — real, wanted, explicitly deferred to its own follow-up spec once that page has actually been read. Not part of this spec's Definition of Done.
- Geographic Distribution, Trends, Emerging/Declining Skills — already region-independent (confirmed in yesterday's grounding), no change needed, not touched here.

## Part A: Remove the dashboard's page-level Region selector

- `templates/dashboard.html`: delete the "Region" `<label>`/`<select id="dashboardRegion">` block from `.dash-controls` (the Listings control stays).
- `static/js/dashboard.js`: `dashboardApi()` stops reading `#dashboardRegion` and stops appending `&region=...` to every API call. The `dashboardRegion` change-listener (cookie write + `loadDashboard()`) is removed.
- `web_viewer.py`: in `dashboard_kpis()` (all 4 queries), `dashboard_top_skills()` (fallback path only — the primary `weekly_metrics` path never had region applied), and `dashboard_companies()`, remove the `region = _default_region()` line and the `_region_scope_clause(region, ...)` call from each query. These routes now always run unscoped by country, exactly like Geographic Distribution already does.
- `_region_scope_clause()` and `_default_region()` themselves are **not deleted** — `jobs_list()` still uses both for `/jobs`'s own Region toggle.

## Part B: Source Performance — kept, now always "entirety"

- No file deletions. `dashboard_sources()` (the `/api/dashboard/sources` route), the Source Performance `<div class="widget">` in `dashboard.html`, and `loadSourcesChart()` in `dashboard.js` all stay exactly as they are today.
- The only change: `dashboard_sources()` drops its own `region = _default_region()` / `_region_scope_clause()` call (same removal as Part A's three routes), so it now always shows true, unscoped per-source counts — which is what surfaced yesterday's "why did every source except PJB/Himalayas shrink" question in the first place.

## Part C: Shared local Region selector for the two IT widgets

- New, small `<select>` — two options, "Pakistan" (default) and "All Countries" — positioned once, directly above "Top IT Jobs" (matching the exact spot described: "before the Top IT jobs"). Governs **both** Top IT Jobs and Top Hiring IT Companies, so the two widgets must be placed directly adjacent to each other (Top IT Jobs first, Top Hiring IT Companies immediately after) with the one selector sitting above the pair — not scattered elsewhere on the page the way the original spec's "new, additional widget" wording alone would have allowed.
- Two new dedicated API routes (dashboard widgets, not full pages, so they get their own endpoints rather than reusing `/api/dashboard/companies` or similar):
  - `GET /api/dashboard/top-it-jobs` — `field_category_id LIKE 'it.%'` always; additionally `AND country = 'Pakistan'` when the selector is "Pakistan", no country restriction when "All Countries" (still IT-scoped either way). Ordered by recency, 5-7 jobs, no backfill from non-IT jobs if fewer are available (same no-backfill rule as the original Pakistan Dashboard Package spec, now just also applying when the selector is broadened to "All Countries" — fewer available IT jobs still never gets padded with non-IT ones).
  - `GET /api/dashboard/top-it-companies` — same `field_category_id LIKE 'it.%'` + conditional `country = 'Pakistan'` scope, grouped by company, ordered by job count descending, same shape as the general Top Hiring Companies widget.
  - Both respect the existing Active/Historical status window via `_status_window_clause()`, same as every other dashboard widget.
  - Both read the selector's value the same way the old dashboard Region control did: query-param-free, just a plain `region` param read directly in `dashboardApi()`-style fetch calls (no cookie persistence needed — this is a page-session-only control, not a sitewide sticky preference like `/jobs`'s Region toggle).
- **Heading text unchanged from the original spec**: "Top IT Jobs" and "Top Hiring IT Companies" — no new naming needed for this part.

## Part D: "See more" deep-links

- **Top IT Jobs** gets a small "See all →" link/button below its 5-7 job cards, target: `/jobs?category=it&region={selector value}` — carries both the IT-category default (from the not-yet-built `/jobs` Category toggle, per the IT-priority spec) and whichever region the shared local selector was set to, so the destination page shows exactly the same scope the widget was previewing.
- **Top Hiring IT Companies** gets the same "See all →" pattern, target: `/companies/intelligence?category=it&region={selector value}` — the query params are forwarded even though that page doesn't yet do anything with them (see Non-goals); they're inert until the follow-up spec adds handling, at which point this link is already correct and needs no further change.

## Definition of done

The dashboard's top-level controls are just "Listings" (Status/Window) — no Region selector. Every general widget (KPIs, Trends, Top Skills, Geographic Distribution, Source Performance, the general Top Hiring Companies table) shows worldwide data, scoped only by status/window. Multi-Location Jobs is gone, replaced by "Top IT Jobs" (5-7 recent IT jobs) and a new "Top Hiring IT Companies" widget, both governed by one shared local Region selector positioned between them, each with a "See all" link carrying `category=it&region=...` forward. `/jobs`'s own Region toggle is untouched throughout.
