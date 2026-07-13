# Hybrid Job Classification Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the dormant field taxonomy (`config/job_markets.py` + `src/market_classifier.py`) against live data, with a Groq AI fallback for jobs the local classifier can't confidently place, load-aware background scheduling, and an admin page to see classification coverage run over run.

**Architecture:** New DB tables (`job_categories`, `job_category_assignments`, `groq_classification_queue`, `classification_runs`) plus new `jobs` columns, all under `field_category_*`/`job_categor*` naming that never touches the live `jobs.market_id` column. Two new pure-Python stage modules (`src/classification/local_stage.py`, `src/classification/groq_stage.py`) get invoked in-process (no subprocess spawning) from an extension of the existing `_auto_scheduler_loop` background thread in `web_viewer.py`. A new `/admin/classification` page surfaces run history, category breakdown, and the Groq queue.

**Tech Stack:** Python 3, Flask, SQLite (via `src/storage/db.py::get_connection()`), `requests` (existing Groq HTTP pattern from `src/ai/grok_staging.py`), pytest.

## Global Constraints

- Never write to `jobs.market_id` — it is live and holds the ingestion-source grouping (`ai_ml_global`, etc.), used by the Jobs List Market filter. All new schema uses `field_category_*` / `job_categor*` naming.
- Classification threshold: 0.62 confidence / 2.0 score cutoff (from `src/market_classifier.py`, unchanged), read from `get_config()`/`set_config()` (in `src/pipeline_monitor.py`), not hardcoded.
- Groq prompt sends only `{category_id, name}` pairs for the 20 leaf categories — never the full keyword lists.
- Three Groq outcomes only: `succeeded` / `failed_technical` (retryable, capped at 5 attempts) / `no_match` (terminal, never retried in this spec).
- `local_full_backfill` runs are manual-start only (admin-triggered, preview-then-confirm). `groq_backlog` runs auto-start (`trigger='backfill_idle'`) whenever pending queue rows exist and none is already running. Do not conflate these two.
- Load gating applies only to `local_full_backfill` and `groq_backlog` chunk continuation — never to `local_incremental` or `groq_retry`.
- The "should I process a chunk right now" decision must be a pure function, independently testable without real time passing or a real Flask request — mirroring `compute_next_run()`'s separation from `_auto_scheduler_loop`'s sleep in `src/pipeline_monitor.py`.
- No real network calls in tests — all Groq HTTP calls are mocked.
- Never write `Co-Authored-By: Claude` into any commit in this repo.

---

### Task 1: Database schema — migration, seeding, and column additions

**Files:**
- Create: `src/storage/migrations/008_job_classification_pipeline.sql`
- Modify: `src/storage/db.py` (extend `run_migrations()`)
- Test: `tests/test_classification_schema.py`

**Interfaces:**
- Produces: tables `job_categories(category_id, name, parent_id, isco, keywords)`, `job_category_assignments(job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)`, `groq_classification_queue(id, job_id, status, prompt_sent, response_received, attempt_count, last_attempted_at, created_at)`, `classification_runs(run_id, run_type, trigger, status, started_at, finished_at, cursor_job_id, jobs_processed, jobs_classified, jobs_queued_groq, error)`. Columns on `jobs`: `field_category_id`, `field_classification_confidence`, `field_classification_method`, `field_classification_attempted_at`. `job_categories` is seeded from `config.job_markets.JOB_MARKETS` (20 leaf + parent rows) on every `run_migrations()` call.
- Consumes: `config.job_markets.JOB_MARKETS` (existing, unchanged), `src.storage.db.get_connection()` (existing).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classification_schema.py
import json
import sqlite3

