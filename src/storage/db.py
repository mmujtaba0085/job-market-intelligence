"""
src/storage/db.py
─────────────────
SQLite connection + all CRUD helpers.
Uses Python's built-in sqlite3 only (no third-party DB library).

Public API:
  get_connection()        → sqlite3.Connection
  run_migrations()        → creates tables if not exist
  upsert_job()            → insert or update-last_seen_at on dedupe hit
  insert_skills()         → bulk insert SkillSignal objects
  upsert_weekly_metric()  → insert or replace weekly_metrics row
  get_jobs_for_week()     → fetch jobs ingested within an ISO week
  get_weekly_metrics()    → fetch metrics for a market + week
  get_prior_week_metrics()→ fetch metrics N weeks back for growth calc
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import (
    DB_PATH,
    SHEETS_CANADA_ID,
    SHEETS_UK_ID,
    SHEETS_US_ID,
    SHEETS_CANADA_PUBLISHED_ID,
    SHEETS_UK_PUBLISHED_ID,
    SHEETS_US_PUBLISHED_ID,
)
from src.storage.models import JobNormalized, SkillSignal, WeeklyMetric

try:
    import fcntl  # Unix only - not available on Windows, guarded for local dev
except ImportError:
    fcntl = None

# ─── Migration path ───────────────────────────────────────────────────────────
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_LOCK_PATH = _MIGRATIONS_DIR.parent / ".migrations.lock"

logger = logging.getLogger(__name__)

# ─── Rotating-DB paths & pointer ──────────────────────────────────────────────
_DATA_DIR = DB_PATH.parent
_OPERATIONAL_DB_PATH = _DATA_DIR / "operational.sqlite"
_SERVING_A_PATH = _DATA_DIR / "serving_a.sqlite"
_SERVING_B_PATH = _DATA_DIR / "serving_b.sqlite"
_BUFFER_DB_PATH = _DATA_DIR / "buffer.sqlite"
_POINTER_PATH = _DATA_DIR / "serving_pointer.txt"
_ROTATION_LOCK_PATH = _DATA_DIR / ".rotation.lock"
_CLASSIFICATION_SCHEDULER_LOCK_PATH = _DATA_DIR / ".classification_scheduler.lock"

# Which logical target get_connection() resolves to. Defaults to "serving" -
# only orchestrator.py's ingest-only path (use_buffer_connection) and
# db_rotation.py's merge step (use_free_connection) ever change this, each
# scoped to a `with` block so it can never leak into an unrelated request.
_connection_target: contextvars.ContextVar[str] = contextvars.ContextVar(
    "connection_target", default="serving"
)


def _serving_path_for(which: str) -> Path:
    return _SERVING_A_PATH if which == "a" else _SERVING_B_PATH


def _read_pointer() -> str:
    if not _POINTER_PATH.exists():
        return "a"
    value = _POINTER_PATH.read_text(encoding="utf-8").strip()
    return value if value in ("a", "b") else "a"


def _write_pointer(which: str) -> None:
    # Atomic: a reader that calls _read_pointer() mid-write either sees the
    # old value or the new one, never a truncated/partial file.
    tmp_path = _POINTER_PATH.with_suffix(_POINTER_PATH.suffix + ".tmp")
    tmp_path.write_text(which, encoding="utf-8")
    os.replace(tmp_path, _POINTER_PATH)


def _serving_path() -> Path:
    return _serving_path_for(_read_pointer())


def _free_path() -> Path:
    other = "b" if _read_pointer() == "a" else "a"
    return _serving_path_for(other)


def serving_db_path() -> Path:
    """Public accessor for callers (web_viewer.py's own get_db_connection())
    that need the raw path rather than a ready-made connection."""
    return _serving_path()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled.

    Resolves dynamically: Serving by default, or Buffer/Free while
    use_buffer_connection()/use_free_connection() is active in the current
    context (see docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md).
    """
    target = _connection_target.get()
    if target == "buffer":
        path = _BUFFER_DB_PATH
    elif target == "free":
        path = _free_path()
    else:
        path = _serving_path()
    return _connect(path)


def get_free_connection() -> sqlite3.Connection:
    """Always the non-Serving of serving_a/serving_b. Used by the
    classification pipeline and db_rotation.py's merge step."""
    return _connect(_free_path())


def get_buffer_connection() -> sqlite3.Connection:
    """Always buffer.sqlite. Used by ingest-only ingestion writes and
    db_rotation.py's merge-then-clear step."""
    return _connect(_BUFFER_DB_PATH)


def get_operational_connection() -> sqlite3.Connection:
    """pipeline_config / pipeline_runs / notifications - never rotates."""
    conn = sqlite3.connect(_OPERATIONAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def use_buffer_connection():
    token = _connection_target.set("buffer")
    try:
        yield
    finally:
        _connection_target.reset(token)


@contextmanager
def use_free_connection():
    token = _connection_target.set("free")
    try:
        yield
    finally:
        _connection_target.reset(token)


def run_migrations() -> None:
    """
    Idempotently apply all SQL migration files in order.

    File-locked on Unix (a no-op on Windows, where fcntl doesn't exist -
    local dev/tests run single-process, so there's no concurrent-writer
    race to guard against there). Without this lock, gunicorn's N worker
    processes each import web_viewer.py independently at startup, and each
    one calls this function - on a deploy carrying real schema changes, all
    of them race to write the same DDL to the same SQLite file at once.
    Confirmed on a real deploy: this raised "database is locked" in every
    worker, which gunicorn treated as "worker failed to boot" and shut the
    entire master process down (Docker's restart policy happened to save
    it on retry, once the migration was already applied and there was
    nothing left to race over - not a fix, just luck).
    """
    if fcntl is None:
        _run_all_migrations()
        return

    _MIGRATION_LOCK_PATH.touch(exist_ok=True)
    with open(_MIGRATION_LOCK_PATH, "r+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _run_all_migrations()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _run_all_migrations() -> None:
    _bootstrap_rotation_files()

    op_conn = sqlite3.connect(_OPERATIONAL_DB_PATH)
    op_conn.row_factory = sqlite3.Row
    try:
        with op_conn:
            _run_operational_migrations_impl(op_conn)
    finally:
        op_conn.close()

    for path in (_SERVING_A_PATH, _SERVING_B_PATH, _BUFFER_DB_PATH):
        rotating_conn = sqlite3.connect(path)
        rotating_conn.row_factory = sqlite3.Row
        try:
            _run_rotating_migrations_impl(rotating_conn)
        finally:
            rotating_conn.close()


def _bootstrap_rotation_files() -> None:
    """One-time split of the legacy single-file DB_PATH into operational.sqlite
    (pipeline_config/pipeline_runs) + serving_a.sqlite AND serving_b.sqlite
    (both get the full legacy dataset - see below for why), run once under
    run_migrations()'s lock. Guarded by _POINTER_PATH existing - once that's
    written, bootstrap already happened and this is a no-op. buffer.sqlite
    is always created fresh (empty) by _run_rotating_migrations_impl() below
    - Buffer is genuinely meant to start empty (only ever holds not-yet-
    merged new ingestion). The legacy DB_PATH file is left on disk untouched
    (not deleted) - nothing writes to it after this point, but it remains as
    a manual recovery copy and stays usable by scripts/warehouse_rollout.py's
    --source argument.

    Both serving_a AND serving_b must start with the real data, not just
    serving_a: db_rotation.py's rotate() assumes Free is always a
    roughly-current mirror of Serving (each prior rotation's "refresh the
    demoted file" step is what keeps them in sync going forward) - an
    assumption that's only true starting with the SECOND rotation. On a
    fresh deploy, the scheduler's first idle tick has no last_rotation_at
    to compare against and rotates immediately (see _auto_scheduler_loop) -
    if Free (serving_b) had been left empty here, that first rotation would
    promote an empty file to Serving, losing the live site's access to
    every pre-existing job until manually recovered. Confirmed in production:
    exactly this happened on this feature's first deploy before this fix."""
    if _POINTER_PATH.exists():
        return

    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        if not _SERVING_A_PATH.exists():
            _sqlite_file_backup(DB_PATH, _SERVING_A_PATH)
        if not _SERVING_B_PATH.exists():
            _sqlite_file_backup(DB_PATH, _SERVING_B_PATH)
        if not _OPERATIONAL_DB_PATH.exists():
            _sqlite_file_backup(DB_PATH, _OPERATIONAL_DB_PATH)

    _write_pointer("a")
    logger.info("[db] Bootstrapped rotating DB files")


def _sqlite_file_backup(source: Path, destination: Path) -> None:
    """Whole-file consistent snapshot via SQLite's Online Backup API - same
    call shape as scripts/warehouse_rollout.py::_sqlite_backup()."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def _run_operational_migrations_impl(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id      TEXT PRIMARY KEY,
            mode        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'running',
            trigger     TEXT NOT NULL DEFAULT 'schedule',
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds INTEGER,
            jobs_fetched    INTEGER DEFAULT 0,
            jobs_inserted   INTEGER DEFAULT 0,
            jobs_deduped    INTEGER DEFAULT 0,
            skills_extracted INTEGER DEFAULT 0,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at DESC);

        CREATE TABLE IF NOT EXISTS pipeline_config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            heading      TEXT NOT NULL,
            body         TEXT NOT NULL,
            severity     TEXT NOT NULL DEFAULT 'info',
            target_pages TEXT NOT NULL DEFAULT 'all',
            created_at   TEXT NOT NULL,
            expires_at   TEXT,
            removed_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_active ON notifications(removed_at, expires_at);

        CREATE TABLE IF NOT EXISTS job_reports (
            report_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id           INTEGER,
            job_url          TEXT NOT NULL,
            job_title        TEXT NOT NULL,
            reason_category  TEXT NOT NULL,
            details          TEXT,
            reporter_user_id INTEGER,
            reporter_email   TEXT,
            reporter_ip      TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'open',
            admin_notes      TEXT,
            created_at       TEXT NOT NULL,
            resolved_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_job_reports_status ON job_reports(status);
        CREATE INDEX IF NOT EXISTS idx_job_reports_job_url ON job_reports(job_url);

        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            category          TEXT NOT NULL,
            subject           TEXT NOT NULL,
            details           TEXT NOT NULL,
            submitter_user_id INTEGER,
            submitter_email   TEXT,
            submitter_ip      TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'open',
            admin_notes       TEXT,
            created_at        TEXT NOT NULL,
            resolved_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
    """)
    defaults = [
        ("ingest_interval_hours",       "12"),
        ("crawl_interval_hours",        "4"),
        ("crawl_max_runtime_minutes",   "30"),
        ("weekly_day",                  "Sunday"),
        ("weekly_time",                 "03:00"),
        ("show_source_names",           "true"),
        ("rotation_max_interval_hours", "12"),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO pipeline_config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )


def _run_rotating_migrations_impl(conn: sqlite3.Connection) -> None:
    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    with conn:
        for mf in migration_files:
            sql = mf.read_text(encoding="utf-8")
            # Skip truly empty migration files only; comment-prefixed SQL files are valid.
            if not sql.strip():
                continue

            # Special handling for migration 002 - add columns if they don't exist
            if mf.name == "002_add_growth_columns.sql":
                # Check if columns already exist
                cursor = conn.execute("PRAGMA table_info(weekly_metrics)")
                columns = {row[1] for row in cursor.fetchall()}
                # Special handling for migration 002 - add columns if they don't exist
                if "absolute_delta" not in columns:
                    conn.execute("ALTER TABLE weekly_metrics ADD COLUMN absolute_delta INTEGER DEFAULT 0")
                    logger.info("[db] Added column: absolute_delta")

                if "mover_score" not in columns:
                    conn.execute("ALTER TABLE weekly_metrics ADD COLUMN mover_score REAL DEFAULT 0.0")
                    logger.info("[db] Added column: mover_score")
                
            # Special handling for migration 003 - multi-location support
            elif mf.name == "003_multi_location_support.sql":
                # Check if columns already exist in jobs table
                cursor = conn.execute("PRAGMA table_info(jobs)")
                job_columns = {row[1] for row in cursor.fetchall()}

                if "job_group_id" not in job_columns:
                    conn.execute("ALTER TABLE jobs ADD COLUMN job_group_id TEXT")
                    logger.info("[db] Added column: job_group_id")
                    # Populate job_group_id from existing canonical_hash
                    conn.execute("UPDATE jobs SET job_group_id = SUBSTR(canonical_hash, 1, 16)")
                    logger.info("[db] Populated job_group_id from canonical_hash")

                if "location_count" not in job_columns:
                    conn.execute("ALTER TABLE jobs ADD COLUMN location_count INTEGER DEFAULT 1")
                    logger.info("[db] Added column: location_count")

                # Create indexes
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_group ON jobs(job_group_id)")

                # Create job_locations table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS job_locations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                        job_group_id TEXT NOT NULL,
                        location TEXT NOT NULL DEFAULT '',
                        country TEXT NOT NULL DEFAULT '',
                        remote_type TEXT NOT NULL DEFAULT 'unknown',
                        salary_min REAL,
                        salary_max REAL,
                        currency TEXT,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        UNIQUE(job_group_id, location, country)
                    )
                """)
                logger.info("[db] Created table: job_locations")

                # Create indexes for job_locations
                conn.execute("CREATE INDEX IF NOT EXISTS idx_job_locations_group ON job_locations(job_group_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_job_locations_location ON job_locations(location)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_job_locations_country ON job_locations(country)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_job_locations_job_id ON job_locations(job_id)")

                # Migrate existing data to job_locations table (only if not already migrated)
                cursor = conn.execute("SELECT COUNT(*) FROM job_locations")
                if cursor.fetchone()[0] == 0:
                    conn.execute("""
                        INSERT OR IGNORE INTO job_locations (
                            job_id, job_group_id, location, country, remote_type,
                            salary_min, salary_max, currency,
                            first_seen_at, last_seen_at
                        )
                        SELECT 
                            job_id, job_group_id, location, country, remote_type,
                            salary_min, salary_max, currency,
                            first_seen_at, last_seen_at
                        FROM jobs
                        WHERE job_group_id IS NOT NULL
                    """)
                    rows_migrated = conn.execute("SELECT COUNT(*) FROM job_locations").fetchone()[0]
                    logger.info(f"[db] Migrated {rows_migrated} job locations from jobs table")

                    # Update location_count for existing jobs
                    conn.execute("""
                        UPDATE jobs
                        SET location_count = (
                            SELECT COUNT(DISTINCT location)
                            FROM job_locations
                            WHERE job_locations.job_group_id = jobs.job_group_id
                        )
                        WHERE job_group_id IS NOT NULL
                    """)
                    logger.info("[db] Updated location_count for existing jobs")
                
            # Skip migrations handled conditionally after the loop
            elif mf.name in ["004_add_normalized_title.sql", "005_add_normalization_confidence.sql", "006_job_click_tracking.sql", "007_listing_status.sql"]:
                pass
                
            else:
                # Run other migrations normally
                conn.executescript(sql)
        
        # Migration 004: Add normalized_title column (conditional)
        cursor = conn.execute("PRAGMA table_info(jobs)")
        job_columns = {row[1] for row in cursor.fetchall()}

        # Ensure week_id exists before creating any index/query path that depends on it.
        if "week_id" not in job_columns:
            logger.info("[db] Running migration 004: add week_id column")
            conn.execute("ALTER TABLE jobs ADD COLUMN week_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_week_id ON jobs(week_id)")
            job_columns.add("week_id")
            logger.info("[db] Migration 004 complete: week_id column added")
        
        if "normalized_title" not in job_columns:
            logger.info("[db] Running migration 004: add normalized_title column")
            conn.execute("ALTER TABLE jobs ADD COLUMN normalized_title TEXT")
            
            # Create indexes for analytics performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_normalized_title 
                ON jobs(normalized_title)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_normalized_title_week 
                ON jobs(normalized_title, week_id)
            """)
            logger.info("[db] Created indexes for normalized_title")
            
            # Initialize with original title (will be updated by backfill script)
            conn.execute("""
                UPDATE jobs 
                SET normalized_title = title 
                WHERE normalized_title IS NULL
            """)
            logger.info("[db] Migration 004 complete: normalized_title column added")
        
        # Migration 005: Add normalization_confidence column (conditional)
        if "normalization_confidence" not in job_columns:
            logger.info("[db] Running migration 005: add normalization_confidence column")
            conn.execute("ALTER TABLE jobs ADD COLUMN normalization_confidence REAL DEFAULT 0.0")
            
            # Create index for querying low-confidence jobs
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_norm_confidence 
                ON jobs(normalization_confidence)
            """)
            logger.info("[db] Migration 005 complete: normalization_confidence column added")

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

        # Migration 006: Add job_id and click_type to sheets_click_tracking (conditional)
        cursor = conn.execute("PRAGMA table_info(sheets_click_tracking)")
        tracking_columns = {row[1] for row in cursor.fetchall()}
        
        if "job_id" not in tracking_columns:
            logger.info("[db] Running migration 006: add job_id column to sheets_click_tracking")
            conn.execute("ALTER TABLE sheets_click_tracking ADD COLUMN job_id INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clicks_job_id ON sheets_click_tracking(job_id)")
            logger.info("[db] Added job_id column and index")
        
        if "click_type" not in tracking_columns:
            logger.info("[db] Running migration 006: add click_type column to sheets_click_tracking")
            conn.execute("ALTER TABLE sheets_click_tracking ADD COLUMN click_type TEXT DEFAULT 'tab_navigation'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clicks_type ON sheets_click_tracking(click_type)")
            logger.info("[db] Added click_type column and index")

        # Migration 007: listing_status column
        _ensure_column(conn, "jobs", "listing_status", "listing_status TEXT NOT NULL DEFAULT 'active'")

        # Migration 007b: Dynamic spreadsheet targets + staging review fields
        _ensure_dynamic_sheet_targets(conn)

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

        # Migration 011: newspaper — the publication a Pakistan Jobs Bank ad
        # ran in (Jang, Dawn, Express, ...). Previously only embedded in
        # raw_description free text; now its own queryable column.
        _ensure_column(conn, "jobs", "newspaper", "newspaper TEXT")

        # Migration 012: ad_image_url / apply_url — Pakistan Jobs Bank ads are
        # scanned newspaper clippings (a per-ad image, not text); apply_url is
        # the source ad's own external "how to apply" link when it has one.
        _ensure_column(conn, "jobs", "ad_image_url", "ad_image_url TEXT")
        _ensure_column(conn, "jobs", "apply_url", "apply_url TEXT")

        # Migration 013: salary_period — distinguishes hourly rates (common
        # for internship listings, e.g. "$62/hr") from annual figures, so
        # salary_min/salary_max are never silently misread as one or the
        # other by anything comparing/sorting on them.
        _ensure_column(conn, "jobs", "salary_period", "salary_period TEXT")

        # Migration 014: precomputed analytics summaries, refreshed once per
        # ingestion pipeline run (src/analytics/precomputed_summaries.py) -
        # replaces two on-demand queries that became too expensive once
        # reachable by anonymous traffic. Also a covering index on skills
        # that speeds up the periodic recompute itself (~29% faster,
        # verified empirically) even though it no longer runs per-request.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_combinations_summary (
                skill_a TEXT NOT NULL,
                skill_b TEXT NOT NULL,
                co_count INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS top_titles_summary (
                title TEXT NOT NULL,
                count INTEGER NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_skills_job_normalized ON skills(job_id, normalized_skill)"
        )


def _extract_sheet_id(value: str | None) -> str:
    """Extract spreadsheet ID from either a raw ID or a Google Sheets URL."""
    if not value:
        return ""

    text = value.strip()
    if "/d/" in text:
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", text)
        if match:
            return match.group(1)
    return text


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    """Add a column if it does not exist."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
        logger.info("[db] Added column %s.%s", table_name, column_name)


