# Feed Diversity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a single high-volume job source (Himalayas, 43% of active jobs) from dominating the `/jobs` browse page's default view.

**Architecture:** A precomputed `diversity_rank` column on `jobs`, recomputed after every pipeline run that fetches new data, ranks each job by recency within its own source. The `/jobs` page's default (unfiltered, `status=active`) view sorts by that rank instead of plain `posted_date`, interleaving sources round-robin. Any filter or non-default status falls back to today's plain sort, since the precomputed rank has no meaning outside the population it was computed against.

**Tech Stack:** Flask + Jinja2, SQLite 3.45.3 (confirmed to support the `UPDATE ... FROM` + window-function syntax this plan uses), Python stdlib `sqlite3`, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-08-feed-diversity-design.md` — read it for full context if needed
- In scope: `/jobs` page only. Do not touch `/api/jobs` or the Google Sheets export path — both explicitly out of scope per the spec
- `diversity_rank` is computed only for jobs matching `listing_status IS NULL OR listing_status = 'active'` — deliberately narrower than the `active_jobs` view's `listing_status != 'hidden'`, so there's no mismatch between what's ranked and what's displayed
- Diversity ordering must be a deterministic round-robin (via the precomputed rank), never randomized
- The migration must be additive only — `ALTER TABLE ... ADD COLUMN`, following the existing `_ensure_column()` helper pattern in `src/storage/db.py`, never a destructive schema rewrite
- `python -m pytest tests -q` must pass after every task (baseline before this plan: whatever the current repo's suite reports — confirm it before Task 1 and treat any count above that baseline as a regression to fix)

---

## Task 1: Schema migration + `recompute_diversity_ranks()`

**Files:**
- Modify: `src/storage/db.py:271-277` (insert the new migration right after Migration 009, before `conn.close()`)
- Create: `src/analytics/diversity_rank.py`
- Test: `tests/test_diversity_rank.py`

**Interfaces:**
- Produces: `recompute_diversity_ranks() -> int` in `src/analytics/diversity_rank.py` — recomputes and writes `diversity_rank` for every job matching `listing_status IS NULL OR listing_status = 'active'`, returns the number of rows updated. Task 2 imports and calls this with no arguments.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_diversity_rank.py`:

```python
"""
tests/test_diversity_rank.py
─────────────────────────────
Unit tests for src/analytics/diversity_rank.py using an in-memory SQLite DB.
"""

import sqlite3

import pytest


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            source_name TEXT,
            posted_date TEXT,
            ingested_at TEXT,
            listing_status TEXT,
            diversity_rank INTEGER
        )
    """)
    return conn


def _insert(conn, rows):
    conn.executemany(
        "INSERT INTO jobs (job_id, source_name, posted_date, ingested_at, listing_status) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()


class TestRecomputeDiversityRanks:
    def test_ranks_each_source_independently_by_recency(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "active"),
            (3, "A", "2026-01-06", "2026-01-06T00:00:00", "active"),
            (4, "B", "2026-01-04", "2026-01-04T00:00:00", "active"),
            (5, "B", "2026-01-01", "2026-01-01T00:00:00", "active"),
        ])

        _recompute(conn)

        ranks = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}
        # Source A: job 3 (01-06) newest -> rank 1, job 1 (01-05) -> rank 2, job 2 (01-03) -> rank 3
        assert ranks[3] == 1
        assert ranks[1] == 2
        assert ranks[2] == 3
        # Source B: job 4 (01-04) newest -> rank 1, job 5 (01-01) -> rank 2
        assert ranks[4] == 1
        assert ranks[5] == 2

    def test_non_active_jobs_left_unranked(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "closed"),
            (3, "A", "2026-01-04", "2026-01-04T00:00:00", "hidden"),
        ])

        _recompute(conn)

        ranks = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}
        assert ranks[1] == 1
        assert ranks[2] is None
        assert ranks[3] is None

    def test_null_listing_status_counts_as_active(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [(1, "A", "2026-01-05", "2026-01-05T00:00:00", None)])

        _recompute(conn)

        rank = conn.execute("SELECT diversity_rank FROM jobs WHERE job_id = 1").fetchone()["diversity_rank"]
        assert rank == 1

    def test_idempotent(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "active"),
        ])

        _recompute(conn)
        first_pass = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}
        _recompute(conn)
        second_pass = {row["job_id"]: row["diversity_rank"] for row in conn.execute("SELECT job_id, diversity_rank FROM jobs")}

        assert first_pass == second_pass

    def test_returns_count_of_active_rows_updated(self):
        from src.analytics.diversity_rank import _recompute

        conn = _make_conn()
        _insert(conn, [
            (1, "A", "2026-01-05", "2026-01-05T00:00:00", "active"),
            (2, "A", "2026-01-03", "2026-01-03T00:00:00", "active"),
            (3, "A", "2026-01-01", "2026-01-01T00:00:00", "closed"),
        ])

        updated = _recompute(conn)

        assert updated == 2


class TestRunMigrationsAddsColumn:
    def test_diversity_rank_column_added(self, tmp_path, monkeypatch):
        import src.storage.db as db

        db_path = tmp_path / "jobs.sqlite"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        db.run_migrations()

        conn = sqlite3.connect(str(db_path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        conn.close()

        assert "diversity_rank" in columns

    def test_running_migrations_twice_does_not_error(self, tmp_path, monkeypatch):
        import src.storage.db as db

        db_path = tmp_path / "jobs.sqlite"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        db.run_migrations()
        db.run_migrations()  # must not raise on a second run

        conn = sqlite3.connect(str(db_path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        conn.close()
        assert "diversity_rank" in columns
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_diversity_rank.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.analytics.diversity_rank'` (the module doesn't exist yet) for the first class, and `AttributeError` on `db.DB_PATH` or column-not-found for `TestRunMigrationsAddsColumn` (the migration doesn't exist yet).

