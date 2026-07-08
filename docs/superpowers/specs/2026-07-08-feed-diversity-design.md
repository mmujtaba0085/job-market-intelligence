# Feed Diversity — Design Spec

## Context

Job Market Intelligence aggregates jobs from ~20 sources of wildly varying volume.
On production, `active_jobs` (a view over `jobs` filtering `listing_status !=
'hidden'`) is dominated by a handful of sources — measured directly:

| Source | Share of active jobs |
|---|---|
| Himalayas | 43.0% |
| Pakistan Jobs Bank | 11.6% |
| GitHub: jobright-ai Engineer Internship | 8.7% |
| Arbeitnow | 8.7% |
| (15 more sources) | remaining ~28% |

Neither the human-facing `/jobs` browse page nor the public `/api/jobs` endpoint
does anything beyond a strict `posted_date DESC` sort — no source balancing. With
Himalayas alone accounting for 43% of the catalog, a plain recency sort means a
large fraction of what a browsing user sees on any given day is Himalayas
postings, which reads as repetitive even though the underlying catalog is diverse.

## Scope

**In scope:** the `/jobs` browse page only (`web_viewer.py:jobs_list`).

**Explicitly out of scope:** the public `/api/jobs` endpoint (used by API key
holders and the two Apify actors built earlier — changing its default order would
be a breaking change for external consumers, not evaluated here) and the Google
Sheets exports (a different audience/workflow). Both could be revisited as
separate, later specs if diversity is wanted there too — this spec does not
preclude that, it simply doesn't cover it.

## Mechanism

**Target distribution:** equal weight per source — each active source gets
roughly the same presence in the default view, regardless of its total volume.
Rejected alternative: capped/diminishing-returns weighting (larger sources keep
proportionally more presence, just bounded) — equal weight was chosen as the more
direct fix for the specific complaint (a single source visibly dominating).

**Ordering:** for each source, jobs are ranked by recency within that source
(`ROW_NUMBER() OVER (PARTITION BY source_name ORDER BY posted_date DESC,
ingested_at DESC)`). The final display order sorts by that per-source rank first,
then by date as a tiebreak — every source's most-recent job appears before any
source's second-most-recent job, and so on. This is a deterministic round-robin,
not randomized sampling: the same page shows the same jobs until new data arrives
or ranks are recomputed, so pagination, bookmarking, and repeat visits behave
predictably. A source running out of jobs simply stops contributing rows; nothing
else breaks or needs special-casing.

**Live vs. precomputed:** computing this ranking live, on every request, via the
window-function query above was evaluated and measured directly against the
production-scale dataset's actual query planner (locally, 14K-row dataset — the
production table is ~7x larger):

| Query | No index | With composite index `(source_name, posted_date, ingested_at)` |
|---|---|---|
| Current plain sort (baseline) | 6.8ms | — |
| Windowed, page 1 | 158.6ms | 39.2ms |
| Windowed, deep page (page 50) | 56.5ms | 50.2ms |

An index closes most of the gap, but a window function must rank the *entire*
filtered result set before `LIMIT`/`OFFSET` applies, so cost scales with total
matching rows, not page size — a property no index removes. This is a
data-volume scaling concern, distinct from concurrent-user load (already
well-handled by the app's existing SQLite WAL mode, which lets reads proceed
concurrently with each other and with periodic ingestion writes). Given the
dataset will keep growing, this spec computes the ranking **ahead of time**
instead of live per-request:

