"""
Dynamic spreadsheet target registry helpers.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime


def extract_sheet_id(value: str | None) -> str:
    """Extract spreadsheet ID from raw ID or Google Sheets URL."""
    if not value:
        return ""

    text = value.strip()
    if "/d/" in text:
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", text)
        if match:
            return match.group(1)
    return text


def _build_spreadsheet_url(sheet_id: str | None) -> str | None:
    if not sheet_id:
        return None
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def list_targets(conn: sqlite3.Connection, active_only: bool = False) -> list[sqlite3.Row]:
    """Return all spreadsheet targets with mapped countries."""
    query = """
        SELECT
            st.id,
            st.name,
            st.private_spreadsheet_id,
            st.private_spreadsheet_url,
            st.published_spreadsheet_id,
            st.published_spreadsheet_url,
            st.is_active,
            GROUP_CONCAT(stc.country, '||') AS countries,
            GROUP_CONCAT(COALESCE(stc.doc_key, ''), '||') AS doc_keys
        FROM sheets_targets st
        LEFT JOIN sheets_target_countries stc ON st.id = stc.target_id
    """
    params: list = []
    if active_only:
        query += " WHERE st.is_active = 1"

    query += " GROUP BY st.id ORDER BY st.name"
    return conn.execute(query, params).fetchall()


def get_target_for_country(conn: sqlite3.Connection, country: str) -> sqlite3.Row | None:
    """Get best active target for a country."""
    return conn.execute(
        """
        SELECT
            st.id,
            st.name,
            st.private_spreadsheet_id,
            st.private_spreadsheet_url,
            st.published_spreadsheet_id,
            st.published_spreadsheet_url,
            stc.country,
            stc.doc_key
        FROM sheets_target_countries stc
        JOIN sheets_targets st ON st.id = stc.target_id
        WHERE stc.country = ?
          AND st.is_active = 1
        ORDER BY stc.is_primary DESC, st.id ASC
        LIMIT 1
        """,
        (country,),
    ).fetchone()


def get_target_by_id(conn: sqlite3.Connection, target_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            id,
            name,
            private_spreadsheet_id,
            private_spreadsheet_url,
            published_spreadsheet_id,
            published_spreadsheet_url,
            is_active
        FROM sheets_targets
        WHERE id = ?
        """,
        (target_id,),
    ).fetchone()


def upsert_target(
    conn: sqlite3.Connection,
    *,
    target_id: int | None,
    name: str,
    private_sheet_value: str,
    published_sheet_value: str | None,
    countries: list[str],
    doc_keys: dict[str, str] | None = None,
    is_active: bool = True,
) -> int:
    """Create/update target with support for same-sheet region merging."""
    private_id = extract_sheet_id(private_sheet_value)
    published_id = extract_sheet_id(published_sheet_value or "")
    private_url = _build_spreadsheet_url(private_id)
    published_url = (
        f"https://docs.google.com/spreadsheets/d/e/{published_id}/pubhtml"
        if published_id
        else None
    )

    if not private_id:
        raise ValueError("Private spreadsheet ID or URL is required")
    if not name.strip():
        raise ValueError("Target name is required")

    if not countries:
        raise ValueError("At least one country must be mapped")

    now = datetime.utcnow().isoformat()
    create_mode = target_id is None

    if create_mode:
        existing = conn.execute(
            """
            SELECT id, name
            FROM sheets_targets
            WHERE private_spreadsheet_id = ? OR LOWER(name) = LOWER(?)
            ORDER BY CASE WHEN private_spreadsheet_id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (private_id, name.strip(), private_id),
        ).fetchone()
        if existing:
            target_id = existing["id"]

    if target_id:
        current_target = conn.execute(
            "SELECT name FROM sheets_targets WHERE id = ?",
            (target_id,),
        ).fetchone()
        effective_name = name.strip() if name.strip() else (current_target["name"] if current_target else "")

        conn.execute(
            """
            UPDATE sheets_targets
            SET name = ?,
                private_spreadsheet_id = ?,
                private_spreadsheet_url = ?,
                published_spreadsheet_id = ?,
                published_spreadsheet_url = ?,
                is_active = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                effective_name,
                private_id,
                private_url,
                published_id or None,
                published_url,
                1 if is_active else 0,
                now,
                target_id,
            ),
        )
    else:
        cursor = conn.execute(
            """
            INSERT INTO sheets_targets
            (name, private_spreadsheet_id, private_spreadsheet_url,
             published_spreadsheet_id, published_spreadsheet_url,
             is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(),
                private_id,
                private_url,
                published_id or None,
                published_url,
                1 if is_active else 0,
                now,
                now,
            ),
        )
        target_id = cursor.lastrowid

    existing_countries = []
    if create_mode:
        existing_countries = [
            row["country"]
            for row in conn.execute(
                "SELECT country FROM sheets_target_countries WHERE target_id = ?",
                (target_id,),
            ).fetchall()
        ]

    clean_countries = [country.strip() for country in countries if country and country.strip()]
    if create_mode and existing_countries:
        merged = []
        seen = set()
        for country in existing_countries + clean_countries:
            normalized = country.strip()
            if normalized and normalized not in seen:
                merged.append(normalized)
                seen.add(normalized)
        clean_countries = merged

    conn.execute("DELETE FROM sheets_target_countries WHERE target_id = ?", (target_id,))

    for idx, country in enumerate(clean_countries):
        doc_key = (doc_keys or {}).get(country) if doc_keys else None
        conn.execute(
            """
            INSERT INTO sheets_target_countries
            (target_id, country, doc_key, is_primary, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (target_id, country, doc_key, 1 if idx == 0 else 0, now),
        )

    if clean_countries:
        placeholders = ",".join(["?"] * len(clean_countries))
        conn.execute(
            f"""
            UPDATE sheets_staging
            SET assigned_target_id = ?
            WHERE assigned_sheet IN ({placeholders})
              AND override_target_id IS NULL
            """,
            [target_id] + clean_countries,
        )

    return int(target_id)


def delete_target(conn: sqlite3.Connection, target_id: int) -> None:
    """Delete target and clear staging assignments that pointed to it."""
    conn.execute("UPDATE sheets_staging SET assigned_target_id = NULL WHERE assigned_target_id = ?", (target_id,))
    conn.execute("UPDATE sheets_staging SET override_target_id = NULL WHERE override_target_id = ?", (target_id,))
    conn.execute("DELETE FROM sheets_targets WHERE id = ?", (target_id,))


def resolve_target_for_staging_row(conn: sqlite3.Connection, country: str, override_target_id: int | None = None) -> sqlite3.Row | None:
    """Resolve target for a row with optional per-row override."""
    if override_target_id:
        override = get_target_by_id(conn, override_target_id)
        if override and override["is_active"]:
            return override
    return get_target_for_country(conn, country)