- [ ] **Step 3: Add the migration to `src/storage/db.py`**

Find (lines 271-279):

```python
        # Migration 009: active_jobs view — excludes listing_status = 'hidden'
        # Always recreated so definition stays current.
        conn.execute("DROP VIEW IF EXISTS active_jobs")
        conn.execute(
            "CREATE VIEW active_jobs AS"
            " SELECT * FROM jobs WHERE listing_status != 'hidden'"
        )

    conn.close()
```

Replace with:

```python
        # Migration 009: active_jobs view — excludes listing_status = 'hidden'
        # Always recreated so definition stays current.
        conn.execute("DROP VIEW IF EXISTS active_jobs")
        conn.execute(
            "CREATE VIEW active_jobs AS"
            " SELECT * FROM jobs WHERE listing_status != 'hidden'"
        )

        # Migration 010: diversity_rank — per-source recency rank, powers the
        # /jobs page's default round-robin sort (see src/analytics/diversity_rank.py)
        _ensure_column(conn, "jobs", "diversity_rank", "diversity_rank INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_diversity_rank ON jobs(diversity_rank)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_source_posted "
            "ON jobs(source_name, posted_date DESC, ingested_at DESC)"
        )

    conn.close()
```

- [ ] **Step 4: Create `src/analytics/diversity_rank.py`**

