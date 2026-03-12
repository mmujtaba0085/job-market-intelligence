# Google Sheets Integration Guide

## Overview

The Job Market Intelligence system now includes a Google Sheets integration that automatically exports jobs to separate spreadsheets for each country, organized by job type in dynamic tabs.

## Features

### 📊 **Three Separate Spreadsheets**
- **Canada**: [View Sheet](https://docs.google.com/spreadsheets/d/1ZaDLm-ffJ62-WojQtRrzk4YRqgDC65vbegJNZO8tBEw)
- **United Kingdom**: [View Sheet](https://docs.google.com/spreadsheets/d/1YeM_Fqvqm7qc2DvunsHuCt27wpv3sgQgEdaPCg41TrY)
- **United States**: [View Sheet](https://docs.google.com/spreadsheets/d/1GEzzNMEG0sjAyFlmiXZY7F_62n2Rxmvb09AQopw8R90)

### 🗂️ **Dynamic Tabs per Job Type**
- Each unique `normalized_title` gets its own tab (e.g., "Data Scientist", "ML Engineer", "AI Researcher")
- Unlimited tabs - automatically created as needed
- Jobs are grouped by their normalized title for easy browsing

### 📋 **Overview Tab**
Each spreadsheet has an **Overview** tab (always first):
- **Statistics Table**: Shows all job types with counts, new jobs this week, and last update time
- **Quick Links**: Navigate to any job type tab with one click
- **Click Tracking**: Every click on a Quick Link is tracked for analytics

### 🔍 **Staging & Admin Interface**
Before jobs are uploaded to Google Sheets, they go through a staging system where you can:
- Review all pending jobs
- Edit titles, companies, locations, remote types
- Reassign jobs to different tabs
- Create custom tab names
- Merge tabs together
- Exclude jobs from upload
- Bulk actions (exclude, delete, etc.)

### 📈 **Click Analytics Dashboard**
Track which job types are getting the most attention:
- Total clicks per job type
- Unique users
- Click trends over time
- User engagement metrics
- Export analytics as CSV

## Configuration

### Enable Google Sheets Integration

Add to your `.env` file or set environment variables:

```bash
SHEETS_ENABLED=true
WEB_VIEWER_URL=http://localhost:5000
```

### Service Account Setup

The service account is already configured:
- **Project**: job-market-intelligence-489015
- **Service Account**: google-service-account@job-market-intelligence-489015.iam.gserviceaccount.com
- **JSON Key**: `config/job-market-intelligence-489015-57c9087db0cf.json`

Make sure the service account has **Editor** permissions on all three spreadsheets.

## Usage

### 1. Run the Pipeline

When you run the orchestrator in **weekly mode**, it automatically populates the staging table:

```bash
python -m src.orchestrator --mode weekly
```

This will:
1. Collect jobs from all sources
2. Normalize and deduplicate
3. Extract skills
4. **Populate sheets_staging with Canada/UK/US jobs**
5. Generate analytics and reports

### 2. Review Jobs in Admin Interface

Open the web viewer:

```bash
python web_viewer.py
```

Then navigate to: **http://localhost:5000/admin/sheets_staging**

Here you can:
- **Filter** by country, tab, or status
- **Edit** any job field directly in the table (click to edit)
- **Exclude** jobs you don't want to upload
- **Create new tabs** by selecting jobs and clicking "Create New Tab"
- **Merge tabs** to combine similar job types
- **Bulk actions** to exclude/include/delete multiple jobs

### 3. Upload to Google Sheets

When ready, click the **"☁️ Upload to Google Sheets"** button.

This will:
1. Query all pending jobs from staging
2. Group by country and tab
3. Create/update tabs in each spreadsheet
4. Generate Overview tab with statistics and links
5. Apply formatting (bold headers, auto-resize columns)
6. Mark jobs as uploaded in staging

### 4. View Analytics

Navigate to: **http://localhost:5000/admin/sheets_analytics**

See:
- Most clicked job types
- Click trends over time (Chart.js visualization)
- User engagement metrics
- Export data as CSV

## Admin Interface Routes

### Main Routes:
- `/admin/sheets_staging` - Main staging admin page
- `/admin/sheets_analytics` - Click analytics dashboard

### API Endpoints:
- `POST /api/admin/sheets_staging/update` - Update single job field
- `POST /api/admin/sheets_staging/exclude` - Toggle exclude status
- `POST /api/admin/sheets_staging/delete` - Delete jobs from staging
- `POST /api/admin/sheets_staging/upload` - Trigger Google Sheets upload
- `POST /api/admin/sheets_staging/create_tab` - Create custom tab
- `POST /api/admin/sheets_staging/merge_tabs` - Merge two tabs
- `GET /sheets/track` - Click tracking redirect (used by Overview tab links)
- `GET /api/admin/sheets_analytics/export` - Export analytics as CSV

## Database Tables

### `sheets_staging`
Stores jobs pending upload with editable overrides:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `job_id` | INTEGER | Reference to jobs table |
| `override_title` | TEXT | Override job title (optional) |
| `override_normalized_title` | TEXT | Override normalized title (optional) |
| `override_company` | TEXT | Override company (optional) |
| `override_location` | TEXT | Override location (optional) |
| `override_country` | TEXT | Override country (optional) |
| `override_remote_type` | TEXT | Override remote type (optional) |
| `assigned_tab` | TEXT | Which tab this job goes to |
| `assigned_sheet` | TEXT | Which country spreadsheet |
| `status` | TEXT | 'pending' or 'uploaded' |
| `exclude_from_upload` | INTEGER | 1 to exclude, 0 to include |
| `uploaded_at` | TEXT | Timestamp of upload |
| `upload_batch_id` | TEXT | UUID of upload batch |
| `created_at` | TEXT | Created timestamp |
| `updated_at` | TEXT | Last modified timestamp |

### `sheets_click_tracking`
Tracks clicks on Overview tab Quick Links:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `country` | TEXT | Which country spreadsheet |
| `tab_name` | TEXT | Which tab was clicked |
| `spreadsheet_id` | TEXT | Google Sheets ID |
| `user_identifier` | TEXT | Session ID or IP |
| `user_agent` | TEXT | Browser info |
| `clicked_at` | TEXT | Click timestamp |
| `referrer` | TEXT | HTTP referrer |

## Workflow Diagram

```
┌─────────────────┐
│  Run Pipeline   │
│  (orchestrator) │
└────────┬────────┘
         │
         v
┌─────────────────┐
│ Populate Staging│  ← Auto-assigns Canada/UK/US jobs to tabs
│  (after ingest) │
└────────┬────────┘
         │
         v
┌─────────────────┐
│  Admin Review   │  ← Edit, exclude, reassign, merge tabs
│  (web_viewer)   │
└────────┬────────┘
         │
         v
┌─────────────────┐
│  Upload Button  │  ← Triggers upload_from_staging()
└────────┬────────┘
         │
         v
┌─────────────────┐
│ Google Sheets   │  ← Creates/updates tabs, generates Overview
│  (3 countries)  │
└────────┬────────┘
         │
         v
┌─────────────────┐
│  Users Click    │  ← Overview tab Quick Links track clicks
│   Quick Links   │
└────────┬────────┘
         │
         v
┌─────────────────┐
│   Analytics     │  ← View which job types get most clicks
│   Dashboard     │
└─────────────────┘
```

## Troubleshooting

### Jobs not appearing in staging

Check:
1. Are jobs from Canada, UK, or US? (Other countries are excluded)
2. Do jobs have a `normalized_title`? (Required for tab assignment)
3. Is `SHEETS_ENABLED=true` in your environment?
4. Check logs: `logs/<week>/ai_ml_global_<run_id>.log`

### Upload fails

Check:
1. Service account JSON file exists: `config/job-market-intelligence-489015-57c9087db0cf.json`
2. Service account has Editor permissions on all 3 spreadsheets
3. Internet connection is working
4. Google Sheets API is enabled for the project

### Admin page shows no data

Check:
1. Have you run the pipeline at least once?
2. Are there pending jobs in staging? Run: `SELECT COUNT(*) FROM sheets_staging WHERE status='pending'`
3. Check web_viewer logs for errors

### Click tracking not working

Check:
1. Is session management working? (Flask secret key set)
2. Are you using the `/sheets/track` redirect URLs from Overview tab?
3. Check `sheets_click_tracking` table: `SELECT * FROM sheets_click_tracking LIMIT 10`

## Technical Details

### Google Sheets API Limits
- **Read requests**: 100/100 seconds per user
- **Write requests**: 100/100 seconds per user
- **Batch write**: Up to 1000 rows per request

The system uses **batch writes** to stay within limits. Each tab is written in a single batch operation.

### Tab Naming
- Tab names are based on `normalized_title`
- Invalid characters are removed (Google Sheets tab name restrictions)
- Max 100 characters

### Overview Tab Generation
The Overview tab is **regenerated on every upload** with:
- Stats table (Job Type | Total Jobs | New This Week | Last Updated | Quick Link)
- Click tracking URLs: `/sheets/track?country=X&tab=Y&sheet_id=Z&gid=N`
- Formatted with bold headers, auto-sized columns

### Click Tracking Flow
1. User clicks Quick Link in Overview tab
2. Browser navigates to `/sheets/track?...` (your web viewer)
3. Web viewer records click in `sheets_click_tracking` table
4. Web viewer redirects to actual Google Sheets tab URL
5. User sees the tab in Google Sheets

## Development

### Test Authentication
```bash
python src/reports/google_sheets_export.py --test-auth
```

### Manual Upload (CLI)
```python
from src.reports.google_sheets_export import upload_from_staging
import uuid

batch_id = str(uuid.uuid4())
result = upload_from_staging(batch_id)
print(result)
```

### Check Staging Table
```python
from src.storage.db import get_db_connection

conn = get_db_connection()
cursor = conn.execute("""
    SELECT assigned_sheet, assigned_tab, COUNT(*) as count
    FROM sheets_staging
    WHERE status = 'pending'
    GROUP BY assigned_sheet, assigned_tab
""")
for row in cursor:
    print(row)
```

## Support

For issues or questions:
1. Check logs in `logs/<week>/`
2. Review database tables with `sqlite3 data/jobs.sqlite`
3. Test authentication with `python src/reports/google_sheets_export.py --test-auth`
