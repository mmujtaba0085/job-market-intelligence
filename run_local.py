"""
Local development runner for Job Market Intelligence.
Run with:  python run_local.py
Then open: http://localhost:5000
"""
import os
from pathlib import Path

# Load .env before importing the app
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# Verify the database exists
db = Path(os.getenv("DB_PATH", "data/jobs.sqlite"))
if not db.exists():
    print(f"ERROR: Database not found at {db.absolute()}")
    print("Run the pipeline first: python -m src.orchestrator --mode weekly")
    raise SystemExit(1)

from web_viewer import app

if __name__ == "__main__":
    print("=" * 60)
    print("  Job Market Intelligence — Local Dev Server")
    print("=" * 60)
    print(f"  URL  : http://localhost:5000")
    print(f"  Login: admin / ***REMOVED-LEAKED-PASSWORD***")
    print(f"  DB   : {db.absolute()}")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False)