```python
"""
src/analytics/diversity_rank.py
────────────────────────────────
Computes a per-source recency rank ("diversity_rank") for active jobs, so the
/jobs page's default view can interleave sources evenly instead of a single
high-volume source dominating a strict posted_date sort.

Algorithm:
  1. Rank each active job within its own source by recency
     (ROW_NUMBER() OVER PARTITION BY source_name ORDER BY posted_date DESC,
     ingested_at DESC)
  2. Write that rank back to jobs.diversity_rank
  3. Sorting ORDER BY diversity_rank ASC then interleaves every source's most
     recent job first, then every source's second-most-recent, and so on —
     a deterministic round-robin, not randomized sampling.

Scoped deliberately to the exact population the /jobs page's default view
queries (listing_status IS NULL OR listing_status = 'active') — not the
broader active_jobs view (listing_status != 'hidden') — so there's no mismatch
between what's ranked and what's displayed.
"""

from __future__ import annotations

import logging
import sqlite3

from src.storage.db import get_connection

logger = logging.getLogger(__name__)


def recompute_diversity_ranks() -> int:
    """
    Recompute diversity_rank for every active job. Idempotent — safe to call
    repeatedly; jobs outside the active population are left with
    diversity_rank NULL.

    Returns:
        Number of active job rows updated.
    """
    conn = get_connection()
    try:
        return _recompute(conn)
    finally:
        conn.close()


def _recompute(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("""
        UPDATE jobs
        SET diversity_rank = ranked.rn
        FROM (
            SELECT job_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY source_name
                       ORDER BY posted_date DESC, ingested_at DESC
                   ) AS rn
            FROM jobs
            WHERE listing_status IS NULL OR listing_status = 'active'
        ) AS ranked
        WHERE jobs.job_id = ranked.job_id
    """)
    updated = cursor.rowcount
    conn.commit()
    logger.info("[diversity_rank] Recomputed diversity_rank for %d active jobs", updated)
    return updated
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_diversity_rank.py -v`
Expected: `10 passed` (5 in `TestRecomputeDiversityRanks`, 2 in `TestRunMigrationsAddsColumn`, plus this file has no other tests — confirm exactly 7 test functions collected and all pass; if the count differs from what you expect, read the pytest output to see which one is missing rather than assuming)

- [ ] **Step 6: Run the full test suite**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests -q`
Expected: no new failures beyond whatever the repo's pre-existing baseline was before this task (record that baseline now if you haven't already, by running this same command before Step 1, so you have something concrete to diff against)

- [ ] **Step 7: Commit**

```bash
git add src/storage/db.py src/analytics/diversity_rank.py tests/test_diversity_rank.py
git commit -m "feat: add diversity_rank column and recompute function"
```

---

## Task 2: Hook recompute into the orchestrator

**Files:**
- Modify: `src/orchestrator.py` (add import, add `_should_recompute_diversity()`, call it in `main()`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `recompute_diversity_ranks()` from Task 1 (`src/analytics/diversity_rank.py`)
- Produces: `_should_recompute_diversity(args: argparse.Namespace) -> bool` in `src/orchestrator.py` — pure decision function, no side effects

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator.py`:

