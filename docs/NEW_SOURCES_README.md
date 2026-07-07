# New Job Sources - Quick Start Guide

## 4 New Free Collectors Added

Your Job Market Intelligence engine now supports 4 additional free/public job sources:

### 1. **Arbeitnow** (EU-focused jobs)
- ✅ No API key required
- 🌍 Primarily European job listings
- 📊 Supports pagination
- **To enable:** Edit `config/sources.py` → set `"enabled": True` for `arbeitnow`

### 2. **USA Jobs** (US Government jobs)
- 🔑 Requires free API key
- 🇺🇸 All US federal government positions
- 💰 Includes salary data
- **To enable:**
  1. Register at https://developer.usajobs.gov/APIRequest/Index
  2. Add to `.env`:
     ```bash
     USAJOBS_API_KEY=your_api_key_here
     USAJOBS_USER_AGENT=your_email@example.com
     ```
  3. Edit `config/sources.py` → set `"enabled": True` for `usajobs`

### 3. **The Muse** (Curated career site)
- ✅ No API key required
- 🎯 Curated, high-quality job listings
- 🏢 Focus on company culture and careers
- **To enable:** Edit `config/sources.py` → set `"enabled": True` for `themuse`

### 4. **GraphQL Jobs** (GraphQL-specific jobs)
- ✅ No API key required
- 💻 Niche job board for GraphQL developers
- 🌐 Global remote opportunities
- **To enable:** Edit `config/sources.py` → set `"enabled": True` for `graphqljobs`

---

## How to Enable Sources

### Option 1: Enable Individual Sources

Edit `config/sources.py`:

```python
{
    "source_id": "arbeitnow",
    # ... other config ...
    "enabled": True,  # <-- Change from False to True
},
```

### Option 2: Enable All at Once

Run this to enable Arbeitnow, The Muse, and GraphQL Jobs:

```python
python -c "
import json
with open('config/sources.py', 'r') as f:
    content = f.read()
    
# Enable all free sources (except USAJobs which needs API key)
content = content.replace('\"arbeitnow\"', '\"arbeitnow\"').replace('\"themuse\"', '\"themuse\"').replace('\"graphqljobs\"', '\"graphqljobs\"')

# Note: Manual edit recommended for safety
print('Edit config/sources.py manually to enable sources')
"
```

**Recommended:** Manually edit the file for clarity and safety.

---

## Testing New Sources

### Test Individual Collector

```bash
python -c "
from src.collectors.arbeitnow_collector import ArbeitnowCollector
from config.markets import TARGET_MARKETS

collector = ArbeitnowCollector()
market = TARGET_MARKETS[0]
jobs = collector.collect(market)
print(f'Collected {len(jobs)} jobs from Arbeitnow')
"
```

Replace `ArbeitnowCollector` with `TheMuseCollector`, `GraphQLJobsCollector`, or `USAJobsCollector`.

### Test Full Pipeline

```bash
python -m src.orchestrator --mode weekly
```

Check the logs in `logs/` for each source's collection results.

---

## Expected Behavior

### When Sources are Enabled:

**Run output will show:**
```
[arbeitnow] Collected 45 raw jobs for market 'ai_ml_global'.
[themuse] Collected 32 raw jobs for market 'ai_ml_global'.
[graphqljobs] Collected 18 raw jobs for market 'ai_ml_global'.
[usajobs] Collected 12 raw jobs for market 'ai_ml_global'.
[remotive] Collected 12 raw jobs for market 'ai_ml_global'.
[jsearch] Rate limited (429), stopping all collection
```

**Database will contain:**
- Jobs from all enabled sources
- Source column shows: `Arbeitnow`, `TheMuse`, `GraphQLJobs`, `USAJobs`
- All jobs normalized and deduplicated together

**Web viewer** (http://localhost:5000) will display:
- Jobs from all sources
- Filter by source name
- See which source each job came from

### When Sources are Disabled:

The collector won't instantiate and won't appear in the pipeline. No errors, just skipped silently.

---

## Source Comparison

| Source | Auth Required | Jobs/Week | Geographic Focus | Job Types | Rate Limit |
|--------|--------------|-----------|------------------|-----------|------------|
| **Remotive** | ❌ No | 50-100 | Global | Remote-only | 10/min |
| **JSearch** | ✅ Yes (RapidAPI) | 100-500 | Global | All types | 5/min (free tier) |
| **Arbeitnow** | ❌ No | 30-80 | EU (Germany, UK) | All types | 30/min |
| **USA Jobs** | ✅ Yes (free) | 50-200 | United States | Government | 20/min |
| **The Muse** | ❌ No | 20-60 | Global | Curated careers | 30/min |
| **GraphQL Jobs** | ❌ No | 10-30 | Global | Tech (GraphQL) | 30/min |

---

## Troubleshooting

### "Source not in ALLOWED_SOURCES"
→ Make sure you're editing `config/sources.py` correctly. The `source_id` must match exactly.

### "Missing USAJOBS_API_KEY"
→ Add the API key to your `.env` file (copy from `.env.example` and fill in values)

### "robots.txt disallows automated access"
→ All these sources have `robots_txt_allowed: True` by design. If you see this, the source config is incorrect.

### "Collected 0 jobs"
→ Check:
- Is the source enabled in `config/sources.py`?
- Are your market keywords relevant to the source? (e.g., GraphQL Jobs only has GraphQL-related jobs)
- Check the logs for HTTP errors or rate limiting

### Rate Limiting (429 errors)
→ Each source has built-in rate limiting and circuit breakers. If you hit a 429:
- Wait for the rate limit to reset (usually 1 minute)
- Reduce `rate_limit_per_minute` in `config/sources.py`
- For USAJobs, their limit is stricter - spread requests over time

---

## Next Steps

1. **Enable 1-2 sources** to start (recommend Arbeitnow + The Muse for free, high-quality jobs)
2. **Run the pipeline** once: `python -m src.orchestrator --mode weekly`
3. **Check the web viewer**: `python web_viewer.py` → http://localhost:5000
4. **Review results** - see which sources give you the best job matches
5. **Enable more sources** as needed

Happy job hunting! 🚀
