# Companies Intelligence — Pakistan-First IT Sections — Design Spec

## Goal

`/companies/intelligence` currently defaults to a single flat grid of every company across every category and country — "Every employer we've aggregated, blended across all connected sources." This makes it the last general-purpose page still untouched by the site's Pakistan-first/IT-priority default established everywhere else (`/jobs`, `/dashboard`). This spec makes it default to two ranked sections — IT companies hiring in Pakistan, then IT companies hiring worldwide — with an explicit toggle back to today's exact original behavior.

Directly completes the dashboard's "Top Hiring IT Companies" widget, whose "See all IT companies →" link already points here with `category=it&region=...` query params that are currently inert.

## Non-goals

- Changing the drill-down panel (click a company card → stats + top skills). Unaffected regardless of which section or mode a card is opened from.
- A region concept for "All Categories" mode — that mode is an unmodified reversion to today's page, which has never had country scoping and doesn't gain any here.
- NULL-inclusive IT filtering (the choice made for `/jobs`'s not-yet-built Category toggle, per `docs/superpowers/specs/2026-07-17-it-priority-launch-readiness-design.md`). This page's two sections are curated rankings extending the dashboard's "Top Hiring IT Companies" widget, which deliberately stays strict `field_category_id LIKE 'it.%'` — precision matters more than recall for a ranked leaderboard, and NULL-inclusion would readmit PJB's non-IT bulk the same way it would have on the dashboard widget.
- Opening this page to anonymous visitors — stays fully gated, confirmed already in yesterday's dashboard-region-restructure spec. An anonymous visitor clicking the dashboard's "See all IT companies →" link still hits the sign-in wall; not addressed here.
- Per-section independent search/sort toolbars. One shared search box and sort dropdown drive both sections at once (each section filters/sorts its own list using the same term/key).

## Design

**Default mode (any path — nav click, direct URL, or the dashboard's arrow link): two sections.**

1. **"IT Companies in Pakistan"** — companies ranked by IT job count, `field_category_id LIKE 'it.%' AND country = 'Pakistan'`.
2. **"IT Companies Worldwide"** — same IT scope, no country restriction — `field_category_id LIKE 'it.%'`, ordered the same way. (This is a superset of section 1, shown separately rather than de-duplicated — a Pakistan company that's also a leading global IT employer legitimately appears in both, matching how the dashboard's own Region toggle treats "All Countries" as broadening, not excluding, Pakistan.)

Each section is its own `.co-grid` of company cards, same visual design as today's cards, with stats recomputed within the IT scope (a company's `job_count`, `skill_diversity`, `remote_pct`, `location_count` reflect its IT hiring specifically, not its blended overall profile — so a hospital with one stray IT-tagged posting doesn't show inflated numbers borrowed from its hundreds of non-IT jobs).

**New "Category" toggle** in the toolbar (alongside the existing search box and sort dropdown): two options, "IT" (default) and "All Categories". Switching to "All Categories" reverts the page to exactly today's behavior — single flat grid via the existing `/api/companies/list` (untouched), original subtitle, no sectioning. This is a full mode swap, not a filter layered on top.

**Subtitle changes with mode:**
- IT mode (default): "Companies hiring for IT roles, Pakistan first."
- All Categories mode: today's unchanged "Every employer we've aggregated, blended across all connected sources."

**Search and sort stay shared across both IT sections**: the existing search box and sort dropdown (Most jobs / Most skills / Most remote / Most countries) apply independently to each section's own list — typing a term filters both grids simultaneously by that term; changing sort re-sorts both by the chosen metric. Same UI, same behavior as today, just driving two lists instead of one when in IT mode.

## Backend

New route, `GET /api/companies/list-it`, alongside the existing `companies_list()` (which is untouched, still backs "All Categories" mode exactly as today):

```python
@app.route("/api/companies/list-it")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def companies_list_it():
    """Two ranked IT-company lists (Pakistan, worldwide) for the
    Companies Intelligence page's new default mode - see
    docs/superpowers/specs/2026-07-18-companies-intelligence-it-first-design.md.
    Strict field_category_id LIKE 'it.%' throughout, matching the
    dashboard's Top Hiring IT Companies widget - a curated ranking, not
    a broad browse list, so precision over recall (no NULL-inclusion)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    def _fetch(country_clause):
        cursor.execute(f"""
            SELECT
                j.company,
                COUNT(DISTINCT j.job_id) as job_count,
                COUNT(DISTINCT s.normalized_skill) as skill_diversity,
                COUNT(DISTINCT j.country) as location_count,
                SUM(CASE WHEN LOWER(j.remote_type) = 'remote' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as remote_pct
            FROM active_jobs j
            LEFT JOIN skills s ON j.job_id = s.job_id
            WHERE j.company IS NOT NULL AND j.company != '' AND j.field_category_id LIKE 'it.%'{country_clause}
            GROUP BY j.company
            HAVING job_count >= 2
            ORDER BY job_count DESC
            LIMIT 100
        """)
        return [{"company": row["company"], "job_count": row["job_count"],
                 "skill_diversity": row["skill_diversity"], "location_count": row["location_count"],
                 "remote_pct": round(row["remote_pct"], 1)}
                for row in cursor.fetchall()]

    pakistan = _fetch(" AND j.country = 'Pakistan'")
    global_ = _fetch("")
    conn.close()
    return jsonify({"pakistan": pakistan, "global": global_})
```

Same `HAVING job_count >= 2` floor as the existing endpoint (a company needs at least 2 IT postings to appear — consistent with today's "at least 2 jobs of any kind" threshold, just scoped to IT).

## Frontend

`templates/companies_intelligence.html` and its inline script:

- Add the Category `<select>` (IT / All Categories) to `.co-toolbar`.
- Add two named grid containers for IT mode (`#coGridPakistan`, `#coGridGlobal`), each with its own `<h3>` section heading, initially hidden; the existing `#coGrid` (today's single flat grid) stays for "All Categories" mode, also toggled visibility-wise.
- On load and on Category-toggle change: IT mode fetches `/api/companies/list-it` and populates both sectioned grids via the existing `renderGrid()`-style rendering logic (reused, called twice — once per section — rather than duplicated); All Categories mode fetches the existing `/api/companies/list` exactly as today.
- `applyView()`'s existing search/sort logic is reused for each section's own array in IT mode, and for the single array in All Categories mode — same filter/sort functions, just invoked per-section when there are two lists instead of one.
- The page's `?category=it` / `?category=all` query param (already sent by the dashboard's "See all IT companies →" link) sets the toggle's initial value on load, so arriving from that link lands directly in the matching mode without an extra click. **No query param at all (a plain nav click or direct URL) defaults to `it`** — same default as every other value in this spec. `?region=` is not consumed here — the two-section layout shows both regions simultaneously by design, so there's nothing for a region param to select between.

## Definition of done

Visiting `/companies/intelligence` by any path defaults to two ranked sections — IT companies in Pakistan, then IT companies worldwide — with correctly IT-scoped stats per company. A Category toggle switches to "All Categories," which reproduces today's page exactly (single grid, original subtitle, `/api/companies/list` unchanged). Search and sort work across both IT sections simultaneously. The dashboard's existing "See all IT companies →" link lands in the correct, already-matching mode.
