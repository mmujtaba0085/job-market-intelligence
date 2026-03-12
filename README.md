# Job Market Intelligence Engine

A modular, local-first Python intelligence engine that collects public job postings, extracts skill signals, computes weekly analytics, and generates Substack-ready reports.

---

## Quick Start

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
copy .env.example .env
# Edit .env — add JSEARCH_API_KEY if you have one (optional)

# 3. Run the full weekly pipeline
python -m src.orchestrator --mode weekly

# 4. Check outputs
# outputs/ai_ml_global/2026-09/report.md     ← your Substack post
# outputs/ai_ml_global/2026-09/top_skills.csv
# outputs/ai_ml_global/2026-09/run_summary.json
```

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

## Project Structure

```
config/          ← Markets, taxonomy, sources, settings
src/
  collectors/    ← Remotive (free) + JSearch (RapidAPI)
  storage/       ← SQLite DB + migration SQL + typed models
  analytics/     ← Weekly metrics, emerging detector, co-occurrence
  reports/       ← Markdown, HTML, CSV, charts exporters
  publisher/     ← Manual export + Substack placeholder
  normalizer.py  ← JobRaw → JobNormalized
  deduplicator.py
  skill_extractor.py
  taxonomy_mapper.py
  monetization.py
  run_manager.py
  orchestrator.py
data/            ← jobs.sqlite
outputs/         ← {market_id}/{YYYY-WW}/ weekly report files
logs/            ← {YYYY-WW}/{market_id}_{run_id}.log
tests/           ← pytest suite
```

---

## Data Sources

| Source | Type | Key required |
|--------|------|-------------|
| [Remotive API](https://remotive.com/api/remote-jobs) | REST API | No |
| [JSearch (RapidAPI)](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) | REST API | Yes (`JSEARCH_API_KEY`) |

---

## Publishing to Substack (Mode A — Default)

1. Run `--mode weekly`
2. Open `outputs/{market_id}/{YYYY-WW}/report.md`
3. Copy content → paste into Substack editor
4. Schedule / publish

> ⚠️ Automated posting is not implemented. No session/cookie automation.

---

## Scheduler (Windows Task Scheduler)

```powershell
# Weekly full pipeline (Monday 06:00)
schtasks /create /tn "JobMarket_Weekly" /tr "python -m src.orchestrator --mode weekly" /sc WEEKLY /d MON /st 06:00

# Daily ingestion only (optional)
schtasks /create /tn "JobMarket_Daily" /tr "python -m src.orchestrator --mode ingest-only" /sc DAILY /st 07:00
```

---

## Run Tests

```powershell
pytest tests/ -v
```

---

## MVP Checklist

- [ ] 1 market (`ai_ml_global`) end-to-end
- [ ] ≥ 200 jobs ingested in SQLite
- [ ] Skills extracted + normalized
- [ ] Weekly metrics computed
- [ ] `report.md` generated and readable
- [ ] Manually published to Substack
