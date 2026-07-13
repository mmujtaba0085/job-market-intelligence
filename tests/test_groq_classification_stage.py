import json
import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, raw_description TEXT DEFAULT '',
            field_category_id TEXT, field_classification_confidence REAL, field_classification_method TEXT
        );
        CREATE TABLE job_categories (category_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, isco TEXT, keywords TEXT);
        CREATE TABLE job_category_assignments (
            job_id INTEGER, category_id TEXT, assignment_type TEXT, confidence REAL,
            method TEXT, evidence_json TEXT, assigned_at TEXT,
            PRIMARY KEY (job_id, category_id, assignment_type)
        );
        CREATE TABLE groq_classification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT DEFAULT 'pending',
            prompt_sent TEXT, response_received TEXT, attempt_count INTEGER DEFAULT 0,
            last_attempted_at TEXT, created_at TEXT, UNIQUE(job_id)
        );
        CREATE TABLE classification_runs (
            run_id TEXT PRIMARY KEY, run_type TEXT, trigger TEXT, status TEXT DEFAULT 'running',
            started_at TEXT, finished_at TEXT, cursor_job_id INTEGER,
            jobs_processed INTEGER DEFAULT 0, jobs_classified INTEGER DEFAULT 0,
            jobs_queued_groq INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    c.execute("INSERT INTO job_categories (category_id, name, parent_id) VALUES ('it.software', 'Software Engineering', 'it')")
    c.execute("INSERT INTO jobs (job_id, title, raw_description) VALUES (1, 'Backend Dev', 'writes python')")
    c.execute("INSERT INTO jobs (job_id, title, raw_description) VALUES (2, 'Mystery Role', 'no clear fit')")
    c.execute("INSERT INTO jobs (job_id, title, raw_description) VALUES (3, 'Flaky Call', 'network will fail')")
    c.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))")
    c.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (2, 'pending', datetime('now'))")
    c.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (3, 'pending', datetime('now'))")
    c.execute("INSERT INTO classification_runs (run_id, run_type, trigger, started_at) VALUES ('run1', 'groq_backlog', 'backfill_idle', datetime('now'))")
    c.commit()
    return c


def _fake_groq_response(job_id_to_category: dict[int, str | None]):
    return {
        "choices": [{"message": {"content": json.dumps({
            "results": [
                {"job_id": jid, "category_id": cat, "confidence": 0.9 if cat else 0.0, "reasoning": "test"}
                for jid, cat in job_id_to_category.items()
            ]
        })}}]
    }


def test_succeeded_outcome_writes_category_and_clears_queue(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    from src.classification import groq_stage

    class _MockResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200
        def json(self):
            return self._data
        def raise_for_status(self):
            pass

    fake_response = _MockResponse(_fake_groq_response({1: "it.software", 2: None, 3: "it.software"}))
    with patch("src.classification.groq_stage.requests.post", return_value=fake_response):
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("pending",))

    assert result["processed"] == 3
    assert result["succeeded"] == 2
    assert result["no_match"] == 1

    job1 = conn.execute("SELECT field_category_id, field_classification_method FROM jobs WHERE job_id = 1").fetchone()
    assert job1["field_category_id"] == "it.software"
    assert job1["field_classification_method"] == "groq_v1"

    q1 = conn.execute("SELECT status, prompt_sent, response_received FROM groq_classification_queue WHERE job_id = 1").fetchone()
    assert q1["status"] == "succeeded"
    assert q1["prompt_sent"] is not None
    assert q1["response_received"] is not None

    q2 = conn.execute("SELECT status FROM groq_classification_queue WHERE job_id = 2").fetchone()
    assert q2["status"] == "no_match"


def test_technical_failure_marks_retryable(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    monkeypatch.setattr("src.classification.groq_stage.time.sleep", lambda seconds: None)  # skip real backoff delay in tests
    from src.classification import groq_stage

    with patch("src.classification.groq_stage.requests.post", side_effect=ConnectionError("network down")):
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("pending",), chunk_size=25)

    assert result["failed_technical"] == 3
    rows = conn.execute("SELECT status, attempt_count, response_received FROM groq_classification_queue").fetchall()
    assert all(r["status"] == "failed_technical" for r in rows)
    assert all(r["attempt_count"] == 1 for r in rows)
    assert all(r["response_received"] is not None for r in rows)  # error text stored


def test_retry_sweep_excludes_exhausted_attempts(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    conn.execute("UPDATE groq_classification_queue SET status = 'failed_technical', attempt_count = 5 WHERE job_id = 1")
    conn.execute("UPDATE groq_classification_queue SET status = 'failed_technical', attempt_count = 2 WHERE job_id = 2")
    conn.execute("DELETE FROM groq_classification_queue WHERE job_id = 3")
    conn.commit()

    from src.classification import groq_stage

    class _MockResponse:
        status_code = 200
        def json(self):
            return _fake_groq_response({2: "it.software"})
        def raise_for_status(self):
            pass

    with patch("src.classification.groq_stage.requests.post", return_value=_MockResponse()) as mock_post:
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("failed_technical",))

    assert result["processed"] == 1  # only job 2 (job 1 exhausted its 5 attempts)


def test_no_match_never_retried_by_retry_sweep(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    conn.execute("UPDATE groq_classification_queue SET status = 'no_match' WHERE job_id = 1")
    conn.execute("DELETE FROM groq_classification_queue WHERE job_id IN (2, 3)")
    conn.commit()

    from src.classification import groq_stage
    with patch("src.classification.groq_stage.requests.post") as mock_post:
        result = groq_stage.process_groq_queue(conn, run_id="run1", statuses=("failed_technical",))

    mock_post.assert_not_called()
    assert result["processed"] == 0


def test_prompt_sends_category_ids_not_full_keywords(conn, monkeypatch):
    monkeypatch.setattr("config.settings.GROQ_API_KEYS", ["fake-key-1"])
    conn.execute("UPDATE job_categories SET keywords = ?", (json.dumps(["a very long keyword list", "that should not appear"]),))
    conn.execute("DELETE FROM groq_classification_queue WHERE job_id IN (2, 3)")
    conn.commit()

    from src.classification import groq_stage
    captured = {}

    class _MockResponse:
        status_code = 200
        def json(self):
            return _fake_groq_response({1: "it.software"})
        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _MockResponse()

    with patch("src.classification.groq_stage.requests.post", side_effect=fake_post):
        groq_stage.process_groq_queue(conn, run_id="run1", statuses=("pending",))

    prompt_text = str(captured["payload"])
    assert "it.software" in prompt_text
    assert "a very long keyword list" not in prompt_text