def _ensure_warehouse_schema(conn: sqlite3.Connection) -> None:
    """
    Create the schema scripts/warehouse_rollout.py classifies jobs into, and
    (re)seed the "markets" ISCO-taxonomy lookup table from config/job_markets.py.

    Called on a shadow copy of the live database (see build_shadow()), never
    on the live DB directly — the taxonomy classification is only meant to
    take effect via an explicit, checked promotion.
    """
    from config.job_markets import JOB_MARKETS

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS markets (
            market_id  TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            parent_id  TEXT,
            isco       TEXT,
            keywords   TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS job_market_assignments (
            job_id          INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            market_id       TEXT NOT NULL,
            assignment_type TEXT NOT NULL,   -- 'primary' | 'tag'
            confidence      REAL,
            method          TEXT,
            evidence_json   TEXT,
            assigned_at     TEXT NOT NULL,
            PRIMARY KEY (job_id, market_id, assignment_type)
        );
        CREATE INDEX IF NOT EXISTS idx_job_market_assignments_job    ON job_market_assignments(job_id);
        CREATE INDEX IF NOT EXISTS idx_job_market_assignments_market ON job_market_assignments(market_id);

        CREATE TABLE IF NOT EXISTS source_records (
            source_record_pk  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id         TEXT NOT NULL,
            source_record_id  TEXT NOT NULL,
            source_url        TEXT,
            payload_hash      TEXT,
            first_seen_at     TEXT NOT NULL,
            last_seen_at      TEXT NOT NULL,
            listing_status    TEXT NOT NULL DEFAULT 'unverified',
            UNIQUE(source_id, source_record_id)
        );

        CREATE TABLE IF NOT EXISTS job_source_links (
            job_id            INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            source_record_pk  INTEGER NOT NULL REFERENCES source_records(source_record_pk) ON DELETE CASCADE,
            linked_at         TEXT NOT NULL,
            match_method      TEXT NOT NULL,
            match_confidence  REAL,
            PRIMARY KEY (job_id, source_record_pk)
        );
        CREATE INDEX IF NOT EXISTS idx_job_source_links_job ON job_source_links(job_id);

        CREATE TABLE IF NOT EXISTS enrichment_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id         INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            field_name     TEXT NOT NULL,
            old_value      TEXT,
            new_value      TEXT,
            confidence     REAL,
            method         TEXT,
            evidence_json  TEXT,
            applied        INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_enrichment_events_job ON enrichment_events(job_id);
        """
    )

    _ensure_column(conn, "jobs", "classification_confidence", "classification_confidence REAL")
    _ensure_column(conn, "jobs", "classification_method", "classification_method TEXT")
    _ensure_column(conn, "jobs", "status_reason", "status_reason TEXT")
    _ensure_column(conn, "jobs", "last_verified_at", "last_verified_at TEXT")

    for market in JOB_MARKETS:
        conn.execute(
            """INSERT OR REPLACE INTO markets (market_id, name, parent_id, isco, keywords)
               VALUES (?, ?, ?, ?, ?)""",
            (
                market["market_id"], market["name"], market["parent_id"],
                market["isco"], json.dumps(market["keywords"]),
            ),
        )


def _ensure_dynamic_sheet_targets(conn: sqlite3.Connection) -> None:
    """Create dynamic spreadsheet target tables and backfill default mappings."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sheets_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            private_spreadsheet_id TEXT NOT NULL,
            private_spreadsheet_url TEXT,
            published_spreadsheet_id TEXT,
            published_spreadsheet_url TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sheets_target_countries (
            target_id INTEGER NOT NULL REFERENCES sheets_targets(id) ON DELETE CASCADE,
            country TEXT NOT NULL,
            doc_key TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (target_id, country)
        );

        CREATE INDEX IF NOT EXISTS idx_target_countries_country
            ON sheets_target_countries(country);
        """
    )

    _ensure_column(conn, "sheets_staging", "assigned_target_id", "assigned_target_id INTEGER REFERENCES sheets_targets(id)")
    _ensure_column(conn, "sheets_staging", "override_target_id", "override_target_id INTEGER REFERENCES sheets_targets(id)")
    _ensure_column(conn, "sheets_staging", "predicted_country", "predicted_country TEXT")
    _ensure_column(conn, "sheets_staging", "prediction_confidence", "prediction_confidence REAL")
    _ensure_column(conn, "sheets_staging", "prediction_votes_json", "prediction_votes_json TEXT")
    _ensure_column(conn, "sheets_staging", "review_status", "review_status TEXT DEFAULT 'pending_review'")
    _ensure_column(conn, "sheets_staging", "review_notes", "review_notes TEXT")
    _ensure_column(conn, "sheets_staging", "reviewed_by", "reviewed_by TEXT")
    _ensure_column(conn, "sheets_staging", "reviewed_at", "reviewed_at TEXT")

    defaults = [
        ("Canada", "Canada", _extract_sheet_id(SHEETS_CANADA_ID), SHEETS_CANADA_PUBLISHED_ID, "ca"),
        ("United Kingdom", "United Kingdom", _extract_sheet_id(SHEETS_UK_ID), SHEETS_UK_PUBLISHED_ID, "uk"),
        ("United States", "United States", _extract_sheet_id(SHEETS_US_ID), SHEETS_US_PUBLISHED_ID, "us"),
    ]

    now = datetime.utcnow().isoformat()
    for target_name, country, private_id, published_id, doc_key in defaults:
        if not private_id:
            continue

        conn.execute(
            """
            INSERT OR IGNORE INTO sheets_targets
            (name, private_spreadsheet_id, published_spreadsheet_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (target_name, private_id, published_id or None, now, now),
        )

        target = conn.execute(
            "SELECT id FROM sheets_targets WHERE name = ?",
            (target_name,),
        ).fetchone()
        if not target:
            continue

        conn.execute(
            """
            INSERT OR IGNORE INTO sheets_target_countries
            (target_id, country, doc_key, is_primary, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (target["id"], country, doc_key, now),
        )

    conn.execute(
        """
        UPDATE sheets_staging
        SET assigned_target_id = (
            SELECT stc.target_id
            FROM sheets_target_countries stc
            JOIN sheets_targets st ON st.id = stc.target_id
            WHERE stc.country = sheets_staging.assigned_sheet
              AND st.is_active = 1
            ORDER BY stc.is_primary DESC, st.id ASC
            LIMIT 1
        )
        WHERE assigned_target_id IS NULL
        """
    )


# ─── Job helpers ──────────────────────────────────────────────────────────────

def upsert_job(job: JobNormalized) -> tuple[Optional[int], str]:
    """
    Insert a job or handle dedupe with multi-location support.

    Returns:
        (job_id, status) where status ∈ {"inserted", "url_dup", "canonical_dup", "location_added"}
        
    Logic:
        1. Check url_hash (exact same posting) → update last_seen_at
        2. Check canonical_hash (same job, possibly different location):
           a. If location already exists → update last_seen_at
           b. If new location → add to job_locations, update location_count
        3. Fresh insert → create both jobs and job_locations entries
    """
    now = _now()
    conn = get_connection()
    try:
        with conn:
            # 1. Check url_hash (fastest - exact same URL)
            row = conn.execute(
                "SELECT job_id, company FROM jobs WHERE url_hash = ?", (job.url_hash,)
            ).fetchone()
            if row:
                job_id = row["job_id"]

                # Self-heal: some sources (e.g. Himalayas) occasionally stamp
                # a broken placeholder for company at collection time. Same
                # URL means same job, so if a later re-crawl carries a real
                # company for it, fix the stored value instead of leaving it
                # broken forever - unlike canonical_hash, url_hash doesn't
                # change when company does, so this reliably finds the row.
                stored_company = (row["company"] or "").strip()
                if stored_company.lower() in ("", "name") and job.company and job.company.strip().lower() != "name":
                    conn.execute(
                        "UPDATE jobs SET company = ? WHERE job_id = ?", (job.company, job_id)
                    )
                    logger.info("[db] Upgraded placeholder company → %s (job_id=%s)", job.company, job_id)

                conn.execute(
                    "UPDATE jobs SET last_seen_at = ? WHERE url_hash = ?",
                    (now, job.url_hash),
                )
                # Also update last_seen_at in job_locations
                conn.execute(
                    "UPDATE job_locations SET last_seen_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
                return job_id, "url_dup"

            # 2. Check canonical_hash (same job, different URL or location)
            row = conn.execute(
                "SELECT job_id, job_group_id, url FROM jobs WHERE canonical_hash = ?",
                (job.canonical_hash,),
            ).fetchone()
            if row:
                job_id = row["job_id"]
                job_group_id = row["job_group_id"]

                # Self-heal: some collectors fall back to a synthetic
                # "source://hash" URL when a real one isn't available at
                # collection time. If a later re-crawl of the same job turns up
                # a real http(s) URL, upgrade the stored placeholder instead of
                # leaving it broken forever (canonical dedup never otherwise
                # touches url/url_hash again).
                stored_url = row["url"] or ""
                if (
                    stored_url
                    and not stored_url.startswith(("http://", "https://"))
                    and job.url.startswith(("http://", "https://"))
                ):
                    conn.execute(
                        "UPDATE jobs SET url = ?, url_hash = ? WHERE job_id = ?",
                        (job.url, job.url_hash, job_id),
                    )
                    logger.info(
                        "[db] Upgraded placeholder URL → %s (job_id=%s)", job.url, job_id,
                    )

                # Determine locations to check/insert
                locations_to_process = []
                if job.all_locations and len(job.all_locations) > 0:
                    # Process all locations from GitHub sources
                    # Deduplicate to avoid processing same location twice
                    locations_to_process = list(dict.fromkeys(job.all_locations))  # Preserves order
                else:
                    # Process single primary location
                    locations_to_process = [job.location] if job.location else []
                
                new_locations_added = 0
                all_locations_exist = True
                
                for loc in locations_to_process:
                    # Check if this specific location already exists
                    location_row = conn.execute(
                        """SELECT id FROM job_locations 
                           WHERE job_group_id = ? AND location = ?""",
                        (job_group_id, loc),
                    ).fetchone()
                    
                    if location_row:
                        # Location already exists, update last_seen_at
                        conn.execute(
                            """UPDATE job_locations SET last_seen_at = ? 
                               WHERE job_group_id = ? AND location = ?""",
                            (now, job_group_id, loc),
                        )
                    else:
                        # New location for existing job
                        all_locations_exist = False
                        new_locations_added += 1
                        
                        # Infer country for this location if needed
                        loc_country = job.country if job.country else ""
                        
                        # Use INSERT OR IGNORE as safety against race conditions
                        conn.execute(
                            """INSERT OR IGNORE INTO job_locations (
                                job_id, job_group_id, location, country, remote_type,
                                salary_min, salary_max, currency,
                                first_seen_at, last_seen_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                job_id, job_group_id, loc, loc_country, job.remote_type,
                                job.salary_min, job.salary_max, job.currency,
                                now, now,
                            ),
                        )
                
                # Update job last_seen_at and location_count
                conn.execute(
                    """UPDATE jobs SET location_count = (
                        SELECT COUNT(DISTINCT location) FROM job_locations 
                        WHERE job_group_id = ?
                    ), last_seen_at = ? WHERE job_id = ?""",
                    (job_group_id, now, job_id),
                )
                
                if new_locations_added > 0:
                    total_locations = conn.execute(
                        "SELECT location_count FROM jobs WHERE job_id = ?", (job_id,)
                    ).fetchone()[0]
                    logger.debug(
                        "[db] Added %d new location%s for job_group %s (total: %d)",
                        new_locations_added,
                        "s" if new_locations_added != 1 else "",
                        job_group_id[:8],
                        total_locations
                    )
                    return job_id, "location_added"
                else:
                    return job_id, "canonical_dup"

            # 3. Fresh insert - completely new job
            # Determine locations to insert
            locations_to_insert = []
            if job.all_locations and len(job.all_locations) > 0:
                # Use all locations from GitHub sources
                # Deduplicate to avoid UNIQUE constraint violations
                locations_to_insert = list(dict.fromkeys(job.all_locations))  # Preserves order
            else:
                # Use single primary location
                locations_to_insert = [job.location] if job.location else []
            
            location_count = len(locations_to_insert) if locations_to_insert else 1
            
            # Calculate week_id from posted_date (or current date as fallback)
            week_id = _get_week_id(job.posted_date)
            
            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    market_id, source_name, url,
                    url_hash, canonical_hash, description_hash, job_group_id,
                    title, normalized_title, normalization_confidence, company, country, location, remote_type,
                    posted_date, salary_min, salary_max, currency, salary_period,
                    raw_description, newspaper, ad_image_url, apply_url, location_count, week_id,
                    first_seen_at, last_seen_at, ingested_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    job.market_id, job.source_name, job.url,
                    job.url_hash, job.canonical_hash, job.description_hash, job.job_group_id,
                    job.title, job.normalized_title, job.normalization_confidence, job.company, job.country, job.location, job.remote_type,
                    job.posted_date.isoformat() if job.posted_date else None,
                    job.salary_min, job.salary_max, job.currency, job.salary_period,
                    job.description_text, job.newspaper, job.ad_image_url, job.apply_url,
                    location_count,
                    week_id,
                    now, now, now,
                ),
            )
            job_id = cursor.lastrowid
            
            # Insert all locations into job_locations
            for loc in locations_to_insert:
                # Infer country for each location if not already set
                loc_country = job.country if job.country else ""
                
                # Use INSERT OR IGNORE to handle any edge case duplicates
                conn.execute(
                    """INSERT OR IGNORE INTO job_locations (
                        job_id, job_group_id, location, country, remote_type,
                        salary_min, salary_max, currency,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        job_id, job.job_group_id, loc, loc_country, job.remote_type,
                        job.salary_min, job.salary_max, job.currency,
                        now, now,
                    ),
                )
            
            logger.debug(
                "[db] Inserted new job: %s at %s (%d location%s)",
                job.title[:50], job.company, location_count, "s" if location_count != 1 else ""
            )
            
            return job_id, "inserted"
    finally:
        conn.close()


def insert_skills(signals: list[SkillSignal]) -> int:
    """Bulk insert SkillSignal objects. Returns number of rows inserted."""
    if not signals:
        return 0
    conn = get_connection()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO skills
                    (job_id, market_id, raw_detected_skill,
                     normalized_skill, category, confidence_score, method)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.job_id, s.market_id, s.raw_detected_skill,
                        s.normalized_skill, s.category,
                        s.confidence_score, s.extraction_method,
                    )
                    for s in signals
                ],
            )
        return len(signals)
    finally:
        conn.close()


def upsert_weekly_metric(metric: WeeklyMetric) -> None:
    """
    Insert or replace a weekly_metrics row (idempotent).

    Writes to BOTH serving-slot files, not just whichever is currently
    "Serving". This is called from the weekly/report-only pipeline mode - a
    separate, far less frequent process than the ingest-only cycle that
    flips the Serving pointer every 12h. A single-file write here would
    reliably get destroyed: the next ingest-only rotation's
    _refresh_demoted_file() overwrites whichever file just got demoted with
    a copy of the new Serving file, and there are ~14 such rotations between
    one weekly run and the next - so no matter which file the write landed
    on, some later rotation was guaranteed to clobber it before the next
    weekly run could refresh it. Confirmed happening in production: the
    weekly timer ran successfully days ago, but weekly_metrics was found
    completely empty on the live Serving file. Writing to both files means
    whichever one rotation promotes to "Serving" already has this row.
    """
    _upsert_weekly_metric_on_current_connection(metric)
    with use_free_connection():
        _upsert_weekly_metric_on_current_connection(metric)


def _upsert_weekly_metric_on_current_connection(metric: WeeklyMetric) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO weekly_metrics (
                    market_id, week_start_date, week_number,
                    skill_name, category,
                    frequency, growth_percentage, absolute_delta, mover_score,
                    emerging_flag, declining_flag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id, week_start_date, skill_name)
                DO UPDATE SET
                    frequency         = excluded.frequency,
                    growth_percentage = excluded.growth_percentage,
                    absolute_delta    = excluded.absolute_delta,
                    mover_score       = excluded.mover_score,
                    emerging_flag     = excluded.emerging_flag,
                    declining_flag    = excluded.declining_flag
                """,
                (
                    metric.market_id,
                    metric.week_start_date.isoformat(),
                    metric.week_number,
                    metric.skill_name,
                    metric.category,
                    metric.frequency,
                    metric.growth_percentage,
                    metric.absolute_delta,
                    metric.mover_score,
                    int(metric.emerging_flag),
                    int(metric.declining_flag),
                ),
            )
    finally:
        conn.close()


