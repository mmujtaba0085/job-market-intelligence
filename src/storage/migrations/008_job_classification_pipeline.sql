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