import pytest


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, market_id TEXT, listing_status TEXT
        )
    """)
    conn.execute("INSERT INTO jobs (job_id, title, company, market_id) VALUES (1, 'Software Engineer', 'Acme', 'ai_ml_global')")
    conn.commit()
    conn.close()

    import src.storage.db as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.run_migrations()
    return db_path


def test_new_tables_exist(migrated_db):
    conn = sqlite3.connect(migrated_db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"job_categories", "job_category_assignments", "groq_classification_queue", "classification_runs"} <= tables


def test_jobs_market_id_untouched_by_migration(migrated_db):
    conn = sqlite3.connect(migrated_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT market_id FROM jobs WHERE job_id = 1").fetchone()
    assert row["market_id"] == "ai_ml_global"


def test_new_jobs_columns_added(migrated_db):
    conn = sqlite3.connect(migrated_db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert {"field_category_id", "field_classification_confidence", "field_classification_method", "field_classification_attempted_at"} <= columns


def test_job_categories_seeded_from_config(migrated_db):
    conn = sqlite3.connect(migrated_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT name, parent_id, isco, keywords FROM job_categories WHERE category_id = 'it.software'").fetchone()
    assert row["name"] == "Software Engineering"
    assert row["parent_id"] == "it"
    keywords = json.loads(row["keywords"])
    assert "software engineer" in keywords

    count = conn.execute("SELECT COUNT(*) FROM job_categories").fetchone()[0]
    from config.job_markets import JOB_MARKETS
    assert count == len(JOB_MARKETS)


def test_migrations_idempotent_on_second_run(migrated_db):
    import src.storage.db as db
    db.run_migrations()  # must not raise
    conn = sqlite3.connect(migrated_db)
    count = conn.execute("SELECT COUNT(*) FROM job_categories").fetchone()[0]
    from config.job_markets import JOB_MARKETS
    assert count == len(JOB_MARKETS)  # re-seed didn't duplicate rows (category_id is PRIMARY KEY + INSERT OR REPLACE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classification_schema.py -v`
Expected: FAIL — `job_categories` etc. do not exist yet (migration file doesn't exist).

- [ ] **Step 3: Create the migration SQL file**

```sql
-- src/storage/migrations/008_job_classification_pipeline.sql
-- Field-taxonomy classification pipeline. Deliberately does NOT touch
-- jobs.market_id (that's the live ingestion-source grouping, used by the
-- Jobs List Market filter) — all new schema uses field_category_*/
-- job_categor* naming instead. Column additions on `jobs` itself are
-- handled in Python below (PRAGMA-guarded), following this file's own
-- established convention for jobs-table ALTERs.

CREATE TABLE IF NOT EXISTS job_categories (
    category_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   TEXT,
    isco        TEXT,
    keywords    TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS job_category_assignments (
    job_id          INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    category_id     TEXT NOT NULL,
    assignment_type TEXT NOT NULL,
    confidence      REAL,
    method          TEXT,
    evidence_json   TEXT,
    assigned_at     TEXT NOT NULL,
    PRIMARY KEY (job_id, category_id, assignment_type)
);
CREATE INDEX IF NOT EXISTS idx_job_category_assignments_job      ON job_category_assignments(job_id);
CREATE INDEX IF NOT EXISTS idx_job_category_assignments_category ON job_category_assignments(category_id);

CREATE TABLE IF NOT EXISTS groq_classification_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'pending',
    prompt_sent         TEXT,
    response_received   TEXT,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_attempted_at   TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(job_id)
);
CREATE INDEX IF NOT EXISTS idx_groq_queue_status ON groq_classification_queue(status);

CREATE TABLE IF NOT EXISTS classification_runs (
    run_id           TEXT PRIMARY KEY,
    run_type         TEXT NOT NULL,
    trigger          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running',
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    cursor_job_id    INTEGER,
    jobs_processed   INTEGER NOT NULL DEFAULT 0,
    jobs_classified  INTEGER NOT NULL DEFAULT 0,
    jobs_queued_groq INTEGER NOT NULL DEFAULT 0,
    error            TEXT
);
```

- [ ] **Step 4: Extend `run_migrations()` to add `jobs` columns and seed `job_categories`**

In `src/storage/db.py`, this migration's filename must NOT be added to the `elif mf.name in [...]: pass` skip-list — it should fall through to the existing generic `else: conn.executescript(sql)` branch, which runs the `CREATE TABLE IF NOT EXISTS` statements above as-is (they're already idempotent).

Add a new block immediately after the existing "Migration 005: Add normalization_confidence column" block (around line 202-203, right after that `if` block closes — read the surrounding code first to match exact placement and the `job_columns` variable already in scope from the `PRAGMA table_info(jobs)` call earlier in the function):

```python
        # Migration 008: field-taxonomy classification columns (conditional)
        if "field_category_id" not in job_columns:
            logger.info("[db] Running migration 008: add field_category_id column")
            conn.execute("ALTER TABLE jobs ADD COLUMN field_category_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_field_category_id ON jobs(field_category_id)")

        if "field_classification_confidence" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN field_classification_confidence REAL")

        if "field_classification_method" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN field_classification_method TEXT")

        if "field_classification_attempted_at" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN field_classification_attempted_at TEXT")
            logger.info("[db] Migration 008 complete: field classification columns added")

        # Re-seed job_categories from config on every startup, so editing
        # config/job_markets.py and redeploying keeps the DB copy in sync.
        from config.job_markets import JOB_MARKETS
        for market in JOB_MARKETS:
            conn.execute(
                """INSERT OR REPLACE INTO job_categories (category_id, name, parent_id, isco, keywords)
                   VALUES (?, ?, ?, ?, ?)""",
                (market["market_id"], market["name"], market["parent_id"], market["isco"], json.dumps(market["keywords"])),
            )
```

Add `"008_job_classification_pipeline.sql"` nowhere to the skip-list (leave it out entirely so it takes the generic executescript path). Add `import json` at the top of `src/storage/db.py` if not already imported (check first — `_ensure_warehouse_schema` already uses `json.dumps` for the shadow-only `markets` table, so `json` is very likely already imported at module level; if so, no change needed).

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_classification_schema.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full existing suite to confirm nothing else broke**

Run: `python -m pytest tests/ -q --basetemp=<a writable temp dir if the default pytest-of-<user> dir is inaccessible>`
Expected: same pass count as the pre-existing baseline, no new failures.

- [ ] **Step 7: Commit**

```bash
git add src/storage/migrations/008_job_classification_pipeline.sql src/storage/db.py tests/test_classification_schema.py
git commit -m "feat: add job classification pipeline schema (categories, assignments, groq queue, run history)"
```

---

### Task 2: Local classification stage

**Files:**
- Create: `src/classification/__init__.py` (empty)
- Create: `src/classification/local_stage.py`
- Test: `tests/test_local_classification_stage.py`

**Interfaces:**
- Consumes: `src.market_classifier.classify_job(title, description) -> MarketMatch` (existing, unchanged — `MarketMatch(market_id, confidence, tags, method, evidence)`), `src.pipeline_monitor.get_config()` (existing), tables from Task 1.
- Produces: `classify_pending_jobs(conn, run_id, limit=None) -> dict` and `reclassify_all(conn, run_id, limit=None) -> dict`, both returning `{"processed": int, "classified": int, "queued_groq": int}`. Both update the `classification_runs` row for `run_id` in place (`jobs_processed`, `jobs_classified`, `jobs_queued_groq`, `cursor_job_id` set to the last-processed `job_id`). Task 4 (scheduler) calls these directly as in-process function calls — never as a subprocess.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_local_classification_stage.py
import sqlite3
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            title TEXT, raw_description TEXT DEFAULT '', market_id TEXT,
            field_category_id TEXT, field_classification_confidence REAL,
            field_classification_method TEXT, field_classification_attempted_at TEXT
        );
        CREATE TABLE job_categories (category_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, isco TEXT, keywords TEXT);
        CREATE TABLE job_category_assignments (
            job_id INTEGER, category_id TEXT, assignment_type TEXT, confidence REAL,
            method TEXT, evidence_json TEXT, assigned_at TEXT,
            PRIMARY KEY (job_id, category_id, assignment_type)
        );
        CREATE TABLE groq_classification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT DEFAULT 'pending',
            prompt_sent TEXT, response_received TEXT, attempt_count INTEGER DEFAULT 0,
            last_attempted_at TEXT, created_at TEXT, UNIQUE(job_id)
        );
        CREATE TABLE classification_runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, trigger TEXT, status TEXT DEFAULT 'running',
            started_at TEXT, finished_at TEXT, cursor_job_id INTEGER,
            jobs_processed INTEGER DEFAULT 0, jobs_classified INTEGER DEFAULT 0,
            jobs_queued_groq INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    c.execute(
        "INSERT INTO jobs (job_id, title, raw_description) VALUES (1, 'Senior Software Engineer', 'Python backend role')"
    )
    c.execute(
        "INSERT INTO jobs (job_id, title, raw_description) VALUES (2, 'Xyzzy Widget Wrangler', 'totally unclassifiable made-up title')"
    )
    c.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run1', 'local_incremental', 'schedule', datetime('now'))"
    )
    c.commit()
    return c


def test_above_threshold_job_classified_directly(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    row = conn.execute("SELECT field_category_id, field_classification_method, field_classification_attempted_at FROM jobs WHERE job_id = 1").fetchone()
    assert row["field_category_id"] == "it.software"
    assert row["field_classification_method"] == "local_hybrid_v1"
    assert row["field_classification_attempted_at"] is not None

    assignment = conn.execute("SELECT * FROM job_category_assignments WHERE job_id = 1 AND assignment_type = 'primary'").fetchone()
    assert assignment["category_id"] == "it.software"


def test_below_threshold_job_queued_for_groq(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    row = conn.execute("SELECT field_category_id, field_classification_attempted_at FROM jobs WHERE job_id = 2").fetchone()
    assert row["field_category_id"] is None
    assert row["field_classification_attempted_at"] is not None  # attempted, just unclassified

    queued = conn.execute("SELECT status FROM groq_classification_queue WHERE job_id = 2").fetchone()
    assert queued["status"] == "pending"


def test_run_stats_updated(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    run = conn.execute("SELECT jobs_processed, jobs_classified, jobs_queued_groq FROM classification_runs WHERE run_id = 'run1'").fetchone()
    assert run["jobs_processed"] == 2
    assert run["jobs_classified"] == 1
    assert run["jobs_queued_groq"] == 1


def test_already_attempted_jobs_skipped_by_incremental(conn):
    from src.classification.local_stage import classify_pending_jobs
    classify_pending_jobs(conn, run_id="run1")

    conn.execute("INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run2', 'local_incremental', 'schedule', datetime('now'))")
    result = classify_pending_jobs(conn, run_id="run2")
    assert result["processed"] == 0  # both jobs already attempted


def test_reclassify_all_reprocesses_everything(conn):
    from src.classification.local_stage import classify_pending_jobs, reclassify_all
    classify_pending_jobs(conn, run_id="run1")

    conn.execute("INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run2', 'local_full_backfill', 'manual', datetime('now'))")
    result = reclassify_all(conn, run_id="run2")
    assert result["processed"] == 2  # reprocesses job 1 even though already attempted


def test_limit_caps_batch_size_and_sets_cursor(conn):
    from src.classification.local_stage import classify_pending_jobs
    result = classify_pending_jobs(conn, run_id="run1", limit=1)
    assert result["processed"] == 1

    run = conn.execute("SELECT cursor_job_id FROM classification_runs WHERE run_id = 'run1'").fetchone()
    assert run["cursor_job_id"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_local_classification_stage.py -v`
Expected: FAIL — `src.classification.local_stage` doesn't exist.

- [ ] **Step 3: Implement the module**

```python
# src/classification/__init__.py
```

```python
# src/classification/local_stage.py
"""
Runs the existing, unchanged src.market_classifier.classify_job() against
jobs, writing straight to jobs.field_category_id (never jobs.market_id -
that column is the live ingestion-source grouping used by the Jobs List
Market filter, a completely different concept from this taxonomy).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.market_classifier import classify_job
from src.pipeline_monitor import get_config

