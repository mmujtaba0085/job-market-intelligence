# IT-Priority Launch Readiness — Design Spec

## Goal

The owner is about to share this platform publicly with the Pakistani CS-student community. A first impression that isn't "interesting or motivating enough" — a jobs list dominated by newspaper-classified hospital/bank/government postings, or a dashboard that reads as generic pipeline stats rather than real market signal — risks losing the audience before it gets going. This spec makes Pakistani IT jobs the priority wherever jobs are shown, accepting today's imperfect PJB categorization rather than waiting for it to be tuned first.

**Grounding data (checked during this brainstorm, changed the design):** a strict `field_category_id LIKE 'it.%'` filter across the Pakistan+Global scope (25,170 jobs) only matches 4,485 (17.8%) — not because most jobs aren't IT, but because 9,252 (36.8%) have no category tag at all, including real losses at known dedicated tech companies (10Pearls: 68 total, 18 tagged; Contour Software: 103 total, 39 tagged; Devsinc: 32 total, 18 tagged). Defaulting `/jobs` to a strict filter would make the site look like it collapsed from ~25K to ~4.5K jobs and would hide genuine postings from IT-only companies purely because the classifier hasn't caught up — the opposite of "motivating." See Part 2 for the fix.

This explicitly reverses two decisions made earlier in the same session, both documented here rather than silently overwritten:

1. **Supersedes the "hard prerequisite" in `docs/superpowers/specs/2026-07-16-pakistan-dashboard-package-design.md`**, which blocked the "Top IT Jobs" and "Top Hiring IT Companies" widgets until PJB categorization Task 2 (keyword tuning + Groq routing) shipped and was spot-checked. That tuning work has not happened. The owner's explicit call: ship now on current accuracy (~70-75% precision on PJB's `it.*` tag per the 2026-07-16 spot-check, 42% of PJB still uncategorized, recall unmeasured) rather than wait. Both widgets' own design (selection query, count, caching, click-through) is unchanged from that spec — only the gate is lifted.
2. **Supersedes a decision made earlier in this same brainstorming session** (`docs/superpowers/specs/2026-07-17-dashboard-deops-redesign-design.md`) to keep the Multi-Location Jobs table unchanged. Widget 1 from the Pakistan Dashboard Package spec explicitly replaces that table. The owner reversed the "keep it" call when reconsidering the whole page for launch-readiness — flagging explicitly here since it happened minutes after the original decision, not because there's any doubt about the reversal itself.

## Non-goals

- Re-litigating the two widgets' own design (selection, count, caching) — already fully speced in the Pakistan Dashboard Package doc; this spec only lifts the gate on building them.
- Running PJB categorization Task 2 (keyword tuning + Groq routing) — explicitly deferred, not a prerequisite anymore. Still worth doing eventually for its own sake (see `project_pjb_categorization_status` memory), just not gating this launch.
- Applying IT-category filtering to the *general* dashboard widgets (Trends, Top 10 Skills, Geographic Distribution, Emerging/Declining Skills, the general Top Hiring Companies table, the KPI row). Those stay Pakistan+Global-scoped exactly as they are today — this spec's IT-priority push is scoped to `/jobs` (where a visitor is actively browsing postings) and the two dedicated dashboard widgets, not a blanket re-scope of every widget on the page.
- A finer-grained category taxonomy filter (e.g. separate "IT - Software" vs. "IT - Data" options). The new `/jobs` Category control is binary — IT / All Categories — matching the existing Region control's exact shape, not a multi-value filter.
- Retroactively re-tagging or improving any job's `field_category_id` — this spec only changes what's *displayed by default*, never touches classification data itself.

## Part 1: Dashboard widgets (lifting the existing gate)

Build both widgets exactly as speced in `docs/superpowers/specs/2026-07-16-pakistan-dashboard-package-design.md`:

