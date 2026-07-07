# Click Tracking System - Complete Integration Guide

## Quick Start (10 minutes)

### Step 1: Deploy Apps Script (5 min)

1. **Open Tracker spreadsheet:**
   https://docs.google.com/spreadsheets/d/1_T9MOTutM_ZJCSkHLmns2Tc5SDVabE25MviGDESaxn8/edit

2. **Extensions > Apps Script**

3. **Copy code:**
   - Open `google_apps_script/Code.gs`
   - Select all, copy
   - Paste into Apps Script editor
   - Save (Ctrl+S)

4. **Set secret token:**
   - Settings (⚙️) > Script Properties
   - Add property:
     - Key: `TRACKER_TOKEN`
     - Value: (generate random 32+ char string, e.g. `openssl rand -hex 32`)
   - Save

5. **Deploy:**
   - Deploy > New deployment
   - Type: Web app
   - Execute as: Me
   - Who has access: Anyone
   - Deploy
   - Copy the URL (ends with `/exec`)

### Step 2: Configure Python (2 min)

Add to your `.env` file:

```bash
TRACKER_SPREADSHEET_ID=1_T9MOTutM_ZJCSkHLmns2Tc5SDVabE25MviGDESaxn8
TRACKER_DEPLOYMENT_BASE_URL=<PASTE_DEPLOYMENT_URL_HERE>
TRACKER_TOKEN=<PASTE_SAME_TOKEN_HERE>
```

⚠️ **CRITICAL:** Token must match exactly between Apps Script and .env

### Step 3: Create Tracker Tabs (2 min)

In the Tracker spreadsheet, create these tabs:

**Tab 1: "Docs"**
```
doc_key | spreadsheet_id                                    | country_name
--------|---------------------------------------------------|----------------
ca      | 1ZaDLm-ffJ62-WojQtRrzk4YRqgDC65vbegJNZO8tBEw   | Canada
uk      | 1YeM_Fqvqm7qc2DvunsHuCt27wpv3sgQgEdaPCg41TrY   | United Kingdom
us      | 1GEzzNMEG0sjAyFlmiXZY7F_62n2Rxmvb09AQopw8R90   | United States
```

**Tab 2: "Clicks"**
```
ts | doc_key | country | tab_name | link_id | apply_url | user_agent
[empty rows - will be auto-filled]
```

**Tab 3: "CountryTotals"**
```
country | total_clicks | last_updated
[empty rows - will be auto-filled]
```

📝 **Note:** Directory tab will be auto-created by Python

### Step 4: Test (1 min)

```bash
# Run the pipeline
python -m src.orchestrator --mode weekly
```

Check logs for:
```
[tracker_directory] Successfully exported 455 jobs to Directory tab
[orchestrator]   Canada: 16 jobs
[orchestrator]   United Kingdom: 58 jobs
[orchestrator]   United States: 381 jobs
```

### Step 5: Verify (Optional)

1. Open Tracker spreadsheet
2. Check Directory tab exists with data
3. Open Canada spreadsheet
4. Find a job row
5. Click the tracking_url
6. You should:
   - Redirect to job posting ✓
   - See clicks column increment ✓
   - See new row in Tracker->Clicks tab ✓
   - See CountryTotals update ✓

### Common Issues

**"TRACKER_TOKEN not set"** — Forgot to add token to Apps Script Script Properties. Settings > Script Properties > Add `TRACKER_TOKEN`.

**"Unauthorized" when clicking link** — Token mismatch between `.env` and Apps Script. Copy token from Apps Script to `.env` (exact match, case-sensitive).

**Directory tab empty** — Tracker config missing in `.env`. Add all 3 `TRACKER_*` variables.

**"Configuration Error: Docs tab not found"** — Missing Docs tab in Tracker spreadsheet. Create Docs tab with headers and country rows.

For the full system design, setup, testing, and troubleshooting reference, continue reading below.

## Overview

