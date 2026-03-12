"""
One-time script to re-upload all staged jobs using CLEAR AND REPLACE mode.
This will replace all current data in Google Sheets with the staged data.

After this runs, normal uploads will continue using merge mode.
"""

import sys
import os
from collections import defaultdict
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage.db import get_connection
from src.reports.google_sheets_export import (
    get_sheets_service,
    clear_and_write_tab,
    get_or_create_tab,
    create_overview_tab,
    format_job_row,
    JOB_COLUMNS
)
from config.settings import (
    GOOGLE_SA_JSON_PATH,
    SHEETS_CANADA_ID,
    SHEETS_UK_ID,
    SHEETS_US_ID,
    SHEETS_CANADA_PUBLISHED_ID,
    SHEETS_UK_PUBLISHED_ID,
    SHEETS_US_PUBLISHED_ID,
    TRACKER_DEPLOYMENT_BASE_URL,
    TRACKER_TOKEN
)
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def reupload_all_staged():
    """Re-upload all staged jobs using clear-and-replace mode."""
    
    conn = get_connection()
    
    # Count total STAGED (already uploaded)
    total_staged = conn.execute("""
        SELECT COUNT(*) as count 
        FROM sheets_staging 
        WHERE status = 'staged' AND exclude_from_upload = 0
    """).fetchone()['count']
    
    logger.info(f"Found {total_staged} STAGED jobs to re-upload")
    
    if total_staged == 0:
        logger.info("No staged jobs to upload")
        return
    
    # Confirm
    print("\n" + "="*60)
    print("⚠️  FULL REPLACEMENT MODE")
    print("="*60)
    print(f"This will REPLACE all data in Google Sheets with {total_staged} staged jobs.")
    print("After this, normal uploads will preserve existing data using merge mode.")
    print("="*60)
    response = input("\nContinue? (yes/no): ").strip().lower()
    
    if response != 'yes':
        logger.info("Cancelled by user")
        return
    
    # Get all STAGED jobs (already uploaded before)
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
            s.assigned_sheet
        FROM sheets_staging s
        JOIN jobs j ON j.job_id = s.job_id
        WHERE s.status = 'staged' 
          AND s.exclude_from_upload = 0
        ORDER BY s.assigned_sheet, s.assigned_tab
    """
    
    jobs = conn.execute(query).fetchall()
    
    # Group by country and tab
    spreadsheet_data = defaultdict(lambda: defaultdict(list))
    
    for job in jobs:
        spreadsheet_data[job['assigned_sheet']][job['assigned_tab']].append(job)
    
    # Get spreadsheet IDs
    sheet_mapping = {
        "Canada": SHEETS_CANADA_ID,
        "United Kingdom": SHEETS_UK_ID,
        "United States": SHEETS_US_ID
    }
    
    published_sheet_mapping = {
        "Canada": SHEETS_CANADA_PUBLISHED_ID,
        "United Kingdom": SHEETS_UK_PUBLISHED_ID,
        "United States": SHEETS_US_PUBLISHED_ID
    }
    
    # Country to doc_key mapping
    COUNTRY_DOC_KEYS = {
        "Canada": "ca",
        "United Kingdom": "uk",
        "United States": "us"
    }
    
    # Generate batch ID
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Upload
    service = get_sheets_service(GOOGLE_SA_JSON_PATH)
    stats = {}
    
    total_tabs = sum(len(tabs) for tabs in spreadsheet_data.values())
    processed_tabs = 0
    
    logger.info(f"Starting upload: {len(jobs)} jobs across {total_tabs} tabs in {len(spreadsheet_data)} countries")
    
    for country, tabs in spreadsheet_data.items():
        spreadsheet_id = sheet_mapping.get(country)
        if not spreadsheet_id:
            logger.warning(f"No spreadsheet ID for country: {country}")
            continue
        
        stats[country] = {}
        tab_stats = {}
        
        logger.info(f"Processing {country}: {len(tabs)} tabs")
        
        for tab_name, jobs_list in tabs.items():
            processed_tabs += 1
            logger.info(f"[{processed_tabs}/{total_tabs}] Uploading {country} / {tab_name} ({len(jobs_list)} jobs)")
            
            # Sort by posted_date descending
            sorted_jobs = sorted(
                jobs_list,
                key=lambda j: j['posted_date'] or '',
                reverse=True
            )
            
            # Format rows with tracking URLs
            doc_key = COUNTRY_DOC_KEYS.get(country, "unknown")
            
            rows = [
                format_job_row(
                    job, 
                    click_count=0,
                    country=country,
                    doc_key=doc_key,
                    tracker_deployment_url=TRACKER_DEPLOYMENT_BASE_URL or "",
                    tracker_token=TRACKER_TOKEN or ""
                ) 
                for job in sorted_jobs
            ]
            
            try:
                # USE CLEAR AND REPLACE MODE FOR THIS ONE-TIME UPLOAD
                count = clear_and_write_tab(service, spreadsheet_id, tab_name, JOB_COLUMNS, rows)
                sheet_id = get_or_create_tab(service, spreadsheet_id, tab_name)
                
                tab_stats[tab_name] = {
                    "total": count,
                    "new": count,
                    "updated": datetime.now().strftime("%Y-%m-%d"),
                    "sheet_id": sheet_id
                }
                
                stats[country][tab_name] = count
                
                # Keep status as 'staged' (don't update status or batch ID)
                # These jobs are already marked as uploaded
                
            except Exception as e:
                logger.warning(f"Failed to upload {country} → {tab_name}: {e}")
                stats[country][tab_name] = 0
        
        # Create Overview tab
        if tab_stats:
            try:
                published_id = published_sheet_mapping.get(country)
                create_overview_tab(service, spreadsheet_id, country, tab_stats, published_id)
                logger.info(f"✓ Created overview tab for {country}")
            except Exception as e:
                logger.warning(f"Failed to create overview for {country}: {e}")
    
    conn.commit()
    
    # Summary
    total_uploaded = sum(sum(tabs.values()) for tabs in stats.values())
    
    print("\n" + "="*60)
    print("✅ UPLOAD COMPLETE")
    print("="*60)
    for country, tabs in stats.items():
        print(f"\n{country}:")
        for tab_name, count in tabs.items():
            print(f"  {tab_name}: {count} jobs")
    print(f"\nTotal: {total_uploaded} jobs uploaded")
    print("="*60)
    print("\n⚡ Future uploads will use MERGE mode (preserve existing data)")
    print("="*60)

if __name__ == "__main__":
    reupload_all_staged()
