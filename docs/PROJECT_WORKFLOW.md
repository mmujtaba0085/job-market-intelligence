# Job Market Intelligence Workflow (End-to-End)

This document explains how data moves through the system from collection to final outputs, including the similarity check flow and country guesser logic.

## 1. Core Pipeline

Main entrypoint: `src/orchestrator.py`

High-level flow:

1. Collect jobs from configured sources.
2. Normalize raw job data.
3. Deduplicate jobs.
4. Persist to SQLite.
5. Extract and map skills.
6. Compute weekly analytics.
7. Generate report and export artifacts.
8. Populate Google Sheets staging (optional).
9. Export to Tracker Directory (optional).

Key orchestration modes:

- `python -m src.orchestrator --mode weekly`
- `python -m src.orchestrator --mode ingest-only`
- `python -m src.orchestrator --mode report-only`
- `python -m src.orchestrator --backfill --start YYYY-MM-DD --end YYYY-MM-DD`

## 2. Data Collection

Collectors live in `src/collectors/`.

Primary sources in active workflow:

- `remotive_collector.py`
- `jsearch_collector.py`

Additional optional collectors exist and can be enabled/configured:

- `adzuna_collector.py`
- `findwork_collector.py`
- `jooble_collector.py`
- `usajobs_collector.py`

Source keys are controlled through `.env` variables.

## 3. Normalization and Storage

Core modules:

- `src/normalizer.py`
- `src/deduplicator.py`
- `src/storage/db.py`
- `src/storage/models.py`

Database:

- SQLite file default: `data/jobs.sqlite`
- `src/storage/db.py` also ensures migrations/required columns for staging and Sheets routing.

## 4. Skill Extraction and Taxonomy

Core modules:

- `src/skill_extractor.py`
- `src/taxonomy_mapper.py`
- `config/taxonomy.py`

Important note about `sklearn`/`scikit-learn`:

- The project currently treats `sklearn` and `scikit-learn` as skill keywords/taxonomy items.
- This repository does not currently rely on the `scikit-learn` Python package as a core runtime analytics dependency.

## 5. Weekly Analytics and Reporting

Analytics modules:

- `src/analytics/weekly_metrics.py`
- `src/analytics/co_occurrence.py`
- `src/analytics/emerging_detector.py`

Report/export modules:

- `src/reports/markdown_report.py`
- `src/reports/csv_export.py`
- `src/reports/charts_export.py`
- `src/reports/html_converter.py`
- `src/monetization.py`

Weekly output location pattern:

- `outputs/{market_id}/{YYYY-WW}/`

Typical outputs include:

- `report.md`
- `report.html` (if `--html`)
- CSV tables (skills, growth, movers, coverage)
- `charts.json`

## 6. Web Admin and Review Layer

Main web app:

- `web_viewer.py`

Run locally:

- `python web_viewer.py`
- Default URL: `http://localhost:5000`

Important admin areas:

- `/admin` dashboard
- `/admin/normalize` country/location normalization
- `/admin/normalize-titles` title normalization
- `/admin/sheets_staging` staging review and upload workflow
- `/admin/sheets_analytics` Sheets click analytics

## 7. Country Guesser (Country Detector)

Module:

- `src/country_detector.py`

Main function:

- `detect_country(location: str, use_geopy: bool = True)`

How it works:

1. Multiple detection methods vote with weighted scores.
2. Methods include exact patterns, city patterns, state code checks, keyword checks, and optional geopy.
3. Weights are combined; highest-weight country wins.
4. Confidence is capped to `[0.0, 1.0]`.

Useful related helpers:

- `detect_country_batch(...)`
- `should_auto_apply(confidence)`

Current threshold constant:

- `MIN_CONFIDENCE = 0.5`

## 8. Similarity Check (Staging + Title Review)

In Google Sheets staging routes, similarity matching is implemented with Python's built-in `difflib.SequenceMatcher`.

Key route:

- `POST /api/admin/sheets_staging/find_similar`
- Implementation in `src/sheets_routes.py`

Behavior:

1. Accepts an input title (or selected row context).
2. Compares against staging titles.
3. Uses `SequenceMatcher(...).ratio()` to score string similarity.
4. Returns nearest matches for manual consolidation/review.

There is also title similarity tooling in `web_viewer.py` under title normalization APIs.

## 9. Google Sheets Staging and Upload

Main modules:

- `src/sheets_routes.py`
- `src/reports/google_sheets_export.py`
- `src/storage/sheet_targets.py`

Workflow:

1. Orchestrator populates `sheets_staging` (`populate_sheets_staging(...)`).
2. Admin reviews/edits/excludes rows in staging UI.
3. Upload step writes grouped tabs into one or more target spreadsheets.
4. Merge strategy preserves existing rows where needed and inserts new rows on top.
5. Overview tab metrics are refreshed.

Routing improvements currently supported:

- Dynamic target registry.
- Country-to-target mappings.
- Per-row target override.
- Multi-country upload in one run.

## 10. Tracker Directory and Click Tracking

Main module:

- `src/reports/tracker_directory_export.py`

What it does:

1. Builds/updates a centralized Tracker spreadsheet `Directory` tab.
2. Preserves click counts where possible.
3. Generates tracking URLs using tracker deployment URL + token.

Orchestrator integration:

- Tracker export runs after market processing when tracker config is available.

## 11. Failure Isolation and Safety

Design principles reflected in code:

- Individual analytics blocks wrapped with guarded `try/except` to avoid total run failure.
- Optional integrations (Sheets, tracker, AI) can be disabled by config.
- Per-stage logging enables root cause tracing from `logs/`.

## 12. Practical Mental Model

Think of the system as three layers:

1. Data pipeline: collect, normalize, dedupe, store, analyze.
2. Curation layer: admin review and quality correction (including similarity and country guess workflows).
3. Distribution layer: reports, Google Sheets publication, and tracker analytics.
