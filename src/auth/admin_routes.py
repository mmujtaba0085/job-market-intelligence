"""Admin-only user, API-key, and access-log routes."""

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from .middleware import require_admin
from .models import (
    change_password,
    create_user,
    generate_api_key,
    get_access_logs,
    get_access_stats,
    get_user_by_id,
    list_api_keys,
    list_users,
    revoke_api_key,
    update_api_key_limit,
    update_user,
)

admin_auth_bp = Blueprint("admin_auth", __name__, template_folder="../../templates/auth")


@admin_auth_bp.route("/admin/auth/users")
@require_admin
def users():
    return render_template("auth/admin_users.html", users=list_users())


@admin_auth_bp.route("/admin/auth/users/create", methods=["POST"])
@require_admin
def create_user_route():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "viewer")
    if not username or not email or not password:
        flash("All fields required.", "error")
        return redirect(url_for("admin_auth.users"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("admin_auth.users"))
    try:
        create_user(username, email, password, role)
        flash(f"User '{username}' created.", "success")
    except Exception as exc:
        flash(f"Error: {exc}", "error")
    return redirect(url_for("admin_auth.users"))


@admin_auth_bp.route("/admin/auth/users/<int:user_id>/toggle", methods=["POST"])
@require_admin
def toggle_user(user_id):
    user = get_user_by_id(user_id)
    if user:
        new_state = 0 if user["active"] else 1
        try:
            update_user(user_id, active=new_state)
            state = "enabled" if new_state else "disabled"
            flash(f"User '{user['username']}' {state}.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    return redirect(url_for("admin_auth.users"))


@admin_auth_bp.route("/admin/auth/users/<int:user_id>/role", methods=["POST"])
@require_admin
def change_role(user_id):
    try:
        update_user(user_id, role=request.form.get("role", "viewer"))
        flash("Role updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_auth.users"))


@admin_auth_bp.route("/admin/auth/users/<int:user_id>/reset-password", methods=["POST"])
@require_admin
def reset_password(user_id):
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("admin_auth.users"))
    change_password(user_id, new_password)
    flash("Password reset.", "success")
    return redirect(url_for("admin_auth.users"))


@admin_auth_bp.route("/admin/auth/keys")
@require_admin
def api_keys():
    return render_template("auth/admin_api_keys.html", keys=list_api_keys(), users=list_users())


@admin_auth_bp.route("/admin/auth/keys/create", methods=["POST"])
@require_admin
def create_key():
    try:
        user_id = int(request.form.get("user_id", 0))
        rate = int(request.form.get("rate_limit_hour", 1000))
    except ValueError:
        flash("Invalid input.", "error")
        return redirect(url_for("admin_auth.api_keys"))

    name = request.form.get("name", "").strip() or "Key"
    scopes = request.form.get(
        "scopes",
        "jobs:read,exports:read,analytics:read,sources:read,markets:read",
    )
    try:
        record = generate_api_key(user_id, name, rate_limit_hour=rate, scopes=scopes)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_auth.api_keys"))
    return render_template(
        "auth/admin_api_keys.html",
        keys=list_api_keys(),
        users=list_users(),
        created_key=record["key"],
    )


@admin_auth_bp.route("/admin/auth/keys/<int:key_id>/revoke", methods=["POST"])
@require_admin
def revoke_key(key_id):
    revoke_api_key(key_id)
    flash("Key revoked.", "success")
    return redirect(url_for("admin_auth.api_keys"))


@admin_auth_bp.route("/admin/auth/keys/<int:key_id>/rate-limit", methods=["POST"])
@require_admin
def update_rate_limit(key_id):
    try:
        limit = int(request.form.get("rate_limit_hour", 1000))
    except ValueError:
        limit = 1000
    update_api_key_limit(key_id, limit)
    flash("Rate limit updated.", "success")
    return redirect(url_for("admin_auth.api_keys"))


@admin_auth_bp.route("/admin/auth/logs")
@require_admin
def access_logs():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 100
    return render_template(
        "auth/admin_access_logs.html",
        logs=get_access_logs(
            limit=per_page,
            offset=(page - 1) * per_page,
            user_id=request.args.get("user_id", type=int),
        ),
        stats=get_access_stats(),
        users=list_users(),
        page=page,
        per_page=per_page,
        user_filter=request.args.get("user_id", type=int),
    )


@admin_auth_bp.route("/api/admin/auth/stats")
@require_admin
def api_stats():
    return jsonify(get_access_stats())
