from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

import requests

from src.storage.sheet_targets import get_target_for_country

logger = logging.getLogger(__name__)

COUNTRY_ALIASES = {
    "uk": "United Kingdom",
    "u.k": "United Kingdom",
    "gb": "United Kingdom",
    "great britain": "United Kingdom",
    "us": "United States",
    "usa": "United States",
    "u.s": "United States",
    "u.s.a": "United States",
    "ca": "Canada",
}

IT_KEYWORDS = {
    "engineer", "developer", "software", "data", "machine learning", "ml", "ai", "devops", "cloud", "analyst",
    "architect", "backend", "frontend", "full stack", "security", "sre", "python", "java", "sql",
}


def _mask_key(api_key: str) -> str:
    if not api_key:
        return "<none>"
    if len(api_key) <= 8:
        return f"{api_key[:2]}***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _retry_after_seconds(error: Exception) -> float | None:
    """Extract retry delay from HTTP 429 errors when available."""
    if not isinstance(error, requests.HTTPError):
        return None

    response = getattr(error, "response", None)
    if response is None:
        return None

    if response.status_code != 429:
        return None

    header = (response.headers or {}).get("Retry-After")
    if not header:
        return None

    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        return None


def _normalize_country(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    lowered = text.lower()
    return COUNTRY_ALIASES.get(lowered, text)


def _normalize_remote_type(value: str | None, title: str, location: str, country: str) -> str:
    raw = (value or "").strip().lower()
    merged = f"{title} {location} {country}".lower()

    if "hybrid" in raw or "hybrid" in merged:
        return "hybrid"
    if "on-site" in raw or "onsite" in raw or "on site" in raw:
        return "on-site"
    if "remote" in raw or "remote" in merged:
        return "remote"
    if raw in {"remote", "hybrid", "on-site", "unknown"}:
        return raw
    return "unknown"


def _normalize_tab_bucket(value: str | None, title: str, normalized_title: str, current_tab: str) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned in {"it", "non-it", "non it"}:
        return "IT" if cleaned == "it" else "Non-IT"

    merged = f"{title} {normalized_title} {current_tab}".lower()
    if any(keyword in merged for keyword in IT_KEYWORDS):
        return "IT"
    return "Non-IT"


def _extract_json_payload(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start:end + 1])
        raise


def _call_grok_batch(rows: list[dict[str, Any]], api_key: str, model: str, base_url: str, timeout_seconds: int) -> list[dict[str, Any]]:
    instruction = (
        "You are validating staging rows for country/location/remote classification and IT vs Non-IT tab bucket. "
        "Return strict JSON with key 'results'. Each result must include: staging_id, proposed_country, proposed_location, "
        "proposed_remote_type, proposed_tab_bucket, confidence, needs_human_review, reasons. "
        "Use full country names (United Kingdom, United States, Canada, etc). "
        "If uncertain, keep existing values and set needs_human_review=true."
    )

    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps({"rows": rows}, ensure_ascii=False)},
        ],
    }

    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    result = _extract_json_payload(content)
    return result.get("results", [])


