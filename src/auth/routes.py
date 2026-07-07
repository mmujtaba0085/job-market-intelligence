"""
src/auth/routes.py
──────────────────
Blueprint: login, logout, my-keys (user's own API keys), change-password.
"""

import logging
import secrets
from flask import (
    Blueprint, flash, g, jsonify, redirect,
    render_template, request, session, url_for,
)

from config.settings import GOOGLE_OAUTH_ENABLED, WEB_VIEWER_URL
from .middleware import (
    get_current_user, require_auth, validate_csrf,
    csrf_token, is_safe_redirect_url,
)
from .models import (
    authenticate_user, change_password, generate_api_key,
    get_user_by_id, list_api_keys, revoke_api_key,
    login_is_rate_limited, record_login_attempt,
    get_user_by_google_id, get_user_by_email, create_google_user,
    link_google_account, touch_last_login,
)

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")
logger = logging.getLogger(__name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("dashboard"))

    error = request.args.get("oauth_error") or None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip = request.remote_addr or "unknown"

        if login_is_rate_limited(username, ip):
            error = "Too many failed attempts. Please wait 15 minutes."
        else:
            user = authenticate_user(username, password)
            record_login_attempt(username, ip, success=bool(user))
            if user:
                session.clear()
                session["user_id"] = user["id"]
                session.permanent = True
                next_url = request.args.get("next", "")
                if next_url and is_safe_redirect_url(next_url):
                    return redirect(next_url)
                return redirect(url_for("dashboard"))
            error = "Invalid username or password."

    # Ensure CSRF token exists for the form
    csrf_token()
    return render_template("auth/login.html", error=error, google_oauth_enabled=GOOGLE_OAUTH_ENABLED)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/google")
def google_login():
    if not GOOGLE_OAUTH_ENABLED:
        return "Google sign-in is not configured.", 404
    from .oauth_google import oauth

    next_url = request.args.get("next", "")
    session["_post_login_next"] = next_url if is_safe_redirect_url(next_url) else ""

    redirect_uri = f"{WEB_VIEWER_URL.rstrip('/')}/auth/google/callback"
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
def google_callback():
    if not GOOGLE_OAUTH_ENABLED:
        return "Google sign-in is not configured.", 404
    from .oauth_google import oauth

    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get("userinfo") or {}
    except Exception as exc:  # noqa: BLE001 — any OAuth/network failure lands here
        logger.warning("[auth] Google OAuth callback failed: %s", exc)
        return redirect(url_for("auth.login", oauth_error="Google sign-in failed. Please try again."))

    email = (userinfo.get("email") or "").strip().lower()
    google_id = userinfo.get("sub")
    if not email or not google_id or not userinfo.get("email_verified", True):
        return redirect(url_for("auth.login", oauth_error="Your Google account's email must be verified."))

    user = get_user_by_google_id(google_id)
    if not user:
        existing = get_user_by_email(email)
        if existing:
            link_google_account(existing["id"], google_id)
            user = existing
        else:
            try:
                user = create_google_user(google_id, email, userinfo.get("name") or "")
            except ValueError as exc:
                return redirect(url_for("auth.login", oauth_error=str(exc)))

    if not user.get("active", 1):
        return redirect(url_for("auth.login", oauth_error="Your account has been disabled."))

    touch_last_login(user["id"])
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True

    next_url = session.pop("_post_login_next", "")
    if next_url and is_safe_redirect_url(next_url):
        return redirect(next_url)
    return redirect(url_for("dashboard"))


@auth_bp.route("/me/keys")
@require_auth
def my_keys():
    user = get_current_user()
    keys = list_api_keys(user_id=user["id"])
    return render_template("auth/my_keys.html", keys=keys, user=user)


@auth_bp.route("/me/keys/create", methods=["POST"])
@require_auth
def create_my_key():
    # CSRF validation
    err = validate_csrf()
    if err:
        return err

    user = get_current_user()
    name = request.form.get("name", "").strip() or "My Key"
    try:
        rate = int(request.form.get("rate_limit_hour", 500))
    except ValueError:
        rate = 500
    scopes = request.form.get("scopes", "jobs:read,exports:read,analytics:read,sources:read,markets:read")
    record = generate_api_key(user["id"], name, rate_limit_hour=rate, scopes=scopes)

    # Return 200 with key in body — never store plaintext in flash/session
    keys = list_api_keys(user_id=user["id"])
    return render_template(
        "auth/my_keys.html",
        keys=keys,
        user=user,
        created_key=record["key"],
    ), 200


@auth_bp.route("/me/keys/<int:key_id>/revoke", methods=["POST"])
@require_auth
def revoke_my_key(key_id):
    # CSRF validation
    err = validate_csrf()
    if err:
        return err
    user = get_current_user()
    if revoke_api_key(key_id, user_id=user["id"]):
        flash("Key revoked.", "success")
    return redirect(url_for("auth.my_keys"))


@auth_bp.route("/me/password", methods=["GET", "POST"])
@require_auth
def change_my_password():
    user = get_current_user()
    error = None
    success = None
    if request.method == "POST":
        err = validate_csrf()
        if err:
            return err
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not authenticate_user(user["username"], current):
            error = "Current password is incorrect."
        elif len(new) < 8:
            error = "New password must be at least 8 characters."
        elif new != confirm:
            error = "Passwords do not match."
        else:
            change_password(user["id"], new)
            success = "Password updated."
    csrf_token()
    return render_template("auth/change_password.html", user=user, error=error, success=success)
