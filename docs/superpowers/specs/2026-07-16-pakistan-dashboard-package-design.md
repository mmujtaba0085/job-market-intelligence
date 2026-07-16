# Pakistan Dashboard Package — Design Spec

## Goal

Two new/replaced dashboard widgets surfacing Pakistan-relevant IT content, plus the policy decision behind them: **every visitor is assumed Pakistani, for now** — no IP geolocation, no per-visitor personalization. This removes the need for GeoLite2 (previously approved, now superseded by this simpler decision) and the per-visitor cache-safety concerns that came with it. Both widgets use one universal ranking, identical for every visitor, so they stay ordinary cacheable routes like the rest of the dashboard.

## Non-goals

- IP-based geolocation / GeoLite2 — explicitly dropped in favor of "assume Pakistani." May be revisited later if the assumption stops being good enough (e.g. if the visitor base becomes meaningfully international), but that is a future decision, not part of this spec.
- Any other dashboard widget not named below.
- The PJB categorization work itself (`docs/superpowers/specs/2026-07-16-pjb-categorization-design.md`) — this spec's two widgets are **blocked on that one shipping first**, not part of it.

## Hard prerequisite

Both widgets below query `field_category_id LIKE 'it.%'` combined with `country = 'Pakistan'`. That combination is only trustworthy once PJB categorization (Task 2: keyword tuning + Groq routing, since the divider-signal approach was ruled out by the spike) is actually done — PJB is the dominant source of Pakistan-country jobs, and today its `field_category_id` is 100% unpopulated (confirmed: classification backlog hasn't reached PJB's job-ID range yet as of this spec). **Do not build/deploy either widget until PJB categorization Task 2 has shipped and been spot-checked against real data.**

## Widget 1: "Top IT Jobs" (replaces the multi-location widget)

Replaces `dashboard_location_diversity()` / the "multiple locations" widget on the dashboard entirely.

- **Selection:** `field_category_id LIKE 'it.%' AND country = 'Pakistan'`, ordered by recency (`posted_date` falling back to `first_seen_at`, same fallback used elsewhere in this app).
- **Count:** 5-6 jobs.
- **Pakistan+IT only, no backfill:** if fewer than 5-6 Pakistan+IT jobs are available, the widget shows fewer. It never fills remaining slots with non-Pakistan or non-IT jobs. (Confirmed decision — the alternative, backfilling with any recent IT job, was explicitly rejected.)
- **Click-through:** clicking a job card goes to that job's own detail page — standard behavior, same as every other job card in the app. A separate "See all IT jobs" link/button (not on each card) navigates to `/jobs` filtered to IT category.
- **Caching:** ordinary route-level caching (same `@cache.cached(timeout=900, key_prefix=_role_aware_cache_key)` pattern already used by the other dashboard widgets) — safe now that the ranking is universal, not personalized.
- **Respects the Active/Historical window** (shipped 2026-07-16) the same way every other dashboard widget does, via the existing `_status_window_clause()` helper.

## Widget 2: "Top Hiring IT Companies" (new, additional widget)

A new widget, not a replacement. Heading text: **"Top Hiring IT Companies"** (not "...of Pakistan" — confirmed exact wording).

- **Selection:** same `field_category_id LIKE 'it.%' AND country = 'Pakistan'` scope as Widget 1, grouped by company, ordered by job count descending — same shape as the existing `dashboard_companies()` widget, just scoped to Pakistan+IT.
- **Static, not personalized:** same universal ranking for every visitor, same caching as every other company-style widget today.

## Definition of done

Both widgets ship together, after PJB categorization Task 2 is done and its accuracy has been spot-checked against real re-classified PJB data (not just theoretical — confirm a sample of PJB jobs now tagged `it.*` are genuinely IT roles, and a sample of non-IT PJB jobs are correctly NOT tagged `it.*`).
