# Google Sheets Integration - Updated Summary

## ✅ Changes Implemented

### 1. **Staging Shows All Jobs from Past Week**
Previously, staging only showed jobs pending upload. Now it shows **all jobs from the past week** (Canada/UK/US), including:
- **Pending**: Ready to upload
- **Staged**: Already uploaded but kept for review
- **Excluded**: Marked to skip

### 2. **Uploaded Jobs Move to "Staged" Status**
When you upload jobs to Google Sheets:
- Status changes from `pending` → `staged`
- Jobs remain in the staging table (not deleted)
- You can review, edit, or delete staged jobs
- Only `pending` jobs will be uploaded on next upload

### 3. **Navigation Added**
**From Admin Dashboard** (http://localhost:5000/admin):
- Two new cards added:
  - 📊 **Google Sheets Staging** - Review and export jobs
  - 📈 **Sheets Click Analytics** - Track clicks on Overview links

**Breadcrumb Navigation**:
- Google Sheets pages have "← Back to Admin Dashboard" link
- Analytics page also links to Staging page

## 🎯 How It Works Now

### Weekly Workflow:

1. **Run Pipeline**
   ```bash
   python -m src.orchestrator --mode weekly
   ```
   - Collects jobs
   - Automatically populates staging with **all jobs from past week**
   - Status: `pending`

2. **Review in Staging** 
   - Go to: http://localhost:5000/admin → "Google Sheets Staging"
   - Shows all jobs from past week grouped by country/tab
   - Edit fields, reassign tabs, exclude jobs

3. **Upload to Google Sheets**
   - Click "☁️ Upload Pending to Google Sheets"
   - Only `pending` jobs are uploaded
   - Uploaded jobs → status changes to `staged`

4. **Post-Upload Review**
   - Staged jobs remain in staging table
   - Can delete staged jobs if needed
   - Can re-run upload with new pending jobs

## 📊 Status Workflow

```
Jobs from past week
        ↓
  [pending] ← Initial status
        ↓
   (Edit/Review)
        ↓
  Click Upload
        ↓
  [staged] ← After upload (kept in staging)
        ↓
   (Optional: Delete)
```

## 📋 Stats Dashboard

Shows breakdown by country/tab:
- **Total Jobs** - All jobs this week
- **Pending** - Ready to upload
- **Staged** - Already uploaded
- **Excluded** - Marked to skip

## 🔧 Filter Options

Filter by:
- **Country**: Canada / UK / US / All
- **Tab**: Any job type / All
- **Status**: All / Pending / Staged

## 💡 Tips

- **Staged jobs won't be re-uploaded** - Only pending jobs upload
- **Delete staged jobs** - Use bulk delete on staged jobs to clean up
- **Week refresh** - Next pipeline run adds new jobs as pending
- **Edit anytime** - Can edit pending or staged jobs
- **Safe to re-upload** - Won't duplicate staged jobs

## 🚀 Access Points

1. **Main Entry**: http://localhost:5000/admin
2. **Direct Links**:
   - Staging: http://localhost:5000/admin/sheets_staging
   - Analytics: http://localhost:5000/admin/sheets_analytics

All pages have breadcrumb navigation for easy movement between sections.
