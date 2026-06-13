"""
config/settings.py
──────────────────
Global configuration: paths, analytics thresholds, scheduling options.
All values have sensible defaults but can be overridden via .env.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Project root ─────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent

# ─── Storage paths ────────────────────────────────────────────────────────────
DB_PATH: Path = ROOT_DIR / os.getenv("DB_PATH", "data/jobs.sqlite")
OUTPUTS_DIR: Path = ROOT_DIR / os.getenv("OUTPUTS_DIR", "outputs")
LOGS_DIR: Path = ROOT_DIR / os.getenv("LOGS_DIR", "logs")

# Ensure directories exist
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ─── Analytics thresholds ─────────────────────────────────────────────────────
MIN_FREQ: int = int(os.getenv("MIN_FREQ", "15"))
GROWTH_THRESHOLD: float = float(os.getenv("GROWTH_THRESHOLD", "50.0"))
DECLINING_THRESHOLD: float = float(os.getenv("DECLINING_THRESHOLD", "-15.0"))
EMERGING_LOOKBACK_WEEKS: int = int(os.getenv("EMERGING_LOOKBACK_WEEKS", "4"))

# ─── Week boundary ────────────────────────────────────────────────────────────
# ISO standard: weeks start Monday, numbered per ISO 8601
WEEK_STARTS_ON: str = "Monday"

# ─── Pipeline behaviour ───────────────────────────────────────────────────────
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"

# ─── Report formatting ────────────────────────────────────────────────────────
TOP_SKILLS_LIMIT: int = 20       # max rows in top skills table
GROWTH_SKILLS_LIMIT: int = 20    # max rows in fastest growing table

# ─── API keys ─────────────────────────────────────────────────────────────────
JSEARCH_API_KEY: str | None = os.getenv("JSEARCH_API_KEY")

def _split_api_keys(raw_value: str | None) -> list[str]:
    """Split comma-separated API keys and trim whitespace."""
    if not raw_value:
        return []
    return [k.strip() for k in raw_value.split(",") if k and k.strip()]


_GROQ_KEY_SINGLE_RAW = os.getenv("GROQ_API_KEY") or os.getenv("Groq_API_Key")
_GROQ_KEYS_RAW = os.getenv("GROQ_API_KEYS", "")

# Accept either:
# - GROQ_API_KEYS=key1,key2,key3
# - GROQ_API_KEY=key1,key2,key3  (legacy/single-key field now supports CSV)
_keys_combined = _split_api_keys(_GROQ_KEYS_RAW) + _split_api_keys(_GROQ_KEY_SINGLE_RAW)

# Preserve order while de-duplicating
_seen_keys: set[str] = set()
GROQ_API_KEYS: list[str] = []
for _key in _keys_combined:
    if _key not in _seen_keys:
        _seen_keys.add(_key)
        GROQ_API_KEYS.append(_key)

# Backward-compatible "single key" alias (first key if available)
GROQ_API_KEY: str | None = GROQ_API_KEYS[0] if GROQ_API_KEYS else None

GROK_API_KEY: str | None = os.getenv("GROK_API_KEY") or GROQ_API_KEY

if os.getenv("GROK_API_KEY"):
    _DEFAULT_GROK_MODEL = "grok-2-latest"
    _DEFAULT_GROK_BASE_URL = "https://api.x.ai/v1"
elif GROQ_API_KEY:
    _DEFAULT_GROK_MODEL = "llama-3.3-70b-versatile"
    _DEFAULT_GROK_BASE_URL = "https://api.groq.com/openai/v1"
else:
    _DEFAULT_GROK_MODEL = "grok-2-latest"
    _DEFAULT_GROK_BASE_URL = "https://api.x.ai/v1"

GROK_MODEL: str = os.getenv("GROK_MODEL") or os.getenv("GROQ_MODEL") or _DEFAULT_GROK_MODEL
GROK_BASE_URL: str = os.getenv("GROK_BASE_URL") or os.getenv("GROQ_BASE_URL") or _DEFAULT_GROK_BASE_URL

# ─── Google Sheets Integration ────────────────────────────────────────────────
SHEETS_ENABLED: bool = os.getenv("SHEETS_ENABLED", "false").lower() == "true"
GOOGLE_SA_JSON_PATH: str = os.getenv(
    "GOOGLE_SA_JSON_PATH",
    str(ROOT_DIR / "config" / "job-market-intelligence-489015-57c9087db0cf.json")
)
WEB_VIEWER_URL: str = os.getenv("WEB_VIEWER_URL", "http://localhost:5000")
APP_ENV: str = os.getenv("APP_ENV", "development").lower()
FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "change-me-now")
SESSION_COOKIE_SECURE: bool = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
TRUST_PROXY_HEADERS: bool = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

# Three separate spreadsheets (one per country)
# Private/edit IDs
SHEETS_CANADA_ID: str = os.getenv("SHEETS_CANADA_ID", "")
SHEETS_UK_ID: str = os.getenv("SHEETS_UK_ID", "")
SHEETS_US_ID: str = os.getenv("SHEETS_US_ID", "")

# Published IDs (from "Publish to web" in Google Sheets)
# Get these by going to: File > Share > Publish to web > Copy the ID from the URL
SHEETS_CANADA_PUBLISHED_ID: str = os.getenv("SHEETS_CANADA_PUBLISHED_ID", "")
SHEETS_UK_PUBLISHED_ID: str = os.getenv("SHEETS_UK_PUBLISHED_ID", "")
SHEETS_US_PUBLISHED_ID: str = os.getenv("SHEETS_US_PUBLISHED_ID", "")

# ─── Tracker Spreadsheet (Click Tracking) ─────────────────────────────────────
TRACKER_SPREADSHEET_ID: str = os.getenv("TRACKER_SPREADSHEET_ID", "")
TRACKER_DEPLOYMENT_BASE_URL: str | None = os.getenv("TRACKER_DEPLOYMENT_BASE_URL")
TRACKER_TOKEN: str | None = os.getenv("TRACKER_TOKEN")
