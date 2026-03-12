"""
src/reports/tracker_directory_export.py
────────────────────────────────────────
Export job directory to central Tracker spreadsheet for click tracking.

This module updates the Directory tab in the Tracker spreadsheet with all jobs
across Canada/UK/US spreadsheets. Each job gets a tracking URL that logs clicks
via the Google Apps Script web app.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import List, Dict
from urllib.parse import quote

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Directory tab columns
DIRECTORY_COLUMNS = [
    "doc_key",
    "country",
    "tab_name", 
    "link_id",
    "title",
    "company",
    "location",
    "apply_url",
    "tracking_url",
    "clicks"
]

DOCS_COLUMNS = [
    "doc_key",
    "spreadsheet_id",
    "country_name",
]

# Country to doc_key mapping
COUNTRY_DOC_KEYS = {
    "Canada": "ca",
    "United Kingdom": "uk",
    "United States": "us"
}


def _slug_doc_key(country: str) -> str:
    """Create a stable doc_key fallback for dynamically added countries."""
    normalized = re.sub(r"[^a-z0-9]+", "_", (country or "").strip().lower()).strip("_")
    return normalized or "unknown"


def load_country_doc_configs(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Return tracker config per country using DB targets, with stable fallback doc_keys."""
    rows = conn.execute(
        """
        SELECT
            stc.country,
            stc.doc_key,
            st.private_spreadsheet_id
        FROM sheets_target_countries stc
        JOIN sheets_targets st ON st.id = stc.target_id
        WHERE st.is_active = 1
        ORDER BY stc.is_primary DESC, st.id ASC, stc.country ASC
        """
    ).fetchall()

    configs: dict[str, dict[str, str]] = {}
    for row in rows:
        country = (row["country"] or "").strip()
        if not country or country in configs:
            continue
        doc_key = (row["doc_key"] or "").strip() or COUNTRY_DOC_KEYS.get(country) or _slug_doc_key(country)
        configs[country] = {
            "doc_key": doc_key,
            "spreadsheet_id": (row["private_spreadsheet_id"] or "").strip(),
        }
    return configs


def get_or_create_tracker_tab(service, spreadsheet_id: str, tab_name: str) -> int:
    """Get or create a tracker tab by title."""
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in spreadsheet.get("sheets", []):
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]

    response = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{
                "addSheet": {
                    "properties": {"title": tab_name}
                }
            }]
        },
    ).execute()
    return response["replies"][0]["addSheet"]["properties"]["sheetId"]


def clear_and_write_tracker_tab(service, spreadsheet_id: str, tab_name: str, rows: List[List]) -> int:
    """Clear a tracker tab and rewrite its contents."""
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A:Z",
    ).execute()

    if not rows:
        return 0

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    sheet_id = get_or_create_tracker_tab(service, spreadsheet_id, tab_name)
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                },
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
            ]
        },
    ).execute()
    return len(rows) - 1


def get_sheets_service(service_account_json_path: str):
    """
    Create and return Google Sheets API service instance.
    
    Args:
        service_account_json_path: Path to service account JSON key file
        
    Returns:
        Google Sheets API service object
    """
    if not Path(service_account_json_path).exists():
        raise FileNotFoundError(f"Service account JSON not found: {service_account_json_path}")
    
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    
    credentials = service_account.Credentials.from_service_account_file(
        service_account_json_path,
        scopes=SCOPES
    )
    
    return build('sheets', 'v4', credentials=credentials)


def generate_tracking_url(
    base_url: str,
    doc_key: str, 
    link_id: str,
    token: str
) -> str:
    """
    Generate tracking URL for Google Apps Script redirect.
    
    Args:
        base_url: Deployment base URL (e.g., https://script.google.com/.../exec)
        doc_key: Document key (ca/uk/us)
        link_id: Unique job identifier
        token: Secret authentication token
        
    Returns:
        Complete tracking URL
    """
    # URL encode parameters for safety
    return f"{base_url}?doc={doc_key}&id={quote(str(link_id))}&token={token}"


def read_existing_directory(service, spreadsheet_id: str, tab_name: str = "Directory") -> Dict[str, int]:
    """
    Read existing Directory tab and extract click counts for preservation.
    
    Args:
        service: Google Sheets API service
        spreadsheet_id: Tracker spreadsheet ID
        tab_name: Name of directory tab
        
    Returns:
        Dict mapping "<doc_key>_<link_id>" to clicks count
    """
    try:
        # Read all data from Directory tab
        range_name = f"{tab_name}!A:J"
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()
        
        values = result.get('values', [])
        
        if not values or len(values) < 2:
            logger.info("[tracker_directory] Directory tab is empty or has no data rows")
            return {}
        
        # Parse headers
        headers = [str(h).lower().strip() for h in values[0]]
        
        try:
            doc_key_idx = headers.index('doc_key')
            link_id_idx = headers.index('link_id')
            clicks_idx = headers.index('clicks')
        except ValueError as e:
            logger.warning(f"[tracker_directory] Missing columns in Directory: {e}")
            return {}
        
        # Build click preservation map
        click_map = {}
        
        for row in values[1:]:  # Skip header
            if len(row) <= max(doc_key_idx, link_id_idx, clicks_idx):
                continue  # Incomplete row
            
            doc_key = str(row[doc_key_idx]).strip()
            link_id = str(row[link_id_idx]).strip()
            clicks = row[clicks_idx] if clicks_idx < len(row) else 0
            
            # Convert clicks to int
            try:
                clicks = int(clicks) if clicks != '' else 0
            except (ValueError, TypeError):
                clicks = 0
            
            # Store with composite key
            key = f"{doc_key}_{link_id}"
            click_map[key] = clicks
        
        logger.info(f"[tracker_directory] Loaded {len(click_map)} click counts from existing Directory")
        return click_map
        
    except HttpError as error:
        logger.warning(f"[tracker_directory] Could not read existing Directory: {error}")
        return {}
    except Exception as e:
        logger.warning(f"[tracker_directory] Unexpected error reading Directory: {e}")
        return {}


