"""
Job Market Intelligence - Web Viewer
────────────────────────────────────
Enhanced Flask web app with BI Dashboard capabilities.

Usage:
    python web_viewer.py
    
Then open: http://localhost:5000
"""

from datetime import datetime, timedelta, timezone
import csv
import io
import json
import logging
import random
import re
import time
import uuid
from queue import Empty, Queue
from threading import Lock, Thread

import requests
import sqlite3
from flask import Flask, g, render_template, request, jsonify, make_response

from config.settings import FLASK_SECRET_KEY, DB_PATH as SETTINGS_DB_PATH

# Auth system
from src.auth.models import init_auth_db
from src.auth.middleware import (
    load_logged_in_user,
    log_request_access,
    get_current_user,
    require_admin,
)
from src.auth.routes import auth_bp
from src.auth.admin_routes import admin_auth_bp

# Google Sheets integration
from src.sheets_routes import register_sheets_routes

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
DB_PATH = SETTINGS_DB_PATH
logger = logging.getLogger(__name__)

# Run DB migrations on startup so the web app is never behind
from src.storage.db import run_migrations as _run_migrations
_run_migrations()

# ── Register auth blueprints ──────────────────────────────────────────────────
app.register_blueprint(auth_bp, url_prefix="/auth")
app.register_blueprint(admin_auth_bp)

# ── Auth hooks ────────────────────────────────────────────────────────────────
app.before_request(load_logged_in_user)
app.after_request(log_request_access)


@app.context_processor
def inject_current_user():
    from src.auth.middleware import csrf_token as _csrf_token
    return {"current_user": get_current_user(), "csrf_token": _csrf_token}


# ── Global auth gate ──────────────────────────────────────────────────────────
_PUBLIC_PATHS = {"/healthz", "/auth/login", "/auth/logout"}
_PUBLIC_PREFIXES = ("/static/",)


_ADMIN_PREFIXES = ("/admin",)
_MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SCOPE_MAP = (
    ("/api/jobs", "jobs:read"),
    ("/api/markets", "markets:read"),
    ("/api/sources", "sources:read"),
    ("/api/dashboard", "analytics:read"),
    ("/api/skills", "analytics:read"),
    ("/api/companies", "analytics:read"),
    ("/api/titles", "analytics:read"),
    ("/api/filters", "analytics:read"),
    ("/export/", "exports:read"),
)


@app.before_request
def global_auth_gate():
    from flask import redirect, url_for
    from src.auth.middleware import _is_api_request, api_key_has_scope
    path = request.path
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return

    user = getattr(g, "current_user", None)
    auth_type = getattr(g, "auth_type", None)

    if not user:
        if auth_type == "api_key_rate_limited":
            return jsonify({"error": "Rate limit exceeded"}), 429
        if _is_api_request():
            return jsonify({"error": "Unauthorized",
                            "hint": "Provide X-API-Key or Authorization: Bearer header"}), 401
        return redirect(url_for("auth.login", next=request.url))

    is_admin = user.get("role") == "admin"
    is_api_key = auth_type == "api_key"

    # Admin routes: session + admin only
    if any(path.startswith(p) for p in _ADMIN_PREFIXES):
        if is_api_key or not is_admin:
            return jsonify({"error": "Forbidden"}), 403

    # API keys are read-only
    if is_api_key and request.method in _MUTATION_METHODS:
        return jsonify({"error": "Forbidden — API keys are read-only"}), 403

    # API key scope enforcement
    if is_api_key:
        required = next((s for prefix, s in _SCOPE_MAP if path.startswith(prefix)), None)
        if required and not api_key_has_scope(user, required):
            return jsonify({"error": f"Forbidden — requires scope: {required}"}), 403


# ── Initialise auth DB on startup ─────────────────────────────────────────────
init_auth_db()


def get_db_connection():
    """Get SQLite database connection. Falls back to .shadow.sqlite if main is unavailable."""
    from pathlib import Path as _Path
    candidates = [DB_PATH, _Path(str(DB_PATH).replace(".sqlite", ".shadow.sqlite"))]
    last_err = None
    for p in candidates:
        if not _Path(str(p)).exists():
            continue
        try:
            conn = sqlite3.connect(str(p), timeout=30)
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM jobs LIMIT 1")
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
    raise sqlite3.OperationalError(f"Cannot open any DB: {last_err}")


COUNTRY_ALIASES = {
    "uk": "United Kingdom",
    "u.k": "United Kingdom",
    "great britain": "United Kingdom",
    "us": "United States",
    "usa": "United States",
    "u.s": "United States",
    "u.s.a": "United States",
    "ca": "Canada",
}


def _normalize_country_name(country: str | None) -> str:
    if not country:
        return ""
    value = country.strip()
    return COUNTRY_ALIASES.get(value.lower(), value)


def _infer_remote_type(title: str, location: str, country: str, current: str | None) -> str:
    merged = f"{title} {location} {country}".lower()
    current_val = (current or "").strip().lower()
    if "hybrid" in merged or current_val == "hybrid":
        return "hybrid"
    if "on-site" in merged or "onsite" in merged or current_val in {"on-site", "onsite"}:
        return "on-site"
    if "remote" in merged or current_val == "remote":
        return "remote"
    return current or "unknown"


def _extract_split_candidates(description: str) -> list[dict]:
    """Extract possible multi-job entries from one description."""
    if not description:
        return []

    titles = re.findall(r"(?im)^\s*(?:title|role)\s*[:\-]\s*(.+)$", description)
    companies = re.findall(r"(?im)^\s*company\s*[:\-]\s*(.+)$", description)
    locations = re.findall(r"(?im)^\s*location\s*[:\-]\s*(.+)$", description)

    max_len = max(len(titles), len(companies), len(locations))
    if max_len < 2:
        return []

    candidates = []
    for idx in range(max_len):
        title = titles[idx].strip() if idx < len(titles) else ""
        company = companies[idx].strip() if idx < len(companies) else ""
        location = locations[idx].strip() if idx < len(locations) else ""
        if title or company or location:
            candidates.append({
                "title": title,
                "company": company,
                "location": location,
            })
    return candidates


def _analyze_job_quality_row(row: sqlite3.Row) -> dict:
    title = (row["title"] or "").strip()
    company = (row["company"] or "").strip()
    location = (row["location"] or "").strip()
    country = (row["country"] or "").strip()
    remote_type = (row["remote_type"] or "").strip()
    description = (row["raw_description"] or "").strip()

    normalized_country = _normalize_country_name(country)
    normalized_remote = _infer_remote_type(title, location, normalized_country, remote_type)

    if location.lower() in {"unknown", "unknow", "n/a"} and description:
        match = re.search(r"(?im)^\s*location\s*[:\-]\s*(.+)$", description)
        if match:
            location = match.group(1).strip()

    split_candidates = _extract_split_candidates(description)

    flags = []
    if not title:
        flags.append("missing_title")
    if not company:
        flags.append("missing_company")
    if not location or location.lower() in {"unknown", "unknow", "n/a"}:
        flags.append("missing_or_unknown_location")
    if normalized_country != country and normalized_country:
        flags.append("country_normalized")
    if normalized_remote != remote_type:
        flags.append("remote_type_normalized")
    if split_candidates:
        flags.append("multi_job_candidate")

    return {
        "job_id": row["job_id"],
        "source_name": row["source_name"],
        "posted_date": row["posted_date"],
        "current": {
            "title": title,
            "company": company,
            "location": (row["location"] or ""),
            "country": country,
            "remote_type": remote_type,
            "description": description,
        },
        "suggested": {
            "title": title,
            "company": company,
            "location": location,
            "country": normalized_country or country,
            "remote_type": normalized_remote,
        },
        "flags": flags,
        "split_candidates": split_candidates,
    }


