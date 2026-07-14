"""
tests/test_workable_collector_retry.py
────────────────────────────────────────
Regression test: WorkableCollector._get_json() had no retry logic at all -
a single 429 (confirmed in production: 125-133 per ingest run against the
'devsinc'/'pmcl' Workable boards) caused that job's description fetch to
fail once and the job to be silently dropped entirely ("No description for
job ... - skipping"), with no run-level error ever recorded. Fixed to match
the retry/backoff pattern already proven in findwork_crawler.py.
"""
from unittest.mock import patch

import pytest
import requests

from src.collectors.workable_collector import DevsincCollector


@pytest.fixture()
def collector(monkeypatch):
    c = DevsincCollector()
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # no real sleeping in tests
    return c


class _MockResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def test_retries_on_429_and_succeeds(collector):
    responses = [
        _MockResponse(429, headers={"Retry-After": "1"}),
        _MockResponse(200, json_data={"title": "Engineer"}),
    ]
    with patch("requests.get", side_effect=responses):
        result = collector._get_json("https://example.com/job/1")
    assert result == {"title": "Engineer"}


def test_gives_up_after_max_attempts_on_persistent_429(collector):
    responses = [_MockResponse(429, headers={"Retry-After": "1"})] * 5
    with patch("requests.get", side_effect=responses):
        result = collector._get_json("https://example.com/job/1")
    assert result is None


def test_retries_on_5xx_then_succeeds(collector):
    responses = [
        _MockResponse(503),
        _MockResponse(200, json_data={"title": "Analyst"}),
    ]
    with patch("requests.get", side_effect=responses):
        result = collector._get_json("https://example.com/job/2")
    assert result == {"title": "Analyst"}


def test_no_retry_on_404(collector):
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _MockResponse(404)

    with patch("requests.get", side_effect=fake_get):
        result = collector._get_json("https://example.com/job/missing")

    assert result is None
    assert call_count == 1  # no retry for a definitive "not found"


def test_retries_on_connection_error_then_succeeds(collector):
    # Matches findwork_crawler's reference pattern: a transient connection
    # error (not a definitive HTTP status) is worth retrying, same as a
    # timeout or a 5xx.
    responses = [requests.ConnectionError("network down"), _MockResponse(200, json_data={"title": "Recovered"})]

    def fake_get(*args, **kwargs):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch("requests.get", side_effect=fake_get):
        result = collector._get_json("https://example.com/job/3")

    assert result == {"title": "Recovered"}


def test_gives_up_after_max_attempts_on_persistent_connection_error(collector):
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise requests.ConnectionError("network down")

    with patch("requests.get", side_effect=fake_get):
        result = collector._get_json("https://example.com/job/3")

    assert result is None
    assert call_count == 3  # capped, not infinite
