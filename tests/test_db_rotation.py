"""
tests/test_db_rotation.py
──────────────────────────
rotate() end-to-end: Buffer merges into Free (reusing db.upsert_job()'s
existing url_hash dedup, not reinventing it), the pointer flips, and the
newly-demoted file is refreshed via backup()+os.replace() - an already-open
handle on the demoted file keeps reading ITS OWN consistent old snapshot
after the replace, which is the whole reader-safety point of this mechanism
(see docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md,
"Safety" section - this is deliberately NOT a lock against readers).
"""
import os
import sqlite3
from datetime import datetime, timezone

import pytest

import src.storage.db as db
import src.db_rotation as db_rotation
from src.storage.models import JobNormalized


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


def _job(url_hash: str, title: str) -> JobNormalized:
    return JobNormalized(
        url_hash=url_hash, canonical_hash=f"c-{url_hash}", description_hash=f"d-{url_hash}",
        job_group_id=f"g-{url_hash}"[:16], market_id="m", source_name="s",
        title=title, normalized_title=title, normalization_confidence=1.0,
        company="Acme", country="US", location="Remote", remote_type="remote",
        posted_date=None, salary_min=None, salary_max=None, currency=None,
        description_text="desc", url=f"https://example.com/{url_hash}",
    )


def test_rotate_merges_buffer_jobs_into_free_and_clears_buffer(isolated_paths):
    with db.use_buffer_connection():
        db.upsert_job(_job("buf-1", "Software Engineer"))

    result = db_rotation.rotate()

    assert result["merged"] == 1
    assert result["rotated"] is True

    free_conn = db.get_buffer_connection()
    remaining = free_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    free_conn.close()
    assert remaining == 0  # buffer cleared after merge

    serving_conn = db.get_connection()  # now points at the newly-promoted file
    row = serving_conn.execute("SELECT title FROM jobs WHERE url_hash = 'buf-1'").fetchone()
    serving_conn.close()
    assert row is not None and row["title"] == "Software Engineer"


def test_rotate_skips_jobs_free_already_has_by_url_hash(isolated_paths):
    with db.use_free_connection():
        db.upsert_job(_job("dup-1", "Existing Title"))
    with db.use_buffer_connection():
        db.upsert_job(_job("dup-1", "Would-be Duplicate"))  # same url_hash

    result = db_rotation.rotate()

    assert result["merged"] == 0  # already present, not counted as newly merged
    serving_conn = db.get_connection()
    row = serving_conn.execute("SELECT title FROM jobs WHERE url_hash = 'dup-1'").fetchone()
    serving_conn.close()
    assert row["title"] == "Existing Title"  # untouched, not overwritten


def test_rotate_flips_the_pointer(isolated_paths):
    db._write_pointer("a")
    db_rotation.rotate()
    assert db._read_pointer() == "b"
    db_rotation.rotate()
    assert db._read_pointer() == "a"


def test_rotate_refreshes_demoted_file_and_open_handle_survives_replace(isolated_paths):
    with db.use_buffer_connection():
        db.upsert_job(_job("refresh-1", "New Job"))

    demoted_path_before = db._serving_path_for(db._read_pointer())  # current Serving, about to be demoted

    if os.name == "posix":
        # POSIX: os.replace() swaps the inode, so a handle opened *before*
        # rotation keeps reading its own OLD, consistent snapshot even after
        # the path is replaced with new content underneath it. Production
        # runs on Linux (gunicorn), so this is the path that actually
        # matters - same reasoning, same os.name branch already used for
        # the pointer file in
        # test_db_rotation_paths.py::test_write_pointer_is_atomic_replace_not_in_place_edit.
        still_open_conn = sqlite3.connect(demoted_path_before)
        still_open_conn.execute("SELECT 1")

        db_rotation.rotate()

        # The stale handle must still be usable (reading its own old
        # snapshot, not raise "no such table" or "database is locked").
        still_open_conn.execute("SELECT 1")
        still_open_conn.close()
    else:
        # Windows: os.replace() onto a path with another open handle raises
        # PermissionError instead of transparently swapping in place (no
        # POSIX-style rename-over-open-file semantics) - there is no way to
        # hold an open reader across the replace here, so just rotate and
        # verify the refreshed file below, mirroring the same os.name split
        # already established in test_db_rotation_paths.py for the pointer
        # file's atomic replace.
        db_rotation.rotate()

    # The now-demoted file on disk (same path) has been refreshed to match
    # the new Serving contents.
    demoted_conn = sqlite3.connect(demoted_path_before)
    row = demoted_conn.execute("SELECT title FROM jobs WHERE url_hash = 'refresh-1'").fetchone()
    demoted_conn.close()
    assert row is not None and row[0] == "New Job"


