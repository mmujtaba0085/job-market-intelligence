# Dashboard De-Ops Redesign — Design Spec

## Goal

Remove operational/pipeline-facing metrics from `/dashboard` that serve the site operator, not a job-seeking visitor — continuing the Pakistan-first mission reframing from 2026-07-16 ("the dashboard is meaningful, the jobs is relevant"). A job seeker has no use for knowing how many scrapers are running or how many jobs one specific source contributed; they care about what skills are in demand, who's hiring, and where.

## Non-goals

- The IT-scoped "Top Hiring IT Companies" / "Top IT Jobs" widgets from `docs/superpowers/specs/2026-07-16-pakistan-dashboard-package-design.md` — considered as a possible replacement for the removed metrics during this brainstorm, explicitly deferred. Real production data pulled during this brainstorm shows why it's not ready: Pakistan Jobs Bank (PJB), a newspaper-classifieds aggregator, dominates Pakistan-country job volume, and an un-tuned "top hiring company" view is dominated by non-IT PJB volume (Shaukat Khanum Hospital: 395 postings, National Bank of Pakistan: 206 — both 100% PJB) rather than real tech employers. Filtering to `field_category_id LIKE 'it.%'` fixes this (Bjak, Contour Software, PITB, NVIDIA appear instead), but that categorization isn't tuned yet — PJB: 638/11,803 in-scope jobs IT-tagged, 42% still uncategorized, last spot-check ~70-75% precision on the IT tag, recall unknown. Ship that pair of widgets only after PJB categorization Task 2 (keyword tuning + Groq routing) lands and is spot-checked — separate, already-speced work, not part of this change.
- Any new replacement KPI or widget for the freed space — deliberately left leaner rather than backfilled with something contrived. Confirmed directly: removing "Total Jobs" and "Active Sources" was not "these slots need filling," it was "these numbers don't belong here at all."
- The dashboard subtitle ("Aggregated across every connected source") — left unchanged, even though its "every connected source" framing is adjacent to what's being removed. Scoped out deliberately to keep this change contained to widgets/KPIs.
- Any other dashboard widget not named below (Trends, Top Skills, Geographic Distribution, Emerging/Declining Skills, Top Hiring Companies table, Multi-Location Jobs table) — all untouched, confirmed explicitly during this brainstorm rather than assumed safe.

## What's removed, and why

Three pieces, all sharing the same shape: they describe the *pipeline's* behavior, not the *job market's*.

1. **"Total Jobs" KPI card** — a raw cumulative count (currently ~25K under the Pakistan-first default region scope). Doesn't help anyone plan a job search; it's a vanity/scale number, not a market signal.
2. **"Active Sources" KPI card** — literally "how many scrapers are currently running." Zero relevance to a visitor.
3. **"Source Performance" widget** — the "jobs by data source" bar chart (e.g. Adzuna: 2,837, Jooble: 1,030). This is the one the owner named directly when raising this idea — a job seeker has no reason to care which scraper contributed which share of the listings.

## What stays, unchanged

Confirmed widget-by-widget during this brainstorm, not assumed:

- **Skills Tracked** KPI and **Remote %** KPI — both already market-relevant.
- **Job posting trends** chart, **Top 10 Skills** chart, **Geographic Distribution** chart, **Emerging Skills** widget, **Declining Skills** widget, **Top Hiring Companies** table, **Multi-Location Jobs** table — all untouched. (Multi-Location specifically confirmed to stay, even though the separate, deferred Pakistan-dashboard-package spec had proposed eventually replacing it with "Top IT Jobs" — that replacement is not part of this change.)
- Dashboard subtitle text ("Aggregated across every connected source").

## Layout consequences (no new CSS work required)

- **KPI row**: `.kpi-grid` already uses `grid-template-columns: repeat(auto-fit, minmax(220px, 1fr))`. Dropping from 4 cards to 2 makes the CSS Grid `auto-fit` behavior stretch the remaining 2 cards to fill the row automatically — confirmed by reading the existing CSS, no restyling needed.
- **Widget grid**: `.dashboard-grid` has no `grid-template-columns` declared at all (confirmed by reading the full `<style>` block) — it's already an implicit single-column stack, meaning the `grid-column: span 2` rules on the Trends / Top-Companies / Multi-Location widgets currently have no visual effect. Removing the Source Performance widget just removes one row from that stack; nothing reflows unexpectedly.

## Implementation shape

- `templates/dashboard.html`: delete the Total Jobs and Active Sources `<div class="kpi-card">` blocks; delete the Source Performance `<div class="widget">` block (including its `sourcesChart` canvas).
- `static/js/dashboard.js`: delete `loadSourcesChart()` and its call in `loadDashboard()`; delete the `total_jobs`/`active_sources` DOM-writing lines from `loadKPIs()` (the two `<div id="kpiJobs">`/`<div id="kpiSources">` targets no longer exist in the template, so these lines would otherwise silently fail on `null`).
- `web_viewer.py`: delete the `/api/dashboard/sources` route (`dashboard_sources()`) entirely, and trim `dashboard_kpis()`'s query/response to stop computing `total_jobs`/`active_sources` — both are now unused once the frontend stops calling/reading them, and this codebase's convention is to delete dead code rather than leave it computing values nothing reads.

## Definition of done

`/dashboard` no longer shows Total Jobs, Active Sources, or Source Performance anywhere; the KPI row visually fills its space with the remaining 2 cards; every other existing widget is pixel-for-pixel unchanged; `/api/dashboard/sources` no longer exists as a route.