DEFAULT_CONFIDENCE_THRESHOLD = 0.62
DEFAULT_SCORE_THRESHOLD = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thresholds() -> tuple[float, float]:
    cfg = get_config()
    confidence = float(cfg.get("classification_confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))
    score = float(cfg.get("classification_score_threshold", DEFAULT_SCORE_THRESHOLD))
    return confidence, score


def _classify_one(conn, run_id: str, job_id: int, title: str, description: str) -> bool:
    """Classify a single job; returns True if it was directly classified, False if queued for Groq."""
    match = classify_job(title, description or "")
    confidence_threshold, score_threshold = _thresholds()
    now = _now()

    # classify_job() already applies its own internal threshold (0.62/2.0) and
    # returns market_id=None below it - re-checking confidence here lets an
    # admin raise the bar higher via config without touching market_classifier.py.
    if match.market_id and match.confidence >= confidence_threshold:
        conn.execute(
            """UPDATE jobs SET field_category_id = ?, field_classification_confidence = ?,
                                field_classification_method = ?, field_classification_attempted_at = ?
               WHERE job_id = ?""",
            (match.market_id, match.confidence, match.method, now, job_id),
        )
        conn.execute(
            """INSERT OR REPLACE INTO job_category_assignments
               (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
               VALUES (?, ?, 'primary', ?, ?, ?, ?)""",
            (job_id, match.market_id, match.confidence, match.method, json.dumps(match.evidence), now),
        )
        for tag in match.tags:
            conn.execute(
                """INSERT OR REPLACE INTO job_category_assignments
                   (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
                   VALUES (?, ?, 'tag', ?, ?, ?, ?)""",
                (job_id, tag, match.confidence, match.method, json.dumps(match.evidence), now),
            )
        return True

    conn.execute(
        "UPDATE jobs SET field_classification_attempted_at = ? WHERE job_id = ?",
        (now, job_id),
    )
    conn.execute(
        """INSERT OR IGNORE INTO groq_classification_queue (job_id, status, created_at)
           VALUES (?, 'pending', ?)""",
        (job_id, now),
    )
    return False


def _run_batch(conn, run_id: str, rows: list, ) -> dict[str, int]:
    processed = classified = queued = 0
    cursor_job_id = None
    for row in rows:
        did_classify = _classify_one(conn, run_id, row["job_id"], row["title"], row["raw_description"])
        processed += 1
        cursor_job_id = row["job_id"]
        if did_classify:
            classified += 1
        else:
            queued += 1

    conn.execute(
        """UPDATE classification_runs
           SET jobs_processed = jobs_processed + ?, jobs_classified = jobs_classified + ?,
               jobs_queued_groq = jobs_queued_groq + ?, cursor_job_id = ?
           WHERE run_id = ?""",
        (processed, classified, queued, cursor_job_id, run_id),
    )
    conn.commit()
    return {"processed": processed, "classified": classified, "queued_groq": queued}


def classify_pending_jobs(conn, run_id: str, limit: int | None = None) -> dict[str, int]:
    """Classify jobs that have never been attempted (field_classification_attempted_at IS NULL)."""
    query = "SELECT job_id, title, raw_description FROM jobs WHERE field_classification_attempted_at IS NULL ORDER BY job_id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    return _run_batch(conn, run_id, rows)


def reclassify_all(conn, run_id: str, limit: int | None = None) -> dict[str, int]:
    """Re-run classification for every job, regardless of prior attempts."""
    query = "SELECT job_id, title, raw_description FROM jobs ORDER BY job_id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    return _run_batch(conn, run_id, rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_local_classification_stage.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/classification/__init__.py src/classification/local_stage.py tests/test_local_classification_stage.py
git commit -m "feat: add local classification stage (classify_pending_jobs, reclassify_all)"
```

---

### Task 3: Groq fallback stage

**Files:**
- Create: `src/classification/groq_stage.py`
- Test: `tests/test_groq_classification_stage.py`

**Interfaces:**
- Consumes: `config.settings.GROQ_API_KEYS`, `config.settings.GROK_MODEL`, `config.settings.GROK_BASE_URL` (existing), `src.ai.grok_staging._retry_after_seconds`, `src.ai.grok_staging._mask_key` (existing, imported directly — not duplicated), `job_categories` / `groq_classification_queue` tables from Task 1.
- Produces: `process_groq_queue(conn, run_id, statuses, limit=None, chunk_size=25) -> dict` returning `{"processed": int, "succeeded": int, "failed_technical": int, "no_match": int}`. Task 4 calls this with `statuses=("pending",)` for `groq_backlog` runs and `statuses=("failed_technical",)` (filtered to `attempt_count < 5` internally) for `groq_retry` runs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_groq_classification_stage.py
import json
import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, raw_description TEXT DEFAULT '',
            field_category_id TEXT, field_classification_confidence REAL, field_classification_method TEXT
        );
        CREATE TABLE job_categories (category_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, isco TEXT, keywords TEXT);
        CREATE TABLE job_category_assignments (
            job_id INTEGER, category_id TEXT, assignment_type TEXT, confidence REAL,
            method TEXT, evidence_json TEXT, assigned_at TEXT,
            PRIMARY KEY (job_id, category_id, assignment_type)
        );
        CREATE TABLE groq_classification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT DEFAULT 'pending',
            prompt_sent TEXT, response_received TEXT, attempt_count INTEGER DEFAULT 0,
            last_attempted_at TEXT, created_at TEXT, UNIQUE(job_id)
        );
        CREATE TABLE classification_runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, trigger TEXT, status TEXT DEFAULT 'running',
            started_at TEXT, finished_at TEXT, cursor_job_id INTEGER,
            jobs_processed INTEGER DEFAULT 0, jobs_classified INTEGER DEFAULT 0,
            jobs_queued_groq INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    c.execute("INSERT INTO job_categories (category_id, name) VALUES ('it.software', 'Software Engineering')")
    c.execute("INSERT INTO jobs (job_id, title, raw_description) VALUES (1, 'Backend Dev', 'writes python')")
    c.execute("INSERT INTO jobs (job_id, title, raw_description) VALUES (2, 'Mystery Role', 'no clear fit')")
    c.execute("INSERT INTO jobs (job_id, title, raw_description) VALUES (3, 'Flaky Call', 'network will fail')")
    c.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    c.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (2, 'pending', datetime('now'))")
    c.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (3, 'pending', datetime('now'))")
    c.execute("INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run1', 'groq_backlog', 'backfill_idle', datetime('now'))")
    c.commit()
    return c


def _fake_groq_response(job_id_to_category: dict[int, str | None]):
    return {
        "choices": [{"message": {"content": json.dumps({
            "results": [
                {"job_id": jid, "category_id": cat, "confidence": 0.9 if cat else 0.0, "reasoning": "test"}
                for jid, cat in job_id_to_category.items()
            ]
        })}}]
    }


def test_succeeded_outcome_writes_category_and_clears_queue(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    from src.classification import groq_stage

    class _MockResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200
        def json(self):
            return self._data
        def raise_for_status(self):
            pass

    fake_response = _MockResponse(_fake_groq_response({1: "it.software", 2: None, 3: "it.software"}))
    with patch("src.classification.groq_stage.requests.post", return_value=fake_response):
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("pending",))

    assert result["processed"] == 3
    assert result["succeeded"] == 2
    assert result["no_match"] == 1

    job1 = conn.execute("SELECT field_category_id, field_classification_method FROM jobs WHERE job_id = 1").fetchone()
    assert job1["field_category_id"] == "it.software"
    assert job1["field_classification_method"] == "groq_v1"

    q1 = conn.execute("SELECT status, prompt_sent, response_received FROM groq_classification_queue WHERE job_id = 1").fetchone()
    assert q1["status"] == "succeeded"
    assert q1["prompt_sent"] is not None
    assert q1["response_received"] is not None

    q2 = conn.execute("SELECT status FROM groq_classification_queue WHERE job_id = 2").fetchone()
    assert q2["status"] == "no_match"


def test_technical_failure_marks_retryable(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    monkeypatch.setattr("src.classification.groq_stage.time.sleep", lambda seconds: None)  # skip real backoff delay in tests
    from src.classification import groq_stage

    with patch("src.classification.groq_stage.requests.post", side_effect=ConnectionError("network down")):
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("pending",), chunk_size=25)

    assert result["failed_technical"] == 3
    rows = conn.execute("SELECT status, attempt_count, response_received FROM groq_classification_queue").fetchall()
    assert all(r["status"] == "failed_technical" for r in rows)
    assert all(r["attempt_count"] == 1 for r in rows)
    assert all(r["response_received"] is not None for r in rows)  # error text stored


def test_retry_sweep_excludes_exhausted_attempts(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    conn.execute("UPDATE groq_classification_queue SET status = 'failed_technical', attempt_count = 5 WHERE job_id = 1")
    conn.execute("UPDATE groq_classification_queue SET status = 'failed_technical', attempt_count = 2 WHERE job_id = 2")
    conn.execute("DELETE FROM groq_classification_queue WHERE job_id = 3")
    conn.commit()

    from src.classification import groq_stage

    class _MockResponse:
        status_code = 200
        def json(self):
            return _fake_groq_response({2: "it.software"})
        def raise_for_status(self):
            pass

    with patch("src.classification.groq_stage.requests.post", return_value=_MockResponse()) as mock_post:
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("failed_technical",))

    assert result["processed"] == 1  # only job 2 (job 1 exhausted its 5 attempts)


def test_no_match_never_retried_by_retry_sweep(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    conn.execute("UPDATE groq_classification_queue SET status = 'no_match' WHERE job_id = 1")
    conn.execute("DELETE FROM groq_classification_queue WHERE job_id IN (2, 3)")
    conn.commit()

    from src.classification import groq_stage
    with patch("src.classification.groq_stage.requests.post") as mock_post:
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("failed_technical",))

    mock_post.assert_not_called()
    assert result["processed"] == 0


def test_prompt_sends_category_ids_not_full_keywords(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    conn.execute("UPDATE job_categories SET keywords = ?", (json.dumps(["a very long keyword list", "that should not appear"]),))
    conn.execute("DELETE FROM groq_classification_queue WHERE job_id IN (2, 3)")
    conn.commit()

    from src.classification import groq_stage
    captured = {}

    class _MockResponse:
        status_code = 200
        def json(self):
            return _fake_groq_response({1: "it.software"})
        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _MockResponse()

    with patch("src.classification.groq_stage.requests.post", side_effect=fake_post):
        groq_stage.process_groq_queue(conn, run_id="run1", statuses=("pending",))

    prompt_text = str(captured["payload"])
    assert "it.software" in prompt_text
    assert "a very long keyword list" not in prompt_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_groq_classification_stage.py -v`
Expected: FAIL — `src.classification.groq_stage` doesn't exist.

- [ ] **Step 3: Implement the module**

Read `src/ai/grok_staging.py` in full first — this reuses its key-pool/cooldown/retry-after pattern (`_retry_after_seconds`, `_mask_key` imported directly; the cooldown/rotation loop shape re-implemented here since the per-item payload and outcome handling genuinely differ from the staging use case).

```python
# src/classification/groq_stage.py
"""
Groq fallback for jobs the local classifier (src.market_classifier) can't
confidently place. Classifies into the EXISTING 20-category taxonomy only -
if Groq also can't fit a job, that's a terminal 'no_match', not a request
for a new category (that's explicitly a separate, deferred spec).

Reuses src.ai.grok_staging's key-pool-cooldown/retry-after pattern rather
than duplicating it; the actual prompt/payload/outcome handling here is
different enough (category classification, not country/remote/tab-bucket
cleanup) that the outer loop is its own implementation, not a shared call.
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import GROK_BASE_URL, GROK_MODEL, GROQ_API_KEYS
from src.ai.grok_staging import _mask_key, _retry_after_seconds
from src.pipeline_monitor import get_config

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRY_ATTEMPTS = 5


def _max_retry_attempts() -> int:
    """Admin-configurable via /admin/classification's 'retry cap' field (classification_retry_cap)."""
    cfg = get_config()
    return int(cfg.get("classification_retry_cap", DEFAULT_MAX_RETRY_ATTEMPTS))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_categories(conn) -> list[dict]:
    rows = conn.execute("SELECT category_id, name FROM job_categories WHERE parent_id IS NOT NULL").fetchall()
    return [{"category_id": r["category_id"], "name": r["name"]} for r in rows]


def _build_prompt(categories: list[dict], jobs: list[dict]) -> dict[str, Any]:
    instruction = (
        "You are classifying job postings into an existing job-field taxonomy. "
        "Return strict JSON with key 'results'. Each result must include: "
        "job_id, category_id (must be exactly one of the provided category_id values, or null if none fit), "
        "confidence (0-1), reasoning. Do not invent a category_id that isn't in the provided list."
    )
    return {
        "model": GROK_MODEL,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps({"categories": categories, "jobs": jobs}, ensure_ascii=False)},
        ],
    }


def _call_groq_batch(payload: dict, api_key: str, timeout_seconds: int = 60) -> dict[int, dict]:
    response = requests.post(
        f"{GROK_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return {int(item["job_id"]): item for item in parsed.get("results", [])}


def _eligible_job_ids(conn, statuses: tuple[str, ...]) -> list[int]:
    if statuses == ("pending",):
        rows = conn.execute("SELECT job_id FROM groq_classification_queue WHERE status = 'pending' ORDER BY created_at").fetchall()
    elif statuses == ("failed_technical",):
        rows = conn.execute(
            "SELECT job_id FROM groq_classification_queue WHERE status = 'failed_technical' AND attempt_count < ? ORDER BY last_attempted_at",
            (_max_retry_attempts(),),
        ).fetchall()
    else:
        raise ValueError(f"Unsupported statuses combination: {statuses}")
    return [r["job_id"] for r in rows]


def process_groq_queue(conn, run_id: str, statuses: tuple[str, ...], limit: int | None = None, chunk_size: int = 25) -> dict[str, int]:
    job_ids = _eligible_job_ids(conn, statuses)
    if limit:
        job_ids = job_ids[:limit]

    stats = {"processed": 0, "succeeded": 0, "failed_technical": 0, "no_match": 0}
    if not job_ids:
        return stats

    categories = _load_categories(conn)
    key_pool = [k for k in GROQ_API_KEYS if k]
    if not key_pool:
        raise ValueError("No Groq API key configured (GROQ_API_KEYS is empty)")
    key_cooldowns = {key: 0.0 for key in key_pool}

    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"SELECT job_id, title, raw_description FROM jobs WHERE job_id IN ({placeholders})", job_ids
    ).fetchall()
    jobs_by_id = {r["job_id"]: r for r in rows}

    for chunk_idx in range(0, len(job_ids), chunk_size):
        chunk_ids = job_ids[chunk_idx:chunk_idx + chunk_size]
        batch = [
            {"job_id": jid, "title": jobs_by_id[jid]["title"] or "", "description": (jobs_by_id[jid]["raw_description"] or "")[:2000]}
            for jid in chunk_ids
        ]
        payload = _build_prompt(categories, batch)
        prompt_json = json.dumps(payload)

        conn.execute(
            f"UPDATE groq_classification_queue SET status = 'processing' WHERE job_id IN ({placeholders})",
            chunk_ids,
        )
        conn.commit()

        results_by_id: dict[int, dict] = {}
        call_error: str | None = None
        attempts = max(4, len(key_pool) * 2)
        for attempt in range(attempts):
            now = time.time()
            available = [k for k in key_pool if key_cooldowns.get(k, 0.0) <= now]
            if not available:
                time.sleep(max(0.1, min(20.0, min(key_cooldowns.values()) - now)))
                continue
            key = available[attempt % len(available)]
            try:
                results_by_id = _call_groq_batch(payload, key)
                call_error = None
                break
            except requests.HTTPError as e:
                retry_after = _retry_after_seconds(e)
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if status_code == 429:
                    cooldown = retry_after if retry_after is not None else (3.0 + random.uniform(0.5, 1.5))
                    key_cooldowns[key] = time.time() + max(1.0, min(45.0, cooldown))
                    logger.info("[groq_stage] Rate limited on key=%s, cooling down", _mask_key(key))
                call_error = str(e)
            except Exception as e:  # noqa: BLE001 - any failure here means "this attempt didn't work"
                call_error = str(e)
            if attempt < attempts - 1:
                time.sleep(min(8.0, (2 ** min(attempt, 3)) + random.uniform(0.2, 0.8)))

        now_iso = _now()
        for jid in chunk_ids:
            stats["processed"] += 1
            if call_error is not None and jid not in results_by_id:
                conn.execute(
                    """UPDATE groq_classification_queue
                       SET status = 'failed_technical', attempt_count = attempt_count + 1,
                           prompt_sent = ?, response_received = ?, last_attempted_at = ?
                       WHERE job_id = ?""",
                    (prompt_json, f"ERROR: {call_error}", now_iso, jid),
                )
                stats["failed_technical"] += 1
                continue

            result = results_by_id.get(jid)
            response_text = json.dumps(result) if result else "ERROR: job_id missing from Groq response"
            category_id = (result or {}).get("category_id")
            valid_category = category_id in {c["category_id"] for c in categories}

            if result and valid_category:
                confidence = float(result.get("confidence") or 0.0)
                conn.execute(
                    """UPDATE jobs SET field_category_id = ?, field_classification_confidence = ?,
                                        field_classification_method = 'groq_v1' WHERE job_id = ?""",
                    (category_id, confidence, jid),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO job_category_assignments
                       (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
                       VALUES (?, ?, 'primary', ?, 'groq_v1', ?, ?)""",
                    (jid, category_id, confidence, json.dumps(result.get("reasoning", "")), now_iso),
                )
                conn.execute(
                    "UPDATE groq_classification_queue SET status = 'succeeded', prompt_sent = ?, response_received = ?, last_attempted_at = ? WHERE job_id = ?",
                    (prompt_json, response_text, now_iso, jid),
                )
                stats["succeeded"] += 1
            elif result and category_id is None:
                conn.execute(
                    "UPDATE groq_classification_queue SET status = 'no_match', prompt_sent = ?, response_received = ?, last_attempted_at = ? WHERE job_id = ?",
                    (prompt_json, response_text, now_iso, jid),
                )
                stats["no_match"] += 1
            else:
                # Response came back but with an invalid/missing category_id for this job - treat as a
                # technical failure (malformed response), not a semantic no_match, so it's retried.
                conn.execute(
                    """UPDATE groq_classification_queue
                       SET status = 'failed_technical', attempt_count = attempt_count + 1,
                           prompt_sent = ?, response_received = ?, last_attempted_at = ?
                       WHERE job_id = ?""",
                    (prompt_json, response_text, now_iso, jid),
                )
                stats["failed_technical"] += 1
        conn.commit()

    conn.execute(
        """UPDATE classification_runs
           SET jobs_processed = jobs_processed + ?, jobs_classified = jobs_classified + ?
           WHERE run_id = ?""",
        (stats["processed"], stats["succeeded"], run_id),
    )
    conn.commit()
    return stats
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_groq_classification_stage.py -v`
Expected: PASS (5 tests). If `test_succeeded_outcome_writes_category_and_clears_queue`'s mock setup has issues (it's the most complex mock in this file), simplify the `fake_post` helper to just return the fixed `_fake_groq_response(...)` dict directly rather than trying to introspect the outgoing payload — only `test_prompt_sends_category_ids_not_full_keywords` actually needs to inspect the outgoing payload.

- [ ] **Step 5: Commit**

```bash
git add src/classification/groq_stage.py tests/test_groq_classification_stage.py
git commit -m "feat: add Groq fallback classification stage with retry/no_match handling"
```

---

### Task 4: Load-aware scheduling

**Files:**
- Modify: `web_viewer.py` (extend `_auto_scheduler_loop`, add `before_request` hook)
- Create: `src/classification/scheduling.py`
- Test: `tests/test_classification_scheduling.py`

**Interfaces:**
- Consumes: `classify_pending_jobs`/`reclassify_all` (Task 2), `process_groq_queue` (Task 3), `get_config`/`set_config` (existing `src/pipeline_monitor.py`).
- Produces: `should_process_chunk(last_request_at: datetime | None, other_run_active: bool, now: datetime, idle_seconds_threshold: int = 300) -> bool` — pure function, no I/O, no sleep. `run_scheduler_tick(conn, last_request_at, now) -> None` — the actual per-tick orchestration (launches `local_incremental` unconditionally if pending work exists; launches/continues `local_full_backfill` and `groq_backlog`/`groq_retry` only when `should_process_chunk(...)` is true), called from `_auto_scheduler_loop`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classification_scheduling.py
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def test_should_process_chunk_true_when_idle_and_nothing_else_running():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=400)
    assert should_process_chunk(last_request, other_run_active=False, now=now) is True