# ─── Query helpers ────────────────────────────────────────────────────────────

def get_jobs_for_week(market_id: str, week_start: str, week_end: str) -> list[sqlite3.Row]:
    """
    Fetch all jobs for a market with posted_date within [week_start, week_end].
    Now uses week_id for proper posted-date grouping.
    week_start / week_end: ISO date strings "YYYY-MM-DD".
    """
    # Convert week_start date to week_id format (YYYY-WW)
    from datetime import datetime
    week_dt = datetime.fromisoformat(week_start)
    iso_year, iso_week, _ = week_dt.isocalendar()
    week_id = f"{iso_year}-{iso_week:02d}"
    
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT * FROM jobs
            WHERE market_id = ?
              AND week_id = ?
            """,
            (market_id, week_id),
        ).fetchall()
    finally:
        conn.close()


def get_skill_frequencies(market_id: str, week_start: str, week_end: str) -> list[sqlite3.Row]:
    """
    Aggregate skill frequencies from the skills table for jobs with posted_date
    in the given week window. Now uses week_id for proper date-based grouping.
    Returns rows: (normalized_skill, category, frequency)
    """
    # Convert week_start date to week_id format (YYYY-WW)
    from datetime import datetime
    week_dt = datetime.fromisoformat(week_start)
    iso_year, iso_week, _ = week_dt.isocalendar()
    week_id = f"{iso_year}-{iso_week:02d}"
    
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT s.normalized_skill, s.category, COUNT(*) AS frequency
            FROM skills s
            JOIN jobs j ON j.job_id = s.job_id
            WHERE j.market_id = ?
              AND j.week_id = ?
            GROUP BY s.normalized_skill, s.category
            ORDER BY frequency DESC
            """,
            (market_id, week_id),
        ).fetchall()
    finally:
        conn.close()


