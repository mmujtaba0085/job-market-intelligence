"""
src/auth/routes.py
──────────────────
Blueprint: login, logout, my-keys (user's own API keys), change-password.
"""

import secrets
from flask import (
    Blueprint, flash, g, jsonify, redirect,
    render_template, request, session, url_for,
)

from .middleware import (
    get_current_user, require_auth, validate_csrf,
    csrf_token, is_safe_redirect_url,
)
from .models import (
    authenticate_user, change_password, generate_api_key,
    get_user_by_id, list_api_keys, revoke_api_key,
    login_is_rate_limited, record_login_attempt,
)

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("dashboard"))

    error = None
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
    return render_template("auth/login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


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
