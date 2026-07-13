"""
Groq fallback for jobs the local classifier (src.market_classifier) can't
confidently place. Classifies into the EXISTING 20-category taxonomy only -
if Groq also can't fit a job, that's a terminal 'no_match', not a request
for a new category (that's explicitly a separate, deferred spec).

Reuses src.ai.grok_staging's key-pool-cooldown/retry-after pattern rather
than duplicating it; the actual prompt/payload/outcome handling here is
different enough (category classification, not country/remote/tab-bucket
cleanup) that the outer loop is its own implementation, not a shared call.
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import GROK_BASE_URL, GROK_MODEL, GROQ_API_KEYS
from src.ai.grok_staging import _mask_key, _retry_after_seconds
from src.pipeline_monitor import get_config

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRY_ATTEMPTS = 5


def _max_retry_attempts() -> int:
    """Admin-configurable via /admin/classification's 'retry cap' field (classification_retry_cap)."""
    cfg = get_config()
    return int(cfg.get("classification_retry_cap", DEFAULT_MAX_RETRY_ATTEMPTS))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_categories(conn) -> list[dict]:
    rows = conn.execute("SELECT category_id, name FROM job_categories WHERE parent_id IS NOT NULL").fetchall()
    return [{"category_id": r["category_id"], "name": r["name"]} for r in rows]


def _build_prompt(categories: list[dict], jobs: list[dict]) -> dict[str, Any]:
    instruction = (
        "You are classifying job postings into an existing job-field taxonomy. "
        "Return strict JSON with key 'results'. Each result must include: "
        "job_id, category_id (must be exactly one of the provided category_id values, or null if none fit), "
        "confidence (0-1), reasoning. Do not invent a category_id that isn't in the provided list."
    )
    return {
        "model": GROK_MODEL,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps({"categories": categories, "jobs": jobs}, ensure_ascii=False)},
        ],
    }


