# 🚀 ANTIGRAVITY MASTER PROMPT
## Project: Global Job Market & Skills Intelligence Engine
### *(Local-first → Cloud-ready → Substack Publishing)*

---

## 🎯 System Objective

Build a modular, configurable intelligence engine that:

- Collects public job postings (legally + ethically)
- Extracts and normalizes skill signals
- Tracks skill demand trends over time
- Detects emerging skills
- Generates automated weekly intelligence reports
- Publishes analytics to Substack
- Supports multi-market expansion without refactoring

**Non-negotiables**
- Configuration-driven (markets + skills + thresholds)
- Fault isolation (one market failing never blocks others)
- Append-only historical storage (time-series analytics)
- Local-first development (works fully on your laptop), then optional cloud deployment

---

## 🧩 Step 0 — Integration Layer (How Components Connect)

### Architecture (Modules)

| # | Module | Responsibility |
|---|--------|---------------|
| 1 | **Collectors** (per source) | Fetch job listings & job details |
| 2 | **Normalizer** | Standardizes fields (title, location, remote_type, posted_date) |
| 3 | **Skill Extractor** | Detects skills from descriptions |
| 4 | **Taxonomy Mapper** | Maps raw skills → normalized skills + categories |
| 5 | **Storage** | DB tables (jobs, skills, weekly_metrics) |
| 6 | **Analytics Engine** | Computes weekly metrics + trend signals |
| 7 | **Report Generator** | Creates Markdown + CSV + chart-ready JSON |
| 8 | **Publisher** | Substack post creation (manual or automated) |
| 9 | **Scheduler/Orchestrator** | Runs pipelines weekly (and optionally daily ingestion) |

### Interfaces (Contracts Between Modules)

Each stage must communicate via stable, versioned data contracts:

**`JobRaw`**
- `source_id`, `source_name`, `url`, `fetched_at`
- `raw_html` OR `raw_json` *(optional)*
- `parsed_fields` (title/company/location/etc.) *(optional at this stage)*

**`JobNormalized`**
- `job_id` (or hash), `market_id`, `source_name`
- `title`, `company`, `country`, `location`, `remote_type`
- `posted_date`, `salary_min`, `salary_max`, `currency`
- `description_text` *(required)*, `url`

**`SkillSignal`**
- `job_id`, `market_id`
- `raw_detected_skill`
- `normalized_skill`
- `category`
- `confidence_score` *(optional)*
- `extraction_method` (regex / taxonomy / llm)

**`WeeklyMetric`**
- `market_id`, `week_start_date`, `week_number`
- `skill_name`, `category`
- `frequency`, `growth_percentage`
- `emerging_flag`, `declining_flag` *(optional)*
- `remote_ratio` *(market-level metric)*

> This integration layer is what ensures you can add new sources or publish methods without breaking the system.

---

## 🟢 Step 1 — Configurable Market Array (Expandable Core Layer)

Create a dynamic configuration object:

```python
TARGET_MARKETS = [
    {
        "market_id": "ai_ml_global",
        "keywords": ["machine learning", "deep learning", "computer vision", "nlp", "llm"],
        "countries": ["United States", "United Kingdom", "Germany"],
        "remote_filter": False,
        "experience_levels": ["entry", "mid", "senior"],
        "salary_required": False
    }
]
```

**Requirements**

- System iterates over `TARGET_MARKETS`
- Each object runs as an independent pipeline
- Add/remove markets with no code refactor
- Scale from 1 → N markets

---

## 🟢 Step 2 — Data Collection Layer (Ethical + Reliable)

For each market, query public job sources using:
- keywords
- country filters
- remote filters

- Respect `robots.txt` and rate limits
- Prefer official APIs where available

### Collected Fields (Minimum)

| Field | Description |
|-------|-------------|
| Job Title | Title of the position |
| Company | Hiring company name |
| Location | City / region |
| Country | Country of posting |
| Remote Type | On-site / Hybrid / Remote |
| Posted Date | Date of publication |
| Salary | If available |
| Full Job Description | Raw text |
| URL | Canonical job link |
| Source | Website/source identifier |

### Rules

- Store raw descriptions **before** processing
- No normalization at scraping stage
- Fault isolation per market & per source
- Support scheduled execution

---

## 🟢 Step 3 — Skill Taxonomy Engine (Expandable Intelligence Layer)

```python
SKILL_TAXONOMY = {
    "cloud": ["aws", "azure", "gcp"],
    "programming": ["python", "java", "c++"],
    "ml_core": ["machine learning", "deep learning"],
    "data_tools": ["pandas", "spark", "airflow"]
}
```

**Requirements**

- Dynamically extendable taxonomy
- Synonyms map to normalized labels
- Store: `raw_detected_skill`, `normalized_skill`, `category`
- Support enable/disable categories without refactoring

**Optional (Highly Useful)**

Add a synonym map to reduce duplicate spellings:

```python
SKILL_SYNONYMS = {
    "k8s": "kubernetes",
    "js": "javascript",
    "tf": "terraform"
}
```

---

## 🟢 Step 4 — Database Structure (Local-first)

### Jobs Table

| Column | Type |
|--------|------|
| `job_id` | Primary Key |
| `market_id` | Foreign Key |
| `source_name` | String |
| `url` | String (unique per source) |
| `title` | String |
| `company` | String |
| `country` | String |
| `location` | String |
| `remote_type` | Enum |
| `posted_date` | Date |
| `raw_description` | Text |
| `salary_min` | Float (nullable) |
| `salary_max` | Float (nullable) |
| `currency` | String (nullable) |
| `ingested_at` | Timestamp |