```python
"""
tests/test_orchestrator.py
────────────────────────────
Tests for the diversity-rank recompute hook in src/orchestrator.py's main().
"""

import argparse

import pytest


def _args(mode=None, backfill=False):
    return argparse.Namespace(mode=mode, backfill=backfill, start=None, end=None, html=False, max_runtime=None, run_id=None)


class TestShouldRecomputeDiversity:
    def test_true_for_ingest_only(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="ingest-only")) is True

    def test_true_for_weekly(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="weekly")) is True

    def test_true_for_crawl(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="crawl")) is True

    def test_false_for_report_only(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode="report-only")) is False

    def test_false_for_backfill(self):
        from src.orchestrator import _should_recompute_diversity
        assert _should_recompute_diversity(_args(mode=None, backfill=True)) is False


class TestMainCallsRecompute:
    def test_recompute_called_after_ingest_only(self, monkeypatch):
        import src.orchestrator as orchestrator

        monkeypatch.setattr(orchestrator, "_parse_args", lambda: _args(mode="ingest-only"))
        monkeypatch.setattr(orchestrator, "run_migrations", lambda: None)
        monkeypatch.setattr(orchestrator, "_run", lambda args, week_start: {})
        monkeypatch.setattr(orchestrator, "_setup_logging", lambda run_id="", week="": None)
        monkeypatch.setattr("src.pipeline_monitor.start_run", lambda mode, trigger="schedule": "test-run-id")
        monkeypatch.setattr("src.pipeline_monitor.finish_run", lambda run_id, **kwargs: None)

        called = {"count": 0}
        monkeypatch.setattr(orchestrator, "recompute_diversity_ranks", lambda: called.__setitem__("count", called["count"] + 1))

        orchestrator.main()

        assert called["count"] == 1

    def test_recompute_not_called_after_report_only(self, monkeypatch):
        import src.orchestrator as orchestrator

        monkeypatch.setattr(orchestrator, "_parse_args", lambda: _args(mode="report-only"))
        monkeypatch.setattr(orchestrator, "run_migrations", lambda: None)
        monkeypatch.setattr(orchestrator, "_run", lambda args, week_start: {})
        monkeypatch.setattr(orchestrator, "_setup_logging", lambda run_id="", week="": None)
        monkeypatch.setattr("src.pipeline_monitor.start_run", lambda mode, trigger="schedule": "test-run-id")
        monkeypatch.setattr("src.pipeline_monitor.finish_run", lambda run_id, **kwargs: None)

        called = {"count": 0}
        monkeypatch.setattr(orchestrator, "recompute_diversity_ranks", lambda: called.__setitem__("count", called["count"] + 1))

        orchestrator.main()

        assert called["count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `TestShouldRecomputeDiversity` tests fail with `ImportError: cannot import name '_should_recompute_diversity'`; `TestMainCallsRecompute` tests fail similarly on the `monkeypatch.setattr(orchestrator, "recompute_diversity_ranks", ...)` line since that name doesn't exist on the module yet.

- [ ] **Step 3: Add the import**

Find (around line 45, in the existing analytics imports block):

```python
from src.analytics.category_analytics import compute_category_stats
from src.analytics.co_occurrence import compute_co_occurrence
from src.analytics.coverage_metrics import compute_coverage_stats
from src.analytics.temporal_trends import compute_trend_stats
from src.analytics.title_analytics import compute_title_stats
from src.analytics.weekly_metrics import compute_weekly_metrics
```

Replace with:

```python
from src.analytics.category_analytics import compute_category_stats
from src.analytics.co_occurrence import compute_co_occurrence
from src.analytics.coverage_metrics import compute_coverage_stats
from src.analytics.diversity_rank import recompute_diversity_ranks
from src.analytics.temporal_trends import compute_trend_stats
from src.analytics.title_analytics import compute_title_stats
from src.analytics.weekly_metrics import compute_weekly_metrics
```

- [ ] **Step 4: Add `_should_recompute_diversity()` and call it in `main()`**

Find (the full `main()` function):

```python
def main() -> None:
    args = _parse_args()

    # Ensure DB + tables exist
    run_migrations()

    week_start = _iso_week_start(date.today())
    week_str = f"{week_start.year}-{week_start.isocalendar()[1]:02d}"

    # Determine run_id before logging so the log file is named per-run
    from src.pipeline_monitor import finish_run, start_run
    mode = args.mode if not args.backfill else "backfill"
    run_id = args.run_id if (hasattr(args, "run_id") and args.run_id) else start_run(mode)

    _setup_logging(run_id=run_id, week=week_str)

    try:
        stats = _run(args, week_start)
        finish_run(run_id, status="success", **stats)
    except Exception as exc:
        finish_run(run_id, status="failed", error=str(exc))
        raise
```

Replace with:

```python
def _should_recompute_diversity(args: argparse.Namespace) -> bool:
    """Diversity rank recompute runs after any mode that actually fetches new job data."""
    if args.backfill:
        return False
    return args.mode != "report-only"


def main() -> None:
    args = _parse_args()

    # Ensure DB + tables exist
    run_migrations()

    week_start = _iso_week_start(date.today())
    week_str = f"{week_start.year}-{week_start.isocalendar()[1]:02d}"

    # Determine run_id before logging so the log file is named per-run
    from src.pipeline_monitor import finish_run, start_run
    mode = args.mode if not args.backfill else "backfill"
    run_id = args.run_id if (hasattr(args, "run_id") and args.run_id) else start_run(mode)

    _setup_logging(run_id=run_id, week=week_str)

    try:
        stats = _run(args, week_start)
        if _should_recompute_diversity(args):
            recompute_diversity_ranks()
        finish_run(run_id, status="success", **stats)
    except Exception as exc:
        finish_run(run_id, status="failed", error=str(exc))
        raise
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_orchestrator.py -v`
Expected: `7 passed`

- [ ] **Step 6: Run the full test suite**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests -q`
Expected: no new failures beyond the baseline recorded in Task 1

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: recompute diversity_rank after pipeline runs that fetch new data"
```

---

## Task 3: `/jobs` page — sort param, query branching, UI toggle

**Files:**
- Modify: `web_viewer.py:1377-1513` (the `jobs_list()` route)
- Modify: `templates/jobs_list.html`
- Test: `tests/test_jobs_list_sort.py`

**Interfaces:**
- Consumes: the `diversity_rank` column from Task 1 (no function call needed — this task only reads the column via SQL)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jobs_list_sort.py`:

