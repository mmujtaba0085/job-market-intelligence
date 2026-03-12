#!/usr/bin/env python3
"""Run database migrations to add Google Sheets tables."""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.storage.db import run_migrations

if __name__ == "__main__":
    print("Running database migrations...")
    run_migrations()
    print("✓ Migrations complete!")
    print()
    print("New tables created:")
    print("  • sheets_staging - Jobs pending upload approval")
    print("  • sheets_click_tracking - Overview link click analytics")
