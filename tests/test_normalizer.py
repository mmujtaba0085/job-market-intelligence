"""
tests/test_normalizer.py
─────────────────────────
Unit tests for src/normalizer.py
"""

from datetime import datetime, timezone, date
import pytest
from src.storage.models import JobRaw
from src.normalizer import normalize, _infer_remote_type, _parse_date, _sha256


def _make_raw(url="https://example.com/job/1", description="Expert in python and machine learning",
               title="ML Engineer", company="Acme", remote_type=""):
    return JobRaw(
        source_id="remotive",
        source_name="Remotive",
        url=url,
        fetched_at=datetime.now(timezone.utc),
        parsed_fields={
            "title": title,
            "company": company,
            "location": "Berlin",
            "country": "Germany",
            "remote_type": remote_type,
            "posted_date": "2026-02-24",
            "description": description,
        },
    )


class TestNormalize:
    def test_returns_normalized_job(self):
        job = normalize(_make_raw(), "ai_ml_global")
        assert job is not None
        assert job.title == "ML Engineer"
        assert job.market_id == "ai_ml_global"
        assert job.source_name == "Remotive"

    def test_missing_url_returns_none(self):
        raw = _make_raw(url="")
        assert normalize(raw, "ai_ml_global") is None

    def test_missing_description_returns_none(self):
        raw = _make_raw(description="")
        assert normalize(raw, "ai_ml_global") is None

    def test_hashes_are_non_empty(self):
        job = normalize(_make_raw(), "ai_ml_global")
        assert job.url_hash and len(job.url_hash) == 64
        assert job.canonical_hash and len(job.canonical_hash) == 64
        assert job.description_hash and len(job.description_hash) == 64

    def test_different_urls_same_content_differ_on_url_hash_same_canonical(self):
        raw1 = _make_raw(url="https://example.com/1")
        raw2 = _make_raw(url="https://example.com/2")
        j1 = normalize(raw1, "ai_ml_global")
        j2 = normalize(raw2, "ai_ml_global")
        assert j1.url_hash != j2.url_hash            # different URLs → different url_hash
        assert j1.canonical_hash == j2.canonical_hash  # same content → same canonical


class TestRemoteTypeInference:
    def test_explicit_remote(self):
        assert _infer_remote_type("remote", "", "") == "remote"

    def test_explicit_hybrid(self):
        assert _infer_remote_type("hybrid", "", "") == "hybrid"

    def test_inferred_from_title(self):
        assert _infer_remote_type("", "Remote ML Engineer", "") == "remote"

    def test_inferred_from_description(self):
        assert _infer_remote_type("", "", "This is a work from home opportunity") == "remote"

    def test_unknown_fallback(self):
        assert _infer_remote_type("", "Senior Engineer", "Office environment") == "on-site"


class TestDateParsing:
    def test_iso_date(self):
        assert _parse_date("2026-02-24") == date(2026, 2, 24)

    def test_partial_iso(self):
        assert _parse_date("2026-02-24T00:00:00Z") == date(2026, 2, 24)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_invalid_string(self):
        assert _parse_date("not-a-date") is None
