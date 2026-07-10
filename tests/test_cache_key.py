"""
Verifies web_viewer.py's cache setup: Flask-Caching's @cache.cached()
does NOT include the query string in its default cache key (confirmed by
reading flask_caching.Cache.cached's source - query_string defaults to
False), so every cached route must use the custom key_prefix callable
tested here instead of relying on defaults. This test would have caught
the bug where two different /jobs?market=... filter combinations
incorrectly share one cached response.
"""
from flask import Flask, request, g
from flask_caching import Cache

from web_viewer import _role_aware_cache_key


def _build_test_app():
    app = Flask(__name__)
    app.config["CACHE_TYPE"] = "SimpleCache"
    cache = Cache(app)
    call_count = {"n": 0}

    @app.before_request
    def _set_role():
        if request.headers.get("X-Test-Admin") == "1":
            g.current_user = {"id": 2, "role": "admin"}
        elif request.headers.get("X-Test-User-Id"):
            g.current_user = {"id": int(request.headers["X-Test-User-Id"]), "role": "viewer"}
        else:
            g.current_user = {"id": 1, "role": "viewer"}

    @app.route("/thing")
    @cache.cached(timeout=900, key_prefix=_role_aware_cache_key, response_hit_indication=True)
    def thing():
        call_count["n"] += 1
        return f"call {call_count['n']}"

    return app, call_count


def test_different_query_strings_get_separate_cache_entries():
    app, call_count = _build_test_app()
    with app.test_client() as c:
        c.get("/thing?market=ai_ml_global")
        c.get("/thing?market=ai_ml_global")  # repeat -> cache hit
        c.get("/thing?market=swe_backend_global")  # different query -> fresh call
    assert call_count["n"] == 2


def test_admin_and_viewer_get_separate_cache_entries_for_same_url():
    app, call_count = _build_test_app()
    with app.test_client() as c:
        c.get("/thing")  # viewer
        c.get("/thing")  # viewer repeat -> cache hit
        c.get("/thing", headers={"X-Test-Admin": "1"})  # admin, same URL -> fresh call
        c.get("/thing", headers={"X-Test-Admin": "1"})  # admin repeat -> cache hit
    assert call_count["n"] == 2


def test_two_viewers_with_different_ids_get_separate_cache_entries_for_same_url():
    """Regression guard for the cross-user leak fixed in _role_aware_cache_key:
    both requests below have the SAME role ("viewer"), so a role-only key
    would incorrectly collide them into one cache entry - meaning the first
    viewer's rendered page (including their own username in the nav) would
    be served to the second viewer. Keying by user id keeps them separate.
    """
    app, call_count = _build_test_app()
    with app.test_client() as c:
        c.get("/thing", headers={"X-Test-User-Id": "1"})  # viewer #1
        c.get("/thing", headers={"X-Test-User-Id": "1"})  # viewer #1 repeat -> cache hit
        c.get("/thing", headers={"X-Test-User-Id": "3"})  # viewer #3, same role, different id -> fresh call
    assert call_count["n"] == 2