def get_or_create_directory_tab(service, spreadsheet_id: str, tab_name: str = "Directory") -> int:
    """
    Get or create the Directory tab in the Tracker spreadsheet.
    
    Args:
        service: Google Sheets API service
        spreadsheet_id: Tracker spreadsheet ID
        tab_name: Name of the directory tab
        
    Returns:
        Sheet ID of the directory tab
    """
    try:
        spreadsheet = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()
        
        sheets = spreadsheet.get('sheets', [])
        
        # Check if tab exists
        for sheet in sheets:
            if sheet['properties']['title'] == tab_name:
                logger.info(f"[tracker_directory] Directory tab exists: {tab_name}")
                return sheet['properties']['sheetId']
        
        # Create new tab
        logger.info(f"[tracker_directory] Creating new tab: {tab_name}")
        request = {
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
            body=request
        ).execute()
        
        sheet_id = response['replies'][0]['addSheet']['properties']['sheetId']
        logger.info(f"[tracker_directory] Created tab with ID: {sheet_id}")
        return sheet_id
        
    except HttpError as error:
        logger.error(f"[tracker_directory] Error managing directory tab: {error}")
        raise


def clear_and_write_directory(
    service,
    spreadsheet_id: str,
    tab_name: str,
    rows: List[List]
) -> int:
    """
    Clear directory tab and write new data.
    
    Args:
        service: Google Sheets API service
        spreadsheet_id: Tracker spreadsheet ID
        tab_name: Name of directory tab
        rows: List of row data (including header)
        
    Returns:
        Number of rows written
    """
    try:
        # Clear existing data
        range_name = f"{tab_name}!A:Z"
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()
        
        logger.info(f"[tracker_directory] Cleared {tab_name}")
        
        # Write new data in batch
        if not rows:
            logger.warning("[tracker_directory] No rows to write")
            return 0
        
        body = {
            'values': rows
        }
        
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{tab_name}!A1",
            valueInputOption='RAW',
            body=body
        ).execute()
        
        updated_cells = result.get('updatedCells', 0)
        logger.info(f"[tracker_directory] Wrote {len(rows)} rows ({updated_cells} cells)")
        
        # Format header row (bold, freeze)
        sheet_id = get_or_create_directory_tab(service, spreadsheet_id, tab_name)
        
        format_request = {
            'requests': [
                # Bold header
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': 0,
                            'endRowIndex': 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'textFormat': {'bold': True}
                            }
                        },
                        'fields': 'userEnteredFormat.textFormat.bold'
                    }
                },
                # Freeze header row
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'gridProperties': {'frozenRowCount': 1}
                        },
                        'fields': 'gridProperties.frozenRowCount'
                    }
                }
            ]
        }
        
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=format_request
        ).execute()
        
        return len(rows) - 1  # Exclude header
        
    except HttpError as error:
        logger.error(f"[tracker_directory] Error writing data: {error}")
        raise


