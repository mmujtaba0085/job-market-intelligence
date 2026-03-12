-- migrations/002_sheets_staging.sql
-- Google Sheets staging and click tracking tables

-- ─── Sheets Staging Table ────────────────────────────────────────────────────
-- Jobs pending review/approval before Google Sheets upload
CREATE TABLE IF NOT EXISTS sheets_staging (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    
    -- Editable field overrides (NULL means use original from jobs table)
    override_title TEXT,
    override_normalized_title TEXT,
    override_company TEXT,
    override_location TEXT,
    override_country TEXT,
    override_remote_type TEXT,
    
    -- Tab assignment
    assigned_tab TEXT NOT NULL,         -- Which tab in Google Sheets (normalized_title)
    assigned_sheet TEXT NOT NULL,       -- Which country spreadsheet (Canada/UK/US)
    
    -- Status tracking
    status TEXT DEFAULT 'pending',      -- pending | approved | excluded | uploaded
    exclude_from_upload INTEGER DEFAULT 0,  -- 0 or 1
    
    -- Upload tracking
    uploaded_at TEXT,                   -- ISO datetime when uploaded
    upload_batch_id TEXT,               -- Group uploads together
    
    -- Metadata
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    
    UNIQUE(job_id, assigned_sheet, assigned_tab)
);

CREATE INDEX IF NOT EXISTS idx_staging_status ON sheets_staging(status);
CREATE INDEX IF NOT EXISTS idx_staging_sheet_tab ON sheets_staging(assigned_sheet, assigned_tab);
CREATE INDEX IF NOT EXISTS idx_staging_job_id ON sheets_staging(job_id);


-- ─── Click Tracking Table ─────────────────────────────────────────────────────
-- Track Overview tab link clicks for analytics
CREATE TABLE IF NOT EXISTS sheets_click_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- What was clicked
    country TEXT NOT NULL,              -- Canada, United Kingdom, United States
    tab_name TEXT NOT NULL,             -- Software Engineer, Data Analyst, etc.
    spreadsheet_id TEXT NOT NULL,       -- Which spreadsheet
    
    -- Who clicked
    user_identifier TEXT,               -- Session ID or IP hash (for privacy)
    user_agent TEXT,                    -- Browser info
    
    -- When clicked
    clicked_at TEXT NOT NULL,           -- ISO datetime
    
    -- Referrer tracking
    referrer TEXT,                      -- Where they came from
    
    -- Metadata
    week_id TEXT                        -- Which week's data they viewed
);

CREATE INDEX IF NOT EXISTS idx_clicks_country_tab ON sheets_click_tracking(country, tab_name);
CREATE INDEX IF NOT EXISTS idx_clicks_timestamp ON sheets_click_tracking(clicked_at);
CREATE INDEX IF NOT EXISTS idx_clicks_user ON sheets_click_tracking(user_identifier);
