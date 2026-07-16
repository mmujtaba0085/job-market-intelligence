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
from src.auth.oauth_google import init_oauth

# Google Sheets integration
from src.sheets_routes import register_sheets_routes

from src.analytics.precomputed_summaries import _role_family

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True   # slide the 2h window on every request
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["CACHE_TYPE"] = "FileSystemCache"
app.config["CACHE_DIR"] = "data/cache/flask"
app.config["CACHE_DEFAULT_TIMEOUT"] = 900  # 15 minutes
DB_PATH = SETTINGS_DB_PATH
logger = logging.getLogger(__name__)

from flask_caching import Cache
cache = Cache(app)


def _role_aware_cache_key() -> str:
    """
    Cache key for @cache.cached(key_prefix=_role_aware_cache_key).

    Flask-Caching's default key does NOT include the query string
    (query_string defaults to False - confirmed by reading the library's
    source) - request.full_path is used explicitly here so that e.g.
    /jobs?market=ai_ml_global and /jobs?market=swe_backend_global get
    separate cache entries instead of colliding into one.

    Keyed per-user, not per-role: every cached page's shared base.html
    nav renders the logged-in user's own username, not just role-gated
    UI. A role-only key (e.g. "viewer") would serve the first viewer's
    username to every other viewer hitting the same cached page - a real
    cross-user identity leak, caught in final review before this shipped.
    Keying by user id means every user still gets the caching benefit on
    their own repeat visits, and automatically separates admin from
    viewer too (different users, different ids), without ever sharing an
    entry with a different user.
    """
    user_id = g.current_user.get("id") if g.current_user else "anon"
    return f"{user_id}:{request.full_path}"

# Run DB migrations on startup so the web app is never behind
from src.storage.db import run_migrations as _run_migrations
_run_migrations()

# ── Register auth blueprints ──────────────────────────────────────────────────
app.register_blueprint(auth_bp, url_prefix="/auth")
app.register_blueprint(admin_auth_bp)
init_oauth(app)

# ── Auth hooks ────────────────────────────────────────────────────────────────
app.before_request(load_logged_in_user)
app.after_request(log_request_access)


@app.context_processor
def inject_current_user():
    from src.auth.middleware import csrf_token as _csrf_token
    dark_mode_locked = request.path.startswith("/admin") or request.path == "/jobs/quality"
    return {"current_user": get_current_user(), "csrf_token": _csrf_token, "dark_mode_locked": dark_mode_locked}


# ── Global auth gate ──────────────────────────────────────────────────────────
_PUBLIC_PATHS = {"/healthz", "/auth/login", "/auth/logout", "/auth/google", "/auth/google/callback", "/robots.txt"}
_PUBLIC_PREFIXES = ("/static/",)

# Reachable without a login, but NOT a full bypass like _PUBLIC_PATHS above -
# g.current_user still populates normally from an existing session/API key,
# and (critically) API-key scope enforcement further down in
# global_auth_gate() still applies. See that function for exactly where
# this is consulted and why the placement matters.
_PUBLIC_VIEWABLE_ENDPOINTS = {
    "index",  # "/" - just redirects to dashboard, but that redirect must
              # itself be reachable anonymously, or the header-brand/logo
              # link (present on every page) sends anonymous visitors to
              # the login page instead of the dashboard.
    "dashboard", "jobs_list", "job_detail",
    # skills_intelligence / companies_intelligence / titles_analytics are
    # deliberately NOT here - fully gated by request: an anonymous click
    # on Skills/Companies/Titles goes straight to /auth/login (the normal
    # global_auth_gate() fallback), not a teased preview.
    "submit_job_report",
    # Reporting a bad listing is explicitly a no-sign-in-required action
    # (see docs/superpowers/specs/2026-07-16-job-report-feature-design.md)
    # - CSRF + its own per-IP rate limit (src.job_reports.is_rate_limited)
    # protect the route itself, same as any other public mutation here.
}
_PUBLIC_API_READS = {
    "/api/dashboard/kpis", "/api/dashboard/companies", "/api/dashboard/location-diversity",
    # /api/skills/search, /api/skills/combinations, /api/companies/list,
    # /api/titles/top removed along with their pages above - anonymous
    # visitors can no longer reach the pages that call them, and leaving
    # the API endpoints public would let the same data through directly.
    # /api/filters/skills removed by explicit request too - the jobs list
    # filter dropdown's skill options are now also locked behind login;
    # anonymous visitors still see the full job list, just without the
    # skill-filter dropdown populated.
}


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

    is_public_viewable = (
        request.endpoint in _PUBLIC_VIEWABLE_ENDPOINTS or path in _PUBLIC_API_READS
    )

    if not user:
        if auth_type == "api_key_rate_limited":
            return jsonify({"error": "Rate limit exceeded"}), 429
        if is_public_viewable:
            return  # anonymous visitor: g.current_user stays None, template/endpoint decides what to show
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


# Tracks the most recent real request so the classification scheduler can
# tell whether the site is idle before running load-gated backfill chunks.
_last_request_at: "datetime | None" = None


@app.before_request
def _track_last_request_at():
    global _last_request_at
    if request.path == "/healthz" or request.path.startswith("/static/"):
        return
    from datetime import datetime, timezone
    _last_request_at = datetime.now(timezone.utc)


@app.before_request
def _load_active_notifications():
    if request.path == "/healthz" or request.path.startswith("/static/"):
        g.active_notifications = []
        return
    from datetime import datetime, timezone
    from src.notifications import load_active_notifications

    # Deliberately NOT filtering by the jmi_dismissed cookie here (unlike
    # the original design): several pages this hook covers (/jobs,
    # /dashboard, ...) are wrapped in @cache.cached(), which caches the
    # full rendered HTML per role, not per visitor. If dismissal were baked
    # into this server-rendered HTML, the first anonymous visitor to
    # dismiss a notification would silently hide it from every other
    # anonymous visitor for the rest of the cache window, and a visitor
    # who dismissed it could see it reappear once a non-dismissing visitor
    # re-warms the cache. Rendering the same, uniform HTML for everyone and
    # hiding already-dismissed bars client-side (see base.html's early
    # <head> script) keeps dismissal genuinely per-visitor regardless of
    # caching.
    try:
        g.active_notifications = load_active_notifications(
            request.path, set(), datetime.now(timezone.utc)
        )
    except sqlite3.OperationalError:
        # The operational DB's notifications table can be legitimately
        # absent - a not-yet-migrated DB, or (the common case in this
        # repo's own test suite) an isolated test fixture that builds a
        # minimal hand-rolled schema without ever calling run_migrations().
        # An optional announcement bar must never turn every page on the
        # site into a 500; degrade to "no notifications" instead. Scoped to
        # sqlite3.OperationalError specifically (not a bare except) so a
        # real bug in the filtering logic itself - e.g. the naive-vs-aware
        # datetime TypeError this hook must avoid - still surfaces loudly
        # instead of being silently swallowed here.
        logger.warning("[notifications] load_active_notifications failed (DB not migrated?); showing no notifications for %s", request.path, exc_info=True)
        g.active_notifications = []


# ── Initialise auth DB on startup ─────────────────────────────────────────────
init_auth_db()


