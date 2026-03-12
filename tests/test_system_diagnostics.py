"""System-level diagnostics for core project subsystems.

These tests focus on configuration wiring and subsystem boot health.
"""

from __future__ import annotations

import importlib
from pathlib import Path


def test_settings_reads_sheet_ids_from_env(monkeypatch):
    """Ensure sheet IDs and Flask secret are truly environment-driven."""
    monkeypatch.setenv("SHEETS_CANADA_ID", "ca_test_id")
    monkeypatch.setenv("SHEETS_UK_ID", "uk_test_id")
    monkeypatch.setenv("SHEETS_US_ID", "us_test_id")
    monkeypatch.setenv("FLASK_SECRET_KEY", "unit-test-secret")

    import config.settings as settings

    settings = importlib.reload(settings)

    assert settings.SHEETS_CANADA_ID == "ca_test_id"
    assert settings.SHEETS_UK_ID == "uk_test_id"
    assert settings.SHEETS_US_ID == "us_test_id"
    assert settings.FLASK_SECRET_KEY == "unit-test-secret"


def test_web_viewer_uses_configured_secret(monkeypatch):
    """Verify Flask app secret comes from config/settings.py."""
    monkeypatch.setenv("FLASK_SECRET_KEY", "web-viewer-secret")

    import config.settings as settings
    settings = importlib.reload(settings)

    import web_viewer
    web_viewer = importlib.reload(web_viewer)

    assert web_viewer.app.secret_key == settings.FLASK_SECRET_KEY
    assert web_viewer.app.secret_key != "job-market-intelligence-secret-key-2026"


def test_core_modules_importable():
    """Core engine modules should import without runtime errors."""
    modules = [
        "src.orchestrator",
        "src.normalizer",
        "src.skill_extractor",
        "src.taxonomy_mapper",
        "src.country_detector",
        "src.storage.db",
        "src.reports.google_sheets_export",
        "src.reports.tracker_directory_export",
        "web_viewer",
    ]

    failed = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # pragma: no cover - diagnostic aggregation
            failed.append(f"{mod}: {exc}")

    assert not failed, "Import failures:\n" + "\n".join(failed)


def test_collector_modules_importable():
    """All collector modules should be importable as a baseline health check."""
    collectors_dir = Path("src/collectors")
    files = sorted(p for p in collectors_dir.glob("*.py") if p.name not in {"__init__.py"})

    failed = []
    for path in files:
        mod = f"src.collectors.{path.stem}"
        try:
            importlib.import_module(mod)
        except Exception as exc:  # pragma: no cover - diagnostic aggregation
            failed.append(f"{mod}: {exc}")

    assert not failed, "Collector import failures:\n" + "\n".join(failed)


def test_critical_web_routes_registered():
    """Ensure main admin and Sheets routes are wired into Flask app."""
    import web_viewer

    rules = {rule.rule for rule in web_viewer.app.url_map.iter_rules()}
    expected = {
        "/admin",
        "/admin/normalize",
        "/admin/normalize-titles",
        "/admin/sheets_staging",
        "/admin/sheets_analytics",
        "/sheets/track",
    }

    missing = sorted(expected - rules)
    assert not missing, f"Missing routes: {missing}"


def test_db_core_tables_exist():
    """Verify core database tables required by major systems exist."""
    from src.storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {row[0] for row in rows}
    finally:
        conn.close()

    required = {
        "jobs",
        "skills",
        "weekly_metrics",
        "sheets_staging",
        "sheets_click_tracking",
        "sheets_targets",
        "sheets_target_countries",
    }

    missing = sorted(required - names)
    assert not missing, f"Missing DB tables: {missing}"