This system implements secure, centralized click tracking across 3 separate Google Sheets (Canada, UK, US) using a Google Apps Script web app and Python integration.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────────┐
│                         USER CLICKS LINK                        │
│                   (in Canada/UK/US spreadsheet)                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │  Google Apps Script Web App            │
        │  (deployed from Tracker spreadsheet)   │
        │                                        │
        │  1. Validates token                    │
        │  2. Finds job row in target sheet      │
        │  3. Increments click counter           │
        │  4. Logs to Tracker->Clicks tab        │
        │  5. Updates Tracker->CountryTotals     │
        │  6. Redirects to job posting           │
        └────────────────────────────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  Job Posting Website │
                  └──────────────────────┘
```

## Components

### 1. Tracker Spreadsheet
**ID:** `1_T9MOTutM_ZJCSkHLmns2Tc5SDVabE25MviGDESaxn8`

**Required Tabs:**

#### Docs Tab
Maps country codes to spreadsheet IDs:
| doc_key | spreadsheet_id | country_name |
|---------|---------------|--------------|
| ca | 1ZaDLm-ffJ62-WojQtRrzk4YRqgDC65vbegJNZO8tBEw | Canada |
| uk | 1YeM_Fqvqm7qc2DvunsHuCt27wpv3sgQgEdaPCg41TrY | United Kingdom |
| us | 1GEzzNMEG0sjAyFlmiXZY7F_62n2Rxmvb09AQopw8R90 | United States |

#### Clicks Tab
Logs every click:
| ts | doc_key | country | tab_name | link_id | apply_url | user_agent |
|----|---------|---------|----------|---------|-----------|------------|
| 2026-03-03 10:15:30 | ca | Canada | Software Engineer | job_12345 | https://... | Mozilla/5.0... |

#### CountryTotals Tab
Aggregates clicks by country:
| country | total_clicks | last_updated |
|---------|--------------|--------------|
| Canada | 147 | 2026-03-03 10:15:30 |
| United Kingdom | 89 | 2026-03-03 09:42:15 |
| United States | 312 | 2026-03-03 10:14:22 |

#### Directory Tab (auto-created by Python)
Master directory of all jobs:
| doc_key | country | tab_name | link_id | title | company | location | apply_url | tracking_url | clicks |
|---------|---------|----------|---------|-------|---------|----------|-----------|--------------|--------|
| ca | Canada | Software Engineer | job_12345 | Senior SWE | Google | Toronto | https://... | https://script.google... | 5 |

### 2. Country Spreadsheets (Canada, UK, US)

Each has:
- **Outline Tab**: Navigation/overview (not searched for clicks)
- **Category Tabs**: e.g., "Software Engineer", "Data Analyst"

**Category Tab Structure:**
| link_id | title | company | location | remote_type | posted_date | source | apply_url | tracking_url | clicks |
|---------|-------|---------|----------|-------------|-------------|--------|-----------|--------------|--------|
| job_12345 | Senior Software Engineer | Google | Toronto, ON | Hybrid | 2026-03-01 | JSearch | https://careers.google.com/... | https://script.google.com/... | 0 |

**Key Columns:**
- `link_id`: Unique identifier (format: `job_{job_id}`)
- `apply_url`: Original job posting URL
- `tracking_url`: Google Apps Script redirect URL (tracks click then redirects)
- `clicks`: Counter incremented by Apps Script on each click

### 3. Google Apps Script Web App

**Location:** See `google_apps_script/Code.gs`

**Deployment:** Bound to Tracker spreadsheet as web app

**Endpoint:** `https://script.google.com/macros/s/<DEPLOYMENT_ID>/exec`

**Parameters:**
- `doc`: Document key (ca/uk/us)
- `id`: Link ID (job_12345)
- `token`: Secret authentication token

**Example URL:**
```
https://script.google.com/macros/s/AKfycbx.../exec?doc=ca&id=job_12345&token=SECRET_TOKEN
```

