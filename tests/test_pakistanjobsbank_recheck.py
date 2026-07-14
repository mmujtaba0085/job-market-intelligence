"""
tests/test_pakistanjobsbank_recheck.py
────────────────────────────────────────
Regression test for a real production gap: Pakistan Jobs Bank populates a
date's ad content with a multi-day lag after that date's page first goes
live. The forward frontier marks a date "crawled" after a single visit and
never revisits it (the backward frontier that would otherwise eventually
retry old dates is permanently disabled once backfill_complete=True) - so
content that wasn't there yet on first visit was silently lost forever.
Confirmed directly against production: 2026-07-12's page had 0 jobs when
the forward frontier first visited it, but 328 real jobs when independently
re-fetched days later.
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.collectors.pakistanjobsbank_collector import PakistanJobsBankCollector
from src.storage.models import JobRaw


def _fake_job(day: date, n: int = 1) -> JobRaw:
    return JobRaw(
        source_id="pakistanjobsbank",
        source_name="Pakistan Jobs Bank",
        url=f"https://www.pakistanjobsbank.com/Jobs-in-Pakistan/{day.isoformat()}/#job{n}",
        fetched_at=None,
        raw_json={},
        parsed_fields={"title": f"Job {n}", "company": "Co", "posted_date": day.isoformat()},
    )


@pytest.fixture()
def collector(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.collectors.pakistanjobsbank_collector._STATE_FILE", tmp_path / "state.json"
    )
    c = PakistanJobsBankCollector()
    monkeypatch.setattr(c, "_wait", lambda: None)  # skip real rate-limit sleeps in tests
    return c


def test_recheck_window_finds_jobs_on_a_date_already_marked_crawled(collector, monkeypatch):
    today = date.today()
    # newest_date_crawled is already "today" - the forward frontier alone
    # has nothing left to do. A date 5 days ago was visited before and
    # found empty at the time (matches production: forward frontier
    # marks a date crawled the moment it's first checked, regardless of
    # whether real content existed there yet).
    stale_date = today - timedelta(days=5)
    collector._save_state({
        "backfill_complete": True,
        "oldest_date_crawled": (today - timedelta(days=300)).isoformat(),
        "newest_date_crawled": today.isoformat(),
        "consecutive_404": 0,
        "total_jobs_collected": 0,
        "total_runs": 1,
    })

    def fake_fetch_date_page(day):
        if day == stale_date:
            # Content has since appeared on a date already marked "crawled".
            return 200, [_fake_job(day)]
        return 200, []

    with patch.object(collector, "_fetch_date_page", side_effect=fake_fetch_date_page):
        results = collector._fetch_raw({"market_id": "pakistan_jobs_all"})

    assert len(results) == 1
    assert results[0].parsed_fields["posted_date"] == stale_date.isoformat()


def test_recheck_does_not_extend_beyond_the_configured_window(collector, monkeypatch):
    from src.collectors import pakistanjobsbank_collector as mod
    today = date.today()
    too_old_date = today - timedelta(days=mod._RECENT_RECHECK_DAYS + 5)
    collector._save_state({
        "backfill_complete": True,
        "oldest_date_crawled": (today - timedelta(days=300)).isoformat(),
        "newest_date_crawled": today.isoformat(),
        "consecutive_404": 0,
        "total_jobs_collected": 0,
        "total_runs": 1,
    })

    calls = []

    def fake_fetch_date_page(day):
        calls.append(day)
        return 200, []

    with patch.object(collector, "_fetch_date_page", side_effect=fake_fetch_date_page):
        collector._fetch_raw({"market_id": "pakistan_jobs_all"})

    assert too_old_date not in calls


def test_forward_frontier_still_advances_past_recheck_window(collector, monkeypatch):
    # The recheck fix must not break the existing, already-working forward
    # frontier: brand new dates beyond newest_date_crawled still get
    # visited and their jobs still get collected.
    today = date.today()
    collector._save_state({
        "backfill_complete": True,
        "oldest_date_crawled": (today - timedelta(days=300)).isoformat(),
        "newest_date_crawled": (today - timedelta(days=1)).isoformat(),
        "consecutive_404": 0,
        "total_jobs_collected": 0,
        "total_runs": 1,
    })

    def fake_fetch_date_page(day):
        if day == today:
            return 200, [_fake_job(day)]
        return 200, []

    with patch.object(collector, "_fetch_date_page", side_effect=fake_fetch_date_page):
        results = collector._fetch_raw({"market_id": "pakistan_jobs_all"})

    assert any(j.parsed_fields["posted_date"] == today.isoformat() for j in results)

    state = collector._load_state()
    assert state["newest_date_crawled"] == today.isoformat()
