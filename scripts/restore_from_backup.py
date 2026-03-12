"""
Restore Google Sheets data from CSV backups.
Usage: python scripts/restore_from_backup.py
"""

import csv
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reports.google_sheets_export import get_sheets_service, merge_and_write_tab, get_or_create_tab
from config.settings import (
    GOOGLE_SA_JSON_PATH,
    SHEETS_CANADA_ID,
    SHEETS_UK_ID,
    SHEETS_US_ID
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

logger = logging.getLogger(__name__)

# Spreadsheet mapping
SHEET_IDS = {
    "canada": SHEETS_CANADA_ID,
    "united_kingdom": SHEETS_UK_ID,
    "united_states": SHEETS_US_ID
}

def restore_from_backup(backup_dir: str):
    """
    Restore Google Sheets from CSV backup files.
    
    Args:
        backup_dir: Path to backup directory (e.g., data/backups/country_sheets_2026-03-03_073540)
    """
    backup_path = Path(backup_dir)
    
    if not backup_path.exists():
        logger.error(f"Backup directory not found: {backup_dir}")
        return
    
    logger.info(f"Restoring from backup: {backup_path}")
    
    # Connect to Google Sheets
    service = get_sheets_service(GOOGLE_SA_JSON_PATH)
    
    stats = {"restored_tabs": 0, "restored_rows": 0, "errors": 0}
    
    # Process each country folder
    for country_folder in backup_path.iterdir():
        if not country_folder.is_dir():
            continue
        
        country_name_lower = country_folder.name.lower()
        spreadsheet_id = SHEET_IDS.get(country_name_lower)
        
        if not spreadsheet_id:
            logger.warning(f"Unknown country folder: {country_folder.name}")
            continue
        
        logger.info(f"Processing {country_folder.name}...")
        
        # Process each CSV file (one per tab)
        for csv_file in country_folder.glob("*.csv"):
            tab_name = csv_file.stem  # Filename without .csv
            
            try:
                logger.info(f"  Restoring tab: {tab_name}")
                
                # Read CSV
                with open(csv_file, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                
                if not rows:
                    logger.warning(f"    Empty file: {csv_file}")
                    continue
                
                # First row is headers
                headers = rows[0]
                data_rows = rows[1:]
                
                if not data_rows:
                    logger.warning(f"    No data rows in: {csv_file}")
                    continue
                
                # Upload to Google Sheets using merge (won't overwrite existing)
                count = merge_and_write_tab(service, spreadsheet_id, tab_name, headers, data_rows)
                
                stats["restored_tabs"] += 1
                stats["restored_rows"] += count
                
                logger.info(f"    ✓ Restored {count} rows to {tab_name}")
                
            except Exception as e:
                logger.error(f"    ✗ Failed to restore {tab_name}: {e}")
                stats["errors"] += 1
    
    logger.info("=" * 60)
    logger.info(f"Restoration complete:")
    logger.info(f"  Tabs restored: {stats['restored_tabs']}")
    logger.info(f"  Rows restored: {stats['restored_rows']}")
    logger.info(f"  Errors: {stats['errors']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Restore Google Sheets from backup")
    parser.add_argument(
        "--backup-dir",
        default="data/backups/country_sheets_2026-03-03_073540",
        help="Path to backup directory"
    )
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("🔄 GOOGLE SHEETS DATA RESTORATION")
    print("=" * 60)
    print(f"Backup: {args.backup_dir}")
    print("=" * 60)
    
    confirm = input("\nThis will merge backup data with current sheets. Continue? (yes/no): ")
    
    if confirm.lower() != 'yes':
        print("Restoration cancelled.")
        sys.exit(0)
    
    restore_from_backup(args.backup_dir)
