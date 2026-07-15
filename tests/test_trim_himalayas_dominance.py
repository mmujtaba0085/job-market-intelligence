"""
tests/test_trim_himalayas_dominance.py
──────────────────────────────────────────
scripts/trim_himalayas_dominance.py's core logic (_target_hide_count() and
trim_connection()), exercised against a throwaway in-memory sqlite
database - never against data/serving_a.sqlite or any real file.

Context: Himalayas grew to 46.9% of all active jobs (confirmed against
production 2026-07-16) after being re-enabled uncapped. This is the
one-time correction - hide the oldest Himalayas jobs by first_seen_at
until its share of active jobs drops to a target percentage.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.trim_himalayas_dominance import _target_hide_count, trim_connection


class TestTargetHideCount:
    def test_computes_correct_count_to_reach_target(self):
        # 60 of 100 active jobs are Himalayas (60%); target 15%.
        # (60 - X) / (100 - X) = 0.15  =>  X = (60 - 15) / 0.85 ≈ 52.94 -> 53
        assert _target_hide_count(total_active=100, himalayas_active=60, target_pct=0.15) == 53

    def test_verifies_against_actual_ratio(self):
        total, himalayas = 112082, 52579
        x = _target_hide_count(total, himalayas, target_pct=0.15)
        remaining_himalayas = himalayas - x
        remaining_total = total - x
        assert abs((remaining_himalayas / remaining_total) - 0.15) < 0.0001

    def test_returns_zero_when_already_at_target(self):
        assert _target_hide_count(total_active=100, himalayas_active=10, target_pct=0.15) == 0

    def test_returns_zero_when_already_below_target(self):
        assert _target_hide_count(total_active=100, himalayas_active=5, target_pct=0.15) == 0

    def test_returns_zero_for_empty_dataset(self):
        assert _target_hide_count(total_active=0, himalayas_active=0, target_pct=0.15) == 0


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY,
            source_name TEXT,
            first_seen_at TEXT,
            listing_status TEXT DEFAULT 'active'
        )
    """)
    conn.execute("CREATE VIEW active_jobs AS SELECT * FROM jobs WHERE listing_status != 'hidden'")
    return conn


class TestTrimConnection:
    def _seed(self, conn, himalayas_count: int, other_count: int):
        for i in range(himalayas_count):
            conn.execute(
                "INSERT INTO jobs (source_name, first_seen_at) VALUES ('Himalayas', ?)",
                (f"2026-01-{(i % 28) + 1:02d}T00:00:00",),
            )
        for i in range(other_count):
            conn.execute(
                "INSERT INTO jobs (source_name, first_seen_at) VALUES ('OtherSource', ?)",
                (f"2026-01-{(i % 28) + 1:02d}T00:00:00",),
            )
        conn.commit()

    def test_hides_oldest_himalayas_jobs_first(self):
        conn = _db()
        # 5 Himalayas jobs with distinct, ordered first_seen_at, plus enough
        # other-source jobs that the target math lands on an exact,
        # unambiguous count (95 others + 5 Himalayas = 100 total, 5%
        # Himalayas; target 1% -> exactly 4 of the 5 oldest get hidden,
        # leaving exactly 1 Himalayas job out of 96 remaining = 1.04%).
        for job_id in range(1, 6):
            conn.execute(
                "INSERT INTO jobs (job_id, source_name, first_seen_at) VALUES (?, 'Himalayas', ?)",
                (job_id, f"2026-01-{job_id:02d}T00:00:00"),
            )
        for job_id in range(6, 101):
            conn.execute("INSERT INTO jobs (job_id, source_name, first_seen_at) VALUES (?, 'OtherSource', '2026-01-01T00:00:00')", (job_id,))
        conn.commit()

        expected = _target_hide_count(total_active=100, himalayas_active=5, target_pct=0.01)
        assert expected == 4  # sanity-check the fixture actually exercises a real, non-degenerate hide

        hidden = trim_connection(conn, target_pct=0.01, dry_run=False)
        assert hidden == expected

        statuses = {r["job_id"]: r["listing_status"] for r in conn.execute("SELECT job_id, listing_status FROM jobs WHERE job_id <= 5")}
        assert statuses[1] == "hidden"  # oldest
        assert statuses[2] == "hidden"
        assert statuses[3] == "hidden"
        assert statuses[4] == "hidden"
        assert statuses[5] == "active"  # newest Himalayas job - kept

        other_still_active = conn.execute("SELECT COUNT(*) FROM jobs WHERE source_name = 'OtherSource' AND listing_status = 'active'").fetchone()[0]
        assert other_still_active == 95  # never touched - not Himalayas

    def test_dry_run_reports_without_writing(self):
        conn = _db()
        self._seed(conn, himalayas_count=60, other_count=40)

        hidden = trim_connection(conn, target_pct=0.15, dry_run=True)
        assert hidden > 0

        still_active = conn.execute("SELECT COUNT(*) FROM jobs WHERE listing_status = 'active'").fetchone()[0]
        assert still_active == 100  # nothing actually hidden

    def test_idempotent_second_run_at_same_target_hides_nothing_more(self):
        conn = _db()
        self._seed(conn, himalayas_count=60, other_count=40)

        first = trim_connection(conn, target_pct=0.15, dry_run=False)
        assert first > 0

        second = trim_connection(conn, target_pct=0.15, dry_run=False)
        assert second == 0

    def test_already_hidden_jobs_are_not_recounted_or_retouched(self):
        conn = _db()
        conn.execute("INSERT INTO jobs (job_id, source_name, first_seen_at, listing_status) VALUES (1, 'Himalayas', '2026-01-01T00:00:00', 'hidden')")
        conn.execute("INSERT INTO jobs (job_id, source_name, first_seen_at) VALUES (2, 'Himalayas', '2026-01-02T00:00:00')")
        conn.execute("INSERT INTO jobs (job_id, source_name, first_seen_at) VALUES (3, 'OtherSource', '2026-01-01T00:00:00')")
        conn.commit()

        # Only 2 active jobs exist (job 1 already hidden, excluded from active_jobs
        # entirely) - 1 Himalayas of 2 active = 50%; target 15% -> hides job 2.
        hidden = trim_connection(conn, target_pct=0.15, dry_run=False)
        assert hidden == 1
        status = conn.execute("SELECT listing_status FROM jobs WHERE job_id = 2").fetchone()[0]
        assert status == "hidden"
