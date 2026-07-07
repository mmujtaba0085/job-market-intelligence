# Job Market Intelligence

A Flask-based job market intelligence platform. It aggregates public job postings from ~20 sources, normalizes and deduplicates them, extracts skills, and serves the results through a BI web dashboard — plus a CLI pipeline for scheduled ingestion and Substack-style markdown reports.

Live at: https://jobs.mujtaba0085.opior.com

---

## Quick Start (local)

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
copy .env.example .env
# Edit .env — add API keys for whichever sources you want enabled (see config/sources.py)

# 3. Run the web dashboard
python web_viewer.py
# Open http://localhost:5000

# 4. (Optional) run the collection pipeline
python -m src.orchestrator --mode weekly
```

## Quick Start (Docker)

```bash
docker compose up -d web              # web dashboard, port 5000
docker compose --profile jobs run --rm pipeline   # one-off ingestion run
```

See [`deploy/VPS_DEPLOY.md`](deploy/VPS_DEPLOY.md) for the full production deployment (Docker Compose + Caddy + systemd timers).

---

## CLI Modes

| Command | What it does |
|---------|-------------|
| `python -m src.orchestrator --mode weekly` | Full pipeline (collect → analytics → report) |
| `python -m src.orchestrator --mode ingest-only` | Collect + store + extract skills only |
| `python -m src.orchestrator --mode report-only` | Recompute analytics + regenerate reports |
| `python -m src.orchestrator --backfill --start YYYY-MM-DD --end YYYY-MM-DD` | Historical reports from stored data |
| Add `--html` to any mode | Also generate `report.html` for Substack HTML editor |

---

## Frontend

The web dashboard is server-rendered Flask/Jinja2 — no separate JS frontend build. If you're changing the design, start here:

```
templates/
  base.html              ← Shared layout: header/nav, CSS theme variables, global <style> block.
                            Every page extends this via {% extends "base.html" %} and fills in
                            {% block title %}, {% block extra_styles %}, {% block content %},
                            {% block filter_sidebar %}, {% block extra_scripts %}.
  auth/login.html         ← The one page that does NOT extend base.html (pre-auth, standalone).
  index.html               ← Orphaned — "/" redirects straight to /dashboard in web_viewer.py,
                            nothing renders this file. Safe to ignore or repurpose.

  dashboard.html           /dashboard          — main BI dashboard (KPIs, trends, charts)
  jobs_list.html           /jobs               — job search/browse with filters
  job_detail.html          /jobs/<id>          — single job detail page
  jobs_quality_review.html /jobs/quality       — admin job-quality review queue
  skills.html               /skills             — skills list
  skills_intelligence.html /skills/intelligence — skill drill-down (trends, co-occurrence, companies)
  companies_intelligence.html /companies/intelligence — company drill-down
  titles_analytics.html    /titles/analytics   — normalized job-title analytics
  metrics.html              /metrics            — weekly metrics view
  api_docs.html             /api/docs           — public API documentation page
  admin_dashboard.html      /admin              — admin home
  admin_pipeline.html       /admin/pipeline     — trigger/monitor pipeline runs
  admin_pipeline_logs.html  /admin/pipeline/logs/<run_id>
  admin_quality.html        /admin/quality      — data quality overview
  admin_normalize.html      /admin/normalize    — country/location normalization tool
  admin_normalize_titles.html /admin/normalize-titles — job-title normalization tool
  admin_sheets_staging.html /admin/sheets_staging   — Google Sheets export staging
  admin_sheets_analytics.html /admin/sheets_analytics — click-tracking analytics
  auth/login.html            /auth/login
  auth/change_password.html  /auth/me/password
  auth/my_keys.html          /auth/me/keys       — self-service API key management
  auth/admin_users.html      /admin/auth/users
  auth/admin_api_keys.html   /admin/auth/keys
  auth/admin_access_logs.html /admin/auth/logs

static/
  css/filters.css   ← shared filter-bar styling, linked from base.html
  js/dashboard.js   ← dashboard widget behavior (charts, KPI cards)
  js/filters.js     ← shared filter-bar behavior (used by jobs/skills/companies pages)