def process_staging_with_grok(
    conn,
    staging_rows,
    *,
    api_keys: list[str] | None = None,
    api_key: str | None = None,
    model: str,
    base_url: str,
    chunk_size: int = 25,
    timeout_seconds: int = 60,
    request_pause_seconds: float = 0.25,
) -> dict[str, Any]:
    """Process pending staging rows with Grok and write cleaned overrides."""
    if not staging_rows:
        return {"processed": 0, "updated": 0, "needs_review": 0, "errors": 0}

    processed = 0
    updated = 0
    needs_review = 0
    errors = 0
    merged_duplicates = 0

    key_pool = [k for k in (api_keys or []) if k]
    if not key_pool and api_key:
        key_pool = [api_key]
    if not key_pool:
        raise ValueError("No API key provided for staging AI processing")

    # Track key-level cooldowns so a rate-limited key is temporarily skipped.
    key_cooldowns = {key: 0.0 for key in key_pool}

    for chunk_idx, idx in enumerate(range(0, len(staging_rows), chunk_size), start=1):
        chunk = staging_rows[idx:idx + chunk_size]
        request_rows = [
            {
                "staging_id": row["id"],
                "title": row["title"] or "",
                "company": row["company"] or "",
                "location": row["location"] or "",
                "country": row["country"] or "",
                "remote_type": row["remote_type"] or "",
                "normalized_title": row["normalized_title"] or "",
                "assigned_tab": row["assigned_tab"] or "",
            }
            for row in chunk
        ]

        grok_results_by_id: dict[int, dict[str, Any]] = {}
        chunk_ok = False
        attempts = max(12, len(key_pool) * 8)
        for attempt in range(attempts):
            now = time.time()
            available_keys = [k for k in key_pool if key_cooldowns.get(k, 0.0) <= now]
            if not available_keys:
                next_ready = min(key_cooldowns.values())
                sleep_seconds = max(0.1, min(20.0, next_ready - now))
                logger.info(
                    "[grok_staging] All Groq keys cooling down for %.2fs before retrying chunk %s-%s",
                    sleep_seconds,
                    idx,
                    idx + len(chunk),
                )
                time.sleep(sleep_seconds)
                continue

            key = available_keys[(chunk_idx + attempt - 1) % len(available_keys)]
            try:
                grok_results = _call_grok_batch(
                    request_rows,
                    api_key=key,
                    model=model,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
                for item in grok_results:
                    try:
                        grok_results_by_id[int(item.get("staging_id"))] = item
                    except Exception:
                        continue
                chunk_ok = True
                break
            except Exception as e:
                retry_after = _retry_after_seconds(e)
                is_rate_limited = isinstance(e, requests.HTTPError) and getattr(getattr(e, "response", None), "status_code", None) == 429

                if is_rate_limited:
                    cooldown = retry_after if retry_after is not None else (3.0 + random.uniform(0.5, 1.5))
                    cooldown = max(1.0, min(45.0, cooldown))
                    key_cooldowns[key] = time.time() + cooldown
                    logger.info(
                        "[grok_staging] Rate limited on key=%s chunk %s-%s attempt %s/%s; cooling key for %.2fs",
                        _mask_key(key),
                        idx,
                        idx + len(chunk),
                        attempt + 1,
                        attempts,
                        cooldown,
                    )

                last_attempt = attempt == attempts - 1
                if last_attempt:
                    errors += len(chunk)
                    logger.warning("[grok_staging] Grok call failed for chunk %s-%s after %s attempts: %s", idx, idx + len(chunk), attempts, e)
                else:
                    # Retry pacing: stronger on rate limits, gentler for transient network/API errors.
                    if is_rate_limited:
                        delay = min(12.0, (retry_after if retry_after is not None else 1.5) + random.uniform(0.2, 0.8))
                    else:
                        delay = min(8.0, (2 ** min(attempt, 3)) + random.uniform(0.2, 0.8))
                    time.sleep(delay)

        if not chunk_ok:
            continue

        for row in chunk:
            processed += 1
            result = grok_results_by_id.get(int(row["id"]), {})

            country = _normalize_country(result.get("proposed_country") or row["country"] or "")
            location = (result.get("proposed_location") or row["location"] or "").strip()
            remote_type = _normalize_remote_type(
                result.get("proposed_remote_type") or row["remote_type"] or "",
                row["title"] or "",
                location,
                country,
            )
            tab_bucket = _normalize_tab_bucket(
                result.get("proposed_tab_bucket"),
                row["title"] or "",
                row["normalized_title"] or "",
                row["assigned_tab"] or "",
            )

            confidence = result.get("confidence", 0.0)
            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.0

            reasons = result.get("reasons") or []
            if not isinstance(reasons, list):
                reasons = [str(reasons)]

            human_review = bool(result.get("needs_human_review", confidence < 0.85))
            if human_review:
                needs_review += 1

            target = get_target_for_country(conn, country)
            assigned_target_id = target["id"] if target else None
            assigned_sheet = country or row["assigned_sheet"]

            conflict = conn.execute(
                """
                SELECT id
                FROM sheets_staging
                WHERE job_id = ?
                  AND assigned_sheet = ?
                  AND assigned_tab = ?
                  AND id != ?
                LIMIT 1
                """,
                (row["job_id"], assigned_sheet, tab_bucket, row["id"]),
            ).fetchone()

            if conflict:
                conn.execute("DELETE FROM sheets_staging WHERE id = ?", (conflict["id"],))
                merged_duplicates += 1

            conn.execute(
                """
                UPDATE sheets_staging
                SET override_country = ?,
                    override_location = ?,
                    override_remote_type = ?,
                    assigned_sheet = ?,
                    assigned_tab = ?,
                    assigned_target_id = ?,
                    predicted_country = ?,
                    prediction_confidence = ?,
                    prediction_votes_json = ?,
                    review_status = ?,
                    review_notes = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    country or None,
                    location or None,
                    remote_type,
                    assigned_sheet,
                    tab_bucket,
                    assigned_target_id,
                    country or None,
                    confidence,
                    json.dumps({"reasons": reasons}, ensure_ascii=False),
                    "pending_review" if human_review else "ai_ready",
                    "; ".join(reasons)[:1000] if reasons else None,
                    row["id"],
                ),
            )
            updated += 1

        # Mild pacing between chunk requests to avoid sustained RPS spikes.
        if chunk_ok and request_pause_seconds > 0:
            time.sleep(request_pause_seconds)

    return {
        "processed": processed,
        "updated": updated,
        "needs_review": needs_review,
        "errors": errors,
        "merged_duplicates": merged_duplicates,
    }
