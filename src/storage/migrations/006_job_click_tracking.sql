-- migrations/006_job_click_tracking.sql
-- Add job-level click tracking for apply URLs

-- Add job_id column to sheets_click_tracking to track individual job posting clicks
ALTER TABLE sheets_click_tracking ADD COLUMN job_id INTEGER REFERENCES jobs(job_id) ON DELETE CASCADE;

-- Add click_type to distinguish between tab navigation vs job posting clicks
ALTER TABLE sheets_click_tracking ADD COLUMN click_type TEXT DEFAULT 'tab_navigation';
-- Values: 'tab_navigation' (clicking Overview → tab), 'job_posting' (clicking job apply URL)

-- Index for efficient job click queries
CREATE INDEX IF NOT EXISTS idx_clicks_job_id ON sheets_click_tracking(job_id);
CREATE INDEX IF NOT EXISTS idx_clicks_type ON sheets_click_tracking(click_type);