**Security Features:**
- ✅ Token validation (all requests must include valid token)
- ✅ Anti-spam (10-second cooldown per link)
- ✅ Error handling (no redirect if job not found)

### 4. Python Integration

#### Module: `src/reports/tracker_directory_export.py`
Exports all jobs to Tracker->Directory tab.

**When it runs:**
- After weekly pipeline completes
- After report-only mode completes
- Automatically called by orchestrator

**What it does:**
1. Reads all staged/uploaded jobs from `sheets_staging` table
2. Generates `link_id` for each job (`job_{job_id}`)
3. Creates tracking URLs using tracker deployment URL + token
4. Writes to Tracker->Directory tab
5. Logs stats (total jobs, breakdown by country)

#### Module: `src/reports/google_sheets_export.py`
Exports jobs to Canada/UK/US spreadsheets with tracking URLs.

**What changed:**
- Column headers now include `link_id`, `tracking_url`, `clicks`
- `format_job_row()` generates tracking URLs using tracker settings
- Click counts read from `sheets_click_tracking` table

## Setup Instructions

### Step 1: Deploy Google Apps Script

1. Open Tracker spreadsheet
2. Extensions > Apps Script
3. Copy contents of `google_apps_script/Code.gs`
4. Set Script Properties:
   - Key: `TRACKER_TOKEN`
   - Value: Generate a random token (32+ characters)
   - Example: `openssl rand -hex 32`
5. Deploy as Web App:
   - Execute as: **Me**
   - Who has access: **Anyone**
   - Copy the deployment URL
6. Test: `testConfiguration()` function

**See:** `google_apps_script/DEPLOYMENT.md` for detailed instructions

### Step 2: Configure Python Environment

Add to `.env`:

```bash
# Tracker Spreadsheet Configuration
TRACKER_SPREADSHEET_ID=1_T9MOTutM_ZJCSkHLmns2Tc5SDVabE25MviGDESaxn8
TRACKER_DEPLOYMENT_BASE_URL=https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec
TRACKER_TOKEN=your_secret_token_from_step_1

# Existing Google Sheets config (unchanged)
GOOGLE_SA_JSON_PATH=config/job-market-intelligence-489015-57c9087db0cf.json
```

**CRITICAL:**
- `TRACKER_TOKEN` must match exactly what you set in Apps Script Script Properties
- `TRACKER_DEPLOYMENT_BASE_URL` should end with `/exec` (no parameters)

### Step 3: Set Up Tracker Spreadsheet Tabs

Create these tabs manually:

#### Docs Tab
```
doc_key | spreadsheet_id                                    | country_name
--------|---------------------------------------------------|----------------
ca      | 1ZaDLm-ffJ62-WojQtRrzk4YRqgDC65vbegJNZO8tBEw   | Canada
uk      | 1YeM_Fqvqm7qc2DvunsHuCt27wpv3sgQgEdaPCg41TrY   | United Kingdom
us      | 1GEzzNMEG0sjAyFlmiXZY7F_62n2Rxmvb09AQopw8R90   | United States
```

#### Clicks Tab
```
ts | doc_key | country | tab_name | link_id | apply_url | user_agent
```

#### CountryTotals Tab
```
country | total_clicks | last_updated
```

**Note:** Directory tab will be auto-created by Python

### Step 4: Run the Pipeline

```bash
# Run weekly pipeline (includes tracker export)
python -m src.orchestrator --mode weekly

# Or just regenerate reports + tracker
python -m src.orchestrator --mode report-only
```

The orchestrator will:
1. Collect jobs from sources
2. Normalize, dedupe, extract skills
3. Compute analytics
4. Export to Canada/UK/US spreadsheets (with tracking URLs)
5. Export to Tracker->Directory tab
6. Generate reports

## How It Works

### Job Upload Flow

```
1. JobPipeline
   ↓
2. sheets_staging table (pending jobs)
   ↓
3. google_sheets_export.py
   ├─→ Formats job rows with tracking URLs
   ├─→ Writes to Canada/UK/US spreadsheets
   └─→ Each row has: link_id | title | ... | tracking_url | clicks
   ↓
4. tracker_directory_export.py
   ├─→ Reads all staged jobs
   ├─→ Generates tracking URLs
   └─→ Writes to Tracker->Directory tab
```

