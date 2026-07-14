"""
tests/test_arbeitnow_collector_retry.py
─────────────────────────────────────────
Regression test: ArbeitnowCollector._fetch_raw() gave up entirely on the
first 429 ("Rate limited (429), stopping collection") instead of backing
off and retrying the same page - even though the collector already imports
`tenacity` for retries elsewhere. Fixed to sleep and retry the same page,
matching findwork_crawler.py's proven pattern, instead of abandoning the
rest of the run's pagination.
"""
from unittest.mock import patch

import pytest

from src.collectors.arbeitnow_collector import ArbeitnowCollector


@pytest.fixture()
def collector(monkeypatch):
    c = ArbeitnowCollector()
    monkeypatch.setattr(c, "_wait", lambda: None)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    return c


class _MockResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json_data


def test_429_retries_same_page_instead_of_stopping(collector):
    page_1_data = {"data": [
        {"title": "Backend Engineer", "company_name": "Acme", "location": "Berlin",
         "remote": True, "url": "https://example.com/1", "created_at": "2026-07-01T00:00:00Z"},
    ]}
    page_2_empty = {"data": []}  # end of results after the retried page succeeds

    responses = [
        _MockResponse(429, headers={"Retry-After": "1"}),  # first attempt at page 1
        _MockResponse(200, json_data=page_1_data),          # retry of page 1 succeeds
        _MockResponse(200, json_data=page_2_empty),          # page 2: no more results
    ]

    with patch("requests.get", side_effect=responses):
        results = collector._fetch_raw({"max_jobs_per_source": 200, "keywords": []})

    assert len(results) == 1
    assert results[0].parsed_fields["title"] == "Backend Engineer"


def test_persistent_429_eventually_stops_without_hanging(collector):
    # A rate limit that never clears must still terminate the run rather
    # than retrying forever.
    responses = [_MockResponse(429, headers={"Retry-After": "1"})] * 10

    with patch("requests.get", side_effect=responses):
        results = collector._fetch_raw({"max_jobs_per_source": 200, "keywords": []})

    assert results == []