```

The routes that render each template live in [`web_viewer.py`](web_viewer.py) (most pages) and in three blueprints: [`src/auth/routes.py`](src/auth/routes.py), [`src/auth/admin_routes.py`](src/auth/admin_routes.py), and [`src/sheets_routes.py`](src/sheets_routes.py) (Google Sheets admin pages). Search either file for `render_template("your_page.html"` to find the route + the data it passes to the template.

Most page-specific styling lives in `{% block extra_styles %}` inside each template rather than in `static/css/` — the shared theme (colors, header, fonts) is in `base.html`'s `:root` CSS variables, so retheming globally means editing those variables in one place.

---

## Project Structure

```
config/          ← Sources allowlist, target markets, skill taxonomy, settings/env loading
src/
  collectors/    ← One module per data source (Remotive, JSearch, Adzuna, GitHub repos, ...)
  storage/       ← SQLite schema, migrations, typed models, connection handling
  analytics/     ← Weekly metrics, emerging/declining detection, skill co-occurrence
  reports/       ← Markdown/HTML/CSV/chart exporters, Google Sheets export, click-tracker export
  auth/          ← Login, sessions, API keys, admin user management (Flask blueprints)
  normalizer.py, deduplicator.py, skill_extractor.py, taxonomy_mapper.py,
  title_normalizer.py, country_detector.py, monetization.py, run_manager.py, orchestrator.py
templates/       ← Jinja2 templates for the web dashboard (see Frontend section above)
static/          ← Shared CSS/JS for the web dashboard
web_viewer.py    ← Flask app entrypoint; most page + API routes live here
scripts/         ← Permanent, maintained tooling (migrations, backups, warehouse rollout — see scripts/README.md)
archive/         ← Frozen one-off/historical scripts and docs, kept for reference (see archive/README.md)
docs/            ← Admin guide, setup/operations, Google Sheets & click-tracking integration docs
deploy/          ← Caddyfile, systemd timers, VPS deployment guide
apify_actors/    ← Apify actor definitions (kept locally, not tracked in git)
data/            ← jobs.sqlite, auth.sqlite (not tracked in git)
outputs/         ← {market_id}/{YYYY-WW}/ weekly report files
logs/            ← {YYYY-WW}/{market_id}_{run_id}.log
tests/           ← pytest suite
```

---

## Data Sources

The platform aggregates from ~20 registered sources (public APIs and RSS/GitHub-hosted lists), most enabled by default. The authoritative, up-to-date list — including which are enabled, rate limits, and ToS notes — lives in [`config/sources.py`](config/sources.py). Notable ones:

| Source | Type | Key required |
|--------|------|-------------|
| [Remotive API](https://remotive.com/api/remote-jobs) | REST API | No |
| [JSearch (RapidAPI)](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) | REST API | Yes (`JSEARCH_API_KEY`) |
| Arbeitnow, The Muse, Himalayas, Jobicy, HireWeb3, Adzuna, Findwork, Jooble | REST APIs | Varies — see `.env.example` |
| USA Jobs | REST API | Yes (`USAJOBS_API_KEY`) |
| Several curated GitHub internship-list repos | Raw file fetch | No |
| Pakistan Jobs Bank | Crawl | No |

---

## Publishing to Substack (CLI report mode)

1. Run `python -m src.orchestrator --mode weekly`
2. Open `outputs/{market_id}/{YYYY-WW}/report.md`
3. Copy content → paste into Substack editor
4. Schedule / publish

> ⚠️ Automated posting is not implemented. No session/cookie automation.

---

## Deployment

Production runs on a VPS via Docker Compose, behind a shared Caddy reverse proxy, with systemd timers for scheduled ingestion/backups. See [`deploy/VPS_DEPLOY.md`](deploy/VPS_DEPLOY.md) for the full setup.

```powershell
# Windows Task Scheduler equivalent, for local/dev scheduling
schtasks /create /tn "JobMarket_Weekly" /tr "python -m src.orchestrator --mode weekly" /sc WEEKLY /d MON /st 06:00
```

---

## Run Tests

```powershell
pytest tests/ -v
```

---

## Further Reading

- [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md) — using the admin panel
- [`docs/SETUP_AND_OPERATIONS.md`](docs/SETUP_AND_OPERATIONS.md) — day-to-day operations
- [`docs/WEB_VIEWER_README.md`](docs/WEB_VIEWER_README.md) — web app internals
- [`docs/GOOGLE_SHEETS_INTEGRATION.md`](docs/GOOGLE_SHEETS_INTEGRATION.md), [`docs/CLICK_TRACKING_SYSTEM.md`](docs/CLICK_TRACKING_SYSTEM.md), [`docs/SHEETS_STAGING_WORKFLOW.md`](docs/SHEETS_STAGING_WORKFLOW.md) — Google Sheets export + click tracking
- [`docs/PROJECT_WORKFLOW.md`](docs/PROJECT_WORKFLOW.md), [`docs/NEW_SOURCES_README.md`](docs/NEW_SOURCES_README.md) — adding new markets/sources
- [`scripts/README.md`](scripts/README.md), [`archive/README.md`](archive/README.md) — tooling conventions
