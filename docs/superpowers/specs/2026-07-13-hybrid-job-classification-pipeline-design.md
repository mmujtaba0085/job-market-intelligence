# Hybrid Job Classification Pipeline — Design Spec

## Goal

Activate the existing-but-dormant field taxonomy (`config/job_markets.py`, `src/market_classifier.py` — "Software Engineering", "Data, AI & Machine Learning", "Cloud, DevOps & Security", etc.) against live production data, with a Groq AI fallback for jobs the deterministic classifier can't confidently place, and give the admin visibility into how much of the catalog is classified, run over run.

## Non-goals (deferred to a later, separate spec)

- Groq proposing **new** taxonomy categories for jobs that don't fit any existing one.
- A convergence loop that feeds previously-AI-proposed categories back into later prompts.
- A promotion/audit workflow for admin-approving AI-proposed categories into `config/job_markets.py`.

These were explicitly scoped out during brainstorming — this spec only ever classifies jobs into the **20 existing leaf categories**. If neither the local classifier nor Groq can place a job into one of those 20, it stays unclassified and is recorded as such; nothing invents a 21st category.

## Grounding data (measured directly against production during brainstorming)

- Local classifier, run read-only against a 4,000-job random sample of live `active_jobs`: **66.4% classified / 33.6% unclassified**. Extrapolated to the full ~110k catalog: ~37,000 jobs would need Groq.
- Full-catalog local classification cost: ~148s per 4,000 jobs → **~68 minutes for a full pass** (the `SequenceMatcher` fuzzy-match fallback dominates this cost). This must run as chunked background work, never inline in a request.
- At the existing `grok_staging.py` batch size (25 rows/request), ~37,000 jobs ≈ **~1,480 Groq API calls** for a first full backlog burn-down.
- "Pakistan Jobs Bank" classifies at ~64% (close to average, not uniquely bad). The real weak spots are Himalayas (highest raw unclassified count, but also highest volume) and the GitHub internship-repo sources (jobright-ai, SimplifyJobs — poor classification because their "titles" are often repo-formatted entries, not clean job titles).

## Critical existing-code correction

The dormant design in `src/storage/db.py::_ensure_warehouse_schema` and `scripts/warehouse_rollout.py` writes the taxonomy result **directly into `jobs.market_id`**. That column is live today and holds the *ingestion-source grouping* (`ai_ml_global`, `swe_backend_global`, `pakistan_jobs_all`, ...), actively used by the Jobs List page's Market filter dropdown. This pipeline must **never** write to `jobs.market_id`. All new schema uses `field_category_*` naming and new tables named `job_categories` / `job_category_assignments`, never reusing "market" terminology, to stop the two concepts colliding.

## Data model

New migration: `src/storage/migrations/008_job_classification_pipeline.sql`, applied via the existing `run_migrations()` idempotent-migration mechanism in `src/storage/db.py`.

```sql
-- The taxonomy itself, seeded from config/job_markets.py on every migration run
-- (INSERT OR REPLACE keyed on category_id, so editing job_markets.py and
-- redeploying re-syncs this table automatically).
CREATE TABLE IF NOT EXISTS job_categories (
    category_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   TEXT,
    isco        TEXT,
    keywords    TEXT NOT NULL DEFAULT '[]'
);

-- Primary + secondary ("tag") category assignments per job, with the
-- evidence needed to audit why a job landed where it did.
CREATE TABLE IF NOT EXISTS job_category_assignments (
    job_id          INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    category_id     TEXT NOT NULL,
    assignment_type TEXT NOT NULL,   -- 'primary' | 'tag'
    confidence      REAL,
    method          TEXT,            -- 'local_hybrid_v1' | 'groq_v1'
    evidence_json   TEXT,
    assigned_at     TEXT NOT NULL,
    PRIMARY KEY (job_id, category_id, assignment_type)
);
CREATE INDEX IF NOT EXISTS idx_job_category_assignments_job      ON job_category_assignments(job_id);
CREATE INDEX IF NOT EXISTS idx_job_category_assignments_category ON job_category_assignments(category_id);

-- Groq fallback queue: one row per job awaiting/undergoing AI review.
-- Prompt + response are stored for audit; status distinguishes technical
-- failure (retryable) from a clean "no existing category fits" (not
-- retryable without a prompt/taxonomy change - that's the deferred spec).
CREATE TABLE IF NOT EXISTS groq_classification_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | succeeded | failed_technical | no_match
    prompt_sent         TEXT,
    response_received   TEXT,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_attempted_at   TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(job_id)
);
CREATE INDEX IF NOT EXISTS idx_groq_queue_status ON groq_classification_queue(status);

-- Run/iteration history, mirroring pipeline_runs' shape so the admin UI
-- pattern is consistent with the existing /admin/pipeline page.
CREATE TABLE IF NOT EXISTS classification_runs (
    run_id           TEXT PRIMARY KEY,
    run_type         TEXT NOT NULL,   -- 'local_incremental' | 'local_full_backfill' | 'groq_backlog' | 'groq_retry'
    trigger          TEXT NOT NULL,   -- 'manual' | 'schedule' | 'backfill_idle'
    status           TEXT NOT NULL DEFAULT 'running',  -- running | success | failed
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    cursor_job_id    INTEGER,          -- resume point for chunked backfill runs
    jobs_processed   INTEGER NOT NULL DEFAULT 0,
    jobs_classified  INTEGER NOT NULL DEFAULT 0,
    jobs_queued_groq INTEGER NOT NULL DEFAULT 0,
    error            TEXT
);
```

