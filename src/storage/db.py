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

import logging
import re
import sqlite3
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

# ─── Migration path ───────────────────────────────────────────────────────────
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # better concurrent read perf
    return conn


def run_migrations() -> None:
    """Idempotently apply all SQL migration files in order."""
    conn = get_connection()
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
                
            # Skip migrations 004, 005, 006 - handled conditionally after the loop
            elif mf.name in ["004_add_normalized_title.sql", "005_add_normalization_confidence.sql", "006_job_click_tracking.sql"]:
                pass  # These migrations are handled conditionally after the loop
                
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

        # Migration 007: Dynamic spreadsheet targets + staging review fields
        _ensure_dynamic_sheet_targets(conn)
    
    conn.close()


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
                "SELECT job_id FROM jobs WHERE url_hash = ?", (job.url_hash,)
            ).fetchone()
            if row:
                job_id = row["job_id"]
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
                "SELECT job_id, job_group_id FROM jobs WHERE canonical_hash = ?",
                (job.canonical_hash,),
            ).fetchone()
            if row:
                job_id = row["job_id"]
                job_group_id = row["job_group_id"]
                
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
                    posted_date, salary_min, salary_max, currency,
                    raw_description, location_count, week_id,
                    first_seen_at, last_seen_at, ingested_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    job.market_id, job.source_name, job.url,
                    job.url_hash, job.canonical_hash, job.description_hash, job.job_group_id,
                    job.title, job.normalized_title, job.normalization_confidence, job.company, job.country, job.location, job.remote_type,
                    job.posted_date.isoformat() if job.posted_date else None,
                    job.salary_min, job.salary_max, job.currency,
                    job.description_text,
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
    """Insert or replace a weekly_metrics row (idempotent)."""
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
        return round(row["remote_count"] / row["total"], 4)
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