def _ai_enhance_quality_rows(
    analyzed_rows: list[dict],
    api_keys: list[str],
    model: str,
    base_url: str,
) -> tuple[dict[int, dict], str | None, dict]:
    """Call Groq/Grok-compatible chat endpoint with multi-key parallel workers."""
    instruction = (
        "You are improving job data quality. Return strict JSON object with key 'results'. "
        "Each result must include: job_id, suggested_title, suggested_company, suggested_location, suggested_country, "
        "suggested_remote_type, split_candidates (array), confidence (0-1), reasons (array). "
        "Use full country names and infer remote_type from title/location/description where obvious."
    )

    chunk_size = 8
    max_description_chars = 1400
    max_retries = 4
    inter_chunk_delay = 0.9
    worker_count = max(1, min(len(api_keys), 6))
    serial_mode = worker_count == 1
    serial_chunk_delay = 3.0
    deferred_retries_per_chunk = 3
    all_results: dict[int, dict] = {}
    chunk_errors: list[str] = []
    lock = Lock()

    def _mask_key(value: str) -> str:
        if not value:
            return "<empty>"
        tail = value[-6:] if len(value) >= 6 else value
        return f"***{tail}"

    if not api_keys:
        return {}, "No AI API keys configured", {
            "workers": 0,
            "chunks_total": 0,
            "chunks_succeeded": 0,
            "chunks_failed": 0,
            "processed_rows": 0,
            "worker_key_hints": {},
            "requests_by_worker": {},
        }

    chunks: list[list[dict]] = [
        analyzed_rows[i:i + chunk_size]
        for i in range(0, len(analyzed_rows), chunk_size)
    ]

    work_queue: Queue = Queue()
    for idx, chunk in enumerate(chunks, start=1):
        work_queue.put((idx, chunk, deferred_retries_per_chunk))

    stats = {
        "workers": worker_count,
        "chunks_total": len(chunks),
        "chunks_succeeded": 0,
        "chunks_failed": 0,
        "processed_rows": 0,
        "worker_key_hints": {},
        "requests_by_worker": {},
    }

    def worker(api_key: str, worker_name: str) -> None:
        with lock:
            stats["worker_key_hints"][worker_name] = _mask_key(api_key)
            stats["requests_by_worker"].setdefault(worker_name, 0)

        while True:
            try:
                chunk_idx, chunk, retries_left = work_queue.get_nowait()
            except Empty:
                return

            input_rows = []
            for row in chunk:
                description = row["current"].get("description", "") or ""
                input_rows.append({
                    "job_id": row["job_id"],
                    "title": row["current"].get("title", ""),
                    "company": row["current"].get("company", ""),
                    "location": row["current"].get("location", ""),
                    "country": row["current"].get("country", ""),
                    "remote_type": row["current"].get("remote_type", ""),
                    "description": description[:max_description_chars],
                })

            payload = {
                "model": model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": json.dumps({"rows": input_rows}, ensure_ascii=False)},
                ],
            }

            chunk_ok = False
            last_error: str | None = None
            for attempt in range(max_retries):
                try:
                    with lock:
                        stats["requests_by_worker"][worker_name] += 1

                    response = requests.post(
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=90,
                    )

                    if response.status_code == 429:
                        retry_after = None
                        header_val = response.headers.get("Retry-After")
                        if header_val:
                            try:
                                retry_after = float(header_val)
                            except (TypeError, ValueError):
                                retry_after = None

                        wait = min(22, (retry_after if retry_after is not None else (2 ** attempt)) + random.uniform(0.4, 1.4))
                        time.sleep(wait)
                        continue

                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]

                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        start = content.find("{")
                        end = content.rfind("}")
                        if start == -1 or end == -1 or end <= start:
                            last_error = "AI returned non-JSON output"
                            break
                        parsed = json.loads(content[start:end + 1])

                    results = parsed.get("results", [])
                    with lock:
                        for item in results:
                            try:
                                all_results[int(item.get("job_id"))] = item
                            except Exception:
                                continue
                        stats["chunks_succeeded"] += 1
                        stats["processed_rows"] += len(chunk)

                    chunk_ok = True
                    break
                except Exception as e:
                    last_error = str(e)
                    if attempt == max_retries - 1:
                        pass
                    else:
                        time.sleep(min(12, (2 ** attempt) + random.uniform(0.2, 0.8)))

            if not chunk_ok:
                if retries_left > 0:
                    # Requeue failed chunks for another pass after a cooldown window.
                    # This helps both serial and parallel workers survive temporary 429 spikes.
                    cooldown = serial_chunk_delay if serial_mode else max(3.0, inter_chunk_delay * 4)
                    time.sleep(cooldown)
                    work_queue.put((chunk_idx, chunk, retries_left - 1))
                else:
                    with lock:
                        chunk_errors.append(f"chunk {chunk_idx} ({worker_name}): {last_error or 'unknown AI error'}")
                        stats["chunks_failed"] += 1

            time.sleep(serial_chunk_delay if serial_mode else inter_chunk_delay)
            work_queue.task_done()

    threads: list[Thread] = []
    for i in range(worker_count):
        key = api_keys[i % len(api_keys)]
        t = Thread(target=worker, args=(key, f"worker-{i+1}"), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()
    error_message = "; ".join(chunk_errors) if chunk_errors else None
    return all_results, error_message, stats


@app.route("/")
def index():
    """Home page with database overview."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get counts
    cursor.execute("SELECT COUNT(*) as count FROM jobs")
    total_jobs = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(*) as count FROM skills")
    total_skills = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(DISTINCT market_id) as count FROM jobs")
    total_markets = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(DISTINCT week_start_date) as count FROM weekly_metrics")
    total_weeks = cursor.fetchone()["count"]
    
    # Get recent jobs
    cursor.execute("""
        SELECT job_id, title, company, location, remote_type, posted_date, source_name
        FROM jobs 
        ORDER BY ingested_at DESC 
        LIMIT 10
    """)
    recent_jobs = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        "index.html",
        total_jobs=total_jobs,
        total_skills=total_skills,
        total_markets=total_markets,
        total_weeks=total_weeks,
        recent_jobs=recent_jobs
    )


@app.route("/dashboard")
def dashboard():
    """BI Dashboard with interactive widgets."""
    return render_template("dashboard.html")


@app.route("/healthz")
def healthz():
    """Container/web health probe with a lightweight SQLite check."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "ok", "db": "ok"}), 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("[healthz] DB check failed: %s", exc)
        return jsonify({"status": "degraded", "db": "error", "error": str(exc)}), 503


@app.route("/api/dashboard/kpis")
def dashboard_kpis():
    """Get KPI metrics for dashboard."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Total jobs
    cursor.execute("SELECT COUNT(*) as count FROM jobs")
    total_jobs = cursor.fetchone()["count"]
    
    # Total skills
    cursor.execute("SELECT COUNT(DISTINCT normalized_skill) as count FROM skills")
    total_skills = cursor.fetchone()["count"]
    
    # Active sources
    cursor.execute("SELECT COUNT(DISTINCT source_name) as count FROM jobs")
    active_sources = cursor.fetchone()["count"]
    
    # Remote percentage
    cursor.execute("""
        SELECT 
            CAST(SUM(CASE WHEN LOWER(remote_type) = 'remote' THEN 1 ELSE 0 END) AS FLOAT) * 100 / COUNT(*) as pct
        FROM jobs
    """)
    remote_pct = cursor.fetchone()["pct"] or 0
    
    # Trend arrows (vs last week if data available)
    # Get current week and prior week
    cursor.execute("""
        SELECT DISTINCT week_start_date FROM weekly_metrics
        ORDER BY week_start_date DESC LIMIT 2
    """)
    weeks = [row["week_start_date"] for row in cursor.fetchall()]
    
    jobs_trend = "→"
    skills_trend = "→"
    
    if len(weeks) >= 2:
        current_week = weeks[0]
        prior_week = weeks[1]
        
        # Compare job counts
        cursor.execute("""
            SELECT COUNT(DISTINCT job_id) as count
            FROM jobs
            WHERE first_seen_at >= ?
        """, (current_week,))
        current_jobs = cursor.fetchone()["count"]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT job_id) as count
            FROM jobs
            WHERE first_seen_at >= ? AND first_seen_at < ?
        """, (prior_week, current_week))
        prior_jobs = cursor.fetchone()["count"]
        
        if current_jobs > prior_jobs:
            jobs_trend = "↑"
        elif current_jobs < prior_jobs:
            jobs_trend = "↓"
        
        # Compare skill counts
        cursor.execute("""
            SELECT COUNT(DISTINCT skill_name) as count
            FROM weekly_metrics
            WHERE week_start_date = ?
        """, (current_week,))
        current_skills = cursor.fetchone()["count"]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT skill_name) as count
            FROM weekly_metrics
            WHERE week_start_date = ?
        """, (prior_week,))
        prior_skills = cursor.fetchone()["count"]
        
        if current_skills > prior_skills:
            skills_trend = "↑"
        elif current_skills < prior_skills:
            skills_trend = "↓"
    
    conn.close()
    
    return jsonify({
        "total_jobs": total_jobs,
        "total_skills": total_skills,
        "active_sources": active_sources,
        "remote_pct": round(remote_pct, 1),
        "jobs_trend": jobs_trend,
        "skills_trend": skills_trend
    })


@app.route("/api/dashboard/trends")
def dashboard_trends():
    """Get job posting trends over last 8-12 weeks using week_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get weekly job counts by week_id (last 12 weeks)
    cursor.execute("""
        SELECT 
            week_id,
            COUNT(DISTINCT job_id) as job_count
        FROM jobs
        WHERE week_id IS NOT NULL AND week_id != 'unknown'
        GROUP BY week_id
        ORDER BY week_id DESC
        LIMIT 12
    """)
    
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        return jsonify({"labels": [], "values": []})
    
    # Reverse to show oldest to newest (for left-to-right timeline)
    results = list(reversed(results))
    
    return jsonify({
        "labels": [row["week_id"] for row in results],
        "values": [row["job_count"] for row in results]
    })