New columns on `jobs` (added via `_ensure_column`, matching the existing idempotent pattern):
- `field_category_id TEXT` — primary classification (FK-by-convention to `job_categories.category_id`), `NULL` = not yet attempted or unclassified.
- `field_classification_confidence REAL`
- `field_classification_method TEXT` — `'local_hybrid_v1'` or `'groq_v1'`
- `field_classification_attempted_at TEXT` — `NULL` distinguishes "never attempted" (eligible for local classify) from "attempted, stayed unclassified" (already went through both stages, not re-attempted by incremental runs).

## Local classification stage

`src/classification/local_stage.py` (new module):

- `classify_pending_jobs(conn, run_id, limit=None) -> dict` — selects jobs where `field_classification_attempted_at IS NULL`, runs `market_classifier.classify_job()` (unchanged, reused as-is) per job. Above threshold (existing 0.62 confidence / 2.0 score cutoff — now read from the same `get_config`/`set_config` mechanism as the other admin-configurable values in this pipeline, not hardcoded; exposed as a config field in the Admin UI section below) → writes `field_category_id`/confidence/method directly and inserts into `job_category_assignments`. Below threshold → leaves `field_category_id` NULL, still stamps `field_classification_attempted_at`, and inserts a `groq_classification_queue` row with `status='pending'`. Every call updates the `classification_runs` row for `run_id` (`jobs_processed`, `jobs_classified`, `jobs_queued_groq`) and, for chunked calls, `cursor_job_id`.
- `reclassify_all(conn, run_id, limit=None)` — same as above but ignores `field_classification_attempted_at` (re-processes everything); used only by the full-backfill run type, always preceded by a preview (see Admin UI).

Two ways this runs:
1. **Incremental, automatic, always-on.** On every 60s scheduler tick (see Scheduling below), if any job has `field_classification_attempted_at IS NULL` and no `classification_runs` row of type `local_incremental` is currently `running`, launch one. This processes newly-ingested jobs continuously with no admin action and no load-gating — the volume per tick is small (new jobs since last ingest, not the historical backlog) so it's cheap enough not to matter.
2. **Full re-classify, load-gated backfill.** Admin-triggered (or resumed automatically by the idle-scheduler once started) `local_full_backfill` run, processing ~500 jobs per chunk, gated by the idle-load check. Because this can **change** existing classifications (e.g., after editing `config/job_markets.py` or the threshold), the admin UI shows a preview (sample of jobs whose category would change, and aggregate before/after counts) before the admin confirms starting it — mirroring `/admin/normalize`'s preview→apply pattern.

## Groq fallback stage

`src/classification/groq_stage.py` (new module), reusing `src/ai/grok_staging.py`'s established patterns (key pool rotation from `config.settings.GROQ_API_KEYS`, `Retry-After` handling, batched requests) rather than duplicating them:

- Prompt sends **only `{category_id, name}` pairs** for the 20 leaf categories (not the full keyword lists — those are an artifact of the local matcher, not needed for an LLM, and keeping them out saves meaningful tokens across ~1,480 calls) plus a batch of `{job_id, title, description}`.
- Requests `response_format: json_object`, asking for a `results` array of `{job_id, category_id | null, confidence, reasoning}`. `category_id` must be one of the 20 given ids or `null`.
- Outcome handling per job:
  - API call throws / non-200 / malformed JSON → `status='failed_technical'`, `attempt_count += 1`, `prompt_sent`/`response_received` stored regardless (store the raw error as `response_received` if no valid response came back).
  - Valid response, `category_id` is one of the 20 → `status='succeeded'`, writes `field_category_id` etc. on `jobs` and a `job_category_assignments` row with `method='groq_v1'`, removes/marks the queue row done.
  - Valid response, `category_id` is `null` → `status='no_match'`, terminal (not retried — the deferred new-category spec is the only thing that could change this outcome).