- `jobs` gets a new nullable `diversity_rank INTEGER` column (additive migration,
  same `ALTER TABLE ADD COLUMN` pattern already used elsewhere in this codebase —
  e.g. `google_id`/`auth_provider` on the auth DB's `users` table)
- A recompute step runs the window-function query once, scoped to exactly the
  population the default `/jobs` view queries (`listing_status IS NULL OR
  listing_status = 'active'` — deliberately narrower than `active_jobs`'s
  `!= 'hidden'`, so there's no mismatch between what was ranked and what's
  displayed), and writes the result back via a bulk `UPDATE`
- The default `/jobs` view then reads `ORDER BY (diversity_rank IS NULL),
  diversity_rank ASC, posted_date DESC` — a plain indexed sort, same cost profile
  as today's query, zero live computation
- Jobs inserted since the last recompute have `diversity_rank IS NULL` and sort
  after every ranked job via the explicit `(diversity_rank IS NULL)` clause above
  — the same null-last pattern the public `/api/jobs` endpoint already uses for
  `posted_date`, applied here to a different column — visible immediately, just
  not yet diversified, until the next recompute folds them in

## Recompute trigger

Every way new job data enters the system was traced to confirm a single hook
point is sufficient:

- `crawl` mode (Findwork, every 4 hours) and `ingest-only`/`weekly` modes (every
  12 hours / Sundays) are two genuinely separate code paths inside
  `src/orchestrator.py` — `crawl` never touches `run_pipeline_for_market`
- Manual admin-triggered runs (`/admin/pipeline/run` → `launch_pipeline()` →
  `subprocess.Popen([sys.executable, "-m", "src.orchestrator", "--mode", mode,
  ...])`) spawn the exact same entry point as the scheduled systemd timers
- All of the above funnel through `src/orchestrator.py`'s `main()`, which has a
  single call site: `stats = _run(args, week_start)`

The recompute is hooked there — right after `_run()` succeeds — gated to skip
`report-only` (fetches no new data) and `backfill` (historical data, not the live
feed). This covers `crawl`, `ingest-only`, and `weekly`, at whatever cadence
actually triggered each one (as often as every 4 hours via `crawl`), with no new
schedule to introduce or maintain, and no risk of a write path being missed.

The recompute function lives in `src/analytics/diversity_rank.py`, matching the
existing pattern of `src/analytics/weekly_metrics.py` for this class of
"derived value computed from job data" concern.

## `/jobs` page behavior

A new `sort` query parameter: `diverse` (default) or `recent`.

Diversity ordering applies **only** in the exact baseline state: `status=active`
(the page's own default) and no other filter set (market, country, source,
company, search, skills, remote type, date range all empty). In that state,
default sort is the precomputed-rank order described above.

The moment any filter is applied, or `status` changes away from `active`, the
page falls back to today's existing `ORDER BY posted_date DESC, ingested_at DESC`
— the precomputed rank doesn't mean anything for an arbitrary filtered subset
(it was computed against the unfiltered `status=active` population), so it isn't
used there rather than silently producing a meaningless order.

**UI:** a sort toggle (Diverse / Most Recent) is shown only when the page is in
the exact baseline state. The moment any filter is applied or status changes,
the toggle disappears — the page is implicitly on Most Recent, with no toggle
offering a choice that wouldn't do anything meaningful. It reappears once every
filter is cleared and status returns to `active`.

## Testing / validation

- Migration: `diversity_rank` column added, nullable, existing queries and the
  full test suite unaffected by its presence
- `src/analytics/diversity_rank.py`: unit tests verifying ranks are assigned
  correctly (each source's own jobs numbered 1..N in recency order), verifying
  jobs outside the `status=active` population are left with `diversity_rank
  IS NULL` (not ranked, not miscounted into another source's sequence), and
  verifying idempotency (running recompute twice with no new data produces the
  same ranks)
- `web_viewer.py:jobs_list`: tests verifying the baseline state (no filters,
  `status=active`, no explicit `sort`) uses `diversity_rank` ordering; verifying
  any single filter or non-active status forces `posted_date DESC` ordering;
  verifying explicit `sort=recent` forces plain ordering even in the baseline
  state; verifying unranked (`NULL` diversity_rank) jobs still appear, sorted
  after ranked ones
- `src/orchestrator.py`: test verifying the recompute call fires after `crawl`,
  `ingest-only`, and `weekly` modes succeed, and does not fire after
  `report-only` or `backfill`

## Out of scope (confirmed, not just deferred)

- `/api/jobs` (public API) and Google Sheets exports — see Scope above
- Randomized/non-deterministic sampling — rejected during brainstorming in favor
  of deterministic round-robin (see Mechanism)
- Capped/proportional-with-ceiling weighting — rejected in favor of equal weight
  per source (see Mechanism)
