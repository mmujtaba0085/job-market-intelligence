"""
tests/test_scheduler_tick_isolation.py
────────────────────────────────────────
Regression test for a real bug found by the final whole-branch review of the
rotating-DB-architecture plan: web_viewer.py's _auto_scheduler_loop() ran the
classification tick and the rotation-due check inside the same try/except.
A persistent exception in the classification tick (e.g. a bad classification
row) would silently prevent the rotation check from EVER running again, as
long as the classification error kept recurring - since the exception jumped
straight past the rotation code to the shared except block.

Fixed by extracting the per-tick body into _scheduler_tick_once(now) and
giving the classification tick and the rotation check their own independent
try/except pairs, so a failure in one cannot block the other on the same
tick (see web_viewer.py::_scheduler_tick_once).
"""
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

import src.storage.db as db


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    db.run_migrations()
    return tmp_path


def test_rotation_check_still_runs_when_classification_tick_raises(isolated_paths):
    import web_viewer

    with patch(
        "src.classification.scheduling.run_scheduler_tick",
        side_effect=RuntimeError("boom - simulated persistent classification failure"),
    ), patch("src.db_rotation.rotate") as mock_rotate:
        now = datetime.now(timezone.utc)
        # No last_rotation_at seeded -> rotation_due defaults True, so a
        # working rotation check should call rotate() unconditionally here.
        web_viewer._scheduler_tick_once(now)

    mock_rotate.assert_called_once_with(last_request_at=web_viewer._last_request_at, now=now)


def test_classification_tick_still_runs_when_rotation_check_raises(isolated_paths):
    import web_viewer

    with patch(
        "src.classification.scheduling.run_scheduler_tick"
    ) as mock_tick, patch(
        "src.db_rotation.rotate", side_effect=RuntimeError("boom - simulated persistent rotation failure")
    ):
        now = datetime.now(timezone.utc)
        # Must not raise - the rotation failure is contained to its own
        # try/except, same as the classification tick's.
        web_viewer._scheduler_tick_once(now)

    mock_tick.assert_called_once()


def test_scheduler_tick_once_does_not_propagate_either_failure(isolated_paths):
    """Belt-and-suspenders: even if BOTH blocks fail on the same tick,
    _scheduler_tick_once() itself must not raise - each failure is logged
    and contained independently."""
    import web_viewer

    with patch(
        "src.classification.scheduling.run_scheduler_tick", side_effect=RuntimeError("classification boom")
    ), patch("src.db_rotation.rotate", side_effect=RuntimeError("rotation boom")):
        web_viewer._scheduler_tick_once(datetime.now(timezone.utc))