```python
"""
tests/test_jobs_list_sort.py
──────────────────────────────
Tests for the /jobs page's diversity-vs-recency sort behavior.
"""

import sqlite3

import pytest


@pytest.fixture()
def jobs_app(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
            remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
            source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
            listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
    """)
    # Source A: 3 jobs, all recent. Source B: 1 job, older. Without diversity,
    # A's 3 jobs would all outrank B's single job on a plain posted_date sort.
    conn.executemany(
        "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status, diversity_rank) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "Job A1", "Co", "2026-01-06", "2026-01-06T00:00:00", "A", "m1", "active", 1),
            (2, "Job A2", "Co", "2026-01-05", "2026-01-05T00:00:00", "A", "m1", "active", 2),
            (3, "Job A3", "Co", "2026-01-04", "2026-01-04T00:00:00", "A", "m1", "active", 3),
            (4, "Job B1", "Co", "2026-01-01", "2026-01-01T00:00:00", "B", "m1", "active", 1),
        ],
    )
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    client = web_viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
    return client


class TestDiversitySortDefault:
    def test_baseline_state_uses_diversity_rank_order(self, jobs_app):
        response = jobs_app.get("/jobs")
        html = response.get_data(as_text=True)
        # Diversity order: A1(rank1), B1(rank1), A2(rank2), A3(rank3)
        # B1 should appear before A2/A3 despite being the oldest job overall.
        pos_b1 = html.index("Job B1")
        pos_a2 = html.index("Job A2")
        pos_a3 = html.index("Job A3")
        assert pos_b1 < pos_a2 < pos_a3

    def test_explicit_sort_recent_uses_plain_date_order(self, jobs_app):
        response = jobs_app.get("/jobs?sort=recent")
        html = response.get_data(as_text=True)
        # Plain posted_date DESC: A1, A2, A3, B1 (B1 last, it's the oldest)
        pos_a3 = html.index("Job A3")
        pos_b1 = html.index("Job B1")
        assert pos_a3 < pos_b1

    def test_any_filter_forces_plain_date_order(self, jobs_app):
        response = jobs_app.get("/jobs?company=Co")
        html = response.get_data(as_text=True)
        pos_a3 = html.index("Job A3")
        pos_b1 = html.index("Job B1")
        assert pos_a3 < pos_b1

    def test_non_active_status_forces_plain_date_order(self, jobs_app):
        response = jobs_app.get("/jobs?status=all")
        html = response.get_data(as_text=True)
        pos_a3 = html.index("Job A3")
        pos_b1 = html.index("Job B1")
        assert pos_a3 < pos_b1


class TestUnrankedJobsInDiversityView:
    @pytest.fixture()
    def jobs_app_with_unranked(self, tmp_path, monkeypatch):
        db_path = tmp_path / "jobs.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE jobs (
                job_id INTEGER PRIMARY KEY,
                title TEXT, company TEXT, location TEXT DEFAULT '', country TEXT DEFAULT '',
                remote_type TEXT DEFAULT 'unknown', posted_date TEXT, ingested_at TEXT,
                source_name TEXT DEFAULT '', market_id TEXT, location_count INTEGER DEFAULT 1,
                listing_status TEXT, normalized_title TEXT DEFAULT '', diversity_rank INTEGER
            );
            CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        """)
        conn.executemany(
            "INSERT INTO jobs (job_id, title, company, posted_date, ingested_at, source_name, market_id, listing_status, diversity_rank) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (1, "Job Ranked", "Co", "2026-01-01", "2026-01-01T00:00:00", "A", "m1", "active", 1),
                # Inserted after the last recompute — no rank assigned yet, but still
                # posted more recently than the ranked job above.
                (2, "Job Unranked New", "Co", "2026-01-10", "2026-01-10T00:00:00", "A", "m1", "active", None),
            ],
        )
        conn.commit()
        conn.close()

        import web_viewer
        monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
        web_viewer.app.config.update(TESTING=True)
        client = web_viewer.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        return client

    def test_unranked_job_appears_and_sorts_after_ranked(self, jobs_app_with_unranked):
        response = jobs_app_with_unranked.get("/jobs")
        html = response.get_data(as_text=True)
        assert "Job Unranked New" in html  # visible immediately, not hidden pending recompute
        pos_ranked = html.index("Job Ranked")
        pos_unranked = html.index("Job Unranked New")
        assert pos_ranked < pos_unranked  # ranked job (even though older) sorts first


class TestSortToggleVisibility:
    def test_toggle_shown_in_baseline_state(self, jobs_app):
        response = jobs_app.get("/jobs")
        html = response.get_data(as_text=True)
        assert 'href="/jobs?sort=recent"' in html
        assert 'href="/jobs?sort=diverse"' in html

    def test_toggle_hidden_when_filter_active(self, jobs_app):
        response = jobs_app.get("/jobs?company=Co")
        html = response.get_data(as_text=True)
        assert 'href="/jobs?sort=recent"' not in html
        assert 'href="/jobs?sort=diverse"' not in html

    def test_toggle_hidden_when_status_not_active(self, jobs_app):
        response = jobs_app.get("/jobs?status=all")
        html = response.get_data(as_text=True)
        assert 'href="/jobs?sort=recent"' not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_jobs_list_sort.py -v`
