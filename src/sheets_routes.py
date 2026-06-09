"""
Google Sheets staging admin routes for web_viewer.py
Import and register these routes in the main web_viewer.py file.
"""

from flask import request, render_template, redirect, jsonify, make_response, session
from datetime import datetime
import sqlite3
import csv
from io import StringIO
import uuid
import json
import logging

from src.storage.sheet_targets import list_targets, upsert_target, delete_target, get_target_for_country

logger = logging.getLogger(__name__)


def register_sheets_routes(app, get_db_connection):
    """Register all Google Sheets related routes to the Flask app."""

    def _safe_int(value, default, minimum=1, maximum=1000):
        """Parse bounded integer query params safely."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))
    
    @app.route("/admin/sheets_staging")
    def admin_sheets_staging():
        """Admin page for reviewing and editing jobs before Google Sheets upload."""
        conn = get_db_connection()
        
        # Get filter parameters
        filter_country = request.args.get("country", "all")
        filter_tab = request.args.get("tab", "all")
        filter_status = request.args.get("status", "all")
        page = _safe_int(request.args.get("page", 1), default=1, minimum=1, maximum=100000)
        per_page = _safe_int(request.args.get("per_page", 100), default=100, minimum=50, maximum=1000)

        # Build shared filter conditions once for both count + data queries.
        where_sql = "WHERE 1=1"
        params = []

        if filter_country != "all":
            where_sql += " AND s.assigned_sheet = ?"
            params.append(filter_country)

        if filter_tab != "all":
            where_sql += " AND s.assigned_tab = ?"
            params.append(filter_tab)

        if filter_status != "all":
            where_sql += " AND s.status = ?"
            params.append(filter_status)

        count_query = f"""
            SELECT COUNT(*)
            FROM sheets_staging s
            JOIN jobs j ON j.job_id = s.job_id
            {where_sql}
        """
        total_rows = conn.execute(count_query, params).fetchone()[0]

        total_pages = max(1, (total_rows + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page

        query = f"""
            SELECT 
                s.id,
                s.job_id,
                COALESCE(s.override_title, j.title) as title,
                COALESCE(s.override_normalized_title, j.normalized_title) as normalized_title,
                COALESCE(s.override_company, j.company) as company,
                COALESCE(s.override_location, j.location) as location,
                COALESCE(s.override_country, j.country) as country,
                COALESCE(s.override_remote_type, j.remote_type) as remote_type,
                j.posted_date,
                j.source_name,
                j.url,
                s.assigned_tab,
                s.assigned_sheet,
                COALESCE(s.override_target_id, s.assigned_target_id) as target_id,
                s.status,
                s.exclude_from_upload
            FROM sheets_staging s
            JOIN jobs j ON j.job_id = s.job_id
            {where_sql}
            ORDER BY s.assigned_sheet, s.assigned_tab, j.posted_date DESC
            LIMIT ? OFFSET ?
        """

        cursor = conn.execute(query, params + [per_page, offset])
        jobs = cursor.fetchall()
        
        # Get stats (filter by country if selected)
        stats_query = """
            SELECT 
                assigned_sheet,
                assigned_tab,
                COUNT(*) as count,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN status = 'staged' THEN 1 ELSE 0 END) as staged_count,
                SUM(CASE WHEN exclude_from_upload = 1 THEN 1 ELSE 0 END) as excluded_count
            FROM sheets_staging
        """
        
        stats_params = []
        if filter_country != "all":
            stats_query += " WHERE assigned_sheet = ?"
            stats_params.append(filter_country)
        
        stats_query += " GROUP BY assigned_sheet, assigned_tab ORDER BY assigned_sheet, count DESC"
        
        stats = conn.execute(stats_query, stats_params).fetchall()
        
        # Get unique tabs and countries for filters
        # Filter tabs by country if selected
        tabs_query = "SELECT DISTINCT assigned_tab FROM sheets_staging"
        tabs_params = []
        
        if filter_country != "all":
            tabs_query += " WHERE assigned_sheet = ?"
            tabs_params.append(filter_country)
        
        tabs_query += " ORDER BY assigned_tab"
        tabs = conn.execute(tabs_query, tabs_params).fetchall()
        
        countries = conn.execute("SELECT DISTINCT assigned_sheet FROM sheets_staging ORDER BY assigned_sheet").fetchall()

        targets = list_targets(conn, active_only=False)

        target_options = [
            {
                "id": row["id"],
                "name": row["name"],
                "private_spreadsheet_id": row["private_spreadsheet_id"],
                "published_spreadsheet_id": row["published_spreadsheet_id"],
                "is_active": row["is_active"],
                "countries": (row["countries"] or "").split("||") if row["countries"] else [],
                "doc_keys": (row["doc_keys"] or "").split("||") if row["doc_keys"] else [],
            }
            for row in targets
        ]
        
        conn.close()

        from config.settings import GROQ_API_KEYS, GROK_API_KEY
        _ai_keys = [k for k in (GROQ_API_KEYS or []) if k]
        if not _ai_keys and GROK_API_KEY:
            _ai_keys = [GROK_API_KEY]
        
        return render_template(
            "admin_sheets_staging.html",
            jobs=jobs,
            stats=stats,
            tabs=tabs,
            countries=countries,
            targets=target_options,
            grok_configured=bool(_ai_keys),
            current_country=filter_country,
            current_tab=filter_tab,
            current_status=filter_status,
            page=page,
            per_page=per_page,
            total_rows=total_rows,
            total_pages=total_pages
        )


    @app.route("/api/admin/sheets_targets", methods=["GET"])
    def api_admin_sheets_targets_list():
        """List spreadsheet targets and country mappings."""
        conn = get_db_connection()
        rows = list_targets(conn, active_only=False)
        conn.close()

        data = []
        for row in rows:
            countries = (row["countries"] or "").split("||") if row["countries"] else []
            doc_keys = (row["doc_keys"] or "").split("||") if row["doc_keys"] else []
            mapping = []
            for idx, country in enumerate(countries):
                mapping.append({
                    "country": country,
                    "doc_key": doc_keys[idx] if idx < len(doc_keys) else "",
                })

            data.append({
                "id": row["id"],
                "name": row["name"],
                "private_spreadsheet_id": row["private_spreadsheet_id"],
                "private_spreadsheet_url": row["private_spreadsheet_url"],
                "published_spreadsheet_id": row["published_spreadsheet_id"],
                "published_spreadsheet_url": row["published_spreadsheet_url"],
                "is_active": bool(row["is_active"]),
                "countries": mapping,
            })

        return jsonify({"success": True, "targets": data})


    @app.route("/api/admin/sheets_targets/save", methods=["POST"])
    def api_admin_sheets_targets_save():
        """Create or update a spreadsheet target and its country mappings."""
        payload = request.get_json() or {}

        target_id = payload.get("id")
        name = (payload.get("name") or "").strip()
        private_sheet = (payload.get("private_sheet") or payload.get("private_spreadsheet_id") or "").strip()
        published_sheet = (payload.get("published_sheet") or payload.get("published_spreadsheet_id") or "").strip()
        countries = payload.get("countries") or []
        is_active = bool(payload.get("is_active", True))

        if isinstance(countries, str):
            countries = [c.strip() for c in countries.split(",") if c.strip()]

        doc_keys: dict[str, str] = {}
        country_entries = payload.get("country_entries") or []
        for entry in country_entries:
            country_name = (entry.get("country") or "").strip()
            if country_name:
                doc_keys[country_name] = (entry.get("doc_key") or "").strip()

        try:
            conn = get_db_connection()
            with conn:
                new_id = upsert_target(
                    conn,
                    target_id=int(target_id) if target_id else None,
                    name=name,
                    private_sheet_value=private_sheet,
                    published_sheet_value=published_sheet,
                    countries=countries,
                    doc_keys=doc_keys,
                    is_active=is_active,
                )
            conn.close()
            return jsonify({"success": True, "id": new_id})
        except Exception as e:
            logger.error("[sheets_targets] Save failed: %s", e)
            return jsonify({"success": False, "error": str(e)})


    @app.route("/api/admin/sheets_targets/delete", methods=["POST"])
    def api_admin_sheets_targets_delete():
        """Delete target mapping."""
        payload = request.get_json() or {}
        target_id = payload.get("id")
        if not target_id:
            return jsonify({"success": False, "error": "Target id is required"})

        try:
            conn = get_db_connection()
            with conn:
                delete_target(conn, int(target_id))
            conn.close()
            return jsonify({"success": True})
        except Exception as e:
            logger.error("[sheets_targets] Delete failed: %s", e)
            return jsonify({"success": False, "error": str(e)})
    
    
    @app.route("/api/admin/sheets_staging/update", methods=["POST"])
    def api_admin_sheets_staging_update():
        """Update a single job field in staging."""
        data = request.get_json()
        staging_id = data.get("id")
        field = data.get("field")
        value = data.get("value")
        
        # Map field to override column
        field_mapping = {
            "title": "override_title",
            "normalized_title": "override_normalized_title",
            "company": "override_company",
            "location": "override_location",
            "country": "override_country",
            "remote_type": "override_remote_type",
            "assigned_tab": "assigned_tab",
            "assigned_sheet": "assigned_sheet",
            "target_id": "override_target_id"
        }
        
        if field not in field_mapping:
            return jsonify({"success": False, "error": "Invalid field"})
        
        db_field = field_mapping[field]
        
        conn = get_db_connection()
        conn.execute(f"""
            UPDATE sheets_staging 
            SET {db_field} = ?, updated_at = datetime('now')
            WHERE id = ?
        """, (value if value != "" else None, staging_id))
        conn.commit()
        conn.close()
        
        return jsonify({"success": True})
    
    
    @app.route("/api/admin/sheets_staging/exclude", methods=["POST"])
    def api_admin_sheets_staging_exclude():
        """Toggle exclude status for jobs."""
        data = request.get_json()
        staging_ids = data.get("ids", [])
        exclude = data.get("exclude", True)
        
        conn = get_db_connection()
        placeholders = ','.join(['?'] * len(staging_ids))
        conn.execute(f"""
            UPDATE sheets_staging 
            SET exclude_from_upload = ?, updated_at = datetime('now')
            WHERE id IN ({placeholders})
        """, [1 if exclude else 0] + staging_ids)
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "affected": len(staging_ids)})
    
    
    @app.route("/api/admin/sheets_staging/delete", methods=["POST"])
    def api_admin_sheets_staging_delete():
        """Delete jobs from staging (won't be uploaded)."""
        data = request.get_json()
        staging_ids = data.get("ids", [])
        
        conn = get_db_connection()
        placeholders = ','.join(['?'] * len(staging_ids))
        conn.execute(f"DELETE FROM sheets_staging WHERE id IN ({placeholders})", staging_ids)
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "deleted": len(staging_ids)})
    
    
    @app.route("/api/admin/sheets_staging/create_tab", methods=["POST"])
    def api_admin_sheets_staging_create_tab():
        """Create a new custom tab and assign selected jobs to it."""
        data = request.get_json()
        new_tab_name = data.get("tab_name", "").strip()
        job_ids = data.get("job_ids", [])
        
        if not new_tab_name or not job_ids:
            return jsonify({"success": False, "error": "Tab name and job IDs required"})
        
        conn = get_db_connection()
        placeholders = ','.join(['?'] * len(job_ids))
        
        conn.execute(f"""
            UPDATE sheets_staging 
            SET assigned_tab = ?,
                updated_at = datetime('now')
            WHERE id IN ({placeholders})
        """, [new_tab_name] + job_ids)
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "tab_name": new_tab_name, "affected": len(job_ids)})
    
    
    @app.route("/api/admin/sheets_staging/merge_tabs", methods=["POST"])
    def api_admin_sheets_staging_merge_tabs():
        """Merge all jobs from source_tab into target_tab."""
        data = request.get_json()
        source_tab = data.get("source_tab")
        target_tab = data.get("target_tab")
        country = data.get("country")
        
        if not all([source_tab, target_tab, country]):
            return jsonify({"success": False, "error": "Missing parameters"})
        
        conn = get_db_connection()
        
        result = conn.execute("""
            UPDATE sheets_staging 
            SET assigned_tab = ?,
                updated_at = datetime('now')
            WHERE assigned_tab = ?
              AND assigned_sheet = ?
        """, (target_tab, source_tab, country))
        
        affected = result.rowcount
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "merged": affected, "from": source_tab, "to": target_tab})
    
    
    @app.route("/api/admin/sheets_staging/upload", methods=["POST"])
    def api_admin_sheets_staging_upload():
        """Trigger Google Sheets upload for approved jobs."""
        try:
            from src.reports.google_sheets_export import upload_from_staging

            data = request.get_json() or {}
            country_filter = data.get("country", None)
            country_filters = data.get("countries", None)
            tab_filter = data.get("tab", None)
            
            batch_id = str(uuid.uuid4())
            result = upload_from_staging(
                batch_id,
                country_filter=country_filter,
                tab_filter=tab_filter,
                country_filters=country_filters,
            )
            return jsonify({
                "success": True,
                "stats": result,
                "batch_id": batch_id,
                "country": country_filter,
                "countries": country_filters,
                "tab": tab_filter
            })
        except ModuleNotFoundError as e:
            logger.error("[sheets_staging] Upload dependency missing: %s", e)
            return jsonify({
                "success": False,
                "error": "Missing dependency for Google Sheets upload. Run: pip install -r requirements.txt"
            }), 500
        except Exception as e:
            logger.error("[sheets_staging] Upload failed: %s", e)
            return jsonify({"success": False, "error": str(e)})


    @app.route("/api/admin/sheets_staging/ai_process", methods=["POST"])
    @app.route("/api/admin/sheets_staging/grok_process", methods=["POST"])
    def api_admin_sheets_staging_ai_process():
        """Process pending staging rows with AI (Groq/Grok compatible) and optionally auto-upload."""
        from config.settings import GROQ_API_KEYS, GROK_API_KEY, GROK_BASE_URL, GROK_MODEL
        from src.ai.grok_staging import process_staging_with_grok

        api_keys = [k for k in (GROQ_API_KEYS or []) if k]
        if not api_keys and GROK_API_KEY:
            api_keys = [GROK_API_KEY]

        if not api_keys:
            return jsonify({"success": False, "error": "No Groq/Grok API key configured"})

        payload = request.get_json() or {}
        country_filter = payload.get("country")
        tab_filter = payload.get("tab")
        country_filters = payload.get("countries") or []
        require_review = bool(payload.get("require_review", True))
        auto_upload = bool(payload.get("auto_upload", False))
        raw_max_rows = payload.get("max_rows", 0)
        max_rows = None
        try:
            raw_text = str(raw_max_rows).strip().lower() if raw_max_rows is not None else ""
            if raw_text not in {"", "0", "-1", "all"}:
                parsed = int(raw_text)
                if parsed > 0:
                    # Keep a practical hard upper bound while allowing large batches.
                    max_rows = min(parsed, 100000)
        except (TypeError, ValueError):
            max_rows = None

        conn = get_db_connection()
        try:
            query = """
                SELECT
                    s.id,
                    s.job_id,
                    COALESCE(s.override_title, j.title) as title,
                    COALESCE(s.override_normalized_title, j.normalized_title) as normalized_title,
                    COALESCE(s.override_company, j.company) as company,
                    COALESCE(s.override_location, j.location) as location,
                    COALESCE(s.override_country, j.country) as country,
                    COALESCE(s.override_remote_type, j.remote_type) as remote_type,
                    s.assigned_tab,
                    s.assigned_sheet
                FROM sheets_staging s
                JOIN jobs j ON j.job_id = s.job_id
                WHERE s.status = 'pending'
                  AND s.exclude_from_upload = 0
            """

            params = []
            effective_countries = [c for c in (country_filters or []) if c and c != 'all']
            if not effective_countries and country_filter and country_filter != 'all':
                effective_countries = [country_filter]

            if effective_countries:
                placeholders = ",".join(["?"] * len(effective_countries))
                query += f" AND s.assigned_sheet IN ({placeholders})"
                params.extend(effective_countries)
            elif country_filter and country_filter != 'all':
                query += " AND s.assigned_sheet = ?"
                params.append(country_filter)

            if tab_filter and tab_filter != 'all':
                query += " AND s.assigned_tab = ?"
                params.append(tab_filter)

            query += " ORDER BY s.assigned_sheet, s.assigned_tab, s.id"
            if max_rows is not None:
                query += " LIMIT ?"
                params.append(max_rows)

            rows = conn.execute(query, params).fetchall()
            if not rows:
                return jsonify({"success": True, "message": "No pending rows to process", "stats": {"processed": 0}})

            with conn:
                stats = process_staging_with_grok(
                    conn,
                    rows,
                    api_keys=api_keys,
                    model=GROK_MODEL,
                    base_url=GROK_BASE_URL,
                )

            upload_result = None
            if auto_upload and not require_review:
                from src.reports.google_sheets_export import upload_from_staging

                batch_id = str(uuid.uuid4())
                upload_result = upload_from_staging(
                    batch_id,
                    country_filter=country_filter,
                    tab_filter=tab_filter,
                    country_filters=effective_countries if effective_countries else None,
                )

            return jsonify({
                "success": True,
                "stats": stats,
                "max_rows": max_rows if max_rows is not None else "all",
                "processed_rows": len(rows),
                "require_review": require_review,
                "auto_upload": auto_upload,
                "upload": upload_result,
            })
        except Exception as e:
            logger.error("[sheets_staging] grok_process failed: %s", e, exc_info=True)
            return jsonify({"success": False, "error": str(e)})
        finally:
            conn.close()


    @app.route("/api/admin/sheets_staging/submit_review", methods=["POST"])
    def api_admin_sheets_staging_submit_review():
        """Submit final human review: persist staging edits to both staging and jobs tables."""
        payload = request.get_json() or {}
        staging_ids = payload.get("ids") or []
        reviewer = (payload.get("reviewer") or "admin").strip()

        if not staging_ids:
            return jsonify({"success": False, "error": "No jobs selected"})

        conn = get_db_connection()
        try:
            placeholders = ",".join(["?"] * len(staging_ids))
            rows = conn.execute(
                f"""
                SELECT
                    s.id,
                    s.job_id,
                    COALESCE(s.override_title, j.title) AS final_title,
                    COALESCE(s.override_normalized_title, j.normalized_title) AS final_normalized_title,
                    COALESCE(s.override_company, j.company) AS final_company,
                    COALESCE(s.override_location, j.location) AS final_location,
                    COALESCE(s.override_country, j.country) AS final_country,
                    COALESCE(s.override_remote_type, j.remote_type) AS final_remote_type
                FROM sheets_staging s
                JOIN jobs j ON j.job_id = s.job_id
                WHERE s.id IN ({placeholders})
                """,
                staging_ids,
            ).fetchall()

            updated_jobs = 0
            for row in rows:
                conn.execute(
                    """
                    UPDATE jobs
                    SET title = ?,
                        normalized_title = ?,
                        company = ?,
                        location = ?,
                        country = ?,
                        remote_type = ?
                    WHERE job_id = ?
                    """,
                    (
                        row["final_title"],
                        row["final_normalized_title"],
                        row["final_company"],
                        row["final_location"],
                        row["final_country"],
                        row["final_remote_type"],
                        row["job_id"],
                    ),
                )
                updated_jobs += 1

            conn.execute(
                f"""
                UPDATE sheets_staging
                SET review_status = 'approved',
                    reviewed_by = ?,
                    reviewed_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id IN ({placeholders})
                """,
                [reviewer] + staging_ids,
            )

            conn.commit()
            return jsonify({"success": True, "reviewed": len(staging_ids), "jobs_updated": updated_jobs})
        except Exception as e:
            conn.rollback()
            logger.error("[sheets_staging] submit_review failed: %s", e, exc_info=True)
            return jsonify({"success": False, "error": str(e)})
        finally:
            conn.close()
    
    
    @app.route("/api/admin/sheets_staging/find_similar", methods=["POST"])
    def api_admin_sheets_staging_find_similar():
        """Find jobs with similar titles using similarity matching."""
        from difflib import SequenceMatcher
        
        data = request.get_json()
        job_ids = data.get("job_ids", [])
        threshold = data.get("threshold", 0.75)  # 75% similarity by default
        country = data.get("country", "all")
        
        if not job_ids:
            return jsonify({"success": False, "error": "No jobs selected"})
        
        conn = get_db_connection()
        
        # Get titles of selected jobs
        placeholders = ','.join(['?'] * len(job_ids))
        selected_jobs = conn.execute(f"""
            SELECT 
                s.id,
                COALESCE(s.override_normalized_title, j.normalized_title, j.title) as title
            FROM sheets_staging s
            JOIN jobs j ON j.job_id = s.job_id
            WHERE s.id IN ({placeholders})
        """, job_ids).fetchall()
        
        if not selected_jobs:
            conn.close()
            return jsonify({"success": False, "error": "Selected jobs not found"})
        
        # Get all titles from staging (same country if filtered)
        query = """
            SELECT 
                s.id,
                s.job_id,
                COALESCE(s.override_normalized_title, j.normalized_title, j.title) as title,
                s.assigned_tab
            FROM sheets_staging s
            JOIN jobs j ON j.job_id = s.job_id
            WHERE 1=1
        """
        params = []
        
        if country != "all":
            query += " AND s.assigned_sheet = ?"
            params.append(country)
        
        all_jobs = conn.execute(query, params).fetchall()
        conn.close()
        
        # Find similar jobs
        similar_groups = {}
        selected_titles = {row[0]: row[1].lower().strip() for row in selected_jobs}
        
        for job_row in all_jobs:
            job_id, job_pk, job_title, current_tab = job_row
            job_title_lower = job_title.lower().strip()
            
            # Check similarity against each selected job
            for selected_id, selected_title in selected_titles.items():
                similarity = SequenceMatcher(None, job_title_lower, selected_title).ratio()
                
                if similarity >= threshold:
                    if selected_id not in similar_groups:
                        similar_groups[selected_id] = []
                    
                    similar_groups[selected_id].append({
                        "id": job_id,
                        "job_id": job_pk,
                        "title": job_title,
                        "current_tab": current_tab,
                        "similarity": round(similarity, 3)
                    })
        
        # Flatten and deduplicate
        all_similar = []
        seen_ids = set()
        
        for group in similar_groups.values():
            for job in group:
                if job["id"] not in seen_ids:
                    all_similar.append(job)
                    seen_ids.add(job["id"])
        
        # Sort by similarity descending
        all_similar.sort(key=lambda x: x["similarity"], reverse=True)
        
        return jsonify({
            "success": True,
            "similar_jobs": all_similar,
            "count": len(all_similar),
            "threshold": threshold
        })
    
    
    @app.route("/api/admin/sheets_staging/move_to_tab", methods=["POST"])
    def api_admin_sheets_staging_move_to_tab():
        """Move selected jobs to a different tab (existing or new)."""
        try:
            data = request.get_json()
            job_ids = data.get("job_ids", [])
            target_tab = data.get("target_tab", "").strip()
            create_new = data.get("create_new", False)
            
            if not job_ids:
                return jsonify({"success": False, "error": "No jobs selected"})
            
            if not target_tab:
                return jsonify({"success": False, "error": "Target tab name required"})
            
            logger.info("[sheets_staging] Moving %d jobs to tab '%s'", len(job_ids), target_tab)
            
            conn = get_db_connection()
            
            moved_count = 0
            skipped_duplicates = 0
            
            # Process each job individually to handle UNIQUE constraint
            for staging_id in job_ids:
                try:
                    # Get current job's assigned_sheet and job_id
                    row = conn.execute("""
                        SELECT job_id, assigned_sheet, assigned_tab 
                        FROM sheets_staging 
                        WHERE id = ?
                    """, (staging_id,)).fetchone()
                    
                    if not row:
                        logger.warning("[sheets_staging] Staging ID %s not found", staging_id)
                        continue
                    
                    job_id = row['job_id']
                    assigned_sheet = row['assigned_sheet']
                    current_tab = row['assigned_tab']
                    
                    # Skip if already in target tab
                    if current_tab == target_tab:
                        logger.debug("[sheets_staging] Job %s already in tab '%s', skipping", job_id, target_tab)
                        skipped_duplicates += 1
                        continue
                    
                    # Check if this job already exists in the target tab
                    existing = conn.execute("""
                        SELECT id FROM sheets_staging
                        WHERE job_id = ? AND assigned_sheet = ? AND assigned_tab = ?
                    """, (job_id, assigned_sheet, target_tab)).fetchone()
                    
                    if existing:
                        # Delete the duplicate entry (keep the one we're trying to move)
                        conn.execute("DELETE FROM sheets_staging WHERE id = ?", (existing['id'],))
                        logger.info("[sheets_staging] Deleted duplicate entry for job %s in tab '%s'", 
                                   job_id, target_tab)
                    
                    # Now update the current entry
                    conn.execute("""
                        UPDATE sheets_staging
                        SET assigned_tab = ?,
                            updated_at = datetime('now')
                        WHERE id = ?
                    """, (target_tab, staging_id))
                    
                    moved_count += 1
                    
                except Exception as e:
                    logger.error("[sheets_staging] Error moving staging_id %s: %s", staging_id, e)
                    continue
            
            conn.commit()
            conn.close()
            
            logger.info("[sheets_staging] Moved %d jobs to tab '%s' (skipped %d duplicates)", 
                       moved_count, target_tab, skipped_duplicates)
            
            return jsonify({
                "success": True,
                "moved": moved_count,
                "target_tab": target_tab,
                "created_new": create_new,
                "skipped_duplicates": skipped_duplicates
            })
            
        except Exception as e:
            logger.error("[sheets_staging] Move to tab failed: %s", e, exc_info=True)
            return jsonify({"success": False, "error": str(e)})
    
    
    @app.route("/api/admin/sheets_staging/rename_tab", methods=["POST"])
    def api_admin_sheets_staging_rename_tab():
        """Rename a tab (updates all jobs in that tab)."""
        data = request.get_json()
        old_name = data.get("old_name", "").strip()
        new_name = data.get("new_name", "").strip()
        country = data.get("country")
        
        if not all([old_name, new_name, country]):
            return jsonify({"success": False, "error": "Missing parameters"})
        
        conn = get_db_connection()
        
        result = conn.execute("""
            UPDATE sheets_staging
            SET assigned_tab = ?,
                updated_at = datetime('now')
            WHERE assigned_tab = ?
              AND assigned_sheet = ?
        """, (new_name, old_name, country))
        
        affected = result.rowcount
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True,
            "renamed": affected,
            "old_name": old_name,
            "new_name": new_name
        })
    
    
    # ─── Click Tracking Routes ────────────────────────────────────────────────
    
    @app.route("/sheets/track")
    def sheets_track_redirect():
        """Track Overview tab link clicks and redirect to Google Sheets tab."""
        country = request.args.get("country", "")
        tab_name = request.args.get("tab", "")
        spreadsheet_id = request.args.get("sheet_id", "")
        gid = request.args.get("gid", "0")
        
        # Track the click
        user_identifier = session.get("session_id") or request.remote_addr
        user_agent = request.headers.get("User-Agent", "")
        referrer = request.headers.get("Referer", "")
        
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT INTO sheets_click_tracking 
                (country, tab_name, spreadsheet_id, user_identifier, user_agent, 
                 clicked_at, referrer, click_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                country,
                tab_name,
                spreadsheet_id,
                user_identifier,
                user_agent,
                datetime.utcnow().isoformat(),
                referrer,
                'tab_navigation'
            ))
            conn.commit()
            
            logger.info("[sheets_track] Click tracked: %s → %s by %s", 
                       country, tab_name, user_identifier[:8] if user_identifier else "unknown")
        except Exception as e:
            logger.warning("[sheets_track] Failed to track click: %s", e)
        finally:
            conn.close()
        
        # Build Google Sheets URL and redirect
        sheets_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={gid}"
        return redirect(sheets_url)
    
    
    @app.route("/sheets/track_job")
    def sheets_track_job_click():
        """Track individual job posting clicks and redirect to apply URL."""
        job_id = request.args.get("job_id", "")
        country = request.args.get("country", "")
        tab_name = request.args.get("tab", "")
        apply_url = request.args.get("url", "")
        
        # Track the click
        user_identifier = session.get("session_id") or request.remote_addr
        user_agent = request.headers.get("User-Agent", "")
        referrer = request.headers.get("Referer", "")
        
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT INTO sheets_click_tracking 
                (job_id, country, tab_name, user_identifier, user_agent, 
                 clicked_at, referrer, click_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                int(job_id) if job_id else None,
                country,
                tab_name,
                user_identifier,
                user_agent,
                datetime.utcnow().isoformat(),
                referrer,
                'job_posting'
            ))
            conn.commit()
            
            logger.info("[sheets_track_job] Job click tracked: job_id=%s, %s/%s by %s", 
                       job_id, country, tab_name, user_identifier[:8] if user_identifier else "unknown")
        except Exception as e:
            logger.warning("[sheets_track_job] Failed to track click: %s", e)
        finally:
            conn.close()
        
        # Validate URL scheme before redirecting
        if apply_url:
            from urllib.parse import urlparse as _up
            scheme = _up(apply_url).scheme.lower()
            if scheme not in ("http", "https", ""):
                return "Unsafe redirect URL", 400
            return redirect(apply_url)
        else:
            return "No URL provided", 400
    
    
    @app.route("/admin/sheets_analytics")
    def admin_sheets_analytics():
        """Admin page showing click analytics for Overview tab links."""
        conn = get_db_connection()
        
        filter_country = request.args.get("country", "all")
        filter_days = int(request.args.get("days", "30"))
        
        from datetime import timedelta
        cutoff_date = (datetime.now() - timedelta(days=filter_days)).isoformat()
        
        # Most clicked tabs
        query_most_clicked = """
            SELECT 
                country,
                tab_name,
                COUNT(*) as click_count,
                COUNT(DISTINCT user_identifier) as unique_users,
                MAX(clicked_at) as last_clicked
            FROM sheets_click_tracking
            WHERE clicked_at >= ?
        """
        params = [cutoff_date]
        
        if filter_country != "all":
            query_most_clicked += " AND country = ?"
            params.append(filter_country)
        
        query_most_clicked += """
            GROUP BY country, tab_name
            ORDER BY click_count DESC
            LIMIT 20
        """
        
        most_clicked = conn.execute(query_most_clicked, params).fetchall()
        
        # Click trends over time
        query_trends = """
            SELECT 
                DATE(clicked_at) as date,
                country,
                COUNT(*) as clicks
            FROM sheets_click_tracking
            WHERE clicked_at >= ?
        """
        trend_params = [cutoff_date]
        
        if filter_country != "all":
            query_trends += " AND country = ?"
            trend_params.append(filter_country)
        
        query_trends += """
            GROUP BY DATE(clicked_at), country
            ORDER BY date DESC
        """
        
        trends = conn.execute(query_trends, trend_params).fetchall()
        
        # User engagement
        query_users = """
            SELECT 
                user_identifier,
                COUNT(*) as total_clicks,
                COUNT(DISTINCT tab_name) as unique_tabs_viewed,
                MIN(clicked_at) as first_click,
                MAX(clicked_at) as last_click
            FROM sheets_click_tracking
            WHERE clicked_at >= ?
        """
        user_params = [cutoff_date]
        
        if filter_country != "all":
            query_users += " AND country = ?"
            user_params.append(filter_country)
        
        query_users += """
            GROUP BY user_identifier
            ORDER BY total_clicks DESC
            LIMIT 20
        """
        
        user_stats = conn.execute(query_users, user_params).fetchall()
        
        # Overall stats
        query_total = """
            SELECT 
                COUNT(*) as total_clicks,
                COUNT(DISTINCT user_identifier) as unique_users,
                COUNT(DISTINCT country) as countries,
                COUNT(DISTINCT tab_name) as unique_tabs
            FROM sheets_click_tracking
            WHERE clicked_at >= ?
        """
        total_params = [cutoff_date]
        
        if filter_country != "all":
            query_total += " AND country = ?"
            total_params.append(filter_country)
        
        overall_stats = conn.execute(query_total, total_params).fetchone()
        
        # Get countries for filter
        countries = conn.execute("""
            SELECT DISTINCT country FROM sheets_click_tracking ORDER BY country
        """).fetchall()
        
        conn.close()
        
        return render_template(
            "admin_sheets_analytics.html",
            most_clicked=most_clicked,
            trends=[dict(row) for row in trends],  # Convert to dict for JSON
            user_stats=user_stats,
            overall_stats=overall_stats,
            countries=countries,
            current_country=filter_country,
            current_days=filter_days
        )
    
    
    @app.route("/api/admin/sheets_analytics/export")
    def api_admin_sheets_analytics_export():
        """Export click tracking data as CSV."""
        conn = get_db_connection()
        
        rows = conn.execute("""
            SELECT 
                country,
                tab_name,
                user_identifier,
                clicked_at,
                user_agent,
                referrer
            FROM sheets_click_tracking
            ORDER BY clicked_at DESC
            LIMIT 5000
        """).fetchall()
        
        conn.close()
        
        # Generate CSV
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['Country', 'Tab', 'User ID', 'Timestamp', 'User Agent', 'Referrer'])
        
        for row in rows:
            writer.writerow(row)
        
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = 'attachment; filename=sheets_clicks.csv'
        
        return response
    
    
    @app.route("/api/admin/sheets_staging/reevaluate", methods=["POST"])
    def api_admin_sheets_staging_reevaluate():
        """
        Reevaluate selected jobs by fetching fresh data from jobs table.
        Clears all overrides and reassigns country/tab based on current job data.
        """
        try:
            data = request.get_json()
            staging_ids = data.get("ids", [])
            
            if not staging_ids:
                return jsonify({"success": False, "error": "No jobs selected"})
            
            conn = get_db_connection()
            
            updated_count = 0
            skipped_count = 0
            
            # Process each job individually to avoid SQL parameter issues
            for staging_id in staging_ids:
                try:
                    # Get current job data from jobs table AND staging overrides
                    row = conn.execute("""
                        SELECT 
                            s.id as staging_id,
                            j.job_id,
                            COALESCE(s.override_country, j.country) as country,
                            COALESCE(s.override_location, j.location) as location,
                            COALESCE(s.override_normalized_title, j.normalized_title) as normalized_title,
                            COALESCE(s.override_title, j.title) as title,
                            COALESCE(s.override_company, j.company) as company,
                            COALESCE(s.override_remote_type, j.remote_type) as remote_type
                        FROM sheets_staging s
                        JOIN jobs j ON j.job_id = s.job_id
                        WHERE s.id = ?
                    """, (staging_id,)).fetchone()
                    
                    if not row:
                        logger.warning("[sheets_staging] Job with staging_id %s not found", staging_id)
                        skipped_count += 1
                        continue
                    
                    country = row['country']
                    normalized_title = row['normalized_title']
                    job_id = row['job_id']
                    
                    # Determine assigned target using dynamic mapping
                    target = get_target_for_country(conn, country)
                    if not target:
                        skipped_count += 1
                        logger.warning(
                            "[sheets_staging] Skipping job %s: country '%s' has no active target",
                            job_id, country
                        )
                        continue

                    assigned_sheet = country
                    assigned_target_id = target['id']
                    
                    # Use normalized_title as tab, fallback to 'Other'
                    assigned_tab = normalized_title or 'Other'
                    
                    # Update assignments (keep the overrides, just update the assignments)
                    conn.execute("""
                        UPDATE sheets_staging 
                        SET assigned_sheet = ?,
                            assigned_tab = ?,
                            assigned_target_id = ?,
                            updated_at = datetime('now')
                        WHERE id = ?
                    """, (assigned_sheet, assigned_tab, assigned_target_id, staging_id))
                    
                    updated_count += 1
                    
                except Exception as e:
                    logger.error("[sheets_staging] Error processing staging_id %s: %s", staging_id, e)
                    skipped_count += 1
            
            conn.commit()
            conn.close()
            
            return jsonify({
                "success": True,
                "updated": updated_count,
                "skipped": skipped_count,
                "total": len(staging_ids)
            })
            
        except Exception as e:
            logger.error("[sheets_staging] Reevaluate failed: %s", e, exc_info=True)
            return jsonify({"success": False, "error": str(e)})
