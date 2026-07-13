from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def secured_app(tmp_path, monkeypatch):
    import src.auth.models as models

    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()

    import web_viewer

    web_viewer.app.config.update(TESTING=True, SECRET_KEY="test-secret", SESSION_COOKIE_SECURE=False)
    return web_viewer.app, models


def _set_session(client, user_id: int, csrf: str = "test-csrf") -> str:
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["_csrf_token"] = csrf
    return csrf


def _user_id(models, username: str) -> int:
    return next(user["id"] for user in models.list_users() if user["username"] == username)


def test_login_rejects_external_next_target(secured_app):
    app, models = secured_app
    client = app.test_client()
    csrf = _set_session(client, user_id=0)

    response = client.post(
        "/auth/login?next=https://evil.example/steal",
        data={"username": "admin", "password": "admin-pass-123", "_csrf_token": csrf},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_missing_csrf_is_rejected(secured_app):
    app, models = secured_app
    models.create_user("viewer", "viewer@example.com", "viewer-pass")
    client = app.test_client()
    _set_session(client, _user_id(models, "viewer"))

    response = client.post("/auth/me/keys/create", data={"name": "unsafe"})

    assert response.status_code == 400


def test_viewer_cannot_access_admin_or_mutations(secured_app):
    app, models = secured_app
    models.create_user("viewer", "viewer@example.com", "viewer-pass")
    client = app.test_client()
    csrf = _set_session(client, _user_id(models, "viewer"))

    assert client.get("/admin").status_code == 403
    response = client.post(
        "/api/jobs/quality/analyze",
        json={"job_ids": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_admin_session_can_reach_admin_mutation(secured_app):
    app, models = secured_app
    client = app.test_client()
    csrf = _set_session(client, _user_id(models, "admin"))

    assert client.get("/admin").status_code == 200
    assert client.get("/admin/quality").status_code == 200
    response = client.post(
        "/api/jobs/quality/analyze",
        json={"job_ids": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400


def test_api_keys_are_read_only_and_scope_limited(secured_app):
    app, models = secured_app
    models.create_user("api-user", "api@example.com", "api-user-pass")
    user_id = _user_id(models, "api-user")
    key = models.generate_api_key(user_id, "jobs-only", scopes="jobs:read")["key"]
    headers = {"X-API-Key": key}
    client = app.test_client()

    assert client.get("/admin", headers=headers).status_code == 403
    assert client.get("/api/jobs?limit=1", headers=headers).status_code == 200
    assert client.get("/api/markets", headers=headers).status_code == 403
    assert client.get("/export/skills", headers=headers).status_code == 403
    assert client.post("/api/jobs/quality/analyze", headers=headers, json={"job_ids": []}).status_code == 403


def test_plaintext_key_is_not_stored_in_flash_session(secured_app):
    app, models = secured_app
    models.create_user("viewer", "viewer@example.com", "viewer-pass")
    client = app.test_client()
    csrf = _set_session(client, _user_id(models, "viewer"))

    response = client.post(
        "/auth/me/keys/create",
        data={"name": "once", "rate_limit_hour": "10", "_csrf_token": csrf},
    )

    assert response.status_code == 200
    assert b"jmi_" in response.data
    with client.session_transaction() as session:
        assert not session.get("_flashes")


def test_login_throttle_and_last_admin_protection(secured_app):
    app, models = secured_app
    for _ in range(5):
        models.record_login_attempt("admin", "127.0.0.1", False)
    assert models.login_is_rate_limited("admin", "127.0.0.1")

    admin_id = _user_id(models, "admin")
    with pytest.raises(ValueError, match="last active admin"):
        models.update_user(admin_id, active=0)


def test_click_tracking_rejects_unsafe_redirect_scheme(secured_app):
    app, models = secured_app
    models.create_user("viewer", "viewer@example.com", "viewer-pass")
    client = app.test_client()
    _set_session(client, _user_id(models, "viewer"))

    assert client.get("/sheets/track_job?url=javascript:alert(1)").status_code == 400
    response = client.get("/sheets/track_job?url=https://jobs.example/apply")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://jobs.example/apply"
