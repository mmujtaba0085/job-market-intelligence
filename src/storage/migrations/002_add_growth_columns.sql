-- migrations/002_add_growth_columns.sql
-- Add enhanced growth metrics columns to weekly_metrics table
-- Adds: absolute_delta (int) and mover_score (real)
-- Idempotent: Only adds columns if they don't already exist

-- Check and add absolute_delta column
-- SQLite doesn't support IF NOT EXISTS for ALTER TABLE, so we use a workaround
-- This will fail silently if the column already exists (we handle in Python migration runner)

-- Add absolute delta column (this_week - prior_week frequency)
-- ALTER TABLE weekly_metrics ADD COLUMN absolute_delta INTEGER DEFAULT 0;

-- Add mover score column (delta * log1p(frequency) to penalize low-base spikes)
-- ALTER TABLE weekly_metrics ADD COLUMN mover_score REAL DEFAULT 0.0;

-- NOTE: This migration is handled in Python code to make it idempotent