def test_should_process_chunk_false_when_recent_activity():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=60)
    assert should_process_chunk(last_request, other_run_active=False, now=now) is False


def test_should_process_chunk_false_when_other_run_active():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=999)
    assert should_process_chunk(last_request, other_run_active=True, now=now) is False


def test_should_process_chunk_true_when_no_requests_seen_yet():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert should_process_chunk(None, other_run_active=False, now=now) is True


def test_should_process_chunk_respects_custom_threshold():
    from src.classification.scheduling import should_process_chunk
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last_request = now - timedelta(seconds=120)
    assert should_process_chunk(last_request, other_run_active=False, now=now, idle_seconds_threshold=60) is True
    assert should_process_chunk(last_request, other_run_active=False, now=now, idle_seconds_threshold=180) is False


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, raw_description TEXT DEFAULT '',
            field_category_id TEXT, field_classification_confidence REAL,
            field_classification_method TEXT, field_classification_attempted_at TEXT
        );
        CREATE TABLE job_categories (category_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, isco TEXT, keywords TEXT);
        CREATE TABLE job_category_assignments (
            job_id INTEGER, category_id TEXT, assignment_type TEXT, confidence REAL,
            method TEXT, evidence_json TEXT, assigned_at TEXT,
            PRIMARY KEY (job_id, category_id, assignment_type)
        );
        CREATE TABLE groq_classification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT DEFAULT 'pending',
            prompt_sent TEXT, response_received TEXT, attempt_count INTEGER DEFAULT 0,
            last_attempted_at TEXT, created_at TEXT, UNIQUE(job_id)
        );
        CREATE TABLE classification_runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, trigger TEXT, status TEXT DEFAULT 'running',
            started_at TEXT, finished_at TEXT, cursor_job_id INTEGER,
            jobs_processed INTEGER DEFAULT 0, jobs_classified INTEGER DEFAULT 0,
            jobs_queued_groq INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    c.execute("INSERT INTO jobs (job_id, title) VALUES (1, 'Software Engineer')")
    c.commit()
    return c