- **"Top IT Jobs"** replaces the Multi-Location Jobs table entirely. `field_category_id LIKE 'it.%' AND country = 'Pakistan'`, ordered by recency, 5-6 jobs, no backfill from non-Pakistan/non-IT jobs if fewer are available, click-through to the job's own detail page, a separate "See all IT jobs" link to `/jobs` filtered to the new Category=IT default (see Part 2 — this link needs no special query param since IT is now `/jobs`'s own default). Same route-level caching as every other dashboard widget.
- **"Top Hiring IT Companies"** is new, additional (not a replacement). Same `field_category_id LIKE 'it.%' AND country = 'Pakistan'` scope, grouped by company, same shape as the existing general Top Hiring Companies widget. Heading text: "Top Hiring IT Companies" (confirmed exact wording in the original spec).

Both respect the existing Active/Historical status window the same way every other dashboard widget does.

**Deliberately stays strict `it.%`-only, not NULL-inclusive (unlike Part 2 below):** these are small, curated, ranked displays (5-6 jobs; a company leaderboard), not exploratory browsing. Widget 1 is recency-ordered specifically, and classification runs on a lag behind ingestion — the newest jobs are the least likely to be classified yet, so NULL-inclusion on a recency-ordered list would systematically bias it toward *unclassified* recent jobs, some of which are non-IT and just haven't been tagged yet. Widget 2 would be worse: NULL-inclusion would readmit PJB's non-IT bulk through the untagged loophole, undoing the entire point of the widget (PJB jobs are rarely NULL — they're confidently tagged into their real category — so the strict filter already excludes them correctly). Part 2's `/jobs` toggle is a different context: a large, self-evaluated list where a visitor can see the title/company themselves and judge relevance, where hiding untagged-but-real postings is the bigger cost.

## Part 2: `/jobs` Category toggle

Mirrors the existing Region toggle (`docs/superpowers/plans/2026-07-16-pakistan-first-default-experience.md`) exactly, as a second, independent dimension alongside it:

- New query param `category`, values `it` (default) / `all`.
- New shared helper `_category_scope_clause(category: str, alias: str = "") -> str` in `web_viewer.py`, same signature shape as `_region_scope_clause()`. **Deliberately NULL-inclusive, not strict-only** — confirmed during this brainstorm after checking real coverage numbers (see Grounding data above): a strict `it.%`-only filter would hide 82% of jobs by default, including real postings from IT-only companies the classifier simply hasn't tagged yet. Returns `f" AND ({alias}field_category_id IS NULL OR {alias}field_category_id LIKE 'it.%')"` when `category == "it"` (shows everything except jobs *confidently* tagged as a real non-IT category — correctly still excludes PJB's healthcare/education/engineering-trades/business bulk, since those are positively tagged, not NULL), else `""`.
- New `_default_category()` helper, same query-param > cookie > hardcoded-default priority as `_default_region()`: `request.args.get("category") or request.cookies.get("jmi_category", "it")`.
- New `jmi_category` cookie, same write pattern as `jmi_region` (`path=/;max-age=31536000;SameSite=Lax`).
- New "Category" `<select>` on `/jobs` only (not the dashboard's general widgets — see Non-goals), positioned next to the existing Region control in the filter sidebar: two options, "IT Jobs" (default) and "All Categories".
- Applies **only to `/jobs`**, composed with the existing Region and Status/Window clauses via simple AND-concatenation (same pattern already used for Region+Status composition) — not applied to any dashboard route.
- Same anonymous-visitor treatment as Region: not reset by the anonymous-filters-ignored block in `jobs_list()` (that block only resets signed-in-only filters — market/remote/search/country/source/company/skills/dates/status; Region already isn't in that list, Category joins it for the same reason: this is a no-sign-in-required default, not a gated filter).
- Same "Active Filters" badge treatment as Region: a badge shows when `current_category != 'it'` (i.e. when a visitor has broadened to "All Categories"), matching the existing `current_region != 'pk'` badge pattern.

## Definition of done

`/jobs` defaults to Pakistani IT postings for every visitor (signed-in or anonymous), with an explicit, stickily-remembered toggle to see every category. The dashboard's Multi-Location Jobs table is replaced by "Top IT Jobs"; a new "Top Hiring IT Companies" widget appears alongside the existing general companies table. Both changes ship on today's PJB categorization accuracy, not gated on further tuning work.
