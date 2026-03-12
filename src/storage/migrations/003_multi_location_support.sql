-- migrations/003_multi_location_support.sql
-- Multi-location job support: Allows same job posting in multiple locations
-- to be properly deduplicated and tracked separately.
-- SQLite-compatible migration (idempotent)

-- ─── Step 1: Add job_group_id to jobs table ──────────────────────────────────
-- Note: SQLite doesn't support conditional column creation in pure SQL
-- The run_migrations() function in db.py handles idempotency via Python

-- Add job_group_id column (16-char hash prefix for grouping multi-location jobs)
-- This will be skipped if column already exists (handled by migration runner)
ALTER TABLE jobs ADD COLUMN job_group_id TEXT;

-- Add location_count column for quick reference
ALTER TABLE jobs ADD COLUMN location_count INTEGER DEFAULT 1;

-- Create index on job_group_id for fast lookups
CREATE INDEX IF NOT EXISTS idx_jobs_group ON jobs(job_group_id);


-- ─── Step 2: Create job_locations table ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    job_group_id TEXT NOT NULL,
    
    -- Location-specific fields
    location TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    remote_type TEXT NOT NULL DEFAULT 'unknown',
    
    -- Per-location salary (may differ by region)
    salary_min REAL,
    salary_max REAL,
    currency TEXT,
    
    -- Tracking when this location was first/last seen
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    
    -- Prevent duplicate location entries for same job group
    UNIQUE(job_group_id, location, country)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_job_locations_group ON job_locations(job_group_id);
CREATE INDEX IF NOT EXISTS idx_job_locations_location ON job_locations(location);
CREATE INDEX IF NOT EXISTS idx_job_locations_country ON job_locations(country);
CREATE INDEX IF NOT EXISTS idx_job_locations_job_id ON job_locations(job_id);