@app.route("/api/dashboard/top-skills")
def dashboard_top_skills():
    """Get top 10 skills for current period."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get latest week
    cursor.execute("""
        SELECT skill_name, frequency, category
        FROM weekly_metrics
        WHERE week_start_date = (SELECT MAX(week_start_date) FROM weekly_metrics)
        ORDER BY frequency DESC
        LIMIT 10
    """)
    
    skills = [{"skill": row["skill_name"], "count": row["frequency"], "category": row["category"]} 
              for row in cursor.fetchall()]
    
    if not skills:
        # Fallback to all-time top skills
        cursor.execute("""
            SELECT normalized_skill as skill, COUNT(*) as count, category
            FROM skills
            GROUP BY normalized_skill, category
            ORDER BY count DESC
            LIMIT 10
        """)
        skills = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return jsonify(skills)


@app.route("/api/dashboard/geo")
def dashboard_geo():
    """Get geographic distribution."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT country, COUNT(*) as count
        FROM jobs
        WHERE country IS NOT NULL AND country != ''
        GROUP BY country
        ORDER BY count DESC
        LIMIT 15
    """)
    
    geo = [{"country": row["country"], "count": row["count"]} 
           for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(geo)


@app.route("/api/dashboard/sources")
def dashboard_sources():
    """Get source performance breakdown."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT source_name, COUNT(*) as count
        FROM jobs
        GROUP BY source_name
        ORDER BY count DESC
    """)
    
    sources = [{"source": row["source_name"], "count": row["count"]} 
               for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(sources)


@app.route("/api/dashboard/emerging")
def dashboard_emerging():
    """Get emerging skills."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT skill_name, category, frequency, growth_percentage
        FROM weekly_metrics
        WHERE emerging_flag = 1
        ORDER BY week_start_date DESC, growth_percentage DESC
        LIMIT 10
    """)
    
    emerging = [{"skill": row["skill_name"], "category": row["category"], 
                 "frequency": row["frequency"], "growth": row["growth_percentage"]} 
                for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(emerging)


@app.route("/api/dashboard/declining")
def dashboard_declining():
    """Get declining skills."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT skill_name, category, frequency, growth_percentage
        FROM weekly_metrics
        WHERE declining_flag = 1
        ORDER BY week_start_date DESC, growth_percentage ASC
        LIMIT 10
    """)
    
    declining = [{"skill": row["skill_name"], "category": row["category"], 
                  "frequency": row["frequency"], "growth": row["growth_percentage"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(declining)


@app.route("/api/dashboard/companies")
def dashboard_companies():
    """Get top hiring companies."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT company, COUNT(*) as count
        FROM jobs
        WHERE company IS NOT NULL AND company != ''
        GROUP BY company
        ORDER BY count DESC
        LIMIT 10
    """)
    
    companies = [{"company": row["company"], "count": row["count"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(companies)


@app.route("/api/dashboard/location-diversity")
def dashboard_location_diversity():
    """Get companies with jobs in most locations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            company, 
            MAX(location_count) as max_locations,
            COUNT(DISTINCT job_group_id) as job_count
        FROM jobs
        WHERE location_count > 1
        GROUP BY company
        ORDER BY max_locations DESC, job_count DESC
        LIMIT 10
    """)
    
    diversity = [
        {
            "company": row["company"], 
            "max_locations": row["max_locations"],
            "job_count": row["job_count"]
        } 
        for row in cursor.fetchall()
    ]
    conn.close()
    
    return jsonify(diversity)


@app.route("/skills/intelligence")
def skills_intelligence():
    """Skills Intelligence Page with detailed analytics."""
    return render_template("skills_intelligence.html")


@app.route("/api/skills/search")
def skills_search():
    """Search skills with autocomplete."""
    query = request.args.get("q", "").lower()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT normalized_skill, category, COUNT(*) as count
        FROM skills
        WHERE LOWER(normalized_skill) LIKE ?
        GROUP BY normalized_skill, category
        ORDER BY count DESC
        LIMIT 20
    """, (f"%{query}%",))
    
    skills = [{"skill": row["normalized_skill"], "category": row["category"], "count": row["count"]} 
              for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(skills)


@app.route("/api/skills/<skill_name>/details")
def skill_details(skill_name):
    """Get detailed information about a specific skill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Total mentions
    cursor.execute("""
        SELECT COUNT(*) as count, category
        FROM skills
        WHERE normalized_skill = ?
        GROUP BY category
    """, (skill_name,))
    
    result = cursor.fetchone()
    if not result:
        conn.close()
        return jsonify({"error": "Skill not found"}), 404
    
    total_mentions = result["count"]
    category = result["category"]
    
    # Job count
    cursor.execute("""
        SELECT COUNT(DISTINCT job_id) as count
        FROM skills
        WHERE normalized_skill = ?
    """, (skill_name,))
    job_count = cursor.fetchone()["count"]
    
    # Average per job
    avg_per_job = total_mentions / job_count if job_count > 0 else 0
    
    # Latest week data
    cursor.execute("""
        SELECT frequency, growth_percentage, emerging_flag, declining_flag
        FROM weekly_metrics
        WHERE skill_name = ?
        ORDER BY week_start_date DESC
        LIMIT 1
    """, (skill_name,))
    
    latest = cursor.fetchone()
    latest_freq = latest["frequency"] if latest else 0
    growth = latest["growth_percentage"] if latest else 0
    is_emerging = latest["emerging_flag"] if latest else 0
    is_declining = latest["declining_flag"] if latest else 0
    
    conn.close()
    
    return jsonify({
        "skill": skill_name,
        "category": category,
        "total_mentions": total_mentions,
        "job_count": job_count,
        "avg_per_job": round(avg_per_job, 2),
        "current_frequency": latest_freq,
        "growth_percentage": round(growth, 1) if growth else 0,
        "is_emerging": bool(is_emerging),
        "is_declining": bool(is_declining)
    })


@app.route("/api/skills/<skill_name>/trends")
def skill_trends(skill_name):
    """Get 8-week trend history for a skill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT week_start_date, frequency, growth_percentage
        FROM weekly_metrics
        WHERE skill_name = ?
        ORDER BY week_start_date DESC
        LIMIT 8
    """, (skill_name,))
    
    trends = [{"week": row["week_start_date"], "frequency": row["frequency"], 
               "growth": row["growth_percentage"]} 
              for row in cursor.fetchall()]
    conn.close()
    
    # Reverse to show oldest to newest
    trends.reverse()
    
    return jsonify(trends)


@app.route("/api/skills/<skill_name>/co-occurring")
def skill_co_occurring(skill_name):
    """Get skills that frequently co-occur with this skill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s2.normalized_skill, COUNT(*) as co_count
        FROM skills s1
        JOIN skills s2 ON s1.job_id = s2.job_id
        WHERE s1.normalized_skill = ? AND s2.normalized_skill != ?
        GROUP BY s2.normalized_skill
        ORDER BY co_count DESC
        LIMIT 15
    """, (skill_name, skill_name))
    
    co_occurring = [{"skill": row["normalized_skill"], "count": row["co_count"]} 
                    for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(co_occurring)


@app.route("/api/skills/<skill_name>/companies")
def skill_companies(skill_name):
    """Get top companies hiring for this skill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT j.company, COUNT(DISTINCT j.job_id) as job_count
        FROM jobs j
        JOIN skills s ON j.job_id = s.job_id
        WHERE s.normalized_skill = ? AND j.company IS NOT NULL AND j.company != ''
        GROUP BY j.company
        ORDER BY job_count DESC
        LIMIT 10
    """, (skill_name,))
    
    companies = [{"company": row["company"], "count": row["job_count"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(companies)


@app.route("/api/skills/<skill_name>/locations")
def skill_locations(skill_name):
    """Get geographic distribution for this skill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT j.country, COUNT(DISTINCT j.job_id) as job_count
        FROM jobs j
        JOIN skills s ON j.job_id = s.job_id
        WHERE s.normalized_skill = ? AND j.country IS NOT NULL AND j.country != ''
        GROUP BY j.country
        ORDER BY job_count DESC
        LIMIT 10
    """, (skill_name,))
    
    locations = [{"country": row["country"], "count": row["job_count"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(locations)


@app.route("/api/skills/combinations")
def skill_combinations():
    """Get top skill pairs/combinations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s1.normalized_skill as skill_a, s2.normalized_skill as skill_b, COUNT(*) as co_count
        FROM skills s1
        JOIN skills s2 ON s1.job_id = s2.job_id
        WHERE s1.normalized_skill < s2.normalized_skill
        GROUP BY s1.normalized_skill, s2.normalized_skill
        ORDER BY co_count DESC
        LIMIT 50
    """)
    
    combinations = [{"skill_a": row["skill_a"], "skill_b": row["skill_b"], "count": row["co_count"]} 
                    for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(combinations)


@app.route("/companies/intelligence")
def companies_intelligence():
    """Company Intelligence Page."""
    return render_template("companies_intelligence.html")


@app.route("/api/companies/list")
def companies_list():
    """Get all companies with statistics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            j.company,
            COUNT(DISTINCT j.job_id) as job_count,
            COUNT(DISTINCT s.normalized_skill) as skill_diversity,
            COUNT(DISTINCT j.country) as location_count,
            SUM(CASE WHEN LOWER(j.remote_type) = 'remote' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as remote_pct
        FROM jobs j
        LEFT JOIN skills s ON j.job_id = s.job_id
        WHERE j.company IS NOT NULL AND j.company != ''
        GROUP BY j.company
        HAVING job_count >= 2
        ORDER BY job_count DESC
        LIMIT 100
    """)
    
    companies = [{"company": row["company"], "job_count": row["job_count"], 
                  "skill_diversity": row["skill_diversity"], "location_count": row["location_count"],
                  "remote_pct": round(row["remote_pct"], 1)} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(companies)


@app.route("/api/companies/<company>/details")
def company_details(company):
    """Get detailed company information."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Job count
    cursor.execute("""
        SELECT COUNT(*) as count FROM jobs WHERE company = ?
    """, (company,))
    job_count = cursor.fetchone()["count"]
    
    # Skill diversity
    cursor.execute("""
        SELECT COUNT(DISTINCT s.normalized_skill) as count
        FROM skills s
        JOIN jobs j ON s.job_id = j.job_id
        WHERE j.company = ?
    """, (company,))
    skill_count = cursor.fetchone()["count"]
    
    # Remote percentage
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN LOWER(remote_type) = 'remote' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as pct
        FROM jobs
        WHERE company = ?
    """, (company,))
    remote_pct = cursor.fetchone()["pct"] or 0
    
    # Location count
    cursor.execute("""
        SELECT COUNT(DISTINCT country) as count
        FROM jobs
        WHERE company = ? AND country IS NOT NULL
    """, (company,))
    location_count = cursor.fetchone()["count"]
    
    conn.close()
    
    return jsonify({
        "company": company,
        "job_count": job_count,
        "skill_diversity": skill_count,
        "remote_pct": round(remote_pct, 1),
        "location_count": location_count
    })


@app.route("/api/companies/<company>/tech-stack")
def company_tech_stack(company):
    """Get company's preferred tech stack (top skills)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.normalized_skill, s.category, COUNT(*) as count
        FROM skills s
        JOIN jobs j ON s.job_id = j.job_id
        WHERE j.company = ?
        GROUP BY s.normalized_skill, s.category
        ORDER BY count DESC
        LIMIT 15
    """, (company,))
    
    skills = [{"skill": row["normalized_skill"], "category": row["category"], "count": row["count"]} 
              for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(skills)


@app.route("/api/companies/<company>/locations")
def company_locations(company):
    """Get company's hiring locations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT country, COUNT(*) as count
        FROM jobs
        WHERE company = ? AND country IS NOT NULL AND country != ''
        GROUP BY country
        ORDER BY count DESC
    """, (company,))
    
    locations = [{"country": row["country"], "count": row["count"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(locations)


@app.route("/titles/analytics")
def titles_analytics():
    """Job Titles Analytics Page."""
    return render_template("titles_analytics.html")


@app.route("/api/titles/top")
def titles_top():
    """Get top job titles (using normalized titles for consolidation)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            normalized_title as title,
            COUNT(*) as count,
            COUNT(DISTINCT title) as variant_count
        FROM jobs
        WHERE normalized_title IS NOT NULL AND normalized_title != ''
        GROUP BY normalized_title
        ORDER BY count DESC
        LIMIT 30
    """)
    
    titles = [{"title": row["title"], "count": row["count"], "variant_count": row["variant_count"]} 
              for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(titles)


@app.route("/api/titles/<title>/skills")
def title_skills(title):
    """Get skills required for a specific job title (using normalized title)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT s.normalized_skill, s.category, COUNT(*) as count
        FROM skills s
        JOIN jobs j ON s.job_id = j.job_id
        WHERE j.normalized_title = ?
        GROUP BY s.normalized_skill, s.category
        ORDER BY count DESC
        LIMIT 15
    """, (title,))
    
    skills = [{"skill": row["normalized_skill"], "category": row["category"], "count": row["count"]} 
              for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(skills)


@app.route("/api/filters/skills")
def get_skills_filter():
    """Get all unique skills for filter."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT normalized_skill, category, COUNT(*) as count
        FROM skills
        GROUP BY normalized_skill, category
        ORDER BY count DESC, normalized_skill
    """)
    
    skills = [{"skill": row["normalized_skill"], "category": row["category"], "count": row["count"]} 
              for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(skills)


@app.route("/api/filters/countries")
def get_countries_filter():
    """Get all unique countries for filter."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT country, COUNT(*) as count
        FROM jobs
        WHERE country IS NOT NULL AND country != ''
        GROUP BY country
        ORDER BY count DESC
    """)
    
    countries = [{"country": row["country"], "count": row["count"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(countries)


@app.route("/api/filters/sources")
def get_sources_filter():
    """Get all unique sources for filter."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT source_name, COUNT(*) as count
        FROM jobs
        GROUP BY source_name
        ORDER BY count DESC
    """)
    
    sources = [{"source": row["source_name"], "count": row["count"]} 
               for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(sources)


@app.route("/api/filters/companies")
def get_companies_filter():
    """Get all unique companies for filter (autocomplete)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    search = request.args.get("q", "")
    
    if search:
        cursor.execute("""
            SELECT DISTINCT company, COUNT(*) as count
            FROM jobs
            WHERE company LIKE ? AND company IS NOT NULL AND company != ''
            GROUP BY company
            ORDER BY count DESC
            LIMIT 50
        """, (f"%{search}%",))
    else:
        cursor.execute("""
            SELECT DISTINCT company, COUNT(*) as count
            FROM jobs
            WHERE company IS NOT NULL AND company != ''
            GROUP BY company
            ORDER BY count DESC
            LIMIT 100
        """)
    
    companies = [{"company": row["company"], "count": row["count"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(companies)


@app.route("/api/filters/categories")
def get_categories_filter():
    """Get all unique skill categories for filter."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT category, COUNT(*) as count
        FROM skills
        WHERE category IS NOT NULL
        GROUP BY category
        ORDER BY count DESC
    """)
    
    categories = [{"category": row["category"], "count": row["count"]} 
                  for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(categories)


@app.route("/jobs")
def jobs_list():
    """List jobs with filters, status selector, and pagination."""
    conn = get_db_connection()
    cursor = conn.cursor()

    PER_PAGE = 100

    # Filters
    market_filter  = request.args.get("market", "")
    remote_filter  = request.args.get("remote_type", "")
    search_query   = request.args.get("search", "")
    country_filter = request.args.get("country", "")
    source_filter  = request.args.get("source", "")
    company_filter = request.args.get("company", "")
    skills_filter  = request.args.getlist("skills")
    date_from      = request.args.get("date_from", "")
    date_to        = request.args.get("date_to", "")
    current_status = request.args.get("status", "active")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    base = """
        SELECT DISTINCT j.job_id, j.title, j.company, j.location, j.country,
               j.remote_type, j.posted_date, j.source_name, j.market_id, j.location_count
        FROM jobs j
        WHERE 1=1
    """
    params = []

    # Status filter
    if current_status == "active":
        base += " AND (j.listing_status IS NULL OR j.listing_status = 'active')"
    elif current_status == "unverified":
        base += " AND j.listing_status IN ('historical','unverified')"
    elif current_status == "closed":
        base += " AND j.listing_status = 'closed'"
    # 'all' → no filter

    if market_filter:
        base += " AND j.market_id = ?"; params.append(market_filter)
    if remote_filter:
        base += " AND j.remote_type = ?"; params.append(remote_filter)
    if country_filter:
        base += " AND j.country = ?"; params.append(country_filter)
    if source_filter:
        base += " AND j.source_name = ?"; params.append(source_filter)
    if company_filter:
        base += " AND j.company LIKE ?"; params.append(f"%{company_filter}%")
    if search_query:
        base += " AND (j.title LIKE ? OR j.company LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%"])
    if date_from:
        base += " AND j.posted_date >= ?"; params.append(date_from)
    if date_to:
        base += " AND j.posted_date <= ?"; params.append(date_to)
    for i, skill in enumerate(skills_filter):
        base += f" AND EXISTS (SELECT 1 FROM skills s{i} WHERE s{i}.job_id = j.job_id AND s{i}.normalized_skill = ?)"
        params.append(skill)

    # Total count
    count_row = cursor.execute(f"SELECT COUNT(*) FROM ({base})", params).fetchone()
    total_jobs = count_row[0] if count_row else 0
    total_pages = max(1, (total_jobs + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    cursor.execute(base + " ORDER BY j.posted_date DESC, j.ingested_at DESC LIMIT ? OFFSET ?",
                   params + [PER_PAGE, offset])
    jobs = cursor.fetchall()

    # Dropdown data — markets as objects with depth+name
    cursor.execute("""
        SELECT m.market_id as id, m.name,
               (LENGTH(m.market_id) - LENGTH(REPLACE(m.market_id,'.','')))/1 as depth
        FROM markets m ORDER BY m.market_id
    """)
    market_rows = cursor.fetchall()
    if not market_rows:
        # Fallback: derive from jobs table
        cursor.execute("SELECT DISTINCT market_id FROM jobs WHERE market_id IS NOT NULL ORDER BY market_id")
        markets = [{"id": r["market_id"], "name": r["market_id"], "depth": 0} for r in cursor.fetchall()]
    else:
        markets = [{"id": r["id"], "name": r["name"], "depth": r["depth"]} for r in market_rows]

    cursor.execute("SELECT DISTINCT remote_type FROM jobs WHERE remote_type IS NOT NULL ORDER BY remote_type")
    remote_types = [r["remote_type"] for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT country FROM jobs WHERE country IS NOT NULL AND country != '' ORDER BY country")
    countries = [r["country"] for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT source_name FROM jobs ORDER BY source_name")
    sources = [r["source_name"] for r in cursor.fetchall()]

    conn.close()

    # Pagination URLs
    def page_url(p):
        args = request.args.copy()
        args["page"] = p
        return request.path + "?" + "&".join(f"{k}={v}" for k, v in args.items(multi=True))

    return render_template(
        "jobs_list.html",
        jobs=jobs,
        total_jobs=total_jobs,
        markets=markets,
        remote_types=remote_types,
        countries=countries,
        sources=sources,
        current_market=market_filter,
        current_remote=remote_filter,
        current_country=country_filter,
        current_source=source_filter,
        current_company=company_filter,
        current_status=current_status,
        search_query=search_query,
        skills_filter=skills_filter,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
        prev_url=page_url(page - 1) if page > 1 else None,
        next_url=page_url(page + 1) if page < total_pages else None,
    )


@app.route("/jobs/quality")
def jobs_quality_review():
    """Quality review workspace for missing/ambiguous job data with description-aware suggestions."""
    conn = get_db_connection()
    cursor = conn.cursor()

    country_filter = request.args.get("country", "")
    limit = int(request.args.get("limit", "200"))
    limit = max(10, min(limit, 3000))

    query = """
        SELECT job_id, title, company, location, country, remote_type, posted_date, source_name, raw_description
        FROM jobs
        WHERE (
            title IS NULL OR TRIM(title) = ''
            OR company IS NULL OR TRIM(company) = ''
            OR location IS NULL OR TRIM(location) = ''
            OR LOWER(TRIM(location)) IN ('unknown', 'unknow', 'n/a')
            OR LOWER(TRIM(country)) IN ('uk', 'u.k', 'us', 'usa', 'ca')
            OR LOWER(COALESCE(remote_type, '')) NOT IN ('remote', 'hybrid', 'on-site', 'unknown')
            OR raw_description LIKE '%Title:%\n%Title:%'
            OR raw_description LIKE '%Role:%\n%Role:%'
        )
    """
    params = []

    if country_filter:
        query += " AND country = ?"
        params.append(country_filter)

    query += " ORDER BY posted_date DESC, job_id DESC LIMIT ?"
    params.append(limit)

    rows = cursor.execute(query, params).fetchall()
    countries = cursor.execute("SELECT DISTINCT country FROM jobs WHERE country IS NOT NULL AND TRIM(country) != '' ORDER BY country").fetchall()
    conn.close()

    return render_template(
        "jobs_quality_review.html",
        rows=rows,
        countries=[r["country"] for r in countries],
        current_country=country_filter,
        current_limit=limit,
    )


@app.route("/api/jobs")
def api_jobs_list():
    """JSON list of jobs — requires jobs:read scope for API keys."""
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT job_id, title, company, location, country, remote_type, "
            "posted_date, source_name, url FROM jobs ORDER BY ingested_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return jsonify({"jobs": [dict(r) for r in rows], "limit": limit, "offset": offset})
    finally:
        conn.close()


@app.route("/api/jobs/quality/analyze", methods=["POST"])
def api_jobs_quality_analyze():
    """Analyze selected jobs and return improved data suggestions + split candidates."""
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Forbidden — admin only"}), 403
    payload = request.get_json() or {}
    job_ids = payload.get("job_ids") or []

    if not job_ids:
        return jsonify({"success": False, "error": "No job_ids provided"}), 400

    conn = get_db_connection()
    placeholders = ",".join(["?"] * len(job_ids))
    rows = conn.execute(
        f"""
        SELECT job_id, title, company, location, country, remote_type, posted_date, source_name, raw_description
        FROM jobs
        WHERE job_id IN ({placeholders})
        ORDER BY posted_date DESC, job_id DESC
        """,
        job_ids,
    ).fetchall()
    conn.close()

    analyzed = [_analyze_job_quality_row(row) for row in rows]

    from config.settings import GROQ_API_KEYS, GROK_API_KEY, GROK_MODEL, GROK_BASE_URL
    use_ai = bool(payload.get("use_ai", True))
    ai_used = False
    ai_error = None
    ai_stats = None

    api_keys = [k for k in (GROQ_API_KEYS or []) if k]
    if not api_keys and GROK_API_KEY:
        api_keys = [GROK_API_KEY]

    if use_ai and api_keys:
        ai_by_job_id, ai_error, ai_stats = _ai_enhance_quality_rows(analyzed, api_keys, GROK_MODEL, GROK_BASE_URL)
        if ai_by_job_id:
            ai_used = True
            for row in analyzed:
                ai_row = ai_by_job_id.get(int(row["job_id"]))
                if not ai_row:
                    continue

                suggested = row["suggested"]
                suggested["title"] = (ai_row.get("suggested_title") or suggested.get("title") or "").strip()
                suggested["company"] = (ai_row.get("suggested_company") or suggested.get("company") or "").strip()
                suggested["location"] = (ai_row.get("suggested_location") or suggested.get("location") or "").strip()
                suggested["country"] = _normalize_country_name(ai_row.get("suggested_country") or suggested.get("country") or "")
                suggested["remote_type"] = (ai_row.get("suggested_remote_type") or suggested.get("remote_type") or "unknown").strip()

                ai_splits = ai_row.get("split_candidates")
                if isinstance(ai_splits, list) and ai_splits:
                    row["split_candidates"] = ai_splits
                    if "multi_job_candidate" not in row["flags"]:
                        row["flags"].append("multi_job_candidate")

                reasons = ai_row.get("reasons")
                if isinstance(reasons, list) and reasons:
                    row["ai_reasons"] = reasons
        else:
            logger.warning("[jobs_quality] AI enhancement failed: %s", ai_error)

    return jsonify({
        "success": True,
        "results": analyzed,
        "count": len(analyzed),
        "ai_used": ai_used,
        "ai_error": ai_error,
        "ai_stats": ai_stats,
    })


@app.route("/api/jobs/quality/apply", methods=["POST"])
@require_admin
def api_jobs_quality_apply():
    """Apply accepted quality fixes and create new rows for accepted split candidates."""
    payload = request.get_json() or {}
    updates = payload.get("updates") or []

    if not updates:
        return jsonify({"success": False, "error": "No updates provided"}), 400

    conn = get_db_connection()
    updated = 0
    created_splits = 0
    try:
        # Lazy imports to keep startup path light.
        import re
        from src.normalizer import normalize
        from src.storage.db import upsert_job
        from src.storage.models import JobRaw

        def _source_id_from_name(source_name: str) -> str:
            cleaned = re.sub(r"[^a-z0-9]+", "_", (source_name or "").lower()).strip("_")
            return cleaned or "quality_split"

        def _clean_split_value(value: str, fallback: str = "") -> str:
            return (value or fallback or "").strip()

        split_jobs_to_insert = []

        for item in updates:
            job_id = item.get("job_id")
            if not job_id:
                continue

            conn.execute(
                """
                UPDATE jobs
                SET title = ?,
                    company = ?,
                    location = ?,
                    country = ?,
                    remote_type = ?
                WHERE job_id = ?
                """,
                (
                    (item.get("title") or "").strip(),
                    (item.get("company") or "").strip(),
                    (item.get("location") or "").strip(),
                    (item.get("country") or "").strip(),
                    (item.get("remote_type") or "unknown").strip(),
                    int(job_id),
                ),
            )
            updated += 1

            split_candidates = item.get("split_candidates") or []
            if not isinstance(split_candidates, list) or not split_candidates:
                continue

            base_row = conn.execute(
                """
                SELECT job_id, market_id, source_name, url, raw_description, posted_date,
                       salary_min, salary_max, currency
                FROM jobs
                WHERE job_id = ?
                """,
                (int(job_id),),
            ).fetchone()

            if not base_row:
                continue

            for split in split_candidates:
                if not isinstance(split, dict):
                    continue

                split_title = _clean_split_value(split.get("title"))
                if not split_title:
                    continue

                split_company = _clean_split_value(split.get("company"), item.get("company"))
                split_location = _clean_split_value(split.get("location"), item.get("location"))
                split_country = _clean_split_value(split.get("country"), item.get("country"))
                split_remote = _clean_split_value(split.get("remote_type"), item.get("remote_type") or "unknown")

                # Split jobs need distinct URLs so they are not de-duplicated as exact url_hash matches.
                base_url = (base_row["url"] or "").strip()
                if not base_url:
                    continue
                separator = "&" if "?" in base_url else "?"
                split_url = f"{base_url}{separator}split_job={uuid.uuid4().hex[:10]}"

                raw = JobRaw(
                    source_id=_source_id_from_name(base_row["source_name"]),
                    source_name=base_row["source_name"],
                    url=split_url,
                    fetched_at=datetime.now(timezone.utc),
                    parsed_fields={
                        "title": split_title,
                        "company": split_company,
                        "location": split_location,
                        "country": split_country,
                        "remote_type": split_remote,
                        "posted_date": base_row["posted_date"] or "",
                        "salary_min": base_row["salary_min"],
                        "salary_max": base_row["salary_max"],
                        "currency": base_row["currency"],
                        "description": base_row["raw_description"] or "",
                        "url": split_url,
                    },
                )

                normalized = normalize(raw, market_id=base_row["market_id"])
                if normalized:
                    split_jobs_to_insert.append(normalized)

        conn.commit()

        # Insert split rows after commit to avoid lock contention with the main
        # transaction; upsert_job opens its own DB connection internally.
        for split_job in split_jobs_to_insert:
            try:
                _, status = upsert_job(split_job)
                if status == "inserted":
                    created_splits += 1
            except Exception as split_exc:
                logger.warning("[jobs_quality] split insert failed: %s", split_exc)

        return jsonify({"success": True, "updated": updated, "created_splits": created_splits})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    """Show full job details including description and all locations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM jobs WHERE job_id = ?
    """, (job_id,))
    job = cursor.fetchone()
    
    if not job:
        conn.close()
        return "Job not found", 404
    
    # Get skills for this job
    cursor.execute("""
        SELECT raw_detected_skill, normalized_skill, category, confidence_score
        FROM skills 
        WHERE job_id = ?
        ORDER BY category, normalized_skill
    """, (job_id,))
    skills = cursor.fetchall()
    
    # Get all locations for this job (multi-location support)
    locations = []
    if job["job_group_id"]:
        cursor.execute("""
            SELECT location, country, remote_type, 
                   salary_min, salary_max, currency,
                   first_seen_at, last_seen_at
            FROM job_locations
            WHERE job_group_id = ?
            ORDER BY first_seen_at
        """, (job["job_group_id"],))
        locations = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        "job_detail.html",
        job=job,
        skills=skills,
        locations=locations
    )


@app.route("/api/jobs/<int:job_id>/locations")
def job_locations_api(job_id):
    """API endpoint to get all locations for a job."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get job_group_id
    cursor.execute("SELECT job_group_id FROM jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    
    if not row or not row["job_group_id"]:
        conn.close()
        return jsonify({"error": "Job not found or no location data"}), 404
    
    # Get all locations
    cursor.execute("""
        SELECT location, country, remote_type,
               salary_min, salary_max, currency,
               first_seen_at, last_seen_at
        FROM job_locations
        WHERE job_group_id = ?
        ORDER BY first_seen_at
    """, (row["job_group_id"],))
    
    locations = [dict(loc) for loc in cursor.fetchall()]
    conn.close()
    
    return jsonify({"locations": locations, "count": len(locations)})


@app.route("/skills")
def skills_overview():
    """Overview of all detected skills."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get skill frequency across all jobs
    cursor.execute("""
        SELECT 
            normalized_skill,
            category,
            COUNT(*) as frequency,
            COUNT(DISTINCT job_id) as job_count
        FROM skills
        GROUP BY normalized_skill, category
        ORDER BY frequency DESC
        LIMIT 100
    """)
    top_skills = cursor.fetchall()
    
    # Get category breakdown
    cursor.execute("""
        SELECT 
            category,
            COUNT(*) as count
        FROM skills
        GROUP BY category
        ORDER BY count DESC
    """)
    categories = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        "skills.html",
        top_skills=top_skills,
        categories=categories
    )


@app.route("/metrics")
def metrics_overview():
    """Weekly metrics and trends."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get latest week metrics
    cursor.execute("""
        SELECT DISTINCT week_start_date, market_id
        FROM weekly_metrics
        ORDER BY week_start_date DESC
        LIMIT 10
    """)
    weeks = cursor.fetchall()
    
    # Get emerging skills (latest week)
    cursor.execute("""
        SELECT skill_name, category, frequency, growth_percentage, week_start_date, market_id
        FROM weekly_metrics
        WHERE emerging_flag = 1
        ORDER BY week_start_date DESC, growth_percentage DESC
        LIMIT 20
    """)
    emerging = cursor.fetchall()
    
    # Get declining skills (latest week)
    cursor.execute("""
        SELECT skill_name, category, frequency, growth_percentage, week_start_date, market_id
        FROM weekly_metrics
        WHERE declining_flag = 1
        ORDER BY week_start_date DESC, growth_percentage ASC
        LIMIT 20
    """)
    declining = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        "metrics.html",
        weeks=weeks,
        emerging=emerging,
        declining=declining
    )


@app.route("/export/jobs")
def export_jobs():
    """Export jobs to CSV."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all jobs (or apply filters if provided)
    cursor.execute("""
        SELECT job_id, title, company, location, country, remote_type, 
               posted_date, source_name, salary_min, salary_max, currency
        FROM jobs
        ORDER BY posted_date DESC
    """)
    
    jobs = cursor.fetchall()
    conn.close()
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(['Job ID', 'Title', 'Company', 'Location', 'Country', 'Remote Type', 
                     'Posted Date', 'Source', 'Salary Min', 'Salary Max', 'Currency'])
    
    # Data
    for job in jobs:
        writer.writerow([
            job['job_id'], job['title'], job['company'], job['location'],
            job['country'], job['remote_type'], job['posted_date'], job['source_name'],
            job['salary_min'], job['salary_max'], job['currency']
        ])
    
    # Create response
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=jobs_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    
    return response


@app.route("/export/skills")
def export_skills():
    """Export skills frequency to CSV."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT normalized_skill, category, COUNT(*) as frequency
        FROM skills
        GROUP BY normalized_skill, category
        ORDER BY frequency DESC
    """)
    
    skills = cursor.fetchall()
    conn.close()
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Skill', 'Category', 'Frequency'])
    
    for skill in skills:
        writer.writerow([skill['normalized_skill'], skill['category'], skill['frequency']])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=skills_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    
    return response


@app.route("/export/companies")
def export_companies():
    """Export companies to CSV."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT company, COUNT(*) as job_count
        FROM jobs
        WHERE company IS NOT NULL AND company != ''
        GROUP BY company
        ORDER BY job_count DESC
    """)
    
    companies = cursor.fetchall()
    conn.close()
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Company', 'Job Count'])
    
    for company in companies:
        writer.writerow([company['company'], company['job_count']])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=companies_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    
    return response


@app.template_filter("truncate_html")
def truncate_html(text, length=200):
    """Truncate HTML text safely."""
    if not text:
        return ""
    # Simple truncation (doesn't handle HTML tags perfectly but good enough)
    if len(text) <= length:
        return text
    return text[:length] + "..."


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: DATA NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/normalize")
def admin_normalize():
    """Admin panel for normalizing country and location data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get country statistics
    cursor.execute("""
        SELECT country, COUNT(*) as count
        FROM jobs
        WHERE country IS NOT NULL AND country != ''
        GROUP BY country
        ORDER BY count DESC
    """)
    countries = [{"value": row["country"], "count": row["count"]} for row in cursor.fetchall()]
    
    # Get location statistics
    cursor.execute("""
        SELECT location, COUNT(*) as count
        FROM jobs
        WHERE location IS NOT NULL AND location != ''
        GROUP BY location
        ORDER BY count DESC
        LIMIT 200
    """)
    locations = [{"value": row["location"], "count": row["count"]} for row in cursor.fetchall()]
    
    # Get total unique locations count
    cursor.execute("""
        SELECT COUNT(DISTINCT location) as total
        FROM jobs
        WHERE location IS NOT NULL AND location != ''
    """)
    total_locations = cursor.fetchone()["total"]
    
    conn.close()
    
    return render_template(
        "admin_normalize.html",
        countries=countries,
        locations=locations,
        total_locations=total_locations
    )


@app.route("/admin/normalize/preview", methods=["POST"])
def admin_normalize_preview():
    """Preview normalization changes before applying."""
    data = request.get_json()
    country_mappings = data.get("country_mappings", {})
    location_mappings = data.get("location_mappings", {})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    preview = {
        "countries": [],
        "locations": []
    }
    
    # Preview country changes
    for old_value, new_value in country_mappings.items():
        if old_value and new_value and old_value != new_value:
            cursor.execute(
                "SELECT COUNT(*) as count FROM jobs WHERE country = ?",
                (old_value,)
            )
            count = cursor.fetchone()["count"]
            preview["countries"].append({
                "old": old_value,
                "new": new_value,
                "count": count
            })
    
    # Preview location changes
    for old_value, new_value in location_mappings.items():
        if old_value and new_value and old_value != new_value:
            cursor.execute(
                "SELECT COUNT(*) as count FROM jobs WHERE location = ?",
                (old_value,)
            )
            count = cursor.fetchone()["count"]
            preview["locations"].append({
                "old": old_value,
                "new": new_value,
                "count": count
            })
    
    conn.close()
    
    return jsonify(preview)


@app.route("/admin/normalize/apply", methods=["POST"])
def admin_normalize_apply():
    """Apply normalization mappings to database."""
    data = request.get_json()
    country_mappings = data.get("country_mappings", {})
    location_mappings = data.get("location_mappings", {})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    results = {
        "countries_updated": 0,
        "locations_updated": 0,
        "changes": []
    }
    
    # Apply country normalizations
    for old_value, new_value in country_mappings.items():
        if old_value and new_value and old_value != new_value:
            cursor.execute(
                "UPDATE jobs SET country = ? WHERE country = ?",
                (new_value, old_value)
            )
            count = cursor.rowcount
            if count > 0:
                results["countries_updated"] += count
                results["changes"].append(f"Country: '{old_value}' → '{new_value}' ({count} jobs)")
    
    # Apply location normalizations
    for old_value, new_value in location_mappings.items():
        if old_value and new_value and old_value != new_value:
            cursor.execute(
                "UPDATE jobs SET location = ? WHERE location = ?",
                (new_value, old_value)
            )
            count = cursor.rowcount
            if count > 0:
                results["locations_updated"] += count
                results["changes"].append(f"Location: '{old_value}' → '{new_value}' ({count} jobs)")
    
    conn.commit()
    conn.close()
    
    return jsonify(results)


@app.route("/admin/normalize/sample", methods=["POST"])
def admin_normalize_sample():
    """Get sample jobs for a specific country or location value."""
    data = request.get_json()
    field = data.get("field")  # "country" or "location"
    value = data.get("value")
    
    if field not in ["country", "location"]:
        return jsonify({"error": "Invalid field"}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = f"""
        SELECT title, company, country, location, remote_type
        FROM jobs
        WHERE {field} = ?
        LIMIT 10
    """
    
    cursor.execute(query, (value,))
    samples = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return jsonify({"samples": samples})


@app.route("/admin/normalize/suggest-country", methods=["POST"])
def admin_normalize_suggest_country():
    """Auto-suggest country name from location data for Unknown countries."""
    data = request.get_json() or {}
    country_value = data.get("country_value") or ""

    if country_value.lower() not in ["unknown", "null", ""]:
        return jsonify({"suggestion": None, "message": "Only works for Unknown countries"})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get location patterns for Unknown country jobs
    cursor.execute("""
        SELECT location, COUNT(*) as count
        FROM jobs
        WHERE (country = ? OR country IS NULL OR country = '')
          AND location IS NOT NULL 
          AND location != ''
          AND location != 'Unknown'
        GROUP BY location
        ORDER BY count DESC
        LIMIT 50
    """, (country_value,))
    
    locations = cursor.fetchall()
    conn.close()
    
    if not locations:
        return jsonify({"suggestion": None, "message": "No location data available"})
    
    # Country inference patterns
    country_patterns = [
        (["united states", "usa", "us", ", ca", ", ny", ", tx", ", fl", "california", "new york", "texas"], "United States"),
        (["united kingdom", "uk", "london", "england", "scotland", "wales"], "United Kingdom"),
        (["germany", "berlin", "munich", "hamburg", "deutschland"], "Germany"),
        (["canada", "toronto", "vancouver", "montreal"], "Canada"),
        (["france", "paris", "lyon"], "France"),
        (["australia", "sydney", "melbourne"], "Australia"),
        (["netherlands", "amsterdam", "rotterdam"], "Netherlands"),
        (["spain", "madrid", "barcelona"], "Spain"),
        (["italy", "rome", "milan"], "Italy"),
        (["india", "bangalore", "mumbai", "delhi"], "India"),
        (["singapore"], "Singapore"),
        (["japan", "tokyo"], "Japan"),
        (["remote", "anywhere", "worldwide", "global"], "Remote/Global"),
    ]
    
    # Analyze locations and suggest most likely country
    country_scores = {}
    total_jobs = sum(loc["count"] for loc in locations)
    
    for location_row in locations:
        location = location_row["location"].lower()
        count = location_row["count"]
        
        for patterns, country in country_patterns:
            if any(pattern in location for pattern in patterns):
                country_scores[country] = country_scores.get(country, 0) + count
    
    if not country_scores:
        return jsonify({
            "suggestion": None, 
            "message": "Could not infer country from location data",
            "sample_locations": [loc["location"] for loc in locations[:10]]
        })
    
    # Get top suggestion
    suggested_country = max(country_scores, key=country_scores.get)
    suggested_count = country_scores[suggested_country]
    confidence = (suggested_count / total_jobs) * 100
    
    return jsonify({
        "suggestion": suggested_country,
        "confidence": round(confidence, 1),
        "affected_jobs": total_jobs,
        "matched_jobs": suggested_count,
        "sample_locations": [loc["location"] for loc in locations[:10]],
        "warning": "This shows the MOST COMMON country, but individual jobs may be from different countries."
    })


@app.route("/admin/normalize/auto-fix-unknown", methods=["POST"])
def admin_normalize_auto_fix_unknown():
    """Auto-fix Unknown countries by analyzing each job's location with weighted voting."""
    from src.country_detector import detect_country, should_auto_apply, get_confidence_label, GEOPY_AVAILABLE
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all jobs with Unknown country
    cursor.execute("""
        SELECT job_id, location
        FROM jobs
        WHERE (country = 'Unknown' OR country IS NULL OR country = '')
          AND location IS NOT NULL
          AND location != ''
          AND location != 'Unknown'
    """)
    
    unknown_jobs = cursor.fetchall()
    
    if not unknown_jobs:
        conn.close()
        return jsonify({
            "success": True,
            "updated": 0,
            "message": "No Unknown countries with location data found"
        })
    
    # Process each job individually with weighted voting
    updates = {}  # country -> count
    confidence_stats = {"high": 0, "medium": 0, "low": 0, "failed": 0}
    updated_count = 0
    debug_samples = []  # Track first 10 for debugging
    
    for job in unknown_jobs:
        job_id = job["job_id"]
        location = job["location"]
        
        # Use weighted voting detection
        country, confidence = detect_country(location, use_geopy=True)
        
        # Debug: collect samples
        if len(debug_samples) < 10:
            debug_samples.append({
                "location": location,
                "country": country,
                "confidence": confidence
            })
        
        if country and should_auto_apply(confidence):
            # Only apply if confidence is above threshold (uses MIN_CONFIDENCE from country_detector)
            cursor.execute(
                "UPDATE jobs SET country = ? WHERE job_id = ?",
                (country, job_id)
            )
            updated_count += cursor.rowcount
            updates[country] = updates.get(country, 0) + 1
            
            # Track confidence level
            conf_label = get_confidence_label(confidence)
            confidence_stats[conf_label] += 1
        elif country:
            # Country detected but confidence too low
            confidence_stats["low"] += 1
        else:
            # Could not detect country
            confidence_stats["failed"] += 1
    
    conn.commit()
    conn.close()
    
    # Build summary
    summary = []
    for country, count in sorted(updates.items(), key=lambda x: x[1], reverse=True):
        summary.append(f"{country}: {count} jobs")
    
    # Build confidence summary
    conf_summary = (
        f"High confidence: {confidence_stats['high']}, "
        f"Medium confidence: {confidence_stats['medium']}, "
        f"Low confidence (not applied): {confidence_stats['low']}, "
        f"Failed to detect: {confidence_stats['failed']}"
    )
    
    return jsonify({
        "success": True,
        "updated": updated_count,
        "total_unknown": len(unknown_jobs),
        "breakdown": updates,
        "confidence_stats": confidence_stats,
        "summary": summary,
        "confidence_summary": conf_summary,
        "debug_samples": debug_samples,  # First 10 locations for debugging
        "geopy_available": GEOPY_AVAILABLE,
        "message": f"Updated {updated_count} out of {len(unknown_jobs)} Unknown jobs using weighted voting"
    })


# ═══════════════════════════════════════════════════════════════════
# ADMIN: MAIN DASHBOARD
# ═══════════════════════════════════════════════════════════════════

@app.route("/admin/quality")
def admin_quality_redirect():
    """Alias so /admin/quality reaches the job quality review panel (returns 200)."""
    return jobs_quality_review()


@app.route("/admin")
def admin_dashboard():
    """Main admin dashboard with links to all admin panels."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Total jobs
    cursor.execute("SELECT COUNT(*) as count FROM jobs")
    total_jobs = cursor.fetchone()["count"]
    
    # Unknown countries
    cursor.execute("""
        SELECT COUNT(*) as count FROM jobs 
        WHERE country = 'Unknown'
    """)
    unknown_countries = cursor.fetchone()["count"]
    
    # Normalized titles
    cursor.execute("""
        SELECT COUNT(*) as count FROM jobs 
        WHERE normalization_confidence > 0.0
    """)
    normalized_titles = cursor.fetchone()["count"]
    
    # Low-confidence titles
    cursor.execute("""
        SELECT COUNT(*) as count FROM jobs 
        WHERE normalization_confidence > 0.0 AND normalization_confidence < 0.6
    """)
    low_conf_titles = cursor.fetchone()["count"]
    
    conn.close()
    
    return render_template(
        "admin_dashboard.html",
        total_jobs=total_jobs,
        unknown_countries=unknown_countries,
        normalized_titles=normalized_titles,
        low_conf_titles=low_conf_titles
    )


# ═══════════════════════════════════════════════════════════════════
# ADMIN: TITLE NORMALIZATION
# ═══════════════════════════════════════════════════════════════════

@app.route("/admin/normalize-titles")
def admin_normalize_titles():
    """Admin panel for reviewing and managing title normalizations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get filter parameters
    filter_type = request.args.get("filter", "all")  # all, manual, low_conf, high_freq
    limit = int(request.args.get("limit", "500"))  # Default 500, user can adjust
    
    # Build query based on filter
    base_query = """
        SELECT 
            title,
            normalized_title,
            COUNT(*) as count,
            ROUND(AVG(normalization_confidence) * 100, 1) as avg_conf,
            MAX(CASE WHEN normalization_confidence = 1.0 THEN 1 ELSE 0 END) as is_manual
        FROM jobs
        GROUP BY title, normalized_title
        HAVING count >= 2
    """
    
    if filter_type == "manual":
        base_query += " AND MAX(CASE WHEN normalization_confidence = 1.0 THEN 1 ELSE 0 END) = 1"
    elif filter_type == "low_conf":
        base_query += " AND ROUND(AVG(normalization_confidence) * 100, 1) < 60 AND ROUND(AVG(normalization_confidence) * 100, 1) > 0"
    elif filter_type == "high_freq":
        base_query += " AND COUNT(*) >= 10"
    
    base_query += " ORDER BY count DESC LIMIT ?"
    
    cursor.execute(base_query, (limit,))
    titles = cursor.fetchall()
    
    # Total unique titles
    cursor.execute("SELECT COUNT(DISTINCT title) as count FROM jobs")
    total_titles = cursor.fetchone()["count"]
    
    # Count titles with ≥2 jobs
    cursor.execute("SELECT COUNT(*) FROM (SELECT title FROM jobs GROUP BY title HAVING COUNT(*) >= 2)")
    titles_with_multiple = cursor.fetchone()[0]
    
    # Stats
    cursor.execute("SELECT COUNT(*) as count FROM jobs WHERE normalization_confidence > 0.0")
    normalized_count = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(DISTINCT normalized_title) as count FROM jobs WHERE normalization_confidence > 0.0")
    unique_normalized = cursor.fetchone()["count"]
    
    # Count manually normalized titles
    cursor.execute("SELECT COUNT(DISTINCT title) FROM jobs WHERE normalization_confidence = 1.0")
    manual_count = cursor.fetchone()[0]
    
    conn.close()
    
    return render_template(
        "admin_normalize_titles.html",
        titles=titles,
        total_titles=total_titles,
        titles_with_multiple=titles_with_multiple,
        normalized_count=normalized_count,
        unique_normalized=unique_normalized,
        manual_count=manual_count,
        current_filter=filter_type,
        current_limit=limit
    )


@app.route("/api/admin/titles/preview", methods=["POST"])
@require_admin
def api_admin_titles_preview():
    """Preview title normalization changes before applying."""
    data = request.get_json()
    title_mappings = data.get("title_mappings", {})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    changes = []
    for old_title, new_normalized in title_mappings.items():
        cursor.execute(
            "SELECT COUNT(*) as count FROM jobs WHERE title = ?",
            (old_title,)
        )
        count = cursor.fetchone()["count"]
        
        if count > 0:
            changes.append({
                "old": old_title,
                "new": new_normalized,
                "count": count
            })
    
    conn.close()
    
    return jsonify({"changes": changes})


@app.route("/api/admin/titles/apply", methods=["POST"])
@require_admin
def api_admin_titles_apply():
    """Apply title normalization mappings to database."""
    data = request.get_json()
    title_mappings = data.get("title_mappings", {})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    updated = 0
    changes = []
    
    for old_title, new_normalized in title_mappings.items():
        cursor.execute(
            """UPDATE jobs 
               SET normalized_title = ?, 
                   normalization_confidence = 1.0
               WHERE title = ?""",
            (new_normalized, old_title)
        )
        
        count = cursor.rowcount
        if count > 0:
            updated += count
            changes.append(f"{old_title} → {new_normalized} ({count} jobs)")
    
    conn.commit()
    conn.close()
    
    return jsonify({
        "success": True,
        "updated": updated,
        "changes": changes
    })


@app.route("/api/admin/titles/revert", methods=["POST"])
@require_admin
def api_admin_titles_revert():
    """Revert manually normalized titles back to automatic normalization."""
    from src.title_normalizer import normalize_title
    
    data = request.get_json()
    titles = data.get("titles", [])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    reverted = 0
    changes = []
    
    for title in titles:
        # Re-normalize using automatic method
        normalized_title, confidence = normalize_title(title)
        
        cursor.execute(
            """UPDATE jobs 
               SET normalized_title = ?, 
                   normalization_confidence = ?
               WHERE title = ?""",
            (normalized_title, confidence, title)
        )
        
        count = cursor.rowcount
        if count > 0:
            reverted += count
            changes.append(f"{title} → {normalized_title} (confidence: {confidence:.0%}, {count} jobs)")
    
    conn.commit()
    conn.close()
    
    return jsonify({
        "success": True,
        "reverted": reverted,
        "changes": changes
    })


@app.route("/api/admin/titles/sample", methods=["POST"])
@require_admin
def api_admin_titles_sample():
    """Get sample jobs for a specific title."""
    data = request.get_json()
    title = data.get("title", "")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT title, normalized_title, company, location, remote_type, posted_date
        FROM jobs
        WHERE title = ?
        LIMIT 10
    """, (title,))
    
    samples = []
    for row in cursor.fetchall():
        samples.append({
            "title": row["title"],
            "normalized_title": row["normalized_title"],
            "company": row["company"],
            "location": row["location"],
            "remote_type": row["remote_type"],
            "posted_date": row["posted_date"]
        })
    
    conn.close()
    
    return jsonify({"samples": samples})


@app.route("/api/admin/titles/suggest-similar", methods=["POST"])
@require_admin
def api_admin_titles_suggest_similar():
    """Find similar titles that could be consolidated."""
    from difflib import SequenceMatcher
    
    data = request.get_json()
    title = data.get("title", "")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all titles
    cursor.execute("""
        SELECT DISTINCT title, normalized_title, COUNT(*) as count
        FROM jobs
        GROUP BY title
        HAVING count >= 2
    """)
    
    all_titles = cursor.fetchall()
    conn.close()
    
    # Find similar titles (>80% similarity)
    similar = []
    title_lower = title.lower()
    
    for row in all_titles:
        other_title = row["title"]
        if other_title == title:
            continue
        
        similarity = SequenceMatcher(None, title_lower, other_title.lower()).ratio()
        if similarity > 0.8:
            similar.append({
                "title": other_title,
                "normalized_title": row["normalized_title"],
                "count": row["count"],
                "similarity": round(similarity * 100, 1)
            })
    
    # Sort by similarity
    similar.sort(key=lambda x: x["similarity"], reverse=True)
    
    return jsonify({
        "title": title,
        "similar": similar[:10],  # Top 10
        "count": len(similar)
    })


@app.route("/api/admin/normalize-titles/export")
@require_admin
def api_admin_normalize_export():
    """Export all title mappings as CSV."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT
            title as raw_title,
            normalized_title,
            normalization_confidence
        FROM jobs
        WHERE normalized_title != title
        ORDER BY normalized_title, title
    """)
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["raw_title", "normalized_title", "confidence"])
    
    for row in cursor.fetchall():
        writer.writerow([
            row["raw_title"],
            row["normalized_title"],
            round(row["normalization_confidence"], 3)
        ])
    
    conn.close()
    
    # Create response
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=title_mappings.csv"
    response.headers["Content-Type"] = "text/csv"

    return response


@app.template_filter("format_date")
def format_date(date_str):
    """Format ISO date string."""
    if not date_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return date_str


@app.template_filter("number_format")
def number_format(value):
    """Format number with thousands separator."""
    if value is None:
        return "0"
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return str(value)


# ─── Google Sheets Routes ─────────────────────────────────────────────────────
register_sheets_routes(app, get_db_connection)


# ═══════════════════════════════════════════════════════════════════
# ADMIN: PIPELINE MONITOR
# ═══════════════════════════════════════════════════════════════════

@app.route("/admin/pipeline")
@require_admin
def admin_pipeline():
    from src.pipeline_monitor import compute_next_run, get_config, get_recent_runs, get_running_runs
    config = get_config()
    runs = get_recent_runs(40)
    running = get_running_runs()
    next_ingest = compute_next_run("ingest-only", config)
    next_crawl  = compute_next_run("crawl", config)
    return render_template(
        "admin_pipeline.html",
        runs=runs,
        running=running,
        config=config,
        next_ingest=next_ingest,
        next_crawl=next_crawl,
    )


@app.route("/admin/pipeline/run", methods=["POST"])
@require_admin
def admin_pipeline_run():
    from src.pipeline_monitor import get_running_runs, launch_pipeline
    mode = request.form.get("mode", "ingest-only")
    if mode not in ("weekly", "ingest-only", "report-only", "crawl"):
        return jsonify({"error": "invalid mode"}), 400
    # Prevent duplicate concurrent runs of the same mode
    running = [r for r in get_running_runs() if r["mode"] == mode]
    if running:
        return jsonify({"error": f"{mode} is already running", "run_id": running[0]["run_id"]}), 409
    run_id = launch_pipeline(mode)
    return jsonify({"run_id": run_id, "mode": mode, "status": "started"})


@app.route("/admin/pipeline/config", methods=["POST"])
@require_admin
def admin_pipeline_config():
    from src.pipeline_monitor import set_config
    allowed = {"ingest_interval_hours", "crawl_interval_hours", "crawl_max_runtime_minutes"}
    updated = []
    for key in allowed:
        val = request.form.get(key, "").strip()
        if val and val.isdigit():
            set_config(key, val)
            updated.append(key)
    return jsonify({"updated": updated})


@app.route("/admin/pipeline/status")
@require_admin
def admin_pipeline_status():
    from src.pipeline_monitor import get_recent_runs, get_running_runs
    return jsonify({
        "running": get_running_runs(),
        "recent": get_recent_runs(10),
    })


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"\u274c Database not found at: {DB_PATH}")
        print("Run the pipeline first: python -m src.orchestrator --mode weekly")
        exit(1)

    print("="*80)
    print("Job Market Intelligence - Web Viewer")
    print("="*80)
    print(f"Database: {DB_PATH.absolute()}")
    print(f"Starting server at: http://localhost:5000")
    print("="*80)
    print("\nPress Ctrl+C to stop the server\n")

    app.run(debug=False, host="localhost", port=5000)

# Reload trigger: 2026-03-02 05:27:47
