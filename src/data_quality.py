"""Read-only warehouse quality metrics used by rollout and admin diagnostics."""

from __future__ import annotations

import sqlite3

from src.enrichment.location_data import US_STATES


def quality_report(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    divisor = total or 1

    def count(sql: str, params: tuple = ()) -> int:
        return conn.execute(sql, params).fetchone()[0]

    bad_codes = tuple(US_STATES)
    placeholders = ",".join("?" for _ in bad_codes)
    return {
        "jobs": total,
        "active_jobs": count("SELECT COUNT(*) FROM jobs WHERE listing_status='active'"),
        "historical_jobs": count("SELECT COUNT(*) FROM jobs WHERE listing_status!='active'"),
        "classified_jobs": count("SELECT COUNT(*) FROM job_market_assignments WHERE assignment_type='primary'"),
        "unclassified_jobs": count(
            "SELECT COUNT(*) FROM jobs j WHERE NOT EXISTS "
            "(SELECT 1 FROM job_market_assignments a WHERE a.job_id=j.job_id AND a.assignment_type='primary')"
        ),
        "source_linked_jobs": count("SELECT COUNT(DISTINCT job_id) FROM job_source_links"),
        "salary_jobs": count("SELECT COUNT(*) FROM jobs WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL"),
        "missing_country_jobs": count(
            "SELECT COUNT(*) FROM jobs WHERE country IS NULL OR trim(country)='' OR lower(country) IN ('unknown','none','n/a')"
        ),
        "state_code_country_jobs": count(
            f"SELECT COUNT(*) FROM jobs WHERE upper(trim(country)) IN ({placeholders})", bad_codes
        ),
        "classification_rate": round(
            count("SELECT COUNT(*) FROM job_market_assignments WHERE assignment_type='primary'") / divisor, 4
        ),
        "source_link_rate": round(count("SELECT COUNT(DISTINCT job_id) FROM job_source_links") / divisor, 4),
        "salary_rate": round(
            count("SELECT COUNT(*) FROM jobs WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL") / divisor, 4
        ),
    }

