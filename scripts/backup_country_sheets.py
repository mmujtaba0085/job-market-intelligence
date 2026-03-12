"""
Backup existing country sheet data before schema migration.
This script exports all country sheet tabs to CSV files for safekeeping.
"""
import os
import csv
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Import settings
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config.settings import (
    SHEETS_CANADA_ID,
    SHEETS_UK_ID,
    SHEETS_US_ID,
    GOOGLE_SA_JSON_PATH
)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

def get_sheets_service():
    """Get authenticated Google Sheets service."""
    creds = Credentials.from_service_account_file(GOOGLE_SA_JSON_PATH, scopes=SCOPES)
    service = build('sheets', 'v4', credentials=creds)
    return service

def get_all_tabs(service, spreadsheet_id):
    """Get list of all tab names in a spreadsheet."""
    sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = sheet_metadata.get('sheets', [])
    tab_names = [sheet['properties']['title'] for sheet in sheets]
    return tab_names

def read_tab_data(service, spreadsheet_id, tab_name):
    """Read all data from a specific tab."""
    range_name = f"'{tab_name}'!A:Z"  # Read columns A-Z
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name
    ).execute()
    return result.get('values', [])

def backup_spreadsheet(service, spreadsheet_id, country_name, backup_dir):
    """Backup all tabs from a spreadsheet to CSV files."""
    print(f"\n📋 Backing up {country_name} spreadsheet...")
    
    # Create country-specific backup directory
    country_dir = os.path.join(backup_dir, country_name.replace(' ', '_').lower())
    os.makedirs(country_dir, exist_ok=True)
    
    # Get all tabs
    tab_names = get_all_tabs(service, spreadsheet_id)
    print(f"   Found {len(tab_names)} tabs: {', '.join(tab_names)}")
    
    backed_up = 0
    for tab_name in tab_names:
        # Read tab data
        data = read_tab_data(service, spreadsheet_id, tab_name)
        
        if not data:
            print(f"   ⚠️  Skipping empty tab: {tab_name}")
            continue
        
        # Write to CSV
        safe_tab_name = tab_name.replace('/', '_').replace('\\', '_')
        csv_path = os.path.join(country_dir, f"{safe_tab_name}.csv")
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)
        
        backed_up += 1
        print(f"   ✅ Backed up: {tab_name} → {os.path.basename(csv_path)} ({len(data)} rows)")
    
    print(f"   ✅ {country_name}: {backed_up} tabs backed up to {country_dir}")
    return backed_up

def main():
    """Main backup function."""
    print("=" * 70)
    print("🔄 COUNTRY SHEETS BACKUP UTILITY")
    print("=" * 70)
    print("\nThis script backs up all country spreadsheet data before schema migration.")
    print("Original sheets will NOT be modified - this is read-only.")
    
    # Create backup directory
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = os.path.join(
        os.path.dirname(__file__), 
        '..', 
        'data', 
        'backups', 
        f'country_sheets_{timestamp}'
    )
    os.makedirs(backup_dir, exist_ok=True)
    print(f"\n📁 Backup location: {os.path.abspath(backup_dir)}")
    
    # Get Google Sheets service
    print("\n🔐 Authenticating with Google Sheets...")
    service = get_sheets_service()
    print("   ✅ Authentication successful")
    
    # Backup each country spreadsheet
    countries = [
        (SHEETS_CANADA_ID, "Canada"),
        (SHEETS_UK_ID, "United Kingdom"),
        (SHEETS_US_ID, "United States")
    ]
    
    total_tabs = 0
    for sheet_id, country_name in countries:
        tabs_backed_up = backup_spreadsheet(service, sheet_id, country_name, backup_dir)
        total_tabs += tabs_backed_up
    
    # Summary
    print("\n" + "=" * 70)
    print("✅ BACKUP COMPLETE")
    print("=" * 70)
    print(f"📊 Total tabs backed up: {total_tabs}")
    print(f"📁 Backup location: {os.path.abspath(backup_dir)}")
    print("\nYou can now safely proceed with the schema migration.")
    print("If anything goes wrong, restore from these CSV files.")
    print("=" * 70)

if __name__ == "__main__":
    main()
