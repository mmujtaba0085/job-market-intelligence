# Pakistan-First Default Experience — Design Spec

## Mission context (why this exists)

This platform is being published for Pakistani CS students, new graduates, and job seekers — a free, trustworthy, single place to find every relevant job posting, in Pakistan and (for genuinely open remote roles) beyond. The owner's own framing: "the dashboard is meaningful, the jobs is relevant and in Pakistan, if they do wish to see outside they have the option." Every design decision in this spec follows from that sentence. International data (the platform's other ~100K jobs) stays fully available — as an explicit opt-in, not the default framing.

## Goal

Extend the "assume Pakistani" decision already made for the two new dashboard widgets (`docs/superpowers/specs/2026-07-16-pakistan-dashboard-package-design.md`) into a **site-wide default**: `/jobs` and every dashboard widget default to a Pakistan-relevant scope, with a single, prominent, sticky toggle to broaden to everything. This supersedes nothing in the existing two-widget spec — those two widgets stay permanently Pakistan+IT scoped regardless of this new toggle, since they're a narrower, IT-specific lens; this spec adds the broader, general-purpose scope that the *rest* of the site (KPIs, geo distribution, top skills, sources, general companies widget, and the `/jobs` listing itself) currently lacks.

## Non-goals

- Changing the two existing Pakistan+IT widgets' own always-Pakistan scoping — untouched by this spec.
- Real IP geolocation — still explicitly out, per the existing spec's decision.
- PJB categorization tuning itself (Task 2, `docs/superpowers/specs/2026-07-16-pjb-categorization-design.md`) — this spec's scope filter depends on `country`, not `field_category_id`, so it doesn't block on Task 2 the way the two IT widgets do. It's independent, can ship first.

## Grounding data (checked directly against production before finalizing scope)

- **PJB classification accuracy, spot-checked 2026-07-16**: of 25 random PJB jobs now tagged `it.*`, ~2 are clearly wrong (government "Research & Analysis"/"Legislative Review" roles tagged `it.data`, likely a fuzzy-similarity false match) and ~5-6 are genuinely ambiguous (bare subject-style titles like "Computer Science" or "Information Technology" with no other context to disambiguate a teaching post from an actual IT role). This confirms PJB Task 2 tuning is still a real prerequisite for the *other* spec's two widgets — noted here for completeness, not solved by this spec.
- **Remote-job country semantics**: confirmed the app's `country` field for a remote-friendly job typically reflects either the hiring company's location (`country='United States'`) or an explicit "open to anyone" signal (`country='Global'`, set explicitly by sources like Himalayas and Himalayas RSS for genuinely worldwide-open roles) — not a separate "eligible countries" field. A specific non-Pakistan country value on a remote job is a reasonable signal that the role is likely restricted to that country (matches real user experience: many "remote" postings are US/UK-restricted in practice), while `country='Global'` is the closer-to-reliable "genuinely open" signal.

## Scope definition: what counts as "Pakistan-relevant"

**`country IN ('Pakistan', 'Global')`.**

- `Pakistan` — jobs physically located in Pakistan (from PJB, Pakistani company boards, 10Pearls, and any other source's Pakistan-located postings).
- `Global` — jobs explicitly marked open to remote applicants anywhere, not tied to a specific country (Himalayas and similar sources set this explicitly for worldwide-open roles).
- Explicitly **excluded** from the default: jobs with a specific non-Pakistan country (even if `remote_type='remote'` — likely geo-restricted in practice, per the grounding data above) and jobs with `country` blank/`Unknown` (ambiguous — could be anything, safer to require the visitor opt into the broader view to see these rather than guess).
- The "All Countries" (broadened) view removes this filter entirely — shows everything, exactly like today's current default behavior. Nothing about the underlying data changes; only what's shown by default does.

## Where this applies

**Every** existing dashboard widget except the two already-Pakistan-scoped ones (`dashboard_kpis`, `dashboard_geo`, `dashboard_top_skills`, `dashboard_sources`, `dashboard_companies`, `dashboard_location_diversity` — noting `dashboard_location_diversity` is being replaced by the "Top IT Jobs" widget per the other spec, so this filter applies to whatever remains active at build time), plus `/jobs`' default listing.

## UI: a new, separate "Region" control

A new toggle, **not merged into the existing "Listings" (Active/Historical) dropdown** — region (where a job is) and listing status/window (whether it's active, and how recently it was posted) are orthogonal dimensions; combining them into one control would multiply into a confusing combinatorial list of options. Follows the same visual/interaction pattern as the existing "Listings" dropdown (same dropdown styling on the dashboard, same filter-section styling on `/jobs`), placed next to it — two clearly-separate, clearly-labeled controls, not one overloaded one.

- Two options: **"Pakistan"** (default) and **"All Countries."**
- **Sticky per-visitor preference**: stored in a cookie (same mechanism already used for `jmi_theme`), so a visitor who switches to "All Countries" doesn't have to re-select it on every page load. Cookie absent = default to "Pakistan."
- Applies identically for anonymous and signed-in visitors — the whole point is to serve the target audience by default without requiring an account.

## Technical approach (reuses existing patterns, no new mechanisms)

- New query param (`region`, values `pk` default / `all`) read the same way `status` already is on both `/jobs` and every dashboard route.
- New shared helper `_region_scope_clause(region: str, alias: str = "") -> str` in `web_viewer.py`, directly mirroring `_status_window_clause()`'s shape (same file, same signature pattern, same "AND clause fragment or empty string" contract) — applied alongside `_status_window_clause()` at every call site that already uses it, so region and status/window filters compose naturally (e.g., "Pakistan + Active" is just both clauses concatenated, no special-casing needed).
- Cookie read/write follows the exact pattern already established for `jmi_theme` in `base.html`'s existing theme-toggle JS — same cookie-write helper, same early-`<head>`-script pattern for avoiding a flash of unscoped content on load (mirrors the theme-flash-avoidance and notification-dismiss-flash-avoidance precedents already in this codebase).
- Caching: the existing `_role_aware_cache_key` already keys on `request.full_path` (confirmed earlier this session while shipping the Active/Historical window), so a new `region` query param automatically gets its own cache entry with zero additional cache-key work needed.

## Definition of done

`/jobs` and the general-purpose dashboard widgets default to showing only `country IN ('Pakistan', 'Global')` jobs for every visitor, with a clearly visible, stickily-remembered toggle to see everything. The two existing Pakistan+IT widgets are unaffected (still always-Pakistan regardless of this toggle). No changes to underlying data — purely a default-view change, fully reversible per-visitor at any time.