Expected: FAIL — all 8 tests fail, since `jobs_list()` doesn't read `sort`, doesn't use `diversity_rank`, and the template doesn't render a toggle yet (the diversity-order tests fail because everything currently sorts by plain `posted_date DESC`, so `Job A3` — the oldest of source A — appears before `Job B1` in every case, including the baseline-state test that expects the opposite; `test_unranked_job_appears_and_sorts_after_ranked` fails for the same reason — plain date sort puts the newer-but-unranked job first, the opposite of what diversity ordering should do)

- [ ] **Step 3: Modify `jobs_list()` in `web_viewer.py`**

Find (the filter-variable block near the top of the function):

```python
    # Filters
    market_filter  = request.args.get("market", "")
    remote_filter  = request.args.get("remote_type", "")
    search_query   = request.args.get("search", "")
    country_filter = request.args.get("country", "")
    source_filter  = request.args.get("source", "")
    company_filter = request.args.get("company", "")
    skills_filter  = request.args.getlist("skills")
    date_from      = request.args.get("date_from", "")
    date_to        = request.args.get("date_to", "")
    current_status = request.args.get("status", "active")
```

Replace with:

```python
    # Filters
    market_filter  = request.args.get("market", "")
    remote_filter  = request.args.get("remote_type", "")
    search_query   = request.args.get("search", "")
    country_filter = request.args.get("country", "")
    source_filter  = request.args.get("source", "")
    company_filter = request.args.get("company", "")
    skills_filter  = request.args.getlist("skills")
    date_from      = request.args.get("date_from", "")
    date_to        = request.args.get("date_to", "")
    current_status = request.args.get("status", "active")
    sort_param     = request.args.get("sort", "diverse")

    # Diversity ordering only means anything against the exact population it
    # was computed for: status=active, zero other filters. Any deviation from
    # that baseline falls back to plain recency, same as before this feature.
    no_filters_active = not any([
        market_filter, remote_filter, search_query, country_filter,
        source_filter, company_filter, skills_filter, date_from, date_to,
    ])
    show_sort_toggle = no_filters_active and current_status == "active"
    use_diversity = show_sort_toggle and sort_param != "recent"
```

Find (the count/pagination + final query block):

```python
    # Total count
    count_row = cursor.execute(f"SELECT COUNT(*) FROM ({base})", params).fetchone()
    total_jobs = count_row[0] if count_row else 0
    total_pages = max(1, (total_jobs + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    cursor.execute(base + " ORDER BY j.posted_date DESC, j.ingested_at DESC LIMIT ? OFFSET ?",
                   params + [PER_PAGE, offset])
    jobs = cursor.fetchall()
```

