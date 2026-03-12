# Click Tracking System - Quick Start Guide

## What You Have

✅ **Google Apps Script** (`google_apps_script/Code.gs`)
- Complete web app for click tracking and redirect
- Security: token validation + 10s anti-spam
- Logs clicks to Tracker spreadsheet
- Updates counters automatically

✅ **Python Integration**
- `tracker_directory_export.py` - Exports master directory to Tracker
- Updated `google_sheets_export.py` - Generates tracking URLs
- Automatic orchestrator integration

✅ **Documentation**
- Complete deployment guide
- Troubleshooting section
- Testing instructions

## 10-Minute Setup

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
     - Value: (generate random 32+ char string)
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

## Common Issues

### "TRACKER_TOKEN not set"
❌ Forgot to add token to Apps Script Script Properties
✅ Settings > Script Properties > Add `TRACKER_TOKEN`

### "Unauthorized" when clicking link
❌ Token mismatch between .env and Apps Script
✅ Copy token from Apps Script to .env (exact match, case-sensitive)

### Directory tab empty
❌ Tracker config missing in .env
✅ Add all 3 TRACKER_* variables to .env

### "Configuration Error: Docs tab not found"
❌ Missing Docs tab in Tracker spreadsheet
✅ Create Docs tab with headers and country rows

## What Happens Next

### When Pipeline Runs

```
1. Collect jobs → sheets_staging table
2. Export to Canada/UK/US spreadsheets
   - Each job row gets link_id + tracking_url + clicks columns
   - tracking_url points to Apps Script web app
3. Export to Tracker->Directory
   - Master list of all jobs
   - Includes tracking URLs and current click counts
4. Generate reports
```

### When User Clicks Link

```
1. User clicks tracking_url in Canada spreadsheet
2. Apps Script web app receives request
3. Validates token ✓
4. Finds job row in Canada sheet
5. Increments clicks column
6. Logs to Tracker->Clicks tab
7. Updates Tracker->CountryTotals
8. Redirects to actual job posting
```

## Next Steps

- **Read:** `CLICK_TRACKING_SYSTEM.md` for complete documentation
- **Deploy:** Follow `google_apps_script/DEPLOYMENT.md` for detailed instructions
- **Monitor:** Check Tracker->Clicks and CountryTotals for analytics
- **Customize:** Modify Apps Script as needed (remember to redeploy)

## File Locations

```
google_apps_script/
  ├── Code.gs                      ← Copy to Apps Script editor
  └── DEPLOYMENT.md                ← Detailed deployment guide

src/reports/
  ├── tracker_directory_export.py  ← Exports to Directory tab
  └── google_sheets_export.py      ← Updated with tracking URLs

config/
  └── settings.py                  ← Added TRACKER_* config

.env.tracker.example               ← Example .env variables
CLICK_TRACKING_SYSTEM.md           ← Complete documentation
QUICKSTART.md                      ← This file
```

## Questions?

1. Check logs:
   - Apps Script: Executions panel
   - Python: `logs/2026-09/ai_ml_global_<run_id>.log`

2. Test Apps Script:
   - Run `testConfiguration()` function
   - Check execution log for ✅ confirmations

3. Test Python:
   - Run `python src/reports/tracker_directory_export.py`
   - Should see export stats

4. Review documentation:
   - `CLICK_TRACKING_SYSTEM.md` - Full system docs
   - `google_apps_script/DEPLOYMENT.md` - Apps Script guide

---

**Ready!** Add the 3 TRACKER_* variables to your .env and run the pipeline.