def get_db_connection():
    """Get SQLite database connection. Falls back to .shadow.sqlite if main is unavailable."""
    from pathlib import Path as _Path
    from src.storage.db import serving_db_path
    serving_path = serving_db_path()
    candidates = [serving_path, _Path(str(serving_path).replace(".sqlite", ".shadow.sqlite"))]
    last_err = None
    for p in candidates:
        if not _Path(str(p)).exists():
            continue
        try:
            conn = sqlite3.connect(str(p), timeout=30)
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM active_jobs LIMIT 1")
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
    raise sqlite3.OperationalError(f"Cannot open any DB: {last_err}")


def _status_window_clause(status: str, alias: str = "") -> str:
    """
    SQL AND-clause fragment for the listing-status + active-window filter
    shared by /jobs and the /api/dashboard/* widgets that read active_jobs
    (directly, or via a join back to it). `alias` is an optional table-alias
    prefix, dot included (e.g. "j." for /jobs's `j` alias and for routes that
    join back to active_jobs as `j`; "" for dashboard routes that query
    active_jobs unaliased).

    Four status values, matching the "Listing Status" / "Listings" dropdown
    in templates/jobs_list.html and templates/dashboard.html:
      - "active": listing_status is unset/active AND the job is within the
        last month. Age is measured by posted_date, falling back to
        first_seen_at when posted_date is NULL/missing - the same fallback
        already used for date *display* elsewhere (see the posted_date-or-
        first_seen_at pattern in templates/jobs_list.html and
        templates/job_detail.html). listing_status alone doesn't age a job
        out today - nothing currently transitions a job to 'historical' on
        its own, so almost every row is NULL/'active' on that column - this
        is what actually makes "Active" mean "posted/seen recently".
      - "unverified": listing_status is 'historical' or 'unverified' - no
        age restriction.
      - "closed": listing_status = 'closed' - no age restriction.
      - anything else (in particular "all"): no restriction at all - every
        job, regardless of status or age. This intentionally matches the
        pre-existing /jobs behavior of falling through to "no filter" for
        any unrecognized status value, not just the literal string "all".
    """
    if status == "active":
        return (
            f" AND ({alias}listing_status IS NULL OR {alias}listing_status = 'active')"
            f" AND date(COALESCE({alias}posted_date, {alias}first_seen_at)) >= date('now', '-1 month')"
        )
    if status == "unverified":
        return f" AND {alias}listing_status IN ('historical','unverified')"
    if status == "closed":
        return f" AND {alias}listing_status = 'closed'"
    return ""  # 'all' (or any unrecognized value) → no filter


def _region_scope_clause(region: str, alias: str = "") -> str:
    """
    SQL AND-clause fragment restricting to Pakistan-relevant jobs by
    default - see
    docs/superpowers/specs/2026-07-16-pakistan-first-default-experience-design.md.

    'pk' (the default): country IN ('Pakistan', 'Global') - jobs physically
    in Pakistan, plus jobs explicitly marked open to remote applicants
    anywhere (sources like Himalayas set country='Global' specifically for
    genuinely worldwide-open roles). A specific non-Pakistan country value
    on a remote job (e.g. country='United States') is deliberately NOT
    included - it's a signal the role is likely restricted to that
    country in practice, not genuinely open to a Pakistan-based applicant.
    'all' (or any unrecognized value): no restriction - every job,
    regardless of country.
    """
    if region == "pk":
        return f" AND {alias}country IN ('Pakistan', 'Global')"
    return ""


def _default_region() -> str:
    """
    Resolves the region default with query-param > cookie > hardcoded 'pk'
    priority - an explicit ?region= always wins (so the toggle's own reload
    works), falling back to the visitor's remembered jmi_region cookie,
    falling back to Pakistan-first for a first-time visitor.
    """
    return request.args.get("region") or request.cookies.get("jmi_region", "pk")


def show_source_names() -> bool:
    """
    Admins always see real source names (they need them to operate the
    pipeline). Everyone else depends on the "show_source_names" admin
    toggle — off by default is not the goal here, the toggle just lets an
    admin hide the source list from regular/API-key viewers without
    touching the underlying data.
    """
    user = getattr(g, "current_user", None)
    if user and user.get("role") == "admin":
        return True
    from src.pipeline_monitor import get_config
    return get_config().get("show_source_names", "true") != "false"


def obscure_source_map(names) -> dict:
    """Deterministic real-name -> "Source A"/"Source B"/... label mapping."""
    uniq = sorted({n for n in names if n})
    mapping = {}
    for i, name in enumerate(uniq):
        mapping[name] = f"Source {chr(65 + i)}" if i < 26 else f"Source {i + 1}"
    return mapping


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
    from flask import redirect, url_for
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard():
    """BI Dashboard with interactive widgets."""
    return render_template("dashboard.html")


@app.route("/healthz")
def healthz():
    """Container/web health probe with a lightweight SQLite check."""
    try:
        from src.storage.db import serving_db_path
        conn = sqlite3.connect(serving_db_path(), timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "ok", "db": "ok"}), 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("[healthz] DB check failed: %s", exc)
        return jsonify({"status": "degraded", "db": "error", "error": str(exc)}), 503


@app.route("/robots.txt")
def robots_txt():
    """Disallow everything for now - not an SEO launch yet."""
    return make_response(("User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}))


@app.route("/api/dashboard/kpis")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_kpis():
    """Get KPI metrics for dashboard."""
    conn = get_db_connection()
    cursor = conn.cursor()

    status = request.args.get("status", "all")
    region = _default_region()
    status_clause = _status_window_clause(status)
    region_clause = _region_scope_clause(region)

    # Total jobs
    cursor.execute(f"SELECT COUNT(*) as count FROM active_jobs WHERE 1=1{status_clause}{region_clause}")
    total_jobs = cursor.fetchone()["count"]

    # Total skills - scoped to skills belonging to in-scope (status/window
    # filtered) jobs, via a join back to active_jobs on skills.job_id, so
    # "total skills" respects the same toggle as "total jobs" instead of
    # always counting every skill ever seen regardless of job age/status.
    cursor.execute(f"""
        SELECT COUNT(DISTINCT s.normalized_skill) as count
        FROM skills s
        JOIN active_jobs j ON j.job_id = s.job_id
        WHERE 1=1{_status_window_clause(status, "j.")}{_region_scope_clause(region, "j.")}
    """)
    total_skills = cursor.fetchone()["count"]

    # Active sources
    cursor.execute(f"SELECT COUNT(DISTINCT source_name) as count FROM active_jobs WHERE 1=1{status_clause}{region_clause}")
    active_sources = cursor.fetchone()["count"]

    # Remote percentage
    cursor.execute(f"""
        SELECT
            CAST(SUM(CASE WHEN LOWER(remote_type) = 'remote' THEN 1 ELSE 0 END) AS FLOAT) * 100 / COUNT(*) as pct
        FROM active_jobs
        WHERE 1=1{status_clause}{region_clause}
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
            FROM active_jobs
            WHERE first_seen_at >= ?
        """, (current_week,))
        current_jobs = cursor.fetchone()["count"]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT job_id) as count
            FROM active_jobs
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
        FROM active_jobs
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
    """
    Get top 10 skills for current period, aggregated across all markets.

    The primary query reads weekly_metrics (just the latest week_start_date),
    which - like dashboard_trends/emerging/declining - has no per-job
    listing_status or posted_date/first_seen_at to filter on: it's already
    pre-aggregated frequency counts by week, not job rows, and only ever
    exposes the single latest week. So, same as those three routes, the
    status/active-window toggle is NOT applied here; there is no "all"
    (all-time, regardless of age) reading of a table that only ever holds
    one week's numbers.

    The fallback query (only reached when weekly_metrics has no rows yet,
    e.g. a fresh/empty deployment) reads the skills table directly and CAN
    be joined back to active_jobs via skills.job_id, so that path does
    honor the status/window filter.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT skill_name, category, SUM(frequency) as frequency
        FROM weekly_metrics
        WHERE week_start_date = (SELECT MAX(week_start_date) FROM weekly_metrics)
          AND category != 'soft_skills'
        GROUP BY skill_name, category
        ORDER BY frequency DESC
        LIMIT 10
    """)

    skills = [{"skill": row["skill_name"], "count": row["frequency"], "category": row["category"]}
              for row in cursor.fetchall()]

    if not skills:
        status = request.args.get("status", "all")
        region = _default_region()
        cursor.execute(f"""
            SELECT s.normalized_skill as skill, COUNT(*) as count, s.category
            FROM skills s
            JOIN active_jobs j ON j.job_id = s.job_id
            WHERE 1=1{_status_window_clause(status, "j.")}{_region_scope_clause(region, "j.")}
            GROUP BY s.normalized_skill, s.category
            ORDER BY count DESC
            LIMIT 10
        """)
        skills = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return jsonify(skills)