def export_directory(
    tracker_spreadsheet_id: str,
    google_sa_json_path: str,
    tracker_deployment_url: str,
    tracker_token: str,
    db_path: str
) -> Dict[str, int]:
    """
    Export job directory to Tracker spreadsheet.
    
    Args:
        tracker_spreadsheet_id: Tracker spreadsheet ID
        google_sa_json_path: Path to service account JSON
        tracker_deployment_url: Base URL for tracking (without params)
        tracker_token: Secret authentication token
        db_path: Path to SQLite database
        
    Returns:
        Dict with stats: {total_jobs, countries}
    """
    logger.info("[tracker_directory] Starting directory export")
    
    # Validate inputs
    if not tracker_spreadsheet_id:
        logger.warning("[tracker_directory] TRACKER_SPREADSHEET_ID not configured, skipping")
        return {"total_jobs": 0, "error": "Not configured"}
    
    if not Path(google_sa_json_path).exists():
        logger.warning("[tracker_directory] Service account JSON not found, skipping")
        return {"total_jobs": 0, "error": "No credentials"}
    
    if not tracker_deployment_url:
        logger.warning("[tracker_directory] TRACKER_DEPLOYMENT_BASE_URL not configured, skipping")
        return {"total_jobs": 0, "error": "No deployment URL"}
    
    if not tracker_token:
        logger.warning("[tracker_directory] TRACKER_TOKEN not configured, skipping")
        return {"total_jobs": 0, "error": "No token"}
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    country_doc_configs = load_country_doc_configs(conn)

    # Get all staged/uploaded jobs from sheets_staging
    query = """
        SELECT 
            s.id as staging_id,
            s.job_id,
            COALESCE(s.override_title, j.title) as title,
            COALESCE(s.override_normalized_title, j.normalized_title) as normalized_title,
            COALESCE(s.override_company, j.company) as company,
            COALESCE(s.override_location, j.location) as location,
            COALESCE(s.override_country, j.country) as country,
            j.url as apply_url,
            s.assigned_tab,
            s.assigned_sheet
        FROM sheets_staging s
        JOIN jobs j ON j.job_id = s.job_id
        WHERE s.status IN ('staged', 'uploaded')
        ORDER BY s.assigned_sheet, s.assigned_tab, j.posted_date DESC
    """
    
    jobs = conn.execute(query).fetchall()
    
    if not jobs:
        logger.info("[tracker_directory] No jobs to export")
        conn.close()
        return {"total_jobs": 0}
    
    logger.info(f"[tracker_directory] Found {len(jobs)} jobs to export")
    
    # Get Google Sheets service (needed for reading existing data)
    try:
        service = get_sheets_service(google_sa_json_path)
    except Exception as e:
        logger.error(f"[tracker_directory] Failed to authenticate: {e}")
        conn.close()
        return {"total_jobs": 0, "error": "Authentication failed"}
    
    # CLICK PRESERVATION: Load existing click counts from Directory tab
    # This ensures clicks are never lost across re-exports
    existing_clicks = read_existing_directory(service, tracker_spreadsheet_id, "Directory")
    logger.info(f"[tracker_directory] Preserving clicks for {len(existing_clicks)} existing jobs")

    docs_rows = [DOCS_COLUMNS]
    for country, config in sorted(country_doc_configs.items()):
        spreadsheet_id = config.get("spreadsheet_id", "")
        if not spreadsheet_id:
            continue
        docs_rows.append([config["doc_key"], spreadsheet_id, country])
    
    conn.close()
    
    # Build directory rows
    rows = [DIRECTORY_COLUMNS]  # Header row
    
    for job in jobs:
        country = job['assigned_sheet']  # e.g., "Canada"
        config = country_doc_configs.get(country, {})
        doc_key = config.get("doc_key") or COUNTRY_DOC_KEYS.get(country) or _slug_doc_key(country)
        
        # Generate unique link_id (use job_id)
        link_id = f"job_{job['job_id']}"
        
        # Generate tracking URL
        tracking_url = generate_tracking_url(
            tracker_deployment_url,
            doc_key,
            link_id,
            tracker_token
        )
        
        # CLICK PRESERVATION: Use existing clicks from Directory tab
        # If this is a new job (not in existing Directory), start at 0
        lookup_key = f"{doc_key}_{link_id}"
        clicks = existing_clicks.get(lookup_key, 0)
        
        # Build row
        row = [
            doc_key,                      # doc_key
            country,                      # country
            job['assigned_tab'],          # tab_name
            link_id,                      # link_id
            job['title'],                 # title
            job['company'],               # company
            job['location'],              # location
            job['apply_url'],             # apply_url
            tracking_url,                 # tracking_url
            clicks                        # clicks
        ]
        
        rows.append(row)
    
    # Write to Google Sheets
    try:
        get_or_create_tracker_tab(service, tracker_spreadsheet_id, "Docs")
        clear_and_write_tracker_tab(service, tracker_spreadsheet_id, "Docs", docs_rows)

        # Ensure Directory tab exists
        get_or_create_directory_tab(service, tracker_spreadsheet_id, "Directory")
        
        # Write data (existing clicks are preserved in rows)
        count = clear_and_write_directory(service, tracker_spreadsheet_id, "Directory", rows)
        
        logger.info(f"[tracker_directory] Successfully exported {count} jobs to Directory tab")
        
        # Calculate country breakdown
        country_counts = {}
        for job in jobs:
            country = job['assigned_sheet']
            country_counts[country] = country_counts.get(country, 0) + 1
        
        return {
            "total_jobs": count,
            "countries": country_counts
        }
        
    except Exception as e:
        logger.error(f"[tracker_directory] Export failed: {e}")
        return {"total_jobs": 0, "error": str(e)}


if __name__ == "__main__":
    # Test/manual run
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    from config.settings import (
        TRACKER_SPREADSHEET_ID,
        GOOGLE_SA_JSON_PATH,
        TRACKER_DEPLOYMENT_BASE_URL,
        TRACKER_TOKEN,
        DB_PATH
    )
    
    result = export_directory(
        TRACKER_SPREADSHEET_ID,
        GOOGLE_SA_JSON_PATH,
        TRACKER_DEPLOYMENT_BASE_URL,
        TRACKER_TOKEN,
        DB_PATH
    )
    
    print(f"Export complete: {result}")