def test_rotate_lock_prevents_double_merge(isolated_paths, monkeypatch):
    if db.fcntl is None:
        pytest.skip("fcntl is Unix-only")

    with db.use_buffer_connection():
        db.upsert_job(_job("lock-1", "Locked Job"))

    call_order = []
    real_flock = db.fcntl.flock

    def tracking_flock(fd, operation):
        if operation == db.fcntl.LOCK_EX:
            call_order.append("lock")
        elif operation == db.fcntl.LOCK_UN:
            call_order.append("unlock")
        return real_flock(fd, operation)

    monkeypatch.setattr(db.fcntl, "flock", tracking_flock)

    db_rotation.rotate()

    assert call_order == ["lock", "unlock"]


def test_rotate_skips_when_site_busy(isolated_paths):
    with db.use_buffer_connection():
        db.upsert_job(_job("busy-1", "Should Not Merge Yet"))

    now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    last_request_at = datetime(2026, 7, 15, 11, 59, 55, tzinfo=timezone.utc)  # 5s ago, well under threshold

    result = db_rotation.rotate(last_request_at=last_request_at, now=now)

    assert result == {"merged": 0, "rotated": False, "new_serving": db._read_pointer()}
    buffer_conn = db.get_buffer_connection()
    remaining = buffer_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    buffer_conn.close()
    assert remaining == 1  # untouched, still pending for next attempt


def test_rotate_proceeds_without_last_request_at(isolated_paths):
    # Callers that don't track site traffic (e.g. orchestrator.py's
    # post-ingestion trigger) simply don't pass last_request_at/now - rotate()
    # must proceed unconditionally in that case.
    with db.use_buffer_connection():
        db.upsert_job(_job("no-gate-1", "Orchestrator Triggered"))
    result = db_rotation.rotate()
    assert result["rotated"] is True


def test_ingest_only_pipeline_writes_land_in_buffer_not_serving(isolated_paths, monkeypatch):
    from datetime import date
    from src.orchestrator import run_pipeline_for_market
    from src.storage.models import JobNormalized

    monkeypatch.setattr(
        "src.orchestrator.run_ingestion",
        lambda market, run: db.upsert_job(_job("ingest-only-1", "Buffer Bound")),
    )

    market = {"market_id": "m", "display_name": "M"}
    run_pipeline_for_market(market=market, mode="ingest-only", week_start=date(2026, 7, 13))

    buffer_conn = db.get_buffer_connection()
    buffer_count = buffer_conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'ingest-only-1'").fetchone()[0]
    buffer_conn.close()
    assert buffer_count == 1

    serving_conn = db.get_connection()
    serving_count = serving_conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'ingest-only-1'").fetchone()[0]
    serving_conn.close()
    assert serving_count == 0


def test_weekly_mode_pipeline_writes_land_in_serving_unchanged(isolated_paths, monkeypatch):
    from datetime import date

    monkeypatch.setattr(
        "src.orchestrator.run_ingestion",
        lambda market, run: db.upsert_job(_job("weekly-1", "Serving Bound")),
    )
    monkeypatch.setattr("src.orchestrator.run_analytics_and_report", lambda *a, **kw: None)

    from src.orchestrator import run_pipeline_for_market
    market = {"market_id": "m", "display_name": "M"}
    run_pipeline_for_market(market=market, mode="weekly", week_start=date(2026, 7, 13))

    serving_conn = db.get_connection()
    serving_count = serving_conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'weekly-1'").fetchone()[0]
    serving_conn.close()
    assert serving_count == 1  # weekly mode is unchanged by this plan - still writes Serving directly
