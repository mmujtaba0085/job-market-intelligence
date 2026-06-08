"""
Run the ingestion pipeline locally (no Docker needed).
Usage:
    python run_ingest.py                  # full weekly pipeline
    python run_ingest.py --mode ingest-only
    python run_ingest.py --mode report-only
    python run_ingest.py --mode crawl
"""
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Pass all args straight through to the orchestrator
from src.orchestrator import main
main()
