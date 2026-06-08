"""
src/auth/middleware.py
──────────────────────
Decorators and helpers for protecting Flask routes.

Usage:
    @require_auth          — session login OR valid API key
    @require_admin         — session login with role='admin'
    @optional_auth         — sets g.current_user if authenticated, never blocks
    get_current_user()     — returns current user dict or None
"""

import secrets
import time
from functools import wraps

from flask import g, jsonify, redirect, request, session, url_for

from .models import authenticate_api_key, check_rate_limit, get_user_by_id, log_access, mark_api_key_used


# ─── Context helpers ──────────────────────────────────────────────────────────

def get_current_user() -> dict | None:
    return getattr(g, "current_user", None)


def csrf_token() -> str:
    """Return the session CSRF token, creating it when needed."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf():
    """Require a matching token for unsafe session and login requests."""
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None
    if getattr(g, "auth_type", None) == "api_key":
        return None

    expected = session.get("_csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        if _is_api_request():
            return jsonify({"error": "Invalid or missing CSRF token"}), 400
        return "Invalid or missing CSRF token", 400
    return None


def required_api_scope(path: str) -> str | None:
    """Map approved read-only API/export paths to their required scope."""
    mappings = (
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
    for prefix, scope in mappings:
        if path.startswith(prefix):
            return scope
    return None


def api_key_has_scope(user: dict, scope: str) -> bool:
    scopes = {item.strip() for item in (user.get("scopes") or "").split(",") if item.strip()}
    return scope in scopes


def _extract_api_key() -> str | None:
    # Authorization: Bearer <key>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    # X-API-Key: <key>
    key = request.headers.get("X-API-Key", "").strip()
    if key:
        return key
    return None


def _load_user_from_request() -> tuple[dict | None, str | None, int | None]:
    """
    Returns (user_dict, auth_type, api_key_id).
    Tries session first, then API key header.
    """
    # 1. Session auth
    user_id = session.get("user_id")
    if user_id:
        user = get_user_by_id(user_id)
        if user and user["active"]:
            return user, "session", None

    # 2. API key auth
    raw_key = _extract_api_key()
    if raw_key:
        record = authenticate_api_key(raw_key)
        if record:
            # Rate limit check
            if not check_rate_limit(record["id"], record["rate_limit_hour"]):
                return None, "api_key_rate_limited", None
            mark_api_key_used(record["id"])
            user = {
                "id": record["user_id"],
                "username": record["username"],
                "role": record["role"],
                "active": record["user_active"],
                "scopes": record.get("scopes", ""),
            }
            return user, "api_key", record["id"]

    return None, None, None


# ─── Before-request hook (call from app factory) ──────────────────────────────

def load_logged_in_user():
    """Register with app.before_request to populate g.current_user on every request."""
    user, auth_type, api_key_id = _load_user_from_request()
    g.current_user = user
    g.auth_type = auth_type
    g.api_key_id = api_key_id
    g._request_start = time.monotonic()


def log_request_access(response):
    """Register with app.after_request to write access log."""
    try:
        user = getattr(g, "current_user", None)
        auth_type = getattr(g, "auth_type", None)
        api_key_id = getattr(g, "api_key_id", None)
        start = getattr(g, "_request_start", time.monotonic())
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Skip healthz and static to keep logs clean
        path = request.path
        if path in ("/healthz",) or path.startswith("/static"):
            return response

        log_access(
            endpoint=path,
            method=request.method,
            ip=request.remote_addr or "",
            status_code=response.status_code,
            response_ms=elapsed_ms,
            user_id=user["id"] if user else None,
            api_key_id=api_key_id,
            auth_type=auth_type,
        )
    except Exception:
        pass
    return response


# ─── Decorators ───────────────────────────────────────────────────────────────

def require_auth(f):
    """Allow only authenticated users (session or API key)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            auth_type = getattr(g, "auth_type", None)
            if auth_type == "api_key_rate_limited":
                return jsonify({"error": "Rate limit exceeded"}), 429
            if _is_api_request():
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Allow only admin-role session users. Returns 403 for authenticated non-admins."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            if _is_api_request():
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("auth.login", next=request.url))
        if user.get("role") != "admin":
            if _is_api_request():
                return jsonify({"error": "Forbidden — admin only"}), 403
            return jsonify({"error": "Forbidden — admin only"}), 403
        return f(*args, **kwargs)
    return decorated


def optional_auth(f):
    """Never blocks — makes g.current_user available if authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _is_api_request() -> bool:
    return (
        request.path.startswith("/api/")
        or request.path.startswith("/export/")
        or request.headers.get("Accept", "").startswith("application/json")
        or bool(request.headers.get("X-API-Key"))
        or request.headers.get("Authorization", "").startswith("Bearer ")
    )


def is_safe_redirect_url(url: str) -> bool:
    """Return True only for relative, same-origin URLs."""
    if not url:
        return False
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return not parsed.netloc and not parsed.scheme
