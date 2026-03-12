# Google Apps Script Deployment Guide (v2.0)

## Overview
This directory contains the Google Apps Script code for the Job Click Tracker web app (v2.0).

**What's New in v2.0:**
- **Directory-based lookup**: Jobs are looked up in Tracker->Directory tab (not country spreadsheets)
- **Landing page with job details**: Shows job title, company, and category with a clear "View Job Posting" button
- **Privacy/Security**: Apply URLs stored ONLY in Tracker spreadsheet
- **Click preservation**: Click counts never lost across re-exports
- **User-friendly**: Works around Google Apps Script iframe restrictions by showing a clickable landing page

## Step-by-Step Deployment

### 1. Open Your Tracker Spreadsheet
Navigate to: https://docs.google.com/spreadsheets/d/1_T9MOTutM_ZJCSkHLmns2Tc5SDVabE25MviGDESaxn8/edit

### 2. Create Apps Script Project
1. In the Tracker spreadsheet, go to **Extensions > Apps Script**
2. Delete the default `myFunction()` code
3. Copy all contents from `Code.gs` and paste into the editor
4. Click the save icon (or Ctrl+S) and name it "Job Click Tracker"

### 3. Set Up Script Properties (IMPORTANT!)
1. In the Apps Script editor, click the **Settings** icon (⚙️) in the left sidebar
2. Scroll to **Script Properties** section
3. Click **Add script property**
4. Enter:
   - **Property**: `TRACKER_TOKEN`
   - **Value**: Generate a long random string (at least 32 characters)
     - Example: `a8f3d9c2e1b4f7a6d5c9b2e3f1a4d8c7b6e2f9a3`
     - You can use: `openssl rand -hex 32` or any password generator
5. Click **Save script properties**

**CRITICAL**: Save this token! You'll need to add it to your Python `.env` file later.

### 4. Deploy as Web App
1. Click **Deploy > New deployment**
2. Click the settings gear icon ⚙️ next to "Select type"
3. Choose **Web app**
4. Configure:
   - **Description**: "Job Click Tracker v2"
   - **Execute as**: **Me** (your email)
   - **Who has access**: **Anyone** (important for public tracking URLs)
5. Click **Deploy**
6. You may need to authorize the app:
   - Click **Authorize access**
   - Choose your Google account
   - Click **Advanced** > **Go to Job Click Tracker (unsafe)**
   - Click **Allow**
7. Copy the **Web app URL** (it ends with `/exec`)
   - Example: `https://script.google.com/macros/s/AKfycbx.../exec`
8. Click **Done**

### 5. Test the Configuration
1. In the Apps Script editor, select function **testConfiguration** from the dropdown
2. Click **Run** (►)
3. Check the **Execution log** (View > Logs)
4. You should see:
   ```
   ✅ TRACKER_TOKEN is set
   Token length: 64 characters
   ✅ Tab "Docs" exists
   ❌ Tab "Directory" missing - REQUIRED! Create it with Python exporter.
   ⚠️  Tab "Clicks" missing (will be auto-created on first click)
   ⚠️  Tab "CountryTotals" missing (will be auto-created on first click)
   ```
   
**Note**: Directory tab will be created when you run the Python exporter for the first time.

### 6. Set Up Required Tabs in Tracker Spreadsheet

#### Tab 1: "Docs"
Create a tab named exactly `Docs` with these headers in row 1:
| doc_key | spreadsheet_id | country_name |
|---------|---------------|--------------|
| ca | YOUR_CANADA_SHEET_ID | Canada |
| uk | YOUR_UK_SHEET_ID | United Kingdom |
| us | YOUR_US_SHEET_ID | United States |

**How to get spreadsheet IDs:**
- Open each country spreadsheet
- The ID is in the URL: `https://docs.google.com/spreadsheets/d/[THIS_PART_IS_THE_ID]/edit`
- Paste each ID into the corresponding row

#### Tab 2: "Directory" (CRITICAL - Auto-populated by Python)
The Python exporter (`tracker_directory_export.py`) will create this tab automatically.

**Structure** (10 columns):
| doc_key | country | tab_name | link_id | title | company | location | apply_url | tracking_url | clicks |

**Important:**
- This tab stores ALL job data and click counts
- Click counts are preserved across re-exports
- Apps Script looks up jobs HERE (not in country spreadsheets)
- First run of Python exporter will populate this tab

#### Tab 3: "Clicks"
Create a tab named exactly `Clicks` with these headers in row 1:
| ts | doc_key | country | tab_name | link_id | apply_url | user_agent |
|----|---------|---------|----------|---------|-----------|------------|

