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
    monkeypatch.setattr(db, "_INGEST_SCHEDULER_LOCK_PATH", tmp_path / ".ingest_scheduler.lock")
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


class TestIngestCrawlSchedulerLock:
    """Regression coverage for the multi-worker race fixed 2026-07-19:
    gunicorn's N independently-started scheduler threads (one per worker
    process) could each see "nothing running yet" via get_running_runs()
    and all launch the same mode within moments of each other - confirmed
    live via pipeline_runs history (ingest-only firing every ~6h instead
    of the configured 12h, with near-simultaneous pairs). Fixed with the
    same cross-process fcntl lock already used for classification
    scheduling (see src.classification.scheduling.run_scheduler_tick)."""

    def test_lock_acquired_before_and_released_after_ingest_crawl_check(self, isolated_paths, monkeypatch):
        if db.fcntl is None:
            pytest.skip("fcntl is Unix-only; this platform uses the no-op path (covered separately)")

        import web_viewer

        call_order = []
        real_flock = db.fcntl.flock

        def tracking_flock(fd, operation):
            if operation == db.fcntl.LOCK_EX:
                call_order.append("lock")
            elif operation == db.fcntl.LOCK_UN:
                call_order.append("unlock")
            return real_flock(fd, operation)

        monkeypatch.setattr(db.fcntl, "flock", tracking_flock)
        monkeypatch.setattr(
            web_viewer, "_run_ingest_crawl_scheduler_tick", lambda now: call_order.append("check")
        )
        with patch("src.classification.scheduling.run_scheduler_tick"), patch("src.db_rotation.rotate"):
            web_viewer._scheduler_tick_once(datetime.now(timezone.utc))

        assert call_order == ["lock", "check", "unlock"]

    def test_no_op_lock_path_still_runs_ingest_crawl_check_on_windows(self, isolated_paths, monkeypatch):
        # Directly exercises the fcntl-unavailable branch regardless of the
        # platform actually running this test.
        import web_viewer

        monkeypatch.setattr(db, "fcntl", None)
        called = []
        monkeypatch.setattr(web_viewer, "_run_ingest_crawl_scheduler_tick", lambda now: called.append(now))
        with patch("src.classification.scheduling.run_scheduler_tick"), patch("src.db_rotation.rotate"):
            web_viewer._scheduler_tick_once(datetime.now(timezone.utc))

        assert len(called) == 1

    def test_concurrent_ticks_launch_each_due_mode_only_once(self, isolated_paths, monkeypatch):
        """The actual race: two ticks - simulating two gunicorn workers'
        scheduler threads - both start at the same moment with ingest-only
        due and nothing running. Without the lock, both used to reach
        get_running_runs(), both see an empty list, and both call
        launch_pipeline(). With it, only the first to acquire the lock may
        act; by the time the second acquires it, the first's start_run()
        write is already visible via get_running_runs(), so the second
        correctly skips. Real start_run()/get_running_runs() run against
        the isolated DB (only subprocess.Popen is mocked, so no real
        orchestrator process is spawned) - this is what makes the lock's
        effect on real state observable, not just call ordering. A
        threading.Barrier synchronizes both threads' *arrival* at
        _scheduler_tick_once() so they genuinely contend for the lock
        instead of happening to run sequentially by chance.

        Skipped on Windows: with fcntl unavailable, _scheduler_tick_once()
        takes the no-op branch and there is no lock to contend for - the
        assertion would only hold by GIL/OS-scheduling luck, not by any
        actual guarantee, since the real protection literally isn't
        active on this platform."""
        if db.fcntl is None:
            pytest.skip("fcntl is Unix-only; no lock exists to test on this platform (covered separately)")

        import threading

        import src.pipeline_monitor as pipeline_monitor
        import web_viewer

        monkeypatch.setattr(
            pipeline_monitor, "compute_next_run",
            lambda mode, cfg: "2020-01-01T00:00:00+00:00" if mode == "ingest-only" else None,
        )

        popen_calls = []
        monkeypatch.setattr(
            pipeline_monitor.subprocess, "Popen",
            lambda cmd, **kwargs: popen_calls.append(cmd) or Mock(),
        )

        start_barrier = threading.Barrier(2, timeout=5)
        results = []

        def run_tick():
            try:
                start_barrier.wait()  # both threads hit _scheduler_tick_once() together
                with patch("src.classification.scheduling.run_scheduler_tick"), patch("src.db_rotation.rotate"):
                    web_viewer._scheduler_tick_once(datetime.now(timezone.utc))
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        t1 = threading.Thread(target=run_tick)
        t2 = threading.Thread(target=run_tick)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert results == ["ok", "ok"]
        ingest_launches = [c for c in popen_calls if "ingest-only" in c]
        assert len(ingest_launches) == 1, f"expected exactly 1 ingest-only launch, got {len(ingest_launches)}: {popen_calls}"