### Click Tracking Flow

```
1. User opens Canada spreadsheet
   ↓
2. User clicks tracking_url in "Software Engineer" tab
   (https://script.google.com/...?doc=ca&id=job_12345&token=TOKEN)
   ↓
3. Google Apps Script doGet():
   ├─→ Validates token ✓
   ├─→ Checks anti-spam cache (10s cooldown) ✓
   ├─→ Looks up "ca" in Tracker->Docs tab → finds Canada sheet ID
   ├─→ Opens Canada spreadsheet
   ├─→ Searches all tabs (except Outline) for link_id = "job_12345"
   ├─→ Finds row in "Software Engineer" tab
   ├─→ Increments clicks column: 0 → 1
   ├─→ Logs click to Tracker->Clicks tab
   ├─→ Updates Tracker->CountryTotals (Canada total_clicks +1)
   └─→ Redirects to apply_url
   ↓
4. User lands on actual job posting
```

### Directory Export Flow

```
1. orchestrator.py completes weekly run
   ↓
2. Calls tracker_directory_export.export_directory()
   ↓
3. Reads sheets_staging table:
   SELECT * FROM sheets_staging WHERE status IN ('staged', 'uploaded')
   ↓
4. For each job:
   ├─→ link_id = f"job_{job_id}"
   ├─→ doc_key = COUNTRY_DOC_KEYS[country]  # Canada → ca
   ├─→ tracking_url = f"{base_url}?doc={doc_key}&id={link_id}&token={token}"
   └─→ clicks = COUNT(*) FROM sheets_click_tracking WHERE job_id = ...
   ↓
5. Clears Tracker->Directory tab
   ↓
6. Writes all rows in batch
   ↓
7. Logs stats:
   [tracker_directory] Successfully exported 455 jobs
   [tracker_directory]   Canada: 16 jobs
   [tracker_directory]   United Kingdom: 58 jobs
   [tracker_directory]   United States: 381 jobs
```

## Testing

### Test Apps Script

```javascript
// In Apps Script editor
// Run: testConfiguration()

// Expected output:
✅ TRACKER_TOKEN is set
Token length: 64 characters
✅ Tab "Docs" exists
✅ Tab "Clicks" exists
✅ Tab "CountryTotals" exists
```

### Test Tracking URL

1. Get a test link_id from your Canada spreadsheet
2. Build URL:
   ```
   https://script.google.com/macros/s/YOUR_ID/exec?doc=ca&id=job_12345&token=YOUR_TOKEN
   ```
3. Visit in browser
4. Expected:
   - Redirects to job posting ✓
   - Clicks column in Canada sheet increments ✓
   - New row appears in Tracker->Clicks tab ✓
   - CountryTotals updates ✓

### Test Python Export

```bash
# Run tracker export manually
python src/reports/tracker_directory_export.py

# Expected output:
[tracker_directory] Starting directory export
[tracker_directory] Found 455 jobs to export
[tracker_directory] Created tab with ID: 123456
[tracker_directory] Wrote 456 rows (4560 cells)
[tracker_directory] Successfully exported 455 jobs to Directory tab
Export complete: {'total_jobs': 455, 'countries': {'Canada': 16, 'United Kingdom': 58, 'United States': 381}}
```

## Monitoring

### Apps Script Logs

View execution logs:
1. Apps Script editor
2. Executions (left sidebar)
3. Click on any execution to see logs

Example:
```
[sheets_track] Click tracked: ca → Software Engineer by 192.168....
[sheets_track_job] Job click tracked: job_id=12345, ca/Software Engineer by 192.168....
```

### Python Logs

```bash
# Check orchestrator logs
tail -f logs/2026-09/ai_ml_global_<run_id>.log | grep tracker
```