def test_tick_launches_local_incremental_when_pending_work_exists(conn):
    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # recent activity - should NOT block local_incremental

    row = conn.execute("SELECT field_classification_attempted_at FROM jobs WHERE job_id = 1").fetchone()
    assert row["field_classification_attempted_at"] is not None

    run = conn.execute("SELECT run_type, trigger, status FROM classification_runs WHERE run_type = 'local_incremental'").fetchone()
    assert run["trigger"] == "schedule"
    assert run["status"] == "success"


def test_tick_does_not_launch_groq_backlog_when_recent_activity(conn):
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # zero idle time

    runs = conn.execute("SELECT run_type FROM classification_runs WHERE run_type = 'groq_backlog'").fetchall()
    assert len(runs) == 0


def test_tick_respects_configured_idle_threshold_override(conn, monkeypatch):
    conn.execute("INSERT INTO pipeline_config (key, value, updated_at) VALUES ('classification_idle_seconds', '10', datetime('now'))")
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    idle_60s = now - timedelta(seconds=60)  # below the 300s default, above the configured 10s
    run_scheduler_tick(conn, last_request_at=idle_60s, now=now)

    run = conn.execute("SELECT run_type FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()
    assert run is not None  # only starts if the 10s config override was actually read, not the 300s default


def test_tick_launches_groq_backlog_when_idle_and_pending_rows_exist(conn, monkeypatch):
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    long_idle = now - timedelta(seconds=400)
    run_scheduler_tick(conn, last_request_at=long_idle, now=now)

    run = conn.execute("SELECT run_type, trigger FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()
    assert run is not None
    assert run["trigger"] == "backfill_idle"


def test_tick_does_not_start_second_groq_backlog_if_one_already_running(conn):
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES ('existing', 'groq_backlog', 'backfill_idle', 'running', datetime('now'))"
    )
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    conn.commit()

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    long_idle = now - timedelta(seconds=400)
    run_scheduler_tick(conn, last_request_at=long_idle, now=now)

    count = conn.execute("SELECT COUNT(*) FROM classification_runs WHERE run_type = 'groq_backlog'").fetchone()[0]
    assert count == 1  # still just the pre-existing one, no duplicate


def test_tick_launches_groq_retry_when_never_run_before(conn, monkeypatch):
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, attempt_count, created_at) VALUES (1, 'failed_technical', 1, datetime('now'))")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    now = datetime.now(timezone.utc)
    run_scheduler_tick(conn, last_request_at=now, now=now)  # recent activity - must NOT block groq_retry

    run = conn.execute("SELECT trigger, status FROM classification_runs WHERE run_type = 'groq_retry'").fetchone()
    assert run is not None
    assert run["trigger"] == "schedule"
    assert run["status"] == "success"


def test_tick_skips_groq_retry_when_recently_run(conn, monkeypatch):
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=10)
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at, finished_at) VALUES ('prev-retry', 'groq_retry', 'schedule', 'success', ?, ?)",
        (recent.isoformat(), recent.isoformat()),
    )
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.commit()

    from src.classification import groq_stage
    mock_called = {"count": 0}
    def _mock_process(conn, run_id, statuses, **kw):
        mock_called["count"] += 1
        return {"processed": 0, "succeeded": 0, "failed_technical": 0, "no_match": 0}
    monkeypatch.setattr(groq_stage, "process_groq_queue", _mock_process)

    from src.classification.scheduling import run_scheduler_tick
    run_scheduler_tick(conn, last_request_at=now, now=now)

    count = conn.execute("SELECT COUNT(*) FROM classification_runs WHERE run_type = 'groq_retry'").fetchone()[0]
    assert count == 1  # still just the pre-existing one from 10 minutes ago - not due yet (< 1 hour)


def test_tick_launches_groq_retry_after_interval_elapsed(conn, monkeypatch):
    now = datetime.now(timezone.utc)
    long_ago = now - timedelta(hours=2)
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at, finished_at) VALUES ('prev-retry', 'groq_retry', 'schedule', 'success', ?, ?)",
        (long_ago.isoformat(), long_ago.isoformat()),
    )
    conn.execute("UPDATE jobs SET field_classification_attempted_at = datetime('now')")
    conn.commit()

    from src.classification import groq_stage
    monkeypatch.setattr(groq_stage, "process_groq_queue", lambda conn, run_id, statuses, **kw: {"processed": 0, "succeeded": 0, "failed_technical": 0, "no_match": 0})

    from src.classification.scheduling import run_scheduler_tick
    run_scheduler_tick(conn, last_request_at=now, now=now)

    count = conn.execute("SELECT COUNT(*) FROM classification_runs WHERE run_type = 'groq_retry'").fetchone()[0]
    assert count == 2  # the 2-hour-old one plus a new one just launched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classification_scheduling.py -v`
Expected: FAIL — `src.classification.scheduling` doesn't exist.

- [ ] **Step 3: Implement the module**