- Two processing paths, same underlying batch function:
  1. **Hourly retry sweep** (`groq_retry` run type): picks up `status='failed_technical' AND attempt_count < 5`, oldest first. Rows that exhaust 5 attempts stay `failed_technical` but are excluded from further automatic retries (visible in the admin queue view as needing manual attention).
  2. **Backlog burn-down** (`groq_backlog` run type): unlike the local full-backfill, this one **auto-starts** (`trigger='backfill_idle'`) — the scheduler tick starts a new `groq_backlog` run whenever `groq_classification_queue` has `status='pending'` rows and no `groq_backlog` run is already active, no admin click required. This is the piece that matches "for the backfill, it only starts working when it senses the load is low": since the always-on incremental local stage keeps feeding `pending` rows into the queue as new jobs arrive, the backlog run effectively keeps itself topped up over time. Once started, its chunks (one 25-row batch each) are paced by the same idle-load check as everything else in this section.
- Admin can **delete** any `groq_classification_queue` row directly (removes it from all future consideration; does not affect other rows — there's no ordering dependency between rows, so deleting one never blocks or skips others).

## Load-aware scheduling (applies to both `local_full_backfill` and `groq_backlog`)

Extends the existing `_auto_scheduler_loop` thread in `web_viewer.py` (60s poll) rather than introducing a second thread or an external scheduler.

- `last_request_at` stamped by a `before_request` hook on every request **except** `/healthz` and `/static/*` (both excluded so health-check pings and asset loads don't make the site look permanently busy). Stored in the existing `pipeline_monitor`-style settings table so it survives a worker restart.
- Each tick, for any backfill-type run (`local_full_backfill`, `groq_backlog`) that is `status='running'` with more work left (`cursor_job_id` short of the end, or queue rows still `pending`): check `(now - last_request_at) >= 300s` AND no other `pipeline_runs`/`classification_runs` row is currently `running`. If both hold, process exactly one chunk (≈500 jobs local / one 25-row batch Groq) and persist the cursor. If not, do nothing this tick — the run simply stays `running` with no progress until the next quiet window. No kill logic, no separate "paused" status needed: chunk boundaries are small enough that "check, then maybe do one bounded unit of work" is the entire mechanism.
- Chunk sizes and the 300s idle threshold are admin-configurable via the existing `get_config`/`set_config` mechanism (same one already backing `ingest_interval_hours` etc.), not hardcoded.
- Manual override: an admin "Run Now" action on a backfill-type run processes the next chunk immediately regardless of load (useful if the admin explicitly wants to eat the performance cost right now).

## Admin UI

New route `/admin/classification` (`require_admin`, following the exact structure of `/admin/pipeline`):

- **Summary cards:** total jobs, classified (local), classified (Groq), queued for Groq (pending/failed_technical/no_match breakdown), never-attempted.
- **Category breakdown table:** count per `job_categories` leaf, most-populous first.
- **Run history:** table of `classification_runs`, mirroring `admin_pipeline.html`'s runs list — run_id, type, trigger, status, started/finished, jobs processed/classified, link to a detail view. This is what answers "how many were classified in this iteration or a previous one."
- **Groq queue viewer:** filterable by status, shows prompt/response per row (truncated with expand), per-row delete button.
- **Actions:** "Run Local Classification" (incremental, always available), "Full Re-classify" (preview → confirm → starts a load-gated backfill run), "Process Groq Backlog Now" (manual override), config fields for threshold / chunk sizes / retry cap / idle-seconds.

## Testing strategy

Following this codebase's existing test conventions (in-memory/tmp-path SQLite fixtures, Flask test client, no real network calls):

- `local_stage.classify_pending_jobs`/`reclassify_all`: unit tests against a small fixture DB, asserting correct `field_category_id`/queue-row creation for above/below-threshold cases, and that `reclassify_all` correctly overwrites prior assignments.
- `groq_stage`: unit tests with a **mocked** Groq HTTP call (no real API calls in CI) covering all three outcomes (succeeded / failed_technical / no_match), retry-eligibility filtering (`attempt_count < 5`), and prompt construction (correct category list, correct batch shape).
- Load-gating logic: the "should I process a chunk right now" decision must be a pure function of `(last_request_at, other_runs_running, now)` — not embedded in the sleep loop — so it's testable without waiting on real time, mirroring how `compute_next_run()` is already separated from `_auto_scheduler_loop`'s actual sleep in `pipeline_monitor.py`.
- Admin routes: `require_admin` gating, rendering, and the delete-queue-row action, following the pattern in `tests/test_public_viewable_routes.py`.

## Open follow-ups (explicitly out of scope here)

- The deferred "Spec B": Groq proposing new categories, convergence loop, promotion/audit workflow.
- Pakistan Jobs Bank collector gap (no postings since 2026-07-05) — separate, unrelated task, to be picked up right after this spec is approved.