@app.route("/api/dashboard/geo")
def dashboard_geo():
    """
    Get geographic distribution.

    Every active job must land in exactly one returned bucket - the sum of
    `count` across the response must equal the active job total, with no
    silent drops. This used to WHERE-exclude NULL/blank/'unknown'/'global'
    countries entirely (~19% of active jobs vanished from the chart with no
    indication they existed) and separately LIMIT 15, which silently
    truncated the long tail of real countries (and, historically, raw US
    state codes like "MA" that used to leak into `country` - see
    src/utils/country_inference.py and scripts/backfill_us_state_country_codes.py)
    once there were more than 15 distinct values. Now every job is bucketed
    (NULL/blank/'unknown' -> "Unknown", 'global' -> "Remote / Global", else
    the country as stored) and no LIMIT is applied - the frontend already
    slices to the top 10 for the doughnut chart itself (see
    static/js/dashboard.js loadGeoChart()), so nothing about the visible
    chart changes; only the underlying data completeness does.

    The "every active job lands in exactly one bucket" invariant now holds
    against the status/window-filtered population, not the whole table: the
    bucket sum equals the *filtered* active job total (see
    _status_window_clause) - still no silent drops relative to that total,
    still no LIMIT.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    status = request.args.get("status", "active")

    cursor.execute(f"""
        SELECT
          CASE
            WHEN country IS NULL OR TRIM(country) = '' OR LOWER(TRIM(country)) = 'unknown' THEN 'Unknown'
            WHEN LOWER(TRIM(country)) = 'global' THEN 'Remote / Global'
            ELSE country
          END AS country,
          COUNT(*) as count
        FROM active_jobs
        WHERE 1=1{_status_window_clause(status)}
        GROUP BY 1
        ORDER BY count DESC
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
    status = request.args.get("status", "all")
    region = _default_region()

    cursor.execute(f"""
        SELECT source_name, COUNT(*) as count
        FROM active_jobs
        WHERE 1=1{_status_window_clause(status)}{_region_scope_clause(region)}
        GROUP BY source_name
        ORDER BY count DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    if show_source_names():
        sources = [{"source": row["source_name"], "count": row["count"]} for row in rows]
    else:
        name_map = obscure_source_map([row["source_name"] for row in rows])
        sources = [{"source": name_map[row["source_name"]], "count": row["count"]} for row in rows]

    return jsonify(sources)


@app.route("/api/dashboard/emerging")
def dashboard_emerging():
    """Get emerging skills, aggregated across all markets."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT skill_name, category,
               SUM(frequency) as frequency,
               AVG(growth_percentage) as growth_percentage,
               MAX(mover_score) as mover_score
        FROM weekly_metrics
        WHERE week_start_date = (SELECT MAX(week_start_date) FROM weekly_metrics)
          AND category != 'soft_skills'
        GROUP BY skill_name, category
        HAVING MAX(emerging_flag) = 1
           AND SUM(frequency) >= 15
        ORDER BY MAX(mover_score) DESC
        LIMIT 10
    """)

    emerging = [{"skill": row["skill_name"], "category": row["category"],
                 "frequency": row["frequency"], "growth": row["growth_percentage"]}
                for row in cursor.fetchall()]
    conn.close()
    return jsonify(emerging)


@app.route("/api/dashboard/declining")
def dashboard_declining():
    """Get declining skills, aggregated across all markets."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT skill_name, category,
               SUM(frequency) as frequency,
               AVG(growth_percentage) as growth_percentage,
               MIN(mover_score) as mover_score
        FROM weekly_metrics
        WHERE week_start_date = (SELECT MAX(week_start_date) FROM weekly_metrics)
          AND category != 'soft_skills'
        GROUP BY skill_name, category
        HAVING MAX(declining_flag) = 1
           AND SUM(frequency) >= 15
        ORDER BY MIN(mover_score) ASC
        LIMIT 10
    """)

    declining = [{"skill": row["skill_name"], "category": row["category"],
                  "frequency": row["frequency"], "growth": row["growth_percentage"]}
                 for row in cursor.fetchall()]
    conn.close()
    return jsonify(declining)


@app.route("/api/dashboard/companies")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_companies():
    """Get top hiring companies."""
    conn = get_db_connection()
    cursor = conn.cursor()
    status = request.args.get("status", "all")
    region = _default_region()

    cursor.execute(f"""
        SELECT company, COUNT(*) as count
        FROM active_jobs
        WHERE company IS NOT NULL AND company != ''{_status_window_clause(status)}{_region_scope_clause(region)}
        GROUP BY company
        ORDER BY count DESC
        LIMIT 10
    """)

    companies = [{"company": row["company"], "count": row["count"]}
                 for row in cursor.fetchall()]
    conn.close()

    return jsonify(companies)


@app.route("/api/dashboard/location-diversity")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def dashboard_location_diversity():
    """Get companies with jobs in most locations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    status = request.args.get("status", "all")
    region = _default_region()

    cursor.execute(f"""
        SELECT
            company,
            MAX(location_count) as max_locations,
            COUNT(DISTINCT job_group_id) as job_count
        FROM active_jobs
        WHERE location_count > 1{_status_window_clause(status)}{_region_scope_clause(region)}
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
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def skills_intelligence():
    """Skills Intelligence Page with detailed analytics."""
    return render_template("skills_intelligence.html")


@app.route("/api/skills/search")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
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
        FROM active_jobs j
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
        FROM active_jobs j
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
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def skill_combinations():
    """Get top skill pairs/combinations (precomputed - see
    src/analytics/precomputed_summaries.py for why).

    No role-based limit here anymore: this endpoint is fully gated (not
    in _PUBLIC_API_READS), so every request that reaches this point is
    already authenticated - g.current_user is always truthy."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT skill_a, skill_b, co_count FROM skill_combinations_summary ORDER BY co_count DESC LIMIT ?",
        (20,),
    )

    combinations = [{"skill_a": row["skill_a"], "skill_b": row["skill_b"], "count": row["co_count"]}
                    for row in cursor.fetchall()]
    conn.close()

    return jsonify(combinations)


@app.route("/companies/intelligence")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def companies_intelligence():
    """Company Intelligence Page."""
    return render_template("companies_intelligence.html")


@app.route("/api/companies/list")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
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
        FROM active_jobs j
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
        SELECT COUNT(*) as count FROM active_jobs WHERE company = ?
    """, (company,))
    job_count = cursor.fetchone()["count"]
    
    # Skill diversity
    cursor.execute("""
        SELECT COUNT(DISTINCT s.normalized_skill) as count
        FROM skills s
        JOIN active_jobs j ON s.job_id = j.job_id
        WHERE j.company = ?
    """, (company,))
    skill_count = cursor.fetchone()["count"]
    
    # Remote percentage
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN LOWER(remote_type) = 'remote' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as pct
        FROM active_jobs
        WHERE company = ?
    """, (company,))
    remote_pct = cursor.fetchone()["pct"] or 0
    
    # Location count
    cursor.execute("""
        SELECT COUNT(DISTINCT country) as count
        FROM active_jobs
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
        JOIN active_jobs j ON s.job_id = j.job_id
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
        FROM active_jobs
        WHERE company = ? AND country IS NOT NULL AND country != ''
        GROUP BY country
        ORDER BY count DESC
    """, (company,))
    
    locations = [{"country": row["country"], "count": row["count"]} 
                 for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(locations)


@app.route("/titles/analytics")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def titles_analytics():
    """Job Titles Analytics Page."""
    return render_template("titles_analytics.html")


@app.route("/api/docs")
def api_docs():
    """Human-readable API documentation (auth, endpoints, examples)."""
    return render_template("api_docs.html")


@app.route("/api/titles/top")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def titles_top():
    """Get top job titles grouped by role family (precomputed - see
    src/analytics/precomputed_summaries.py for why)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT title, count FROM top_titles_summary ORDER BY count DESC LIMIT 30")

    titles = [{"title": row["title"], "count": row["count"]} for row in cursor.fetchall()]
    conn.close()

    return jsonify(titles)


@app.route("/api/titles/<title>/skills")
def title_skills(title):
    """Get skills for a role family — aggregates all seniority variants."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Pull all distinct normalized_titles in this family
    cursor.execute(
        "SELECT DISTINCT normalized_title FROM active_jobs WHERE normalized_title IS NOT NULL",
    )
    family_titles = [
        row["normalized_title"] for row in cursor.fetchall()
        if _role_family(row["normalized_title"]) == title
    ]
    if not family_titles:
        conn.close()
        return jsonify([])

    ph = ",".join("?" * len(family_titles))
    cursor.execute(f"""
        SELECT s.normalized_skill, s.category, COUNT(*) as count
        FROM skills s
        JOIN active_jobs j ON s.job_id = j.job_id
        WHERE j.normalized_title IN ({ph})
          AND s.category != 'soft_skills'
        GROUP BY s.normalized_skill, s.category
        ORDER BY count DESC
        LIMIT 15
    """, family_titles)

    skills = [{"skill": row["normalized_skill"], "category": row["category"], "count": row["count"]}
              for row in cursor.fetchall()]
    conn.close()
    return jsonify(skills)


@app.route("/api/filters/skills")
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
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
        FROM active_jobs
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
        FROM active_jobs
        GROUP BY source_name
        ORDER BY count DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    if show_source_names():
        sources = [{"source": row["source_name"], "count": row["count"]} for row in rows]
    else:
        name_map = obscure_source_map([row["source_name"] for row in rows])
        sources = [{"source": name_map[row["source_name"]], "count": row["count"]} for row in rows]

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
            FROM active_jobs
            WHERE company LIKE ? AND company IS NOT NULL AND company != ''
            GROUP BY company
            ORDER BY count DESC
            LIMIT 50
        """, (f"%{search}%",))
    else:
        cursor.execute("""
            SELECT DISTINCT company, COUNT(*) as count
            FROM active_jobs
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
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def jobs_list():
    """List jobs with filters, status selector, and pagination."""
    conn = get_db_connection()
    cursor = conn.cursor()

    PER_PAGE = 20 if g.current_user else 10

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
    current_status = request.args.get("status", "all")
    region         = _default_region()
    sort_param     = request.args.get("sort", "diverse")

    # Filtering is a signed-in feature - the sidebar is hidden for anonymous
    # visitors, but a filtered URL can still be typed or shared directly, so
    # the query params must be ignored here too, not just hidden in the UI.
    # current_status is pinned to the same "all" baseline signed-in visitors
    # get by default (not the narrower "active") - anonymous visitors are
    # this app's primary target audience, so they must not be the one group
    # still seeing status/region compound into an overly narrow default.
    if not g.current_user:
        market_filter = remote_filter = search_query = ""
        country_filter = source_filter = company_filter = ""
        skills_filter = []
        date_from = date_to = ""
        current_status = "all"

    # Diversity ordering only means anything against the exact population it
    # was computed for: status=active, zero other filters. Any deviation from
    # that baseline falls back to plain recency, same as before this feature.
    no_filters_active = not any([
        market_filter, remote_filter, search_query, country_filter,
        source_filter, company_filter, skills_filter, date_from, date_to,
    ])
    show_sort_toggle = no_filters_active and current_status == "active"
    use_diversity = show_sort_toggle and sort_param != "recent"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    show_names = show_source_names()
    cursor.execute("SELECT DISTINCT source_name FROM active_jobs ORDER BY source_name")
    all_sources = [r["source_name"] for r in cursor.fetchall()]
    source_name_map = {} if show_names else obscure_source_map(all_sources)
    source_reverse_map = {v: k for k, v in source_name_map.items()}

    base = """
        SELECT DISTINCT j.job_id, j.title, j.company, j.location, j.country,
               j.remote_type, j.posted_date, j.source_name, j.market_id, j.location_count
        FROM active_jobs j
        WHERE 1=1
    """
    params = []

    # Status + active-window filter (see _status_window_clause)
    base += _status_window_clause(current_status, "j.")
    # Region scope filter (see _region_scope_clause)
    base += _region_scope_clause(region, "j.")

    if market_filter:
        base += " AND j.market_id = ?"; params.append(market_filter)
    if remote_filter:
        base += " AND j.remote_type = ?"; params.append(remote_filter)
    if country_filter:
        base += " AND j.country = ?"; params.append(country_filter)
    if source_filter:
        real_source = source_filter if show_names else source_reverse_map.get(source_filter, source_filter)
        base += " AND j.source_name = ?"; params.append(real_source)
    if company_filter:
        base += " AND j.company LIKE ?"; params.append(f"%{company_filter}%")
    if search_query:
        base += " AND (j.title LIKE ? OR j.company LIKE ? OR j.normalized_title LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])
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

    if use_diversity:
        order_clause = " ORDER BY (j.diversity_rank IS NULL), j.diversity_rank ASC, j.posted_date DESC LIMIT ? OFFSET ?"
    else:
        order_clause = " ORDER BY j.posted_date DESC, j.ingested_at DESC LIMIT ? OFFSET ?"

    cursor.execute(base + order_clause, params + [PER_PAGE, offset])
    jobs = cursor.fetchall()
    if not show_names:
        jobs = [dict(j) for j in jobs]
        for j in jobs:
            j["source_name"] = source_name_map.get(j["source_name"], j["source_name"])

    # Dropdown data — derived from the jobs table's own market_id, not the
    # separate ISCO-taxonomy "markets" lookup table: that table's ids
    # (it.software, healthcare.clinical, ...) don't correspond to any actual
    # job's market_id (ai_ml_global, swe_backend_global, pakistan_jobs_all)
    # until/unless the taxonomy classification is promoted, so filtering by
    # a taxonomy id here always silently matched zero rows.
    from config.markets import TARGET_MARKETS
    market_display_names = {m["market_id"]: m["display_name"] for m in TARGET_MARKETS}
    cursor.execute("SELECT DISTINCT market_id FROM active_jobs WHERE market_id IS NOT NULL ORDER BY market_id")
    markets = [
        {"market_id": r["market_id"], "name": market_display_names.get(r["market_id"], r["market_id"]), "depth": 0}
        for r in cursor.fetchall()
    ]

    cursor.execute("SELECT DISTINCT remote_type FROM active_jobs WHERE remote_type IS NOT NULL ORDER BY remote_type")
    remote_types = [r["remote_type"] for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT country FROM active_jobs WHERE country IS NOT NULL AND country != '' ORDER BY country")
    countries = [r["country"] for r in cursor.fetchall()]

    sources = all_sources if show_names else [source_name_map[n] for n in all_sources]

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
        current_region=region,
        search_query=search_query,
        skills_filter=skills_filter,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
        prev_url=page_url(page - 1) if page > 1 else None,
        next_url=page_url(page + 1) if page < total_pages else None,
        show_sort_toggle=show_sort_toggle,
        current_sort=sort_param if use_diversity else "recent",
    )


@app.route("/jobs/quality")
@require_admin
def jobs_quality_review():
    """Quality review workspace for missing/ambiguous job data with description-aware suggestions."""
    conn = get_db_connection()
    cursor = conn.cursor()

    country_filter = request.args.get("country", "")
    limit = int(request.args.get("limit", "200"))
    limit = max(10, min(limit, 3000))

    query = """
        SELECT job_id, title, company, location, country, remote_type, posted_date, source_name, raw_description
        FROM active_jobs
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
    countries = cursor.execute("SELECT DISTINCT country FROM active_jobs WHERE country IS NOT NULL AND TRIM(country) != '' ORDER BY country").fetchall()
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
    """
    JSON list of jobs — requires jobs:read scope for API keys.

    Query params:
      limit    max rows to return (default 50, capped at 200)
      offset   pagination offset
      market   filter to one market_id (e.g. pakistan_jobs_all, ai_ml_global,
               swe_backend_global) - omit for all markets
      exclude_market  filter OUT one market_id - the inverse of `market`,
               e.g. exclude_market=pakistan_jobs_all for "everything else"

    Sorted by posted_date (the job's own listing date) descending, not
    ingestion time - a job we only just discovered via a historical
    backfill shouldn't jump to the front of a "most recent" feed just
    because we collected it a minute ago. Jobs with no posted_date (~41%
    of the catalog, an inherent source data-quality gap) sort after every
    dated job, ranked among themselves by ingestion recency instead of
    disappearing to page one thousand.
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400

    market_filter = request.args.get("market", "")
    exclude_market = request.args.get("exclude_market", "")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        where = ["1=1"]
        params: list = []
        if market_filter:
            where.append("market_id = ?")
            params.append(market_filter)
        if exclude_market:
            where.append("market_id != ?")
            params.append(exclude_market)

        cursor.execute(
            f"""
            SELECT job_id, title, company, location, country, remote_type,
                   posted_date, source_name, market_id, url
            FROM active_jobs
            WHERE {' AND '.join(where)}
            ORDER BY (posted_date IS NULL), posted_date DESC, ingested_at DESC, job_id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = cursor.fetchall()

        jobs = [dict(r) for r in rows]
        if not show_source_names():
            cursor.execute("SELECT DISTINCT source_name FROM active_jobs")
            name_map = obscure_source_map([r["source_name"] for r in cursor.fetchall()])
            for j in jobs:
                j["source_name"] = name_map.get(j["source_name"], j["source_name"])

        return jsonify({"jobs": jobs, "limit": limit, "offset": offset})
    finally:
        conn.close()


@app.route("/api/jobs/quality/analyze", methods=["POST"])
@require_admin
def api_jobs_quality_analyze():
    """Analyze selected jobs and return improved data suggestions + split candidates."""
    payload = request.get_json() or {}
    job_ids = payload.get("job_ids") or []

    if not job_ids:
        return jsonify({"success": False, "error": "No job_ids provided"}), 400

    conn = get_db_connection()
    placeholders = ",".join(["?"] * len(job_ids))
    rows = conn.execute(
        f"""
        SELECT job_id, title, company, location, country, remote_type, posted_date, source_name, raw_description
        FROM active_jobs
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
                FROM active_jobs
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
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
def job_detail(job_id):
    """Show full job details including description and all locations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM active_jobs WHERE job_id = ?
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
    
    if not show_source_names():
        cursor.execute("SELECT DISTINCT source_name FROM active_jobs ORDER BY source_name")
        name_map = obscure_source_map([r["source_name"] for r in cursor.fetchall()])
        job = dict(job)
        job["source_name"] = name_map.get(job["source_name"], job["source_name"])

    conn.close()

    return render_template(
        "job_detail.html",
        job=job,
        skills=skills,
        locations=locations
    )


@app.route("/jobs/<int:job_id>/report", methods=["POST"])
def submit_job_report(job_id):
    from src.auth.middleware import validate_csrf
    from src.job_reports import create_report, is_rate_limited, validate_report_input
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    conn = get_db_connection()
    job = conn.execute("SELECT job_id, title, url FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    if job is None:
        return jsonify({"error": "Job not found"}), 404

    reason_category = request.form.get("reason_category", "")
    details = request.form.get("details", "").strip()
    validation_error = validate_report_input(reason_category, details)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    reporter_ip = request.remote_addr or "unknown"
    op_conn = get_operational_connection()
    try:
        if is_rate_limited(op_conn, reporter_ip, datetime.now(timezone.utc)):
            return jsonify({"error": "Too many reports from this IP recently - please try again later"}), 429

        reporter_user_id = g.current_user.get("id") if g.current_user else None
        reporter_email = None if g.current_user else (request.form.get("email", "").strip() or None)

        create_report(
            op_conn, job_id=job["job_id"], job_url=job["url"], job_title=job["title"],
            reason_category=reason_category, details=details,
            reporter_user_id=reporter_user_id, reporter_email=reporter_email,
            reporter_ip=reporter_ip, now=datetime.now(timezone.utc),
        )
    finally:
        op_conn.close()

    return jsonify({"status": "ok"})


@app.route("/api/jobs/<int:job_id>/locations")
def job_locations_api(job_id):
    """API endpoint to get all locations for a job."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get job_group_id
    cursor.execute("SELECT job_group_id FROM active_jobs WHERE job_id = ?", (job_id,))
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
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
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
@cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
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
    
    # Get emerging skills (latest week, aggregated across markets)
    cursor.execute("""
        SELECT skill_name, category,
               SUM(frequency) as frequency,
               AVG(growth_percentage) as growth_percentage,
               MAX(week_start_date) as week_start_date
        FROM weekly_metrics
        WHERE week_start_date = (SELECT MAX(week_start_date) FROM weekly_metrics)
          AND category != 'soft_skills'
        GROUP BY skill_name, category
        HAVING MAX(emerging_flag) = 1
           AND SUM(frequency) >= 15
        ORDER BY MAX(mover_score) DESC
        LIMIT 20
    """)
    emerging = cursor.fetchall()

    # Get declining skills (latest week, aggregated across markets)
    cursor.execute("""
        SELECT skill_name, category,
               SUM(frequency) as frequency,
               AVG(growth_percentage) as growth_percentage,
               MAX(week_start_date) as week_start_date
        FROM weekly_metrics
        WHERE week_start_date = (SELECT MAX(week_start_date) FROM weekly_metrics)
          AND category != 'soft_skills'
        GROUP BY skill_name, category
        HAVING MAX(declining_flag) = 1
           AND SUM(frequency) >= 15
        ORDER BY MIN(mover_score) ASC
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
        FROM active_jobs
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
        FROM active_jobs
        WHERE country IS NOT NULL AND country != ''
        GROUP BY country
        ORDER BY count DESC
    """)
    countries = [{"value": row["country"], "count": row["count"]} for row in cursor.fetchall()]
    
    # Get location statistics
    cursor.execute("""
        SELECT location, COUNT(*) as count
        FROM active_jobs
        WHERE location IS NOT NULL AND location != ''
        GROUP BY location
        ORDER BY count DESC
        LIMIT 200
    """)
    locations = [{"value": row["location"], "count": row["count"]} for row in cursor.fetchall()]
    
    # Get total unique locations count
    cursor.execute("""
        SELECT COUNT(DISTINCT location) as total
        FROM active_jobs
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
                "SELECT COUNT(*) as count FROM active_jobs WHERE country = ?",
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
                "SELECT COUNT(*) as count FROM active_jobs WHERE location = ?",
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
    
    _queries = {
        "country":  "SELECT title, company, country, location, remote_type FROM active_jobs WHERE country = ? LIMIT 10",
        "location": "SELECT title, company, country, location, remote_type FROM active_jobs WHERE location = ? LIMIT 10",
    }
    if field not in _queries:
        return jsonify({"error": "Invalid field"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(_queries[field], (value,))
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
        FROM active_jobs
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
        FROM active_jobs
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
    cursor.execute("SELECT COUNT(*) as count FROM active_jobs")
    total_jobs = cursor.fetchone()["count"]
    
    # Unknown countries
    cursor.execute("""
        SELECT COUNT(*) as count FROM active_jobs 
        WHERE country = 'Unknown'
    """)
    unknown_countries = cursor.fetchone()["count"]
    
    # Normalized titles
    cursor.execute("""
        SELECT COUNT(*) as count FROM active_jobs 
        WHERE normalization_confidence > 0.0
    """)
    normalized_titles = cursor.fetchone()["count"]
    
    # Low-confidence titles
    cursor.execute("""
        SELECT COUNT(*) as count FROM active_jobs 
        WHERE normalization_confidence > 0.0 AND normalization_confidence < 0.6
    """)
    low_conf_titles = cursor.fetchone()["count"]

    conn.close()

    from src.pipeline_monitor import get_config
    current_show_source_names = get_config().get("show_source_names", "true") != "false"

    return render_template(
        "admin_dashboard.html",
        total_jobs=total_jobs,
        unknown_countries=unknown_countries,
        normalized_titles=normalized_titles,
        low_conf_titles=low_conf_titles,
        show_source_names=current_show_source_names,
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
        FROM active_jobs
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
    cursor.execute("SELECT COUNT(DISTINCT title) as count FROM active_jobs")
    total_titles = cursor.fetchone()["count"]
    
    # Count titles with ≥2 jobs
    cursor.execute("SELECT COUNT(*) FROM (SELECT title FROM active_jobs GROUP BY title HAVING COUNT(*) >= 2)")
    titles_with_multiple = cursor.fetchone()[0]
    
    # Stats
    cursor.execute("SELECT COUNT(*) as count FROM active_jobs WHERE normalization_confidence > 0.0")
    normalized_count = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(DISTINCT normalized_title) as count FROM active_jobs WHERE normalization_confidence > 0.0")
    unique_normalized = cursor.fetchone()["count"]
    
    # Count manually normalized titles
    cursor.execute("SELECT COUNT(DISTINCT title) FROM active_jobs WHERE normalization_confidence = 1.0")
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
            "SELECT COUNT(*) as count FROM active_jobs WHERE title = ?",
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
        FROM active_jobs
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
        FROM active_jobs
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
        FROM active_jobs
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


@app.template_filter("display_source")
def display_source(source_name):
    """Clean display label for source_name. GitHub-collected jobs store
    "GitHub:owner/repo" internally (useful for debugging/grouping which
    repo a job came from) but should show a plain "GitHub" label to users."""
    if source_name and source_name.startswith("GitHub:"):
        return "GitHub"
    return source_name


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


@app.route("/admin/pipeline/rotate", methods=["POST"])
@require_admin
def admin_pipeline_rotate():
    from src.db_rotation import rotate
    result = rotate()
    return jsonify(result)


@app.route("/admin/pipeline/config", methods=["POST"])
@require_admin
def admin_pipeline_config():
    from src.pipeline_monitor import set_config
    allowed = {"ingest_interval_hours", "crawl_interval_hours", "crawl_max_runtime_minutes", "rotation_max_interval_hours"}
    updated = []
    for key in allowed:
        val = request.form.get(key, "").strip()
        if val and val.isdigit():
            set_config(key, val)
            updated.append(key)
    return jsonify({"updated": updated})


@app.route("/admin/display-settings", methods=["POST"])
@require_admin
def admin_display_settings():
    """Admin-only toggles controlling what non-admin viewers see (e.g. source names)."""
    from src.pipeline_monitor import set_config
    set_config("show_source_names", "true" if request.form.get("show_source_names") == "on" else "false")
    return jsonify({"updated": ["show_source_names"]})


@app.route("/admin/pipeline/status")
@require_admin
def admin_pipeline_status():
    from src.pipeline_monitor import get_recent_runs, get_running_runs
    return jsonify({
        "running": get_running_runs(),
        "recent": get_recent_runs(10),
    })


@app.route("/admin/pipeline/logs/<run_id>")
@require_admin
def admin_pipeline_logs(run_id: str):
    from config.settings import LOGS_DIR
    import re as _re
    if not _re.fullmatch(r"[a-f0-9\-]{6,36}", run_id):
        return "Invalid run ID", 400
    log_path = LOGS_DIR / f"run_{run_id}.log"
    if not log_path.exists():
        content = "(Log file not found — this run may have been started before per-run logging was enabled, or the log was cleaned up.)"
    else:
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            content = f"Error reading log: {e}"
    return render_template("admin_pipeline_logs.html", run_id=run_id, log_content=content)


# ═══════════════════════════════════════════════════════════════════
# ADMIN: CLASSIFICATION PIPELINE
# ═══════════════════════════════════════════════════════════════════

@app.route("/admin/classification")
@require_admin
def admin_classification():
    from src.storage.db import get_free_connection
    conn = get_free_connection()
    cursor = conn.cursor()

    total = cursor.execute("SELECT COUNT(*) FROM active_jobs").fetchone()[0]
    classified_local = cursor.execute(
        "SELECT COUNT(*) FROM active_jobs WHERE field_classification_method = 'local_hybrid_v1'"
    ).fetchone()[0]
    classified_groq = cursor.execute(
        "SELECT COUNT(*) FROM active_jobs WHERE field_classification_method = 'groq_v1'"
    ).fetchone()[0]
    never_attempted = cursor.execute(
        "SELECT COUNT(*) FROM active_jobs WHERE field_classification_attempted_at IS NULL"
    ).fetchone()[0]
    queue_by_status = cursor.execute(
        "SELECT status, COUNT(*) as n FROM groq_classification_queue GROUP BY status"
    ).fetchall()

    category_breakdown = cursor.execute("""
        SELECT jc.name, COUNT(j.job_id) as n
        FROM job_categories jc
        LEFT JOIN active_jobs j ON j.field_category_id = jc.category_id
        WHERE jc.parent_id IS NOT NULL
        GROUP BY jc.category_id
        ORDER BY n DESC
    """).fetchall()

    runs = cursor.execute(
        "SELECT * FROM classification_runs ORDER BY started_at DESC LIMIT 40"
    ).fetchall()

    queue_rows = cursor.execute(
        "SELECT gcq.*, j.title FROM groq_classification_queue gcq JOIN jobs j ON j.job_id = gcq.job_id ORDER BY gcq.created_at DESC LIMIT 100"
    ).fetchall()

    from src.pipeline_monitor import get_config
    config = get_config()

    conn.close()
    return render_template(
        "admin_classification.html",
        total=total, classified_local=classified_local, classified_groq=classified_groq,
        never_attempted=never_attempted, queue_by_status={r["status"]: r["n"] for r in queue_by_status},
        category_breakdown=category_breakdown, runs=runs, queue_rows=queue_rows, config=config,
    )


@app.route("/admin/classification/run-local", methods=["POST"])
@require_admin
def admin_classification_run_local():
    import uuid
    from src.classification.local_stage import classify_pending_jobs
    from src.classification.scheduling import _any_run_active
    from src.pipeline_monitor import get_config
    from src.storage import db
    from src.storage.db import get_free_connection as get_connection

    # This manual trigger used to race the automatic scheduler tick (or
    # itself, on a double-click) with no coordination at all - neither an
    # _any_run_active() check nor the file lock run_scheduler_tick() uses.
    # Confirmed happening in production: this route and a scheduled tick
    # both started a local_incremental run within microseconds of each
    # other, one hit "database is locked" against the other's in-flight
    # 5000-row batch, and - since there was no except clause here either -
    # crashed the request without ever marking its own run 'failed',
    # leaving an orphaned 'running' row. Reuses the exact same lock
    # run_scheduler_tick() uses so the two paths can never overlap again.
    def _run() -> dict:
        conn = get_connection()
        try:
            if _any_run_active(conn, "local_incremental"):
                return {"run_id": None, "status": "skipped", "reason": "a local_incremental run is already active"}
            run_id = str(uuid.uuid4())[:8]
            conn.execute(
                "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, 'local_incremental', 'manual', 'running', datetime('now'))",
                (run_id,),
            )
            conn.commit()
            try:
                # Capped, same reasoning as the scheduler's own local_incremental
                # tick: an admin click must return within a normal request/proxy
                # timeout, not hang for the ~68 minutes a full backlog would take.
                cfg = get_config()
                chunk_size = int(cfg.get("classification_local_chunk_size", 500))
                result = classify_pending_jobs(conn, run_id=run_id, limit=chunk_size)
                conn.execute("UPDATE classification_runs SET status='success', finished_at=datetime('now') WHERE run_id=?", (run_id,))
                conn.commit()
            except Exception:
                conn.execute("UPDATE classification_runs SET status='failed', finished_at=datetime('now') WHERE run_id=?", (run_id,))
                conn.commit()
                raise
            remaining = conn.execute("SELECT COUNT(*) FROM jobs WHERE field_classification_attempted_at IS NULL").fetchone()[0]
            return {"run_id": run_id, "status": "started", **result, "remaining": remaining}
        finally:
            conn.close()

    if db.fcntl is None:
        return jsonify(_run())

    db._CLASSIFICATION_SCHEDULER_LOCK_PATH.touch(exist_ok=True)
    with open(db._CLASSIFICATION_SCHEDULER_LOCK_PATH, "r+") as lock_file:
        db.fcntl.flock(lock_file, db.fcntl.LOCK_EX)
        try:
            return jsonify(_run())
        finally:
            db.fcntl.flock(lock_file, db.fcntl.LOCK_UN)


@app.route("/admin/classification/full-reclassify/preview", methods=["POST"])
@require_admin
def admin_classification_full_reclassify_preview():
    from src.market_classifier import classify_job
    from src.pipeline_monitor import get_config
    from src.storage.db import get_free_connection as get_connection
    conn = get_connection()
    confidence_threshold = float(get_config().get("classification_confidence_threshold", 0.62))
    rows = conn.execute("SELECT job_id, title, raw_description, field_category_id FROM jobs LIMIT 500").fetchall()
    would_change = 0
    for row in rows:
        match = classify_job(row["title"], row["raw_description"] or "")
        new_id = match.market_id if (match.market_id and match.confidence >= confidence_threshold) else None
        if new_id != row["field_category_id"]:
            would_change += 1
    conn.close()
    return jsonify({"sampled": len(rows), "would_change": would_change})


@app.route("/admin/classification/full-reclassify/confirm", methods=["POST"])
@require_admin
def admin_classification_full_reclassify_confirm():
    import uuid
    from src.storage.db import get_free_connection as get_connection
    run_id = str(uuid.uuid4())[:8]
    conn = get_connection()
    already = conn.execute("SELECT 1 FROM classification_runs WHERE run_type='local_full_backfill' AND status='running'").fetchone()
    if already:
        conn.close()
        return jsonify({"error": "a full re-classify is already running"}), 409
    conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, 'local_full_backfill', 'manual', 'running', datetime('now'))",
        (run_id,),
    )
    conn.commit()
    conn.close()
    return jsonify({"run_id": run_id, "status": "started"})


@app.route("/admin/classification/groq-backlog/run-now", methods=["POST"])
@require_admin
def admin_classification_groq_run_now():
    import uuid
    from src.classification.groq_stage import process_groq_queue
    from src.pipeline_monitor import get_config
    from src.storage.db import get_free_connection as get_connection
    conn = get_connection()
    run = conn.execute("SELECT run_id FROM classification_runs WHERE run_type='groq_backlog' AND status='running' LIMIT 1").fetchone()
    run_id = run["run_id"] if run else str(uuid.uuid4())[:8]
    if not run:
        conn.execute(
            "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES (?, 'groq_backlog', 'manual', 'running', datetime('now'))",
            (run_id,),
        )
        conn.commit()
    # Capped to one chunk per click, same reasoning as run-local above - an
    # unbounded call here would be ~1,480 sequential Groq API calls on a full
    # backlog, far past any request/proxy timeout.
    chunk_size = int(get_config().get("classification_groq_chunk_size", 25))
    result = process_groq_queue(conn, run_id=run_id, statuses=("pending",), limit=chunk_size)
    conn.close()
    return jsonify({"run_id": run_id, **result})


@app.route("/admin/classification/queue/<int:queue_id>/delete", methods=["POST"])
@require_admin
def admin_classification_queue_delete(queue_id: int):
    from src.storage.db import get_free_connection as get_connection
    conn = get_connection()
    conn.execute("DELETE FROM groq_classification_queue WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": queue_id})


@app.route("/admin/classification/config", methods=["POST"])
@require_admin
def admin_classification_config():
    from src.pipeline_monitor import set_config
    allowed = {
        "classification_confidence_threshold",
        "classification_idle_seconds", "classification_retry_cap",
        "classification_local_chunk_size", "classification_groq_chunk_size",
    }
    updated = []
    for key in allowed:
        val = request.form.get(key, "").strip()
        if val:
            set_config(key, val)
            updated.append(key)
    return jsonify({"updated": updated})


# ═══════════════════════════════════════════════════════════════════
# ADMIN: NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════

@app.route("/admin/notifications")
@require_admin
def admin_notifications():
    from src.notifications import PAGE_KEYS
    from src.storage.db import get_operational_connection
    conn = get_operational_connection()
    rows = conn.execute(
        "SELECT * FROM notifications ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("admin_notifications.html", notifications=rows, page_keys=PAGE_KEYS)


@app.route("/admin/notifications/create", methods=["POST"])
@require_admin
def admin_notifications_create():
    from datetime import datetime, timedelta, timezone
    from flask import redirect, url_for
    from src.auth.middleware import validate_csrf
    from src.notifications import PAGE_KEYS
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    heading = request.form.get("heading", "").strip()
    body = request.form.get("body", "").strip()
    severity = request.form.get("severity", "info")
    if severity not in ("info", "warning", "urgent"):
        severity = "info"

    all_pages = request.form.get("target_pages") == "all"
    if all_pages:
        target_pages = "all"
    else:
        selected = [p for p in request.form.getlist("pages") if p in PAGE_KEYS]
        target_pages = ",".join(selected) if selected else "all"

    expires_at = None
    hours_raw = request.form.get("expires_in_hours", "").strip()
    if hours_raw:
        try:
            hours = float(hours_raw)
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        except ValueError:
            pass

    if not heading or not body:
        return jsonify({"error": "heading and body are required"}), 400

    conn = get_operational_connection()
    conn.execute(
        "INSERT INTO notifications (heading, body, severity, target_pages, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (heading, body, severity, target_pages, datetime.now(timezone.utc).isoformat(), expires_at),
    )
    conn.commit()
    conn.close()
    # A newly-created notification must show up immediately, not wait out
    # the up-to-900s @cache.cached() window on /jobs, /dashboard, etc. -
    # this is a rare admin action, so clearing the whole cache rather than
    # tracking which specific cached routes this notification targets is
    # an acceptable, much simpler trade.
    cache.clear()
    return redirect(url_for("admin_notifications"))


@app.route("/admin/notifications/<int:notification_id>/remove", methods=["POST"])
@require_admin
def admin_notifications_remove(notification_id: int):
    from datetime import datetime, timezone
    from flask import redirect, url_for
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    conn = get_operational_connection()
    conn.execute(
        "UPDATE notifications SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), notification_id),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_notifications"))


@app.route("/admin/reports")
@require_admin
def admin_reports():
    from src.storage.db import get_operational_connection
    status = request.args.get("status", "open")
    conn = get_operational_connection()
    if status == "all":
        rows = conn.execute("SELECT * FROM job_reports ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM job_reports WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    conn.close()
    return render_template("admin_reports.html", reports=rows, current_status=status)


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@require_admin
def admin_reports_resolve(report_id):
    from flask import redirect, url_for
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    admin_notes = request.form.get("admin_notes", "").strip()
    conn = get_operational_connection()
    conn.execute(
        "UPDATE job_reports SET status = 'resolved', admin_notes = ?, resolved_at = ? WHERE report_id = ? AND status = 'open'",
        (admin_notes or None, datetime.now(timezone.utc).isoformat(), report_id),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_reports"))


@app.route("/admin/reports/<int:report_id>/dismiss", methods=["POST"])
@require_admin
def admin_reports_dismiss(report_id):
    from flask import redirect, url_for
    from src.auth.middleware import validate_csrf
    from src.storage.db import get_operational_connection

    err = validate_csrf()
    if err:
        return err

    conn = get_operational_connection()
    conn.execute(
        "UPDATE job_reports SET status = 'dismissed', resolved_at = ? WHERE report_id = ? AND status = 'open'",
        (datetime.now(timezone.utc).isoformat(), report_id),
    )
    conn.commit()
    conn.close()
    cache.clear()
    return redirect(url_for("admin_reports"))


# ── Auto-scheduler background thread ─────────────────────────────────────────

def _scheduler_tick_once(now) -> None:
    """One iteration of the auto-scheduler's work, extracted from
    _auto_scheduler_loop() so it's directly callable/testable without the
    surrounding time.sleep(60)/while True loop. `now` is passed in (not
    read internally) so tests can control it precisely.

    Classification tick and the rotation check each get their own
    try/except - a persistent failure in one (e.g. a bad classification
    row) must not silently block the other from ever running again. Both
    used to share one try/except with the ingest/crawl scheduler section
    above, so a classification exception on every tick would skip the
    rotation check forever, starving it."""
    from datetime import timedelta, timezone as _tz
    from src.pipeline_monitor import compute_next_run, get_config, get_running_runs, launch_pipeline

    _log = logging.getLogger("auto_scheduler")

    cfg = get_config()
    for mode in ("ingest-only", "crawl"):
        nxt = compute_next_run(mode, cfg)
        if not nxt:
            continue
        nxt_dt = datetime.fromisoformat(nxt.replace("Z", "+00:00"))
        if nxt_dt.tzinfo is None:
            nxt_dt = nxt_dt.replace(tzinfo=_tz.utc)
        if now >= nxt_dt:
            already = [r for r in get_running_runs() if r["mode"] == mode]
            if not already:
                launch_pipeline(mode, trigger="schedule")
                _log.info("Auto-launched %s (was due %s)", mode, nxt_dt.isoformat())

    try:
        from src.classification.scheduling import run_scheduler_tick
        from src.storage.db import get_free_connection as _get_classification_conn
        classification_conn = _get_classification_conn()
        try:
            run_scheduler_tick(classification_conn, last_request_at=_last_request_at, now=now)
        finally:
            classification_conn.close()
    except Exception as exc:
        _log.error("Classification scheduler tick failed: %s", exc)

    try:
        from src.db_rotation import rotate
        from src.pipeline_monitor import get_config as _get_rotation_cfg
        rotation_cfg = _get_rotation_cfg()
        last_rotation_at = rotation_cfg.get("last_rotation_at")
        max_interval_hours = int(rotation_cfg.get("rotation_max_interval_hours", 12))
        rotation_due = True
        if last_rotation_at:
            last_dt = datetime.fromisoformat(last_rotation_at.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=_tz.utc)
            rotation_due = now >= last_dt + timedelta(hours=max_interval_hours)
        if rotation_due:
            rotate(last_request_at=_last_request_at, now=now)
    except Exception as exc:
        _log.error("Rotation check failed: %s", exc)


def _auto_scheduler_loop() -> None:
    """Check every 60 s whether a scheduled run is due; launch it if so."""
    import time as _time
    from datetime import timezone as _tz

    while True:
        _time.sleep(60)
        try:
            _scheduler_tick_once(datetime.now(_tz.utc))
        except Exception as exc:
            logging.getLogger("auto_scheduler").error("Scheduler error: %s", exc)


_scheduler_thread = Thread(target=_auto_scheduler_loop, daemon=True, name="auto-scheduler")
_scheduler_thread.start()


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