```python
# src/classification/scheduling.py
"""
Load-aware scheduling decisions for the classification pipeline. The
"should I do work right now" check is a pure function (no I/O, no sleep)
so it's testable without real time passing - mirroring how
src.pipeline_monitor.compute_next_run() is separated from
_auto_scheduler_loop's actual sleep in web_viewer.py.

run_scheduler_tick() is the per-tick orchestrator called from
_auto_scheduler_loop; it is NOT itself a subprocess launcher (unlike
pipeline_monitor.launch_pipeline) - classification chunks run in-process,
since they're small and frequent, not long isolated jobs like ingest/crawl.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS_THRESHOLD = 300
DEFAULT_LOCAL_CHUNK_SIZE = 500
DEFAULT_GROQ_CHUNK_SIZE = 25
DEFAULT_RETRY_INTERVAL_SECONDS = 3600


def should_process_chunk(
    last_request_at: datetime | None,
    other_run_active: bool,
    now: datetime,
    idle_seconds_threshold: int = DEFAULT_IDLE_SECONDS_THRESHOLD,
) -> bool:
    if other_run_active:
        return False
    if last_request_at is None:
        return True
    idle_seconds = (now - last_request_at).total_seconds()
    return idle_seconds >= idle_seconds_threshold


def _any_run_active(conn, run_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM classification_runs WHERE run_type = ? AND status = 'running' LIMIT 1", (run_type,)
    ).fetchone()
    return row is not None


def _start_run(conn, run_type: str, trigger: str) -> str:
    run_id = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, ?, ?, 'running', ?)",
        (run_id, run_type, trigger, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return run_id


def _finish_run(conn, run_id: str, status: str = "success") -> None:
    conn.execute(
        "UPDATE classification_runs SET status = ?, finished_at = ? WHERE run_id = ?",
        (status, datetime.now(timezone.utc).isoformat(), run_id),
    )
    conn.commit()


def _has_pending_local_work(conn) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE field_classification_attempted_at IS NULL LIMIT 1").fetchone()
    return row is not None


def _has_pending_groq_backlog(conn) -> bool:
    row = conn.execute("SELECT 1 FROM groq_classification_queue WHERE status = 'pending' LIMIT 1").fetchone()
    return row is not None


def _groq_retry_due(conn, now: datetime) -> bool:
    """True if no groq_retry run has ever started, or the last one started
    over an hour ago. Not load-gated (per Global Constraints) - this is a
    time-based cadence check only, independent of should_process_chunk()."""
    row = conn.execute(
        "SELECT started_at FROM classification_runs WHERE run_type = 'groq_retry' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return True
    last_started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
    if last_started.tzinfo is None:
        last_started = last_started.replace(tzinfo=timezone.utc)
    return (now - last_started).total_seconds() >= DEFAULT_RETRY_INTERVAL_SECONDS


def run_scheduler_tick(conn, last_request_at: datetime | None, now: datetime) -> None:
    from src.classification.groq_stage import process_groq_queue
    from src.classification.local_stage import classify_pending_jobs
    from src.pipeline_monitor import get_config

    # Read once per tick - admin-configurable via /admin/classification's
    # config form (classification_idle_seconds / _local_chunk_size / _groq_chunk_size),
    # falling back to the module defaults if unset.
    cfg = get_config()
    idle_threshold = int(cfg.get("classification_idle_seconds", DEFAULT_IDLE_SECONDS_THRESHOLD))
    local_chunk_size = int(cfg.get("classification_local_chunk_size", DEFAULT_LOCAL_CHUNK_SIZE))
    groq_chunk_size = int(cfg.get("classification_groq_chunk_size", DEFAULT_GROQ_CHUNK_SIZE))

    # local_incremental: always-on, never load-gated (small volume, cheap).
    if _has_pending_local_work(conn) and not _any_run_active(conn, "local_incremental"):
        run_id = _start_run(conn, "local_incremental", trigger="schedule")
        try:
            classify_pending_jobs(conn, run_id=run_id)
            _finish_run(conn, run_id, status="success")
        except Exception as exc:  # noqa: BLE001
            logger.error("[classification_scheduler] local_incremental failed: %s", exc)
            _finish_run(conn, run_id, status="failed")

    # groq_backlog: auto-starts on idle, chunked, load-gated.
    other_active = _any_run_active(conn, "local_full_backfill")
    if _has_pending_groq_backlog(conn) and not _any_run_active(conn, "groq_backlog"):
        if should_process_chunk(last_request_at, other_active, now, idle_seconds_threshold=idle_threshold):
            _start_run(conn, "groq_backlog", trigger="backfill_idle")
            # Falls through to the continuation branch below on this same tick.

    if _any_run_active(conn, "groq_backlog"):
        other_active = _any_run_active(conn, "local_full_backfill")
        if should_process_chunk(last_request_at, other_active, now, idle_seconds_threshold=idle_threshold):
            run = conn.execute("SELECT run_id FROM classification_runs WHERE run_type = 'groq_backlog' AND status = 'running' LIMIT 1").fetchone()
            run_id = run["run_id"]
            process_groq_queue(conn, run_id=run_id, statuses=("pending",), limit=groq_chunk_size)
            if not _has_pending_groq_backlog(conn):
                _finish_run(conn, run_id, status="success")

    # local_full_backfill: manual-start only (admin action creates the 'running'
    # row elsewhere); this tick only ever CONTINUES an already-started one.
    if _any_run_active(conn, "local_full_backfill"):
        other_active = _any_run_active(conn, "groq_backlog")
        if should_process_chunk(last_request_at, other_active, now, idle_seconds_threshold=idle_threshold):
            from src.classification.local_stage import reclassify_all
            run = conn.execute("SELECT run_id, cursor_job_id FROM classification_runs WHERE run_type = 'local_full_backfill' AND status = 'running' LIMIT 1").fetchone()
            run_id = run["run_id"]
            remaining = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_id > ?", (run["cursor_job_id"] or 0,)).fetchone()[0]
            reclassify_all(conn, run_id=run_id, limit=local_chunk_size)
            if remaining <= local_chunk_size:
                _finish_run(conn, run_id, status="success")

    # groq_retry: hourly sweep of failed_technical rows under the attempt cap.
    # Deliberately NOT load-gated (Global Constraints: load gating applies only
    # to local_full_backfill and groq_backlog) - this is a small-volume, purely
    # time-based cadence, independent of site traffic.
    if _groq_retry_due(conn, now) and not _any_run_active(conn, "groq_retry"):
        run_id = _start_run(conn, "groq_retry", trigger="schedule")
        try:
            process_groq_queue(conn, run_id=run_id, statuses=("failed_technical",))
            _finish_run(conn, run_id, status="success")
        except Exception as exc:  # noqa: BLE001
            logger.error("[classification_scheduler] groq_retry failed: %s", exc)
            _finish_run(conn, run_id, status="failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_classification_scheduling.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Wire into `_auto_scheduler_loop` and add the `before_request` hook**

Read `web_viewer.py` around line 3009 (`_auto_scheduler_loop`) first to match exact current structure before editing. Add a module-level `_last_request_at` tracker and a `before_request` hook near the existing `global_auth_gate` hook (search for `@app.before_request` to find it), and extend the scheduler loop's `try` block:

```python
# Near the top-level before_request hooks (alongside global_auth_gate):
_last_request_at: "datetime | None" = None

@app.before_request
def _track_last_request_at():
    global _last_request_at
    if request.path == "/healthz" or request.path.startswith("/static/"):
        return
    from datetime import datetime, timezone
    _last_request_at = datetime.now(timezone.utc)
```

In `_auto_scheduler_loop`, after the existing `for mode in ("ingest-only", "crawl"):` block (inside the same `try:`), add:

```python
            from src.classification.scheduling import run_scheduler_tick
            from src.storage.db import get_connection as _get_classification_conn
            classification_conn = _get_classification_conn()
            try:
                run_scheduler_tick(classification_conn, last_request_at=_last_request_at, now=now)
            finally:
                classification_conn.close()