def _call_groq_batch(payload: dict, api_key: str, timeout_seconds: int = 60) -> dict[int, dict]:
    response = requests.post(
        f"{GROK_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return {int(item["job_id"]): item for item in parsed.get("results", [])}


def _eligible_job_ids(conn, statuses: tuple[str, ...]) -> list[int]:
    if statuses == ("pending",):
        rows = conn.execute("SELECT job_id FROM groq_classification_queue WHERE status = 'pending' ORDER BY created_at").fetchall()
    elif statuses == ("failed_technical",):
        rows = conn.execute(
            "SELECT job_id FROM groq_classification_queue WHERE status = 'failed_technical' AND attempt_count < ? ORDER BY last_attempted_at",
            (_max_retry_attempts(),),
        ).fetchall()
    else:
        raise ValueError(f"Unsupported statuses combination: {statuses}")
    return [r["job_id"] for r in rows]


STALE_PROCESSING_MINUTES = 10


def _reclaim_stale_processing_rows(conn) -> None:
    """A crash/restart (e.g. a Docker redeploy mid-chunk) between setting
    status='processing' and the final outcome commit would otherwise orphan
    these rows forever - _eligible_job_ids() only ever selects pending or
    failed_technical, never processing. Reclaim anything stuck past a
    generous staleness window back to pending so it gets picked up again."""
    # last_attempted_at is stored via _now(), i.e. datetime.now(timezone.utc).isoformat()
    # ("...T...+00:00"), which sorts lexicographically *after* SQLite's own
    # datetime('now', ...) output ("...  ...", space-separated, no offset) for
    # any same-day timestamp - a raw string comparison would therefore never
    # consider a row stale. Wrap both sides in datetime(...) to normalize.
    conn.execute(
        """UPDATE groq_classification_queue SET status = 'pending'
           WHERE status = 'processing'
             AND (last_attempted_at IS NULL OR datetime(last_attempted_at) < datetime('now', ?))""",
        (f"-{STALE_PROCESSING_MINUTES} minutes",),
    )
    conn.commit()


def process_groq_queue(conn, run_id: str, statuses: tuple[str, ...], limit: int | None = None, chunk_size: int = 25) -> dict[str, int]:
    _reclaim_stale_processing_rows(conn)
    job_ids = _eligible_job_ids(conn, statuses)
    if limit:
        job_ids = job_ids[:limit]

    stats = {"processed": 0, "succeeded": 0, "failed_technical": 0, "no_match": 0}
    if not job_ids:
        return stats

    categories = _load_categories(conn)
    key_pool = [k for k in GROQ_API_KEYS if k]
    if not key_pool:
        raise ValueError("No Groq API key configured (GROQ_API_KEYS is empty)")
    key_cooldowns = {key: 0.0 for key in key_pool}

    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"SELECT job_id, title, raw_description FROM jobs WHERE job_id IN ({placeholders})", job_ids
    ).fetchall()
    jobs_by_id = {r["job_id"]: r for r in rows}

    for chunk_idx in range(0, len(job_ids), chunk_size):
        chunk_ids = job_ids[chunk_idx:chunk_idx + chunk_size]
        batch = [
            {"job_id": jid, "title": jobs_by_id[jid]["title"] or "", "description": (jobs_by_id[jid]["raw_description"] or "")[:2000]}
            for jid in chunk_ids
        ]
        payload = _build_prompt(categories, batch)
        prompt_json = json.dumps(payload)

        # NOTE: uses its own chunk-scoped placeholder string, not the outer
        # `placeholders` (which was sized for the full job_ids list, not one
        # chunk) - reusing that one here would raise a parameter-count
        # mismatch as soon as job_ids spans more than one chunk_size batch.
        chunk_placeholders = ",".join("?" for _ in chunk_ids)
        conn.execute(
            f"UPDATE groq_classification_queue SET status = 'processing', last_attempted_at = ? WHERE job_id IN ({chunk_placeholders})",
            [_now(), *chunk_ids],
        )
        conn.commit()

        results_by_id: dict[int, dict] = {}
        call_error: str | None = None
        attempts = max(4, len(key_pool) * 2)
        for attempt in range(attempts):
            now = time.time()
            available = [k for k in key_pool if key_cooldowns.get(k, 0.0) <= now]
            if not available:
                time.sleep(max(0.1, min(20.0, min(key_cooldowns.values()) - now)))
                continue
            key = available[attempt % len(available)]
            try:
                results_by_id = _call_groq_batch(payload, key)
                call_error = None
                break
            except requests.HTTPError as e:
                retry_after = _retry_after_seconds(e)
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if status_code == 429:
                    cooldown = retry_after if retry_after is not None else (3.0 + random.uniform(0.5, 1.5))
                    key_cooldowns[key] = time.time() + max(1.0, min(45.0, cooldown))
                    logger.info("[groq_stage] Rate limited on key=%s, cooling down", _mask_key(key))
                call_error = str(e)
            except Exception as e:  # noqa: BLE001 - any failure here means "this attempt didn't work"
                call_error = str(e)
            if attempt < attempts - 1:
                time.sleep(min(8.0, (2 ** min(attempt, 3)) + random.uniform(0.2, 0.8)))

        now_iso = _now()
        for jid in chunk_ids:
            stats["processed"] += 1
            if call_error is not None and jid not in results_by_id:
                conn.execute(
                    """UPDATE groq_classification_queue
                       SET status = 'failed_technical', attempt_count = attempt_count + 1,
                           prompt_sent = ?, response_received = ?, last_attempted_at = ?
                       WHERE job_id = ?""",
                    (prompt_json, f"ERROR: {call_error}", now_iso, jid),
                )
                stats["failed_technical"] += 1
                continue

            result = results_by_id.get(jid)
            response_text = json.dumps(result) if result else "ERROR: job_id missing from Groq response"
            category_id = (result or {}).get("category_id")
            valid_category = category_id in {c["category_id"] for c in categories}

            if result and valid_category:
                confidence = float(result.get("confidence") or 0.0)
                conn.execute(
                    """UPDATE jobs SET field_category_id = ?, field_classification_confidence = ?,
                                        field_classification_method = 'groq_v1' WHERE job_id = ?""",
                    (category_id, confidence, jid),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO job_category_assignments
                       (job_id, category_id, assignment_type, confidence, method, evidence_json, assigned_at)
                       VALUES (?, ?, 'primary', ?, 'groq_v1', ?, ?)""",
                    (jid, category_id, confidence, json.dumps(result.get("reasoning", "")), now_iso),
                )
                conn.execute(
                    "UPDATE groq_classification_queue SET status = 'succeeded', prompt_sent = ?, response_received = ?, last_attempted_at = ? WHERE job_id = ?",
                    (prompt_json, response_text, now_iso, jid),
                )
                stats["succeeded"] += 1
            elif result and category_id is None:
                conn.execute(
                    "UPDATE groq_classification_queue SET status = 'no_match', prompt_sent = ?, response_received = ?, last_attempted_at = ? WHERE job_id = ?",
                    (prompt_json, response_text, now_iso, jid),
                )
                stats["no_match"] += 1
            else:
                # Response came back but with an invalid/missing category_id for this job - treat as a
                # technical failure (malformed response), not a semantic no_match, so it's retried.
                conn.execute(
                    """UPDATE groq_classification_queue
                       SET status = 'failed_technical', attempt_count = attempt_count + 1,
                           prompt_sent = ?, response_received = ?, last_attempted_at = ?
                       WHERE job_id = ?""",
                    (prompt_json, response_text, now_iso, jid),
                )
                stats["failed_technical"] += 1
        conn.commit()

    conn.execute(
        """UPDATE classification_runs
           SET jobs_processed = jobs_processed + ?, jobs_classified = jobs_classified + ?
           WHERE run_id = ?""",
        (stats["processed"], stats["succeeded"], run_id),
    )
    conn.commit()
    return stats
