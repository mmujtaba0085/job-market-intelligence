# 📊 Job Market Intelligence - Web Viewer

A simple Flask web application to browse and explore job market data stored in the SQLite database.

## Quick Start

1. **Install Flask** (if not already installed):
   ```bash
   pip install flask
   ```

2. **Start the web server**:
   ```bash
   python web_viewer.py
   ```

3. **Open your browser**:
   Navigate to: **http://localhost:5000**

## Features

### 🏠 Dashboard (/)
- Overview statistics (total jobs, skills, markets, weeks)
- Recent jobs at a glance
- Quick navigation to other sections

### 💼 Jobs (/jobs)
- Browse all collected jobs
- Filter by:
  - Market (e.g., ai_ml_global)
  - Remote type (Remote, Hybrid, On-site)
  - Search by title or company name
- View detailed job information

### 📄 Job Details (/jobs/<id>)
- Complete job information
- Full job description (HTML formatted)
- All detected skills with categories
- Metadata (posted date, salary, location, etc.)

### 🎯 Skills (/skills)
- Top 100 most frequent skills across all jobs
- Skills grouped by category
- Frequency and job count statistics

### 📈 Metrics (/metrics)
- Weekly trend data
- Emerging skills (fastest growing)
- Declining skills
- Historical tracking by week

## Database Location

The web viewer reads from:
```
d:\vs code\Job Market Intelligence\data\jobs.sqlite
```

Make sure you've run the pipeline at least once to populate the database:
```bash
python -m src.orchestrator --mode weekly
```

## Tech Stack

- **Backend**: Flask (Python web framework)
- **Database**: SQLite3 (no additional setup needed)
- **Frontend**: HTML5 + CSS3 (no JavaScript required)
- **Templating**: Jinja2

## Stopping the Server

Press `Ctrl+C` in the terminal where the server is running.

## Troubleshooting

**Database not found error:**
- Make sure you're running from the project root directory
- Run the pipeline first: `python -m src.orchestrator --mode weekly`

**Port already in use:**
- Change the port in `web_viewer.py` (line 245):
  ```python
  app.run(debug=True, host="localhost", port=5001)  # Change to any available port
  ```

**No data showing:**
- The database is empty. Run the ingestion pipeline to collect jobs.

## File Structure

```
Job Market Intelligence/
├── web_viewer.py           # Main Flask application
├── templates/              # HTML templates
│   ├── base.html          # Base layout with navigation
│   ├── index.html         # Dashboard
│   ├── jobs_list.html     # Jobs listing with filters
│   ├── job_detail.html    # Individual job view
│   ├── skills.html        # Skills overview
│   └── metrics.html       # Weekly metrics
└── data/
    └── jobs.sqlite        # SQLite database
```

## Development Mode

The server runs in debug mode by default:
- Auto-reloads on code changes
- Detailed error pages
- Not suitable for production use

## Next Steps

Once the server is running:
1. Browse to http://localhost:5000
2. Explore the dashboard
3. Filter jobs by market or remote type
4. Click on any job to see full details
5. Check skills and metrics sections

Enjoy exploring your job market data! 🚀