Replace with:

```python
    # Total count
    count_row = cursor.execute(f"SELECT COUNT(*) FROM ({base})", params).fetchone()
    total_jobs = count_row[0] if count_row else 0
    total_pages = max(1, (total_jobs + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    if use_diversity:
        order_clause = " ORDER BY (j.diversity_rank IS NULL), j.diversity_rank ASC, j.posted_date DESC LIMIT ? OFFSET ?"
    else:
        order_clause = " ORDER BY j.posted_date DESC, j.ingested_at DESC LIMIT ? OFFSET ?"

    cursor.execute(base + order_clause, params + [PER_PAGE, offset])
    jobs = cursor.fetchall()
```

Find (the `render_template` call at the end of the function):

```python
    return render_template(
        "jobs_list.html",
        jobs=jobs,
        total_jobs=total_jobs,
        markets=markets,
        remote_types=remote_types,
        countries=countries,
        sources=sources,
        current_market=market_filter,
        current_remote=remote_filter,
        current_country=country_filter,
        current_source=source_filter,
        current_company=company_filter,
        current_status=current_status,
        search_query=search_query,
        skills_filter=skills_filter,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
        prev_url=page_url(page - 1) if page > 1 else None,
        next_url=page_url(page + 1) if page < total_pages else None,
    )
```

Replace with:

```python
    return render_template(
        "jobs_list.html",
        jobs=jobs,
        total_jobs=total_jobs,
        markets=markets,
        remote_types=remote_types,
        countries=countries,
        sources=sources,
        current_market=market_filter,
        current_remote=remote_filter,
        current_country=country_filter,
        current_source=source_filter,
        current_company=company_filter,
        current_status=current_status,
        search_query=search_query,
        skills_filter=skills_filter,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
        prev_url=page_url(page - 1) if page > 1 else None,
        next_url=page_url(page + 1) if page < total_pages else None,
        show_sort_toggle=show_sort_toggle,
        current_sort=sort_param if use_diversity else "recent",
    )
```

- [ ] **Step 4: Add the sort toggle to `templates/jobs_list.html`**

Find:

```html
{% block content %}
<h2 style="margin-bottom: 1.5rem; color: var(--text-primary);">Jobs ({{ total_jobs | number_format }})</h2>
<div style="margin-top: -0.75rem; margin-bottom: 1rem;">
    {% if g.current_user and g.current_user.get('role') == 'admin' %}
    <a href="/jobs/quality" class="btn" style="padding: 0.4rem 0.9rem; font-size: 0.85rem;">🛠️ Open Data Quality Review</a>
    {% endif %}
</div>
```

Replace with:

```html
{% block content %}
<h2 style="margin-bottom: 1.5rem; color: var(--text-primary);">Jobs ({{ total_jobs | number_format }})</h2>
<div style="margin-top: -0.75rem; margin-bottom: 1rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
    <div>
        {% if g.current_user and g.current_user.get('role') == 'admin' %}
        <a href="/jobs/quality" class="btn" style="padding: 0.4rem 0.9rem; font-size: 0.85rem;">🛠️ Open Data Quality Review</a>
        {% endif %}
    </div>
    {% if show_sort_toggle %}
    <div class="text-muted text-sm">
        Sort:
        <a href="/jobs?sort=diverse" {% if current_sort == 'diverse' %}style="font-weight: 600; color: var(--text-primary);"{% endif %}>Diverse</a>
        &middot;
        <a href="/jobs?sort=recent" {% if current_sort == 'recent' %}style="font-weight: 600; color: var(--text-primary);"{% endif %}>Most Recent</a>
    </div>
    {% endif %}
</div>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_jobs_list_sort.py -v`
Expected: `8 passed`

- [ ] **Step 6: Run the full test suite**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests -q`
Expected: no new failures beyond the baseline recorded in Task 1

- [ ] **Step 7: Commit**

```bash
git add web_viewer.py templates/jobs_list.html tests/test_jobs_list_sort.py
git commit -m "feat: diversity-ordered default sort on the /jobs page"
```