(Leave rows 2+ empty - they'll be populated automatically)

#### Tab 4: "CountryTotals"
Create a tab named exactly `CountryTotals` with these headers in row 1:
| country | total_clicks | last_updated |
|---------|--------------|--------------|

(Leave rows 2+ empty - they'll be populated automatically)

### 7. Update Your Python Environment
Add to your `.env` file:

```bash
# Tracker Spreadsheet Configuration
TRACKER_SPREADSHEET_ID=1_T9MOTutM_ZJCSkHLmns2Tc5SDVabE25MviGDESaxn8
TRACKER_DEPLOYMENT_BASE_URL=https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec
TRACKER_TOKEN=your_token_from_step_3
```

Replace:
- `YOUR_DEPLOYMENT_ID` with the ID from the Web app URL
- `your_token_from_step_3` with the exact token you set in Script Properties

### 8. Test a Tracking URL
Create a test URL:
```
https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec?doc=ca&id=job_12345&token=YOUR_TOKEN
```

Visit it in your browser. You should:
1. See a "Not Found" error (because job_12345 doesn't exist in Directory) - this is GOOD! It means auth worked.
2. If you see "Unauthorized" - your token is wrong
3. If you see "Missing Parameter" - check your URL format
4. If you see "Configuration Error: Directory tab not found" - run the Python exporter first

## Redeploying After Changes

If you modify `Code.gs`:
1. **Deploy > Manage deployments**
2. Click the **Edit** icon (✏️) next to your deployment
3. Change **Version** to "New version"
4. Add a description of changes (optional)
5. Click **Deploy**

**Note**: The Web app URL stays the same - you don't need to update your `.env` file.

## Security Features

### Token Validation
- All requests must include `token=YOUR_SECRET_TOKEN`
- Invalid tokens return "Unauthorized" with no logging
- Never share your token publicly

### Anti-Spam Protection
- Same link can only be clicked once per 10 seconds
- Uses Google Apps Script CacheService
- Duplicate clicks within 10s: redirects but doesn't log or increment counters
- After 10s: normal logging resumes

### User Agent Tracking
Add `&ua=...` to track which browser/device:
```
?doc=ca&id=12345&token=SECRET&ua=Mozilla/5.0...
```

The Python exporter does NOT add this parameter - it's for manual testing or if you want to add it via JavaScript on a landing page.

## Troubleshooting

### "Script function not found: doGet"
- You didn't paste the code correctly
- Make sure the entire `Code.gs` file is copied

### "Unauthorized" error
- Token mismatch between Script Properties and your URL
- Check both values are EXACTLY the same (case-sensitive)

### "Configuration Error: Docs tab not found"
- Create the Docs tab with exact name (case-sensitive: "Docs")
- Add the required headers

### "Configuration Error: Directory tab not found"
- Run the Python tracker exporter first: `python -m src.reports.tracker_directory_export`
- This will create and populate the Directory tab
- Or manually create Directory tab with 10 columns (see Tab 2 structure above)

### Clicks not incrementing
- Check that Directory tab has the job with matching link_id
- Make sure the `link_id` value in your tracking URL exists in a Directory row
- Run Python exporter to populate Directory if it's empty

### "Not Found" error for valid jobs
- Jobs must exist in Tracker->Directory tab (not country spreadsheets)
- Run the Python exporter to populate Directory
- Verify link_id matches: `job_<job_id>` format

### "Refused to connect" errors (Adzuna, etc.)
- **This should be fixed in v2.0!** Top-level redirects break out of iframes
- If still occurring, check browser console for specific error
- Ensure you deployed the latest Code.gs (v2.0)

## How Click Tracking Works

### User Flow:
1. User sees job in country spreadsheet (e.g., Canada)
2. User clicks tracking URL: `https://script.google.com/.../exec?doc=ca&id=job_12345&token=...`
3. Apps Script:
   - Validates token
   - Looks up job in Tracker→Directory
   - Increments click count in Directory
   - Logs event to Tracker→Clicks
   - Updates Tracker→CountryTotals
4. User sees beautiful landing page with:
   - Job title and company
   - Job category badge
   - "View Job Posting →" button
   - Confirmation that click was tracked
5. User clicks button → Opens job site in new window

### Why a Landing Page?
Google Apps Script runs in an iframe sandbox that prevents automatic redirects to external sites. Instead of fighting this restriction, we embrace it with a user-friendly landing page that:
- Shows job details for context
- Provides a clear call-to-action button
- Confirms the click was tracked
- Uses `target="_top"` to open job posting in full browser window

This approach is **more reliable** than trying to break out of the iframe with JavaScript.

## Example Tracking URL

Final format:
```
https://script.google.com/macros/s/AKfycbxABC123.../exec?doc=ca&id=job_12345&token=a8f3d9c2e1b4f7a6
```

Parameters:
- `doc=ca` - Document key (looks up spreadsheet in Docs tab)
- `id=job_12345` - Link ID to find in target spreadsheet
- `token=...` - Your secret token for authentication

## Directory Tab (Auto-populated by Python)

The Python exporter (`src/reports/tracker_directory_export.py`) creates/updates the `Directory` tab automatically during pipeline runs.

**Schema** (10 columns):
| doc_key | country | tab_name | link_id | title | company | location | apply_url | tracking_url | clicks |

**Key Features:**
- **Master Directory**: All jobs from all countries in one place
- **Click Preservation**: Click counts are NEVER lost across re-exports
- **Privacy/Security**: Apply URLs stored here only (NOT in public country spreadsheets)
- **Fast Lookup**: Apps Script loads Directory into memory for fast job lookups

**How it Works:**
1. Python exporter runs (during `weekly` or `report-only` modes)
2. Reads existing Directory tab to preserve click counts
3. Builds new directory from `sheets_staging` table
4. For each job:
   - If exists in old Directory → preserve clicks
   - If new job → clicks = 0
5. Overwrites Directory with updated data (clicks preserved)

**Country Spreadsheet Changes (v2.0):**
- Country sheets now have 7 columns: `link_id | title | company | location | remote_type | posted_date | tracking_url`
- **NO** `apply_url` column (privacy - only in Tracker)
- **NO** `clicks` column (data only in Tracker->Directory)
- **NO** `source` column (removed for simplicity)

**Manual Population:**
If you need to manually populate Directory:
```bash
python -m src.reports.tracker_directory_export
```
