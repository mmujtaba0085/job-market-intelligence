"""Build and validate a conservative shadow warehouse before promotion."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DB_PATH
from src.data_quality import quality_report
from src.enrichment.location_data import US_STATES
from src.market_classifier import classify_job, summarize_unknown_titles
from src.storage.db import _ensure_warehouse_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_backup(source: Path, destination: Path) -> None:
    """Create a consistent SQLite snapshot, including any committed WAL data."""
    if not source.exists():
        raise FileNotFoundError(f"Database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def build_shadow(source: Path, shadow: Path) -> dict:
    if source.resolve() == shadow.resolve():
        raise ValueError("Shadow database path must differ from source database path")
    _sqlite_backup(source, shadow)
    conn = sqlite3.connect(shadow)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    with conn:
        _ensure_warehouse_schema(conn)
        now = _now()
        state_codes = tuple(US_STATES)
        placeholders = ",".join("?" for _ in state_codes)
        bad_rows = conn.execute(
            f"SELECT job_id, country FROM jobs WHERE upper(trim(country)) IN ({placeholders})",
            state_codes,
        ).fetchall()
        for row in bad_rows:
            conn.execute(
                "UPDATE jobs SET country='Unknown' WHERE job_id=?",
                (row["job_id"],),
            )
            conn.execute(
                """INSERT INTO enrichment_events
                   (job_id, field_name, old_value, new_value, confidence, method, evidence_json, applied, created_at)
                   VALUES (?, 'country', ?, 'Unknown', 1.0, 'invalid_state_code_cleanup', ?, 1, ?)""",
                (row["job_id"], row["country"], json.dumps({"reason": "US state code is not a country"}), now),
            )

        rows = conn.execute(
            "SELECT job_id, title, raw_description, source_name, url_hash, url, last_seen_at FROM jobs"
        ).fetchall()
        unknown_titles: list[str] = []
        for row in rows:
            match = classify_job(row["title"], row["raw_description"])
            if match.market_id:
                conn.execute(
                    "UPDATE jobs SET market_id=?, classification_confidence=?, classification_method=? WHERE job_id=?",
                    (match.market_id, match.confidence, match.method, row["job_id"]),
                )
                assignments = [(match.market_id, "primary"), *[(tag, "tag") for tag in match.tags]]
                for market_id, assignment_type in assignments:
                    conn.execute(
                        """INSERT OR REPLACE INTO job_market_assignments
                           (job_id, market_id, assignment_type, confidence, method, evidence_json, assigned_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (row["job_id"], market_id, assignment_type, match.confidence, match.method, json.dumps(match.evidence), now),
                    )
            else:
                unknown_titles.append(row["title"])

            source_id = row["source_name"].lower().replace(" ", "_")
            conn.execute(
                """INSERT OR IGNORE INTO source_records
                   (source_id, source_record_id, source_url, payload_hash, first_seen_at, last_seen_at, listing_status)
                   VALUES (?, ?, ?, NULL, ?, ?, 'unverified')""",
                (source_id, row["url_hash"], row["url"], row["last_seen_at"], row["last_seen_at"]),
            )
            source_pk = conn.execute(
                "SELECT source_record_pk FROM source_records WHERE source_id=? AND source_record_id=?",
                (source_id, row["url_hash"]),
            ).fetchone()[0]
            conn.execute(
                """INSERT OR IGNORE INTO job_source_links
                   (job_id, source_record_pk, linked_at, match_method, match_confidence)
                   VALUES (?, ?, ?, 'historical_url_hash', 0.9)""",
                (row["job_id"], source_pk, now),
            )

        conn.execute(
            """UPDATE jobs SET listing_status='unverified', status_reason='historical_backfill',
               last_verified_at=NULL WHERE listing_status='active'"""
        )
        report = quality_report(conn)
        report["unknown_title_patterns"] = summarize_unknown_titles(unknown_titles, limit=25)
    conn.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(DB_PATH))
    parser.add_argument("--shadow", type=Path, default=Path(DB_PATH).with_name("jobs.shadow.sqlite"))
    parser.add_argument("--promote", action="store_true", help="Promote only after acceptance checks pass")
    args = parser.parse_args()
    report = build_shadow(args.source, args.shadow)
    accepted = (
        report["state_code_country_jobs"] == 0
        and report["source_link_rate"] >= 0.99
        and report["classification_rate"] >= 0.50
    )
    report["accepted"] = accepted
    report["shadow_path"] = str(args.shadow)
    print(json.dumps(report, indent=2))
    if args.promote:
        if not accepted:
            raise SystemExit("Shadow database failed acceptance checks; active database was not changed.")
        backup = args.source.with_name(f"{args.source.stem}.pre_promote.{datetime.now():%Y%m%d_%H%M%S}.sqlite")
        _sqlite_backup(args.source, backup)
        _sqlite_backup(args.shadow, args.source)
        print(f"Promoted shadow database. Pre-promotion backup: {backup}")
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