def get_prior_skill_frequency(
    market_id: str, skill_name: str, prior_week_start: str, prior_week_end: str
) -> int:
    """Return frequency of a skill in a prior week window (0 if not found).
    Now uses week_id for proper date-based comparison."""
    # Convert prior_week_start date to week_id format (YYYY-WW)
    from datetime import datetime
    week_dt = datetime.fromisoformat(prior_week_start)
    iso_year, iso_week, _ = week_dt.isocalendar()
    prior_week_id = f"{iso_year}-{iso_week:02d}"
    
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS freq
            FROM skills s
            JOIN jobs j ON j.job_id = s.job_id
            WHERE j.market_id = ?
              AND s.normalized_skill = ?
              AND j.week_id = ?
            """,
            (market_id, skill_name, prior_week_id),
        ).fetchone()
        return row["freq"] if row else 0
    finally:
        conn.close()


def get_weekly_metrics(market_id: str, week_start_date: str) -> list[sqlite3.Row]:
    """Fetch all metric rows for a market + week, ordered by frequency desc."""
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT * FROM weekly_metrics
            WHERE market_id = ? AND week_start_date = ?
            ORDER BY frequency DESC
            """,
            (market_id, week_start_date),
        ).fetchall()
    finally:
        conn.close()


def get_remote_ratio(market_id: str, week_start: str, week_end: str) -> float:
    """Return fraction of jobs this week that are remote (0.0–1.0).
    Now uses week_id for proper posted-date grouping."""
    # Convert week_start date to week_id format (YYYY-WW)
    from datetime import datetime
    week_dt = datetime.fromisoformat(week_start)
    iso_year, iso_week, _ = week_dt.isocalendar()
    week_id = f"{iso_year}-{iso_week:02d}"
    
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN remote_type = 'remote' THEN 1 ELSE 0 END) AS remote_count
            FROM jobs
            WHERE market_id = ? AND week_id = ?
            """,
            (market_id, week_id),
        ).fetchone()
        if not row or row["total"] == 0:
            return 0.0
        return round((row["remote_count"] or 0) / row["total"], 4)
    finally:
        conn.close()


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_week_id(posted_date: Optional[datetime] = None) -> str:
    """
    Calculate week_id (YYYY-WW format) from posted_date.
    Falls back to current date if posted_date is None.
    
    Args:
        posted_date: The date to calculate week_id from
    
    Returns:
        Week ID like "2026-09" or "unknown" on error
    """
    try:
        if posted_date is None:
            dt = datetime.now(timezone.utc)
        else:
            dt = posted_date
        
        # ISO week date: year and week number
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-{iso_week:02d}"
    except Exception:
        return "unknown"
