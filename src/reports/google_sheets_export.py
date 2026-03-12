"""
src/reports/google_sheets_export.py
───────────────────────────────────
Export normalized jobs to Google Sheets, organized by country and job type.
Each country has a separate spreadsheet with dynamic tabs for each normalized_title.
Includes Overview tab with navigation and click tracking.
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.reports.tracker_directory_export import export_directory, load_country_doc_configs

logger = logging.getLogger(__name__)

# Column headers for job data (NEW SCHEMA: no source, no apply_url, no clicks)
# Apply URLs and click counts are stored ONLY in Tracker->Directory
JOB_COLUMNS = [
    "link_id",
    "title",
    "company",
    "location",
    "country",
    "remote_type",
    "posted_date",
    "url"  # Header displays "url" but contains tracking URLs
]


def get_sheets_service(service_account_json_path: str):
    """
    Create and return Google Sheets API service instance.
    
    Args:
        service_account_json_path: Path to service account JSON key file
        
    Returns:
        Google Sheets API service object
        
    Raises:
        FileNotFoundError: If JSON key file doesn't exist
        Exception: If authentication fails
    """
    if not Path(service_account_json_path).exists():
        raise FileNotFoundError(f"Service account JSON not found: {service_account_json_path}")
    
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_json_path,
            scopes=SCOPES
        )
        service = build('sheets', 'v4', credentials=credentials)
        logger.info("[google_sheets] Successfully authenticated with service account")
        return service
    except Exception as e:
        logger.error("[google_sheets] Authentication failed: %s", e)
        raise


def get_or_create_tab(service, spreadsheet_id: str, tab_name: str) -> int:
    """
    Get tab ID if exists, otherwise create it.
    
    Args:
        service: Google Sheets API service
        spreadsheet_id: Target spreadsheet ID
        tab_name: Name of the tab to get/create
        
    Returns:
        Sheet ID (integer)
    """
    try:
        # Get existing sheets
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        
        # Check if tab already exists
        for sheet in sheets:
            if sheet['properties']['title'] == tab_name:
                logger.debug("[google_sheets] Tab '%s' already exists", tab_name)
                return sheet['properties']['sheetId']
        
        # Create new tab
        request_body = {
            'requests': [{
                'addSheet': {
                    'properties': {
                        'title': tab_name
                    }
                }
            }]
        }
        
        response = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=request_body
        ).execute()
        
        sheet_id = response['replies'][0]['addSheet']['properties']['sheetId']
        logger.info("[google_sheets] Created new tab: '%s'", tab_name)
        return sheet_id
        
    except HttpError as e:
        logger.error("[google_sheets] Failed to get/create tab '%s': %s", tab_name, e)
        raise


def clear_and_write_tab(service, spreadsheet_id: str, tab_name: str, headers: list[str], rows: list[list]) -> int:
    """
    Clear tab and write headers + data rows in batch.
    
    Args:
        service: Google Sheets API service
        spreadsheet_id: Target spreadsheet ID
        tab_name: Name of the tab to write to
        headers: List of column headers
        rows: List of data rows (each row is a list of values)
        
    Returns:
        Number of rows written (excluding header)
    """
    try:
        # Ensure tab exists
        get_or_create_tab(service, spreadsheet_id, tab_name)
        
        # Clear existing content
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A:Z"
        ).execute()
        
        # Prepare data: headers + rows
        all_data = [headers] + rows
        
        # Batch write
        body = {
            'values': all_data
        }
        
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption='RAW',
            body=body
        ).execute()
        
        logger.info("[google_sheets] Wrote %d rows to '%s'", len(rows), tab_name)
        return len(rows)
        
    except HttpError as e:
        logger.error("[google_sheets] Failed to write tab '%s': %s", tab_name, e)
        return 0


def merge_and_write_tab(service, spreadsheet_id: str, tab_name: str, headers: list[str], new_rows: list[list]) -> int:
    """
    Merge new rows with existing data (deduplicating by link_id), then write to tab.
    New jobs appear at the top, existing jobs remain below.
    
    Args:
        service: Google Sheets API service
        spreadsheet_id: Target spreadsheet ID
        tab_name: Name of the tab to write to
        headers: List of column headers
        new_rows: List of new data rows to add/update
        
    Returns:
        Number of total rows written (excluding header)
    """
    try:
        # Ensure tab exists
        get_or_create_tab(service, spreadsheet_id, tab_name)
        
        # Read existing data from the sheet
        existing_data = []
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab_name}'!A:Z"
            ).execute()
            
            values = result.get('values', [])
            if values and len(values) > 1:
                # Skip header row, keep data rows
                existing_data = values[1:]
                logger.info("[google_sheets] Found %d existing rows in '%s'", len(existing_data), tab_name)
        except HttpError as e:
            logger.info("[google_sheets] No existing data in '%s' (new tab)", tab_name)
        
        # Find link_id column index
        try:
            link_id_idx = headers.index("link_id")
        except ValueError:
            logger.warning("[google_sheets] No link_id column found, using full replace")
            return clear_and_write_tab(service, spreadsheet_id, tab_name, headers, new_rows)
        
        # Build dict of new jobs by link_id for deduplication
        new_jobs_dict = {}
        for row in new_rows:
            if len(row) > link_id_idx:
                link_id = row[link_id_idx]
                new_jobs_dict[link_id] = row
        
        # Build dict of existing jobs by link_id
        existing_jobs_dict = {}
        for row in existing_data:
            if len(row) > link_id_idx:
                link_id = row[link_id_idx]
                # Only keep existing jobs that are NOT being updated
                if link_id not in new_jobs_dict:
                    existing_jobs_dict[link_id] = row
        
        # Merge: new jobs first (top), then existing jobs (bottom)
        merged_rows = list(new_jobs_dict.values()) + list(existing_jobs_dict.values())
        
        logger.info("[google_sheets] Merging: %d new + %d existing = %d total rows for '%s'",
                   len(new_jobs_dict), len(existing_jobs_dict), len(merged_rows), tab_name)
        
        # Clear and write merged data
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A:Z"
        ).execute()
        
        # Prepare data: headers + merged rows
        all_data = [headers] + merged_rows
        
        # Batch write
        body = {
            'values': all_data
        }
        
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption='RAW',
            body=body
        ).execute()
        
        logger.info("[google_sheets] Wrote %d total rows to '%s' (new at top)", len(merged_rows), tab_name)
        return len(merged_rows)
        
    except HttpError as e:
        logger.error("[google_sheets] Failed to merge and write tab '%s': %s", tab_name, e)
        return 0


def format_job_row(
    job, 
    click_count: int = 0, 
    country: str = "",
    doc_key: str = "",
    tracker_deployment_url: str = "",
    tracker_token: str = ""
) -> list:
    """
    Convert a job database row to a list matching JOB_COLUMNS.
    
    Args:
        job: sqlite3.Row or dict with job fields
        click_count: Number of times this job posting was clicked
        country: Country name (for doc lookup)
        doc_key: Document key (ca/uk/us) for tracker
        tracker_deployment_url: Google Apps Script web app URL
        tracker_token: Authentication token for tracker
        
    Returns:
        List of values in column order
    """
    # Get job data
    job_id = job.get('job_id') if isinstance(job, dict) else job['job_id']
    apply_url = job.get('url') if isinstance(job, dict) else job['url']
    
    # Generate link_id (used by Apps Script to find the row)
    link_id = f"job_{job_id}"
    
    # Build tracking URL through Google Apps Script
    if tracker_deployment_url and tracker_token:
        tracking_url = (
            f'{tracker_deployment_url}'
            f'?doc={doc_key}'
            f'&id={quote(link_id)}'
            f'&token={tracker_token}'
        )
    else:
        # Fallback if tracker not configured
        tracking_url = apply_url
    
    # Column displays "url" but contains tracking URLs for click analytics
    return [
        link_id,                        # link_id
        job['title'],                   # title
        job['company'],                 # company
        job['location'],                # location
        job['country'],                 # country
        job['remote_type'],             # remote_type
        job['posted_date'] or '',       # posted_date
        tracking_url                    # url (tracking redirect)
    ]


def build_full_overview_tab_stats(
    service,
    spreadsheet_id: str,
    uploaded_tab_stats: dict[str, dict],
) -> dict[str, dict]:
    """
    Build tab stats for ALL tabs in a spreadsheet (excluding Overview).
    Uses live row counts from sheet data so totals are always accurate.
    """
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = spreadsheet.get("sheets", [])

        tabs = []
        for sheet in sheets:
            title = sheet["properties"].get("title")
            if not title or title == "📊 Overview":
                continue
            tabs.append((title, sheet["properties"].get("sheetId")))

        if not tabs:
            return uploaded_tab_stats

        ranges = [f"'{title}'!A:A" for title, _ in tabs]
        values_result = service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=ranges,
        ).execute()

        value_ranges = values_result.get("valueRanges", [])
        range_to_values = {
            vr.get("range", ""): vr.get("values", [])
            for vr in value_ranges
        }

        today = datetime.now().strftime("%Y-%m-%d")
        full_stats: dict[str, dict] = {}

        for tab_title, sheet_id in tabs:
            lookup_range = f"'{tab_title}'!A:A"
            column_values = range_to_values.get(lookup_range, [])

            # First row is header when tab has been initialized.
            total_rows = max(0, len(column_values) - 1) if column_values else 0
            uploaded_stats = uploaded_tab_stats.get(tab_title, {})

            full_stats[tab_title] = {
                "total": total_rows,
                "new": uploaded_stats.get("new", 0),
                "updated": uploaded_stats.get("updated", today),
                "sheet_id": sheet_id,
            }

        return full_stats

    except Exception as e:
        logger.warning("[google_sheets] Failed to build full overview stats, using upload batch stats: %s", e)
        return uploaded_tab_stats


def create_overview_tab(
    service,
    spreadsheet_id: str,
    country_name: str,
    tab_stats: dict[str, dict],
    published_spreadsheet_id: str | None = None
) -> None:
    """
    Create/update Overview tab with navigation and statistics.
    
    Args:
        service: Google Sheets API service
        spreadsheet_id: Target spreadsheet (private ID)
        country_name: Country name for title
        tab_stats: Dict of {tab_name: {"total": X, "new": Y, "updated": "date", "sheet_id": ID}}
        published_spreadsheet_id: Published spreadsheet ID (from "Publish to web"), if available
    """
    tab_name = "📊 Overview"
    
    from config.settings import WEB_VIEWER_URL
    tracking_base = WEB_VIEWER_URL or "http://localhost:5000"
    
    # Prepare overview data with navigation links to tabs
    headers = [
        "#",
        "Job Type",
        "Total Jobs",
        "New This Week",
        "Last Updated",
        "Quick Link"
    ]
    
    rows = []
    total_jobs = 0
    total_new = 0
    
    # Sort tabs by total jobs descending
    sorted_tabs = sorted(
        tab_stats.items(),
        key=lambda x: x[1]["total"],
        reverse=True
    )
    
    for idx, (tab, stats) in enumerate(sorted_tabs, 1):
        total_jobs += stats["total"]
        total_new += stats["new"]
        
        # Add hyperlink to navigate to tab
        sheet_id = stats.get("sheet_id")
        if sheet_id is not None:
            if published_spreadsheet_id:
                # Use full published URL for published spreadsheets (avoids /u/0/ redirect issue)
                sheet_url = f"https://docs.google.com/spreadsheets/d/e/{published_spreadsheet_id}/pubhtml#gid={sheet_id}"
                quick_link = f'=HYPERLINK("{sheet_url}", "View →")'
            else:
                # Fall back to relative anchor (works in edit mode)
                quick_link = f'=HYPERLINK("#gid={sheet_id}", "View →")'
        else:
            quick_link = ""
        
        rows.append([
            idx,
            tab,
            stats["total"],
            stats["new"],
            stats["updated"],
            quick_link
        ])
    
    # Add summary row
    rows.append([
        "",
        f"TOTAL: {len(sorted_tabs)} Job Types",
        total_jobs,
        total_new,
        "",
        ""  # No link for summary row
    ])
    
    # Add title rows at top
    title_rows = [
        [f"{country_name} - Job Market Overview"],
        [f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        [f"📊 Click 'View →' links to navigate to category tabs"],
        [""],  # Empty row
    ]
    
    all_data = title_rows + [headers] + rows
    
    # Get or create Overview tab
    try:
        # Check if Overview tab exists, if not create it at index 0
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        
        overview_exists = False
        overview_sheet_id = None
        
        for sheet in sheets:
            if sheet['properties']['title'] == tab_name:
                overview_exists = True
                overview_sheet_id = sheet['properties']['sheetId']
                break
        
        if not overview_exists:
            # Create Overview tab at first position
            request = {
                'requests': [{
                    'addSheet': {
                        'properties': {
                            'title': tab_name,
                            'index': 0,  # Always first tab
                            'gridProperties': {
                                'frozenRowCount': 4  # Freeze header rows
                            }
                        }
                    }
                }]
            }
            response = service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=request
            ).execute()
            overview_sheet_id = response['replies'][0]['addSheet']['properties']['sheetId']
        
        # Clear and write data
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A:Z"
        ).execute()
        
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption='USER_ENTERED',  # Important for formulas
            body={'values': all_data}
        ).execute()
        
        # Format the overview tab
        format_overview_tab(service, spreadsheet_id, overview_sheet_id, len(sorted_tabs))
        
        logger.info("[google_sheets] Created Overview tab for %s", country_name)
        
    except Exception as e:
        logger.warning("[google_sheets] Failed to create Overview tab: %s", e)


def format_overview_tab(service, spreadsheet_id: str, sheet_id: int, data_rows: int):
    """Apply formatting to Overview tab for better readability."""
    requests = [
        # Bold title rows
        {
            'repeatCell': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': 0,
                    'endRowIndex': 2
                },
                'cell': {
                    'userEnteredFormat': {
                        'textFormat': {'bold': True, 'fontSize': 14}
                    }
                },
                'fields': 'userEnteredFormat.textFormat'
            }
        },
        # Bold header row
        {
            'repeatCell': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': 3,
                    'endRowIndex': 4
                },
                'cell': {
                    'userEnteredFormat': {
                        'textFormat': {'bold': True},
                        'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                    }
                },
                'fields': 'userEnteredFormat(textFormat,backgroundColor)'
            }
        },
        # Bold summary row
        {
            'repeatCell': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': 4 + data_rows,
                    'endRowIndex': 5 + data_rows
                },
                'cell': {
                    'userEnteredFormat': {
                        'textFormat': {'bold': True},
                        'backgroundColor': {'red': 0.8, 'green': 0.9, 'blue': 1.0}
                    }
                },
                'fields': 'userEnteredFormat(textFormat,backgroundColor)'
            }
        },
        # Auto-resize columns
        {
            'autoResizeDimensions': {
                'dimensions': {
                    'sheetId': sheet_id,
                    'dimension': 'COLUMNS',
                    'startIndex': 0,
                    'endIndex': 6
                }
            }
        }
    ]
    
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute()
    except Exception as e:
        logger.warning("[google_sheets] Failed to format Overview tab: %s", e)


def upload_from_staging(
    batch_id: str,
    country_filter: str = None,
    tab_filter: str = None,
    country_filters: list[str] | None = None,
) -> dict:
    """
    Upload jobs from sheets_staging table (after admin approval).
    
    Args:
        batch_id: Unique identifier for this upload batch
        country_filter: Optional country to filter (legacy single-country filter).
        tab_filter: Optional tab to filter (e.g., "Software Engineer"). If None, uploads all.
        country_filters: Optional list of countries to upload together.
        
    Returns:
        Dict of {country: {job_type: row_count}} showing what was uploaded
    """
    import sqlite3
    from config.settings import GOOGLE_SA_JSON_PATH, DB_PATH
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    from src.storage.sheet_targets import get_target_by_id, get_target_for_country
    
    # Get pending jobs (not excluded), optionally filtered by country
    query = """
        SELECT 
            s.id as staging_id,
            s.job_id,
            COALESCE(s.override_title, j.title) as title,
            COALESCE(s.override_normalized_title, j.normalized_title) as normalized_title,
            COALESCE(s.override_company, j.company) as company,
            COALESCE(s.override_location, j.location) as location,
            COALESCE(s.override_country, j.country) as country,
            COALESCE(s.override_remote_type, j.remote_type) as remote_type,
            j.posted_date,
            j.source_name,
            j.url,
            s.assigned_tab,
            s.assigned_sheet,
            s.assigned_target_id,
            s.override_target_id
        FROM sheets_staging s
        JOIN jobs j ON j.job_id = s.job_id
        WHERE s.status = 'pending' 
          AND s.exclude_from_upload = 0
    """
    
    params = []
    effective_countries = [c for c in (country_filters or []) if c and c != "all"]
    if not effective_countries and country_filter and country_filter != "all":
        effective_countries = [country_filter]

    if effective_countries:
        placeholders = ",".join(["?"] * len(effective_countries))
        query += f" AND s.assigned_sheet IN ({placeholders})"
        params.extend(effective_countries)
    elif country_filter and country_filter != "all":
        query += " AND s.assigned_sheet = ?"
        params.append(country_filter)
    
    if tab_filter and tab_filter != "all":
        query += " AND s.assigned_tab = ?"
        params.append(tab_filter)
    
    query += " ORDER BY s.assigned_sheet, s.assigned_tab"
    
    jobs = conn.execute(query, params).fetchall()
    
    if not jobs:
        logger.info("[google_sheets] No jobs to upload from staging")
        return {"message": "No jobs to upload"}
    
    # Group by target spreadsheet and tab
    spreadsheet_data = defaultdict(lambda: defaultdict(list))

    for job in jobs:
        target_row = None
        preferred_target_id = job['override_target_id'] or job['assigned_target_id']
        if preferred_target_id:
            target_row = get_target_by_id(conn, int(preferred_target_id))
            if target_row and not target_row['is_active']:
                target_row = None

        if not target_row:
            target_row = get_target_for_country(conn, job['assigned_sheet'])

        if not target_row:
            logger.warning(
                "[google_sheets] Skipping staging_id=%s (country=%s): no active target",
                job['staging_id'],
                job['assigned_sheet'],
            )
            continue

        target_id = target_row['id']
        spreadsheet_data[target_id][job['assigned_tab']].append(dict(job))
    
    # Get tracking base URL for click tracking
    from config.settings import (
        TRACKER_DEPLOYMENT_BASE_URL,
        TRACKER_TOKEN
    )
    
    # Country to doc_key mapping from dynamic targets, with fallback doc_keys for new sheets.
    country_doc_keys = {
        country: config["doc_key"]
        for country, config in load_country_doc_configs(conn).items()
    }
    
    # Click counts are NO LONGER shown in country sheets
    # They are stored and tracked in Tracker->Directory only
    # (removed click_counts query)
    
    # Upload
    service = get_sheets_service(GOOGLE_SA_JSON_PATH)
    stats = {}
    
    total_tabs = sum(len(tabs) for tabs in spreadsheet_data.values())
    processed_tabs = 0
    
    logger.info(
        "[google_sheets] Starting upload: %d jobs across %d tabs in %d target spreadsheets",
        len(jobs),
        total_tabs,
        len(spreadsheet_data),
    )
    
    for target_id, tabs in spreadsheet_data.items():
        target = get_target_by_id(conn, int(target_id))
        if not target:
            logger.warning("[google_sheets] Target %s not found; skipping", target_id)
            continue

        spreadsheet_id = target['private_spreadsheet_id']
        if not spreadsheet_id:
            logger.warning("[google_sheets] No spreadsheet ID for target %s", target['name'])
            continue

        target_label = target['name']
        stats[target_label] = {}
        tab_stats = {}

        logger.info("[google_sheets] Processing target %s: %d tabs", target_label, len(tabs))
        
        for tab_name, jobs_list in tabs.items():
            processed_tabs += 1
            logger.info(
                "[google_sheets] [%d/%d] Uploading %s / %s (%d jobs)",
                processed_tabs,
                total_tabs,
                target_label,
                tab_name,
                len(jobs_list),
            )
            
            # Sort by posted_date descending
            sorted_jobs = sorted(
                jobs_list,
                key=lambda j: j['posted_date'] or '',
                reverse=True
            )
            
            # Format rows with tracking URLs (no click counts)
            rows = [
                format_job_row(
                    job, 
                    click_count=0,  # Not displayed in country sheets
                    country=job['country'],
                    doc_key=country_doc_keys.get(job['country'], 'unknown'),
                    tracker_deployment_url=TRACKER_DEPLOYMENT_BASE_URL or "",
                    tracker_token=TRACKER_TOKEN or ""
                ) 
                for job in sorted_jobs
            ]
            
            try:
                count = merge_and_write_tab(service, spreadsheet_id, tab_name, JOB_COLUMNS, rows)
                sheet_id = get_or_create_tab(service, spreadsheet_id, tab_name)
                
                tab_stats[tab_name] = {
                    "total": count,
                    "new": count,
                    "updated": datetime.now().strftime("%Y-%m-%d"),
                    "sheet_id": sheet_id
                }
                
                stats[target_label][tab_name] = count
                
                # Mark as staged (uploaded but kept in staging for review)
                staging_ids = [job['staging_id'] for job in jobs_list]
                placeholders = ','.join(['?'] * len(staging_ids))
                conn.execute(f"""
                    UPDATE sheets_staging 
                    SET status = 'staged', 
                        uploaded_at = datetime('now'),
                        upload_batch_id = ?
                    WHERE id IN ({placeholders})
                """, [batch_id] + staging_ids)
                
            except Exception as e:
                logger.warning("[google_sheets] Failed to upload %s → %s: %s", target_label, tab_name, e)
                stats[target_label][tab_name] = 0
        
        # Create Overview tab
        if tab_stats:
            try:
                published_id = target['published_spreadsheet_id']
                full_tab_stats = build_full_overview_tab_stats(service, spreadsheet_id, tab_stats)
                create_overview_tab(service, spreadsheet_id, target_label, full_tab_stats, published_id)
            except Exception as e:
                logger.warning("[google_sheets] Failed to create Overview for %s: %s", target_label, e)
    
    conn.commit()
    conn.close()
    
    # Auto-export to Tracker Directory after successful staging upload
    try:
        from config.settings import (
            TRACKER_SPREADSHEET_ID,
            TRACKER_DEPLOYMENT_BASE_URL,
            TRACKER_TOKEN,
            DB_PATH
        )
        
        logger.info("[google_sheets] Auto-exporting to Tracker Directory...")
        tracker_stats = export_directory(
            tracker_spreadsheet_id=TRACKER_SPREADSHEET_ID,
            google_sa_json_path=GOOGLE_SA_JSON_PATH,
            tracker_deployment_url=TRACKER_DEPLOYMENT_BASE_URL,
            tracker_token=TRACKER_TOKEN,
            db_path=DB_PATH
        )
        logger.info("[google_sheets] Tracker Directory export complete: %s", tracker_stats)
    except Exception as e:
        logger.warning("[google_sheets] Failed to auto-export Tracker Directory: %s", e)
    
    return stats


# ─── CLI Testing ──────────────────────────────────────────────────────────────

def main():
    """Standalone testing entry point."""
    import argparse
    import os
    
    # Add workspace root to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    
    parser = argparse.ArgumentParser(description="Test Google Sheets export")
    parser.add_argument("--test-auth", action="store_true", help="Test authentication only")
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    from config.settings import GOOGLE_SA_JSON_PATH
    
    if args.test_auth:
        logger.info("Testing Google Sheets authentication...")
        try:
            service = get_sheets_service(GOOGLE_SA_JSON_PATH)
            logger.info("✓ Authentication successful!")
            
            # Try to get Canada spreadsheet info
            from config.settings import SHEETS_CANADA_ID
            spreadsheet = service.spreadsheets().get(spreadsheetId=SHEETS_CANADA_ID).execute()
            logger.info("✓ Successfully accessed Canada spreadsheet: %s", spreadsheet['properties']['title'])
            
        except Exception as e:
            logger.error("✗ Authentication failed: %s", e)
    else:
        logger.info("Use --test-auth to test authentication")


if __name__ == "__main__":
    main()
