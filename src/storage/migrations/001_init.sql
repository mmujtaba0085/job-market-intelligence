-- migrations/001_init.sql
-- Initial schema for the Job Market Intelligence Engine.
-- Designed for SQLite but column types are Postgres-compatible.
-- Run via: src/storage/db.py :: run_migrations()

-- ─── Jobs ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jobs (
    job_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT    NOT NULL,
    source_name     TEXT    NOT NULL,
    url             TEXT    NOT NULL,

    -- Dedupe hashes
    url_hash        TEXT    NOT NULL UNIQUE,
    canonical_hash  TEXT    NOT NULL,
    description_hash TEXT   NOT NULL,

    -- Core fields
    title           TEXT    NOT NULL,
    company         TEXT    NOT NULL DEFAULT '',
    country         TEXT    NOT NULL DEFAULT '',
    location        TEXT    NOT NULL DEFAULT '',
    remote_type     TEXT    NOT NULL DEFAULT 'unknown',  -- remote | hybrid | on-site | unknown

    -- Dates
    posted_date     TEXT,           -- ISO date string YYYY-MM-DD (nullable)

    -- Salary (nullable)
    salary_min      REAL,
    salary_max      REAL,
    currency        TEXT,

    -- Full content
    raw_description TEXT    NOT NULL DEFAULT '',

    -- Tracking timestamps
    first_seen_at   TEXT    NOT NULL,   -- ISO datetime
    last_seen_at    TEXT    NOT NULL,   -- ISO datetime
    ingested_at     TEXT    NOT NULL    -- ISO datetime
);

CREATE INDEX IF NOT EXISTS idx_jobs_market    ON jobs(market_id);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical ON jobs(canonical_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_posted    ON jobs(posted_date);


-- ─── Skills ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    market_id           TEXT    NOT NULL,

    raw_detected_skill  TEXT    NOT NULL,
    normalized_skill    TEXT    NOT NULL,
    category            TEXT    NOT NULL,

    confidence_score    REAL,           -- nullable; for future LLM extraction
    method              TEXT    NOT NULL DEFAULT 'regex_taxonomy'
);

CREATE INDEX IF NOT EXISTS idx_skills_job_id     ON skills(job_id);
CREATE INDEX IF NOT EXISTS idx_skills_normalized ON skills(normalized_skill);
CREATE INDEX IF NOT EXISTS idx_skills_market     ON skills(market_id);


-- ─── Weekly Metrics ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weekly_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT    NOT NULL,
    week_start_date     TEXT    NOT NULL,   -- ISO date YYYY-MM-DD (always a Monday)
    week_number         INTEGER NOT NULL,

    skill_name          TEXT    NOT NULL,
    category            TEXT    NOT NULL,

    frequency           INTEGER NOT NULL DEFAULT 0,
    growth_percentage   REAL    NOT NULL DEFAULT 0.0,

    emerging_flag       INTEGER NOT NULL DEFAULT 0,    -- 0/1 (SQLite has no BOOLEAN)
    declining_flag      INTEGER NOT NULL DEFAULT 0,

    UNIQUE(market_id, week_start_date, skill_name)
);

CREATE INDEX IF NOT EXISTS idx_metrics_market_week ON weekly_metrics(market_id, week_start_date);
CREATE INDEX IF NOT EXISTS idx_metrics_emerging     ON weekly_metrics(emerging_flag);