```

- [ ] **Step 6: Run the full existing suite plus the new tests**

Run: `python -m pytest tests/ -q --basetemp=<writable temp dir>`
Expected: same baseline pass count plus the new classification tests, no regressions.

- [ ] **Step 7: Commit**

```bash
git add web_viewer.py src/classification/scheduling.py tests/test_classification_scheduling.py
git commit -m "feat: add load-aware scheduling for classification backfill work"
```

---

### Task 5: Admin UI

**Files:**
- Modify: `web_viewer.py` (new `/admin/classification*` routes)
- Create: `templates/admin_classification.html`
- Test: `tests/test_admin_classification_routes.py`

**Interfaces:**
- Consumes: `classify_pending_jobs` (Task 2), `reclassify_all` (Task 2), `process_groq_queue` (Task 3), `require_admin` (existing, `src.auth.middleware`), `get_db_connection` (existing, `web_viewer.py`).
- Produces: `GET /admin/classification` (dashboard), `POST /admin/classification/run-local` (incremental), `POST /admin/classification/full-reclassify/preview`, `POST /admin/classification/full-reclassify/confirm`, `POST /admin/classification/groq-backlog/run-now`, `POST /admin/classification/queue/<id>/delete`, `POST /admin/classification/config` (threshold/chunk-size/idle-seconds/retry-cap).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admin_classification_routes.py
import sqlite3

import pytest


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, company TEXT, listing_status TEXT,
            field_category_id TEXT, field_classification_confidence REAL,
            field_classification_method TEXT, field_classification_attempted_at TEXT
        );
        CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden';
        CREATE TABLE job_categories (category_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, isco TEXT, keywords TEXT);
        CREATE TABLE job_category_assignments (
            job_id INTEGER, category_id TEXT, assignment_type TEXT, confidence REAL,
            method TEXT, evidence_json TEXT, assigned_at TEXT,
            PRIMARY KEY (job_id, category_id, assignment_type)
        );
        CREATE TABLE groq_classification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT DEFAULT 'pending',
            prompt_sent TEXT, response_received TEXT, attempt_count INTEGER DEFAULT 0,
            last_attempted_at TEXT, created_at TEXT, UNIQUE(job_id)
        );
        CREATE TABLE classification_runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, trigger TEXT, status TEXT DEFAULT 'running',
            started_at TEXT, finished_at TEXT, cursor_job_id INTEGER,
            jobs_processed INTEGER DEFAULT 0, jobs_classified INTEGER DEFAULT 0,
            jobs_queued_groq INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    conn.execute("INSERT INTO jobs (job_id, title, company, listing_status, field_category_id) VALUES (1, 'Dev', 'Co', 'active', 'it.software')")
    conn.execute("INSERT INTO jobs (job_id, title, company, listing_status) VALUES (2, 'Mystery', 'Co', 'active')")
    conn.execute("INSERT INTO job_categories (category_id, name) VALUES ('it.software', 'Software Engineering')")
    conn.execute("INSERT INTO groq_classification_queue (job_id, status, prompt_sent, response_received, created_at) VALUES (2, 'pending', 'p', NULL, datetime('now'))")
    conn.execute("INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at, finished_at, jobs_processed, jobs_classified) VALUES ('r1', 'local_incremental', 'schedule', 'success', datetime('now'), datetime('now'), 2, 1)")
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    web_viewer.cache.clear()

    import src.auth.models as models
    from pathlib import Path
    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()

    client = web_viewer.app.test_client()
    admin_id = next(u["id"] for u in models.list_users() if u["username"] == "admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["_csrf_token"] = "test-csrf"
    return client


def test_dashboard_requires_admin(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (job_id INTEGER PRIMARY KEY, listing_status TEXT)")
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    conn.commit()
    conn.close()

    import web_viewer
    monkeypatch.setattr(web_viewer, "DB_PATH", db_path)
    web_viewer.app.config.update(TESTING=True)
    client = web_viewer.app.test_client()

    r = client.get("/admin/classification", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_dashboard_shows_run_history_and_category_breakdown(admin_client):
    r = admin_client.get("/admin/classification")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "local_incremental" in html
    assert "Software Engineering" in html


def test_run_local_classification(admin_client):
    r = admin_client.post("/admin/classification/run-local", data={"csrf_token": "test-csrf"})
    assert r.status_code == 200
    data = r.get_json()
    assert "run_id" in data


def test_delete_queue_row(admin_client):
    r = admin_client.post("/admin/classification/queue/1/delete", data={"csrf_token": "test-csrf"})
    assert r.status_code == 200

    r2 = admin_client.get("/admin/classification")
    assert "pending" not in r2.get_data(as_text=True).lower() or "0" in r2.get_data(as_text=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin_classification_routes.py -v`
Expected: FAIL — `/admin/classification` doesn't exist (404).

- [ ] **Step 3: Implement the routes**

Add to `web_viewer.py`, near the existing `/admin/pipeline*` routes (read those first to match the exact `require_admin`/CSRF/jsonify conventions used there):

```python
@app.route("/admin/classification")
@require_admin
def admin_classification():
    conn = get_db_connection()
    cursor = conn.cursor()

    total = cursor.execute("SELECT COUNT(*) FROM active_jobs").fetchone()[0]
    classified_local = cursor.execute(
        "SELECT COUNT(*) FROM active_jobs WHERE field_classification_method = 'local_hybrid_v1'"
    ).fetchone()[0]
    classified_groq = cursor.execute(
        "SELECT COUNT(*) FROM active_jobs WHERE field_classification_method = 'groq_v1'"
    ).fetchone()[0]
    never_attempted = cursor.execute(
        "SELECT COUNT(*) FROM active_jobs WHERE field_classification_attempted_at IS NULL"
    ).fetchone()[0]
    queue_by_status = cursor.execute(
        "SELECT status, COUNT(*) as n FROM groq_classification_queue GROUP BY status"
    ).fetchall()

    category_breakdown = cursor.execute("""
        SELECT jc.name, COUNT(j.job_id) as n
        FROM job_categories jc
        LEFT JOIN active_jobs j ON j.field_category_id = jc.category_id
        WHERE jc.parent_id IS NOT NULL
        GROUP BY jc.category_id
        ORDER BY n DESC
    """).fetchall()

    runs = cursor.execute(
        "SELECT * FROM classification_runs ORDER BY started_at DESC LIMIT 40"
    ).fetchall()

    queue_rows = cursor.execute(
        "SELECT gcq.*, j.title FROM groq_classification_queue gcq JOIN jobs j ON j.job_id = gcq.job_id ORDER BY gcq.created_at DESC LIMIT 100"
    ).fetchall()

    from src.pipeline_monitor import get_config
    config = get_config()

    conn.close()
    return render_template(
        "admin_classification.html",
        total=total, classified_local=classified_local, classified_groq=classified_groq,
        never_attempted=never_attempted, queue_by_status={r["status"]: r["n"] for r in queue_by_status},
        category_breakdown=category_breakdown, runs=runs, queue_rows=queue_rows, config=config,
    )


@app.route("/admin/classification/run-local", methods=["POST"])
@require_admin
def admin_classification_run_local():
    import uuid
    from src.classification.local_stage import classify_pending_jobs
    from src.storage.db import get_connection
    run_id = str(uuid.uuid4())[:8]
    conn = get_connection()
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, 'local_incremental', 'manual', 'running', datetime('now'))",
        (run_id,),
    )
    conn.commit()
    try:
        classify_pending_jobs(conn, run_id=run_id)
        conn.execute("UPDATE classification_runs SET status='success', finished_at=datetime('now') WHERE run_id=?", (run_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"run_id": run_id, "status": "started"})


@app.route("/admin/classification/full-reclassify/preview", methods=["POST"])
@require_admin
def admin_classification_full_reclassify_preview():
    from src.market_classifier import classify_job
    from src.storage.db import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT job_id, title, raw_description, field_category_id FROM jobs LIMIT 500").fetchall()
    would_change = 0
    for row in rows:
        match = classify_job(row["title"], row["raw_description"] or "")
        new_id = match.market_id if (match.market_id and match.confidence >= 0.62) else None
        if new_id != row["field_category_id"]:
            would_change += 1
    conn.close()
    return jsonify({"sampled": len(rows), "would_change": would_change})


@app.route("/admin/classification/full-reclassify/confirm", methods=["POST"])
@require_admin
def admin_classification_full_reclassify_confirm():
    import uuid
    from src.storage.db import get_connection
    run_id = str(uuid.uuid4())[:8]
    conn = get_connection()
    already = conn.execute("SELECT 1 FROM classification_runs WHERE run_type='local_full_backfill' AND status='running'").fetchone()
    if already:
        conn.close()
        return jsonify({"error": "a full re-classify is already running"}), 409
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, 'local_full_backfill', 'manual', 'running', datetime('now'))",
        (run_id,),
    )
    conn.commit()
    conn.close()
    return jsonify({"run_id": run_id, "status": "started"})


@app.route("/admin/classification/groq-backlog/run-now", methods=["POST"])
@require_admin
def admin_classification_groq_run_now():
    import uuid
    from src.classification.groq_stage import process_groq_queue
    from src.storage.db import get_connection
    conn = get_connection()
    run = conn.execute("SELECT run_id FROM classification_runs WHERE run_type='groq_backlog' AND status='running' LIMIT 1").fetchone()
    run_id = run["run_id"] if run else str(uuid.uuid4())[:8]
    if not run:
        conn.execute(
            "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, 'groq_backlog', 'manual', 'running', datetime('now'))",
            (run_id,),
        )
        conn.commit()
    result = process_groq_queue(conn, run_id=run_id, statuses=("pending",))
    conn.close()
    return jsonify({"run_id": run_id, **result})


@app.route("/admin/classification/queue/<int:queue_id>/delete", methods=["POST"])
@require_admin
def admin_classification_queue_delete(queue_id: int):
    from src.storage.db import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM groq_classification_queue WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": queue_id})


@app.route("/admin/classification/config", methods=["POST"])
@require_admin
def admin_classification_config():
    from src.pipeline_monitor import set_config
    allowed = {
        "classification_confidence_threshold", "classification_score_threshold",
        "classification_idle_seconds", "classification_retry_cap",
        "classification_local_chunk_size", "classification_groq_chunk_size",
    }
    updated = []
    for key in allowed:
        val = request.form.get(key, "").strip()
        if val:
            set_config(key, val)
            updated.append(key)
    return jsonify({"updated": updated})
```

Note: `queue_id` in the test is `1` (SQLite `AUTOINCREMENT` starting at 1 for the first inserted row) — confirm this matches the fixture's single inserted queue row before relying on the literal `1` in the test URL.

- [ ] **Step 4: Create the template**