Example:
```
[orchestrator] Exporting to Tracker Directory spreadsheet
[tracker_directory] Starting directory export
[tracker_directory] Found 455 jobs to export
[tracker_directory] Successfully exported 455 jobs to Directory tab
[orchestrator] Tracker export complete: 455 jobs exported
[orchestrator]   Canada: 16 jobs
[orchestrator]   United Kingdom: 58 jobs
[orchestrator]   United States: 381 jobs
```

### Analytics

Check Tracker spreadsheet tabs:
- **Clicks**: See individual click events
- **CountryTotals**: See aggregated stats per country
- **Directory**: See all jobs with current click counts

## Troubleshooting

### "Unauthorized" error when clicking link
- Token mismatch between `.env` and Apps Script Script Properties
- Check both values are exactly identical
- Token is case-sensitive

### "Not Found" error
- link_id doesn't exist in any tab of the target spreadsheet
- Check the Canada/UK/US spreadsheet has the job row
- Verify link_id matches (format: `job_12345`)

### Clicks not incrementing
- Apps Script doesn't have permission to access target spreadsheet
- Share Canada/UK/US spreadsheets with the same Google account running the script
- Check that the category tab has a `clicks` column

### Directory tab empty
- Tracker export might have failed
- Check logs for errors
- Verify `TRACKER_SPREADSHEET_ID`, `TRACKER_DEPLOYMENT_BASE_URL`, `TRACKER_TOKEN` are all set in `.env`
- Run manually: `python src/reports/tracker_directory_export.py`

### "Configuration Error: Docs tab not found"
- Create the Docs tab in Tracker spreadsheet
- Add headers: `doc_key | spreadsheet_id | country_name`
- Add rows for each country

## Security Considerations

### Token Security
- Token is stored in:
  - Apps Script Script Properties (encrypted by Google)
  - Python `.env` file (gitignored)
- Never commit `.env` to git
- Rotate token if compromised

### Access Control
- Apps Script runs as YOU
- Web app access: "Anyone" (required for public tracking)
- Only users with valid token can log clicks
- Invalid tokens return "Unauthorized"

### Anti-Spam
- 10-second cooldown per unique link
- Prevents accidental double-clicks
- Prevents bot abuse

### URL Encoding
- All parameters properly URL-encoded
- Prevents injection attacks

## Maintenance

### Updating Apps Script

After code changes:
1. Deploy > Manage deployments
2. Edit (pencil icon)
3. Version: "New version"
4. Deploy
5. URL stays the same - no `.env` update needed

### Adding New Countries

1. Add row to Tracker->Docs tab
2. Update `COUNTRY_DOC_KEYS` in:
   - `src/reports/google_sheets_export.py`
   - `src/reports/tracker_directory_export.py`
3. Update `config/settings.py` (add new sheet ID constant)
4. Update `sheet_mapping` in `upload_from_staging()`

### Archiving Old Clicks

Clicks tab can grow large. To archive:
```javascript
// In Apps Script
function archiveOldClicks() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var clicksSheet = ss.getSheetByName('Clicks');
  var data = clicksSheet.getDataRange().getValues();
  
  // Keep only last 30 days
  var cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 30);
  
  // Archive logic here...
}
```

## File Reference

```
google_apps_script/
  ├── Code.gs                    Google Apps Script web app
  └── DEPLOYMENT.md              Deployment instructions

src/reports/
  ├── google_sheets_export.py    Exports to Canada/UK/US sheets
  └── tracker_directory_export.py Exports to Tracker Directory

config/
  └── settings.py                Configuration (TRACKER_* vars)

src/
  └── orchestrator.py            Main pipeline (calls exports)

.env                              Environment variables (TRACKER_TOKEN)
```

## Support

For issues:
1. Check logs (Apps Script Executions + Python logs)
2. Verify all tabs exist in Tracker spreadsheet
3. Test tracking URL manually
4. Check token matches in both places
5. Review this doc's Troubleshooting section
