# Setup and Workspace Operations Guide

This guide is the practical runbook for setting up, running, and maintaining this workspace.

## 1. Local Setup

### Prerequisites

- Python 3.10+
- Pip
- Git

### Install dependencies

```powershell
pip install -r requirements.txt
```

### Configure environment

```powershell
copy .env.example .env
```

Then edit `.env` values based on the features you want to use.

## 2. First-Time Run

Run the weekly pipeline once to initialize data and outputs:

```powershell
python -m src.orchestrator --mode weekly
```

If successful, verify:

- DB file exists at `data/jobs.sqlite`
- Weekly output folder exists under `outputs/`
- Logs are written under `logs/`

## 3. Run Modes (Day-to-Day)

- Full weekly run:
  `python -m src.orchestrator --mode weekly`
- Ingest only:
  `python -m src.orchestrator --mode ingest-only`
- Report only:
  `python -m src.orchestrator --mode report-only`
- Backfill historical windows:
  `python -m src.orchestrator --backfill --start YYYY-MM-DD --end YYYY-MM-DD`

Optional HTML rendering:

- Add `--html` to include `report.html` generation.

## 4. Web Viewer and Admin Operations

Start local admin UI:

```powershell
python web_viewer.py
```

Important pages:

- `http://localhost:5000/admin`
- `http://localhost:5000/admin/normalize`
- `http://localhost:5000/admin/normalize-titles`
- `http://localhost:5000/admin/sheets_staging`
- `http://localhost:5000/admin/sheets_analytics`

## 5. Google Sheets Operations

### Enable feature

Set in `.env`:

- `SHEETS_ENABLED=true`
- `GOOGLE_SA_JSON_PATH=...`

### Operational flow

1. Run pipeline (`weekly` or ingestion + report flows).
2. Open staging UI and review pending rows.
3. Correct title/country/target assignments as needed.
4. Upload from staging to Sheets.
5. Verify tabs and overview totals.

## 6. Tracker Operations

Required `.env` values:

- `TRACKER_SPREADSHEET_ID`
- `TRACKER_DEPLOYMENT_BASE_URL`
- `TRACKER_TOKEN`

Manual export command:

```powershell
python -m src.reports.tracker_directory_export
```

Expected behavior:

- `Directory` tab is created/updated.
- Existing click counts are preserved when possible.

## 7. Testing

Run core tests:

```powershell
pytest tests/ -v
```

Use this before and after major data-flow or schema changes.

## 8. Common Maintenance Tasks

- Re-run migrations/setup logic by running the orchestrator once after pulling updates.
- Keep `.env` in sync with `.env.example` after config changes.
- Inspect logs in `logs/{YYYY-WW}/` for stage-level failures.
- Use targeted scripts in `scripts/` for one-off maintenance and migration tasks.

## 9. Troubleshooting Checklist

### No data in reports

1. Confirm jobs were fetched in logs.
2. Confirm DB has fresh rows.
3. Re-run `ingest-only`, then `report-only`.

### Web viewer refuses to start

1. Ensure DB exists (`data/jobs.sqlite`).
2. Run weekly pipeline once.

### Sheets upload does nothing

1. Check `SHEETS_ENABLED=true`.
2. Verify service account JSON path is valid.
3. Confirm staging has pending rows.
4. Confirm target mappings are active.

### Tracker export skipped

1. Verify all `TRACKER_*` variables are set.
2. Confirm Apps Script deployment URL ends with `/exec`.

## 10. Recommended Operating Rhythm

Weekly (core):

1. Run `--mode weekly`.
2. Review admin panels.
3. Upload approved jobs to Sheets.
4. Verify tracker export.
5. Publish/report from `outputs/.../report.md`.

Daily (optional):

1. Run `--mode ingest-only`.
2. Spot-check normalization and staging quality.