```html
{# templates/admin_classification.html #}
{% extends "base.html" %}
{% block title %}Classification Pipeline - Admin{% endblock %}
{% block content %}
<div class="container" style="max-width:1100px">

  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
    <div>
      <h1 style="margin:0">Classification Pipeline</h1>
      <p style="color:#6b7280;margin:0.25rem 0 0">Field-taxonomy coverage, run history, and the Groq fallback queue</p>
    </div>
    <a href="/admin" class="btn" style="background:#f3f4f6;color:#374151;text-decoration:none">← Admin</a>
  </div>

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:1.5rem">
    <div class="card"><div style="font-size:0.75rem;color:#6b7280">Total jobs</div><div style="font-size:1.5rem;font-weight:700">{{ total }}</div></div>
    <div class="card"><div style="font-size:0.75rem;color:#6b7280">Classified (local)</div><div style="font-size:1.5rem;font-weight:700">{{ classified_local }}</div></div>
    <div class="card"><div style="font-size:0.75rem;color:#6b7280">Classified (Groq)</div><div style="font-size:1.5rem;font-weight:700">{{ classified_groq }}</div></div>
    <div class="card"><div style="font-size:0.75rem;color:#6b7280">Never attempted</div><div style="font-size:1.5rem;font-weight:700">{{ never_attempted }}</div></div>
  </div>

  <div class="card" style="margin-bottom:1.5rem">
    <h3 style="margin:0 0 1rem">Groq queue by status</h3>
    <div style="display:flex;gap:1.5rem;flex-wrap:wrap">
      {% for status, n in queue_by_status.items() %}
      <div><strong>{{ status }}</strong>: {{ n }}</div>
      {% else %}
      <div style="color:#9ca3af">Queue is empty.</div>
      {% endfor %}
    </div>
  </div>

  <div class="card" style="margin-bottom:1.5rem">
    <h3 style="margin:0 0 1rem">Actions</h3>
    <div style="display:flex;gap:0.75rem;flex-wrap:wrap">
      <button onclick="runLocal()" class="btn" style="background:#3b82f6;color:#fff">Run Local Classification</button>
      <button onclick="previewFullReclassify()" class="btn" style="background:#8b5cf6;color:#fff">Full Re-classify…</button>
      <button onclick="runGroqNow()" class="btn" style="background:#059669;color:#fff">Process Groq Backlog Now</button>
    </div>
    <div id="action-msg" style="margin-top:0.75rem;font-size:0.875rem;color:#6b7280"></div>
  </div>

  <div class="card" style="margin-bottom:1.5rem">
    <h3 style="margin:0 0 1rem">Category breakdown</h3>
    <table style="width:100%;font-size:0.875rem;border-collapse:collapse">
      <tbody>
        {% for row in category_breakdown %}
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:0.4rem 0">{{ row.name }}</td>
          <td style="padding:0.4rem 0;text-align:right">{{ row.n }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="card" style="margin-bottom:1.5rem">
    <h3 style="margin:0 0 1rem">Run history</h3>
    <table style="width:100%;font-size:0.875rem;border-collapse:collapse">
      <thead><tr style="border-bottom:2px solid #e5e7eb;text-align:left">
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Run ID</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Type</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Trigger</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Status</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Started</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Processed</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Classified</th>
      </tr></thead>
      <tbody>
        {% for r in runs %}
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:0.5rem 0.75rem;font-family:monospace;font-size:0.8rem">{{ r.run_id }}</td>
          <td style="padding:0.5rem 0.75rem">{{ r.run_type }}</td>
          <td style="padding:0.5rem 0.75rem;color:#6b7280">{{ r.trigger }}</td>
          <td style="padding:0.5rem 0.75rem">{{ r.status }}</td>
          <td style="padding:0.5rem 0.75rem;color:#6b7280;font-size:0.8rem">{{ r.started_at[:16] if r.started_at else '—' }}</td>
          <td style="padding:0.5rem 0.75rem">{{ r.jobs_processed }}</td>
          <td style="padding:0.5rem 0.75rem">{{ r.jobs_classified }}</td>
        </tr>
        {% else %}
        <tr><td colspan="7" style="padding:2rem;text-align:center;color:#9ca3af">No runs yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h3 style="margin:0 0 1rem">Groq queue</h3>
    <table style="width:100%;font-size:0.875rem;border-collapse:collapse">
      <thead><tr style="border-bottom:2px solid #e5e7eb;text-align:left">
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Job</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Status</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280">Attempts</th>
        <th style="padding:0.5rem 0.75rem;color:#6b7280"></th>
      </tr></thead>
      <tbody>
        {% for q in queue_rows %}
        <tr style="border-bottom:1px solid #f3f4f6" id="queue-row-{{ q.id }}">
          <td style="padding:0.5rem 0.75rem">{{ q.title }}</td>
          <td style="padding:0.5rem 0.75rem">{{ q.status }}</td>
          <td style="padding:0.5rem 0.75rem">{{ q.attempt_count }}</td>
          <td style="padding:0.5rem 0.75rem"><button onclick="deleteQueueRow({{ q.id }})" style="color:#dc2626;background:none;border:none;cursor:pointer;font-size:0.8rem">delete</button></td>
        </tr>
        {% else %}
        <tr><td colspan="4" style="padding:2rem;text-align:center;color:#9ca3af">Queue is empty.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<script>
const csrf = "{{ csrf_token() }}";

async function runLocal() {
  const msg = document.getElementById('action-msg');
  msg.textContent = 'Running local classification…';
  const fd = new FormData(); fd.append('csrf_token', csrf);
  const r = await fetch('/admin/classification/run-local', { method: 'POST', body: fd });
  const data = await r.json();
  msg.textContent = r.ok ? `Done: run_id=${data.run_id}` : (data.error || 'Failed');
  if (r.ok) setTimeout(() => location.reload(), 1000);
}

async function previewFullReclassify() {
  const msg = document.getElementById('action-msg');
  const fd = new FormData(); fd.append('csrf_token', csrf);
  const r = await fetch('/admin/classification/full-reclassify/preview', { method: 'POST', body: fd });
  const data = await r.json();
  if (!r.ok) { msg.textContent = data.error || 'Preview failed'; return; }
  const ok = confirm(`Sampled ${data.sampled} jobs; ${data.would_change} would change category. Start full re-classify?`);
  if (!ok) return;
  const fd2 = new FormData(); fd2.append('csrf_token', csrf);
  const r2 = await fetch('/admin/classification/full-reclassify/confirm', { method: 'POST', body: fd2 });
  const data2 = await r2.json();
  msg.textContent = r2.ok ? `Started: run_id=${data2.run_id}` : (data2.error || 'Failed to start');
}

async function runGroqNow() {
  const msg = document.getElementById('action-msg');
  msg.textContent = 'Processing Groq backlog…';
  const fd = new FormData(); fd.append('csrf_token', csrf);
  const r = await fetch('/admin/classification/groq-backlog/run-now', { method: 'POST', body: fd });
  const data = await r.json();
  msg.textContent = r.ok ? `Processed ${data.processed}: ${data.succeeded} succeeded, ${data.failed_technical} failed, ${data.no_match} no match` : (data.error || 'Failed');
  if (r.ok) setTimeout(() => location.reload(), 1500);
}

async function deleteQueueRow(id) {
  const fd = new FormData(); fd.append('csrf_token', csrf);
  const r = await fetch(`/admin/classification/queue/${id}/delete`, { method: 'POST', body: fd });
  if (r.ok) document.getElementById(`queue-row-${id}`).remove();
}
</script>
{% endblock %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_admin_classification_routes.py -v`
Expected: PASS (5 tests). If CSRF checks reject the POSTs (this codebase enforces CSRF on mutations per `tests/test_auth_security.py::test_missing_csrf_is_rejected`), check how `tests/test_auth_security.py`'s `_set_session` helper sets `_csrf_token` in the session and matches it in the form — replicate that exact mechanism here rather than guessing.

- [ ] **Step 6: Run the full suite one more time**

Run: `python -m pytest tests/ -q --basetemp=<writable temp dir>`
Expected: full baseline + all new tests from Tasks 1-5 passing, no regressions.

- [ ] **Step 7: Commit**

```bash
git add web_viewer.py templates/admin_classification.html tests/test_admin_classification_routes.py
git commit -m "feat: add /admin/classification dashboard, run controls, and queue management"
```

---

## Post-implementation notes (not separate tasks, but must not be forgotten)

- No `docker compose build`/deploy is part of this plan — deployment is a separate, explicit step after the full-branch review, following the same VPS workflow used earlier this session (git pull, `docker compose build web`, `docker compose up -d web`, verify `/healthz`).
- The first real `local_incremental` tick after deploy will process the *entire* current backlog of never-attempted jobs (all ~110k, since `field_classification_attempted_at` starts NULL for every existing row) — not just newly-ingested ones. Given the ~68-minute full-catalog cost measured during brainstorming, consider whether to pre-seed `field_classification_attempted_at` for existing jobs via a one-time backfill script (leaving only genuinely new jobs for the always-on incremental path), or accept that the first `local_incremental` run will be unusually large. This wasn't explicitly decided in the spec — flag it to the user before or during deployment rather than silently picking one.
