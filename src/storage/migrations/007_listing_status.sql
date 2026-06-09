-- Add listing_status column to jobs table (default: active)
ALTER TABLE jobs ADD COLUMN listing_status TEXT NOT NULL DEFAULT 'active';