### Skills Table

| Column | Type |
|--------|------|
| `job_id` | Foreign Key |
| `market_id` | Foreign Key |
| `raw_detected_skill` | String |
| `normalized_skill` | String |
| `category` | String |
| `confidence_score` | Float (nullable) |
| `method` | String |

### Weekly Metrics Table

| Column | Type |
|--------|------|
| `market_id` | Foreign Key |
| `week_start_date` | Date |
| `week_number` | Integer |
| `skill_name` | String |
| `category` | String |
| `frequency` | Integer |
| `growth_percentage` | Float |
| `emerging_flag` | Boolean |
| `declining_flag` | Boolean (optional) |

**Local-first choice**
- Start with **SQLite** (fast dev)
- Keep schema compatible with **Postgres** for cloud

---

## 🟢 Step 5 — Analytics Engine (Weekly Intelligence)

For each market:

### Core Outputs

- Top 20 skills by frequency
- Week-over-week growth (%)
- Emerging skill detection (threshold-based)
- Remote job ratio
- Experience-level distribution *(if extractable)*

### Advanced Intelligence (Optional)

- Skill co-occurrence matrix
- Skill clusters
- Declining skills
- Cross-market comparisons

### Emerging Rule (Example)

```
emerging_flag = True if:
    frequency >= MIN_FREQ
    AND growth_percentage >= GROWTH_THRESHOLD
```

---

## 🟢 Step 6 — Automation Flow (Orchestrator + Pipelines)

### Pipeline

```
Scrape → Store Raw → Extract Skills → Normalize → Update DB
       → Compute Weekly Metrics → Generate Reports → Publish
```

### Scheduling Strategy (Recommended)

| Cadence | Tasks |
|---------|-------|
| **Daily** *(optional)* | Scrape + store raw + extract skills |
| **Weekly** *(required)* | Compute weekly metrics + generate report + publish to Substack |

**Requirements**
- Independent per `market_id`
- Append-only history
- Market failure must not block other markets
- Logging per stage and per market

---

## 🟢 Step 7 — Report Generation (Substack-ready Assets)

### Outputs per market per week

| File | Purpose |
|------|---------|
| `report.md` | Substack post body |
| `top_skills.csv` | Top skill frequencies |
| `growth_skills.csv` | Fastest growing skills |
| `charts.json` | Chart-ready data for future dashboard |

### Report Structure (Template)

1. Market Overview (job count, sources, countries)
2. Top Skills
3. Fastest Growing Skills
4. Emerging Signals (new skills / spikes)
5. Remote Hiring Trend
6. Experience Distribution *(if available)*
7. Notes + Methodology *(for credibility)*

---

## 🟢 Step 8 — Substack Publishing Layer (Posting Workflow)

### Two Publishing Modes

**Mode A — Manual** *(recommended initially)*
1. Generate `report.md`
2. Copy/paste into Substack editor
3. Upload charts/images if generated

**Mode B — Automated** *(later)*
- Publisher module creates drafts/posts via supported integration method
- Keep a **"dry-run"** mode that generates files only

### Substack Output Formatting Rules

- Use clean Markdown
- Start with a 3–5 bullet executive summary
- Use tables sparingly *(Substack table support can be limited)*
- Include a short **"Data Sources + Notes"** disclaimer

---

## 🟢 Step 9 — Monetization Layer Config (Free vs Premium)

```python
MONETIZATION_MODE = {
    "free": ["top_skills", "remote_ratio"],
    "premium": ["growth_percentage", "co_occurrence", "country_breakdown", "emerging_skills"]
}
```

**Requirements**
- Automatically generate free + premium versions
- Future-ready for API gating or paid newsletter tiers

---

## 🟢 Step 10 — Local-first Build Plan (What You Run First)

### Local Working Checklist

- [ ] Configure 1 market in `TARGET_MARKETS`
- [ ] Run one collector on one source (tiny sample)
- [ ] Store jobs in SQLite
- [ ] Extract skills using taxonomy
- [ ] Compute one weekly metric snapshot
- [ ] Generate `report.md`
- [ ] Manually post to Substack

### Local Folder Outputs (Recommended)

```
/data
  jobs.sqlite
/outputs
  /ai_ml_global
    2026-W09/
      report.md
      top_skills.csv
      growth_skills.csv
      charts.json
/logs
  pipeline.log
```

---

## 🟢 Step 11 — Optional Deployment Plan (Only After Local Works)

### Cloud-ready Upgrades

- Switch SQLite → **Postgres**
- Use a scheduler (cron / GitHub Actions / cloud scheduler)
- Store outputs in object storage *(optional)*
- Add monitoring + alerts *(basic)*

> **Rule:** Do not deploy until local pipeline is stable and repeatable.

---

## 🔥 Design Principles

| Principle | Mechanism |
|-----------|-----------|
| Market scope | Controlled only by `TARGET_MARKETS` |
| Intelligence depth | Controlled by `SKILL_TAXONOMY` (+ synonyms) |
| Architecture | Modular contracts + isolated pipelines |
| Expandability | Add markets/sources without refactor |
| Publishing | Substack-ready Markdown outputs |
| Local-first | SQLite + file outputs; cloud later |
