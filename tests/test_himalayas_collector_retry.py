"""
tests/test_himalayas_collector_retry.py
─────────────────────────────────────────
Regression test: HimalayasCollector._fetch_raw() had inconsistent, weak
error handling - a 429 gave up immediately ("Rate limited (429), stopping"),
while 5xx/timeout errors did retry (up to max_errors=3) but with zero
backoff delay between attempts, just hammering the server immediately.
Fixed to back off on all three (429, 5xx, timeout), matching the pattern
already proven in findwork_crawler.py.
"""
from unittest.mock import patch

import pytest
import requests

from src.collectors.himalayas_collector import HimalayasCollector


@pytest.fixture()
def collector(monkeypatch):
    c = HimalayasCollector()
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


def _page(jobs, total_count=1):
    return {"totalCount": total_count, "jobs": jobs}


def test_429_retries_instead_of_stopping_immediately(collector):
    job_item = {
        "title": "ML Engineer", "companyName": "Acme", "location": "Remote",
        "applicationLink": "https://example.com/job/1", "description": "desc",
    }
    responses = [
        _MockResponse(429, headers={"Retry-After": "1"}),
        _MockResponse(200, json_data=_page([job_item], total_count=1)),
    ]
    with patch("requests.get", side_effect=responses):
        results = collector._fetch_raw({"max_jobs_per_source": 200, "keywords": []})

    assert len(results) == 1
    assert results[0].parsed_fields["title"] == "ML Engineer"


def test_5xx_retry_actually_sleeps_between_attempts(collector):
    job_item = {
        "title": "Data Scientist", "companyName": "Acme", "location": "Remote",
        "applicationLink": "https://example.com/job/2", "description": "desc",
    }
    responses = [
        _MockResponse(503),
        _MockResponse(200, json_data=_page([job_item], total_count=1)),
    ]
    sleep_calls = []
    with patch("requests.get", side_effect=responses), \
         patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        results = collector._fetch_raw({"max_jobs_per_source": 200, "keywords": []})

    assert len(results) == 1
    assert len(sleep_calls) >= 1  # a real backoff delay was actually applied
    assert sleep_calls[0] > 0


def test_persistent_429_eventually_stops(collector):
    responses = [_MockResponse(429, headers={"Retry-After": "1"})] * 10
    with patch("requests.get", side_effect=responses):
        results = collector._fetch_raw({"max_jobs_per_source": 200, "keywords": []})

    assert results == []


def test_timeout_retry_actually_sleeps_between_attempts(collector):
    job_item = {
        "title": "Recovered Job", "companyName": "Acme", "location": "Remote",
        "applicationLink": "https://example.com/job/3", "description": "desc",
    }
    call_sequence = [requests.Timeout("timed out"), _MockResponse(200, json_data=_page([job_item], total_count=1))]

    def fake_get(*args, **kwargs):
        item = call_sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    sleep_calls = []
    with patch("requests.get", side_effect=fake_get), \
         patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        results = collector._fetch_raw({"max_jobs_per_source": 200, "keywords": []})

    assert len(results) == 1
    assert len(sleep_calls) >= 1
    assert sleep_calls[0] > 0
