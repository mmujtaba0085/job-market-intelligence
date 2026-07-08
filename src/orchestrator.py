"""
src/orchestrator.py
─────────────────────
Main pipeline runner. Wires all modules together end-to-end.

CHANGES (per requirements):
- Modified run_ingestion() to use run.record_source_jobs() per collector
- Tracks fetched count accurately from len(raw) per source
- Only includes sources in sources_used if they fetched > 0 jobs

CLI modes:
  --mode weekly          Full pipeline: collect → normalize → dedupe → extract → analytics → report
  --mode ingest-only     Collect + normalize + dedupe + extract only (no analytics/report)
  --mode report-only     Recompute analytics + regenerate report from existing DB data
  --mode crawl           Continuous Findwork full-catalogue crawler (runs until CTRL+C)
  --backfill             Generate historical weekly reports from stored data

Usage:
  python -m src.orchestrator --mode weekly
  python -m src.orchestrator --mode ingest-only
  python -m src.orchestrator --mode report-only
  python -m src.orchestrator --mode crawl
  python -m src.orchestrator --backfill --start 2026-01-01 --end 2026-02-27
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
from datetime import date, timedelta
from pathlib import Path

# Add workspace root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.markets import TARGET_MARKETS
from config.settings import EMERGING_LOOKBACK_WEEKS, OUTPUTS_DIR
from src.analytics.category_analytics import compute_category_stats
from src.analytics.co_occurrence import compute_co_occurrence
from src.analytics.coverage_metrics import compute_coverage_stats
from src.analytics.diversity_rank import recompute_diversity_ranks
from src.analytics.temporal_trends import compute_trend_stats
from src.analytics.title_analytics import compute_title_stats
from src.analytics.weekly_metrics import compute_weekly_metrics
from src.collectors.remotive_collector import RemotiveCollector
from src.collectors.jsearch_collector import JSearchCollector
from src.collectors.arbeitnow_collector import ArbeitnowCollector
from src.collectors.usajobs_collector import USAJobsCollector
from src.collectors.themuse_collector import TheMuseCollector
from src.collectors.graphqljobs_collector import GraphQLJobsCollector
from src.collectors.himalayas_collector import HimalayasCollector
from src.collectors.himalayas_rss_collector import HimalayasRSSCollector
from src.collectors.jobicy_collector import JobicyCollector
from src.collectors.hireweb3_collector import HireWeb3Collector
from src.collectors.adzuna_collector import AdzunaCollector
from src.collectors.findwork_collector import FindworkCollector
from src.collectors.jooble_collector import JoobleCollector
from src.collectors.pakistanjobsbank_collector import PakistanJobsBankCollector
from src.collectors.github_repo_collector import (
    GitHubSimplify2026Collector,
    GitHubVansh2026Collector,
    GitHubSpeedyApply2026Collector,
    GitHubJobright2026Collector,
    GitHubNUFTQuant2026Collector,
    GitHubOffSeasonInternshipsCollector,
)
from src.deduplicator import deduplicate_and_store
from src.monetization import generate_free_report, generate_premium_report
from src.normalizer import normalize_batch
from src.publisher.manual_export import publish
from src.reports.chart_generator import generate_all_charts
from src.reports.charts_export import export_charts
from src.reports.csv_export import (
    export_categories,
    export_companies,
    export_growth_skills,
    export_job_titles,
    export_locations,
    export_movers_by_delta,
    export_movers_by_score,
    export_skill_pairs,
    export_skill_trends,
    export_sources_breakdown,
    export_title_trends,
    export_top_skills,
)
from src.reports.html_converter import convert_to_html
from src.reports.markdown_report import generate_markdown_report
from src.reports.tracker_directory_export import export_directory
from src.run_manager import RunContext
from src.skill_extractor import extract_skills_batch
from src.storage.db import get_remote_ratio, run_migrations
from config.sources import SOURCES_BY_ID

logger = logging.getLogger(__name__)

# ── Registered collectors ─────────────────────────────────────────────────────
# Only instantiate collectors for enabled sources
COLLECTORS = []

_COLLECTOR_CLASSES = [
    RemotiveCollector,
    JSearchCollector,
    ArbeitnowCollector,
    USAJobsCollector,
    TheMuseCollector,
    GraphQLJobsCollector,
    HimalayasCollector,
    HimalayasRSSCollector,
    JobicyCollector,
    HireWeb3Collector,
    AdzunaCollector,
    FindworkCollector,
    JoobleCollector,
    PakistanJobsBankCollector,
    GitHubSimplify2026Collector,
    GitHubVansh2026Collector,
    GitHubSpeedyApply2026Collector,
    GitHubJobright2026Collector,
    GitHubNUFTQuant2026Collector,
    GitHubOffSeasonInternshipsCollector,
]

for collector_class in _COLLECTOR_CLASSES:
    source_id = collector_class.source_id
    
    if source_id in SOURCES_BY_ID and SOURCES_BY_ID[source_id].get("enabled", False):
        try:
            COLLECTORS.append(collector_class())
        except Exception as e:
            logger.warning("[orchestrator] Skipping collector %s: %s", source_id, e)


# ─────────────────────────────────────────────────────────────────────────────
# Public pipeline functions
# ─────────────────────────────────────────────────────────────────────────────

def run_ingestion(market: dict, run: RunContext) -> None:
    """Collect → Normalize → Dedupe → Extract Skills → Store."""
    market_id = market["market_id"]
    logger.info("[orchestrator] ── INGESTION START: %s ──", market_id)

    all_jobs_raw = []

    # 1. Collect - track per source
    source_allowlist = market.get("source_allowlist")
    for collector in COLLECTORS:
        # A market may restrict itself to a subset of sources (e.g. a
        # single-source, all-categories market like pakistan_jobs_all).
        # Absent = run every registered collector, as before.
        if source_allowlist is not None and collector.source_id not in source_allowlist:
            continue

        # Track attempt
        run.record_source_attempted()
        
        raw = collector.collect(market)
        # Record fetched count per source (inserted/deduped set to 0 here, updated later if needed)
        run.record_source_jobs(collector.source_id, fetched=len(raw), inserted=0, deduped=0)
        all_jobs_raw.extend(raw)
        logger.info("[orchestrator] %s → %d raw jobs", collector.source_id, len(raw))

    # 2. Normalize
    normalized = normalize_batch(all_jobs_raw, market_id)
    logger.info("[orchestrator] Normalized: %d / %d", len(normalized), len(all_jobs_raw))

    # 3. Dedupe + Store
    results, summary = deduplicate_and_store(normalized)
    run.record_jobs(
        fetched=len(all_jobs_raw),
        inserted=summary.inserted,
        deduped=summary.url_dups + summary.canonical_dups,
    )

    # 4. Extract skills for new jobs only
    new_jobs = [(r.job_id, r.job.description_text) for r in results if r.is_new and r.job_id]
    from src.storage.db import insert_skills
    if new_jobs:
        signals = extract_skills_batch(new_jobs, market_id)
        insert_skills(signals)
        run.record_skills(len(signals))
        logger.info("[orchestrator] Skills extracted: %d", len(signals))


def populate_sheets_staging(market_id: str, week_start: date, week_end: date) -> None:
    """
    Populate sheets_staging table with ALL jobs from the past week.
    Auto-assigns country, tab, and target spreadsheet based on dynamic mappings.
    Shows both pending and staged jobs in the admin interface.
    """
    from src.storage.db import get_connection
    from src.storage.sheet_targets import get_target_for_country
    from config.settings import SHEETS_ENABLED
    
    if not SHEETS_ENABLED:
        logger.debug("[orchestrator] Google Sheets integration disabled, skipping staging population")
        return
    
    logger.info("[orchestrator] Populating Google Sheets staging for week %s", week_start.isoformat())
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get ALL jobs from the past week (not just new ones)
    # This allows reviewing all jobs from the week, including previously uploaded ones
    query = """
        SELECT 
            job_id,
            country,
            normalized_title
        FROM jobs
        WHERE market_id = ?
          AND posted_date >= ?
          AND posted_date < ?
          AND country IS NOT NULL
          AND TRIM(country) != ''
          AND normalized_title IS NOT NULL
          AND normalized_title != ''
    """
    
    cursor.execute(query, (market_id, week_start.isoformat(), week_end.isoformat()))
    jobs = cursor.fetchall()
    
    if not jobs:
        logger.info("[orchestrator] No jobs found for Google Sheets staging this week")
        conn.close()
        return
    
    # Insert into staging (ignore duplicates)
    inserted = 0
    skipped_unmapped = 0
    for row in jobs:
        job_id, country, normalized_title = row

        target = get_target_for_country(conn, country)
        if not target:
            skipped_unmapped += 1
            continue

        assigned_sheet = country
        assigned_target_id = target["id"]
        
        # Use normalized_title as tab name
        assigned_tab = normalized_title or 'Other'
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO sheets_staging 
                (job_id, assigned_tab, assigned_sheet, assigned_target_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', datetime('now'), datetime('now'))
            """, (job_id, assigned_tab, assigned_sheet, assigned_target_id))
            
            if cursor.rowcount > 0:
                inserted += 1
        except Exception as e:
            logger.warning("[orchestrator] Failed to insert job %s into staging: %s", job_id, e)
    
    conn.commit()
    conn.close()
    
    logger.info(
        "[orchestrator] Google Sheets staging populated: %d jobs added (from %d total, %d unmapped countries skipped)",
        inserted,
        len(jobs),
        skipped_unmapped,
    )


def run_analytics_and_report(
    market: dict,
    week_start: date,
    run: RunContext,
    generate_html: bool = False,
) -> None:
    """Compute analytics → generate all report files → publish (manual mode)."""
    market_id = market["market_id"]
    week_str = f"{week_start.year}-{week_start.isocalendar()[1]:02d}"
    week_end = week_start + timedelta(days=7)
    output_dir = OUTPUTS_DIR / market_id / week_str
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[orchestrator] ── ANALYTICS START: %s week %s ──", market_id, week_str)

    # 5. Compute weekly metrics
    metrics = compute_weekly_metrics(market_id, week_start)
    run.record_metrics(len(metrics))

    if not metrics:
        logger.warning("[orchestrator] No metrics computed for %s — skipping report.", market_id)
        return

    # 6. Coverage analytics
    coverage_data = compute_coverage_stats(
        market_id,
        week_start.isoformat(),
        week_end.isoformat(),
    )
    
    # Track remote breakdown in run summary
    if coverage_data and coverage_data.get("remote_breakdown"):
        run.record_remote_breakdown(coverage_data["remote_breakdown"])

    # 7. Remote ratio
    remote_ratio = get_remote_ratio(market_id, week_start.isoformat(), week_end.isoformat())

    # 8. Co-occurrence (optional)
    co_occ = compute_co_occurrence(market_id, week_start.isoformat(), week_end.isoformat())

    # 8b. Phase 2: Deep-dive analytics
    title_stats = None
    trend_stats = None
    category_stats = None
    
    try:
        title_stats = compute_title_stats(market_id, week_start, week_end)
        logger.info("[orchestrator] Title analytics computed: %d unique titles", 
                   title_stats.get("total_unique_titles", 0))
    except Exception as exc:
        logger.warning("[orchestrator] Title analytics failed: %s", exc)
    
    try:
        trend_stats = compute_trend_stats(market_id, week_start, top_n=20)
        logger.info("[orchestrator] Trend analytics computed: %d skills tracked",
                   len(trend_stats.get("skill_trends", [])))
    except Exception as exc:
        logger.warning("[orchestrator] Trend analytics failed: %s", exc)
    
    try:
        category_stats = compute_category_stats(market_id, week_start, week_end)
        logger.info("[orchestrator] Category analytics computed: %d categories",
                   category_stats.get("total_categories", 0))
    except Exception as exc:
        logger.warning("[orchestrator] Category analytics failed: %s", exc)

    # 8c. Populate Google Sheets staging
    try:
        populate_sheets_staging(market_id, week_start, week_end)
    except Exception as exc:
        logger.warning("[orchestrator] Google Sheets staging population failed: %s", exc)

    # 9. Generate all CSV exports
    export_top_skills(metrics, output_dir)
    export_growth_skills(metrics, output_dir)
    export_movers_by_delta(metrics, output_dir)
    export_movers_by_score(metrics, output_dir)
    
    # Export coverage breakdowns
    if coverage_data:
        export_sources_breakdown(coverage_data.get("sources_breakdown", []), output_dir)
        export_locations(coverage_data.get("countries_breakdown", []), output_dir)
        export_companies(coverage_data.get("companies_breakdown", []), output_dir)
    
    # Phase 2: Deep-dive exports
    if co_occ:
        try:
            export_skill_pairs(co_occ, output_dir, limit=50)
        except Exception as exc:
            logger.warning("[orchestrator] Skill pairs export failed: %s", exc)
    
    if title_stats:
        try:
            export_job_titles(title_stats.get("top_titles", []), output_dir)
            export_title_trends(title_stats.get("title_trends", []), output_dir)
        except Exception as exc:
            logger.warning("[orchestrator] Title exports failed: %s", exc)
    
    if trend_stats:
        try:
            export_skill_trends(trend_stats.get("skill_trends", []), output_dir)
        except Exception as exc:
            logger.warning("[orchestrator] Trend export failed: %s", exc)
    
    if category_stats:
        try:
            export_categories(category_stats.get("category_breakdown", []), output_dir)
        except Exception as exc:
            logger.warning("[orchestrator] Category export failed: %s", exc)

    # 10. Generate charts JSON
    export_charts(metrics, output_dir, coverage_data=coverage_data, co_occurrence=co_occ,
                 trend_stats=trend_stats, category_stats=category_stats)

    # 11. Generate chart PNGs from JSON
    charts_json = output_dir / "charts.json"
    if charts_json.exists():
        try:
            chart_paths = generate_all_charts(charts_json)
            logger.info("[orchestrator] Generated %d chart images", len(chart_paths))
        except Exception as exc:
            logger.warning("[orchestrator] Chart generation failed: %s", exc, exc_info=True)

    # 12. Generate markdown report
    # Prepare skill pairs for report
    skill_pairs = []
    if co_occ:
        for skill_a, co_skills in co_occ.items():
            for skill_b, count in co_skills.items():
                skill_pairs.append({
                    "skill_a": skill_a,
                    "skill_b": skill_b,
                    "co_occurrence_count": count
                })
        skill_pairs.sort(key=lambda p: p["co_occurrence_count"], reverse=True)
    
    generate_markdown_report(
        market=market,
        week_start=week_start,
        metrics=metrics,
        output_dir=output_dir,
        remote_ratio=remote_ratio,
        sources_used=run.sources_used or ["Remotive", "JSearch"],
        jobs_collected=run.jobs_fetched,
        coverage_data=coverage_data,
        title_stats=title_stats,
        trend_stats=trend_stats,
        category_stats=category_stats,
        skill_pairs=skill_pairs,
    )

    # 13. HTML paste helper (Mode B — optional)
    if generate_html:
        convert_to_html(output_dir / "report.md")

    # 14. Monetization splits
    generate_free_report(metrics, remote_ratio, output_dir, market.get("display_name", market_id), week_str)
    generate_premium_report(metrics, co_occ, remote_ratio, output_dir, market.get("display_name", market_id), week_str)

    # 15. Publish (Mode A — manual instructions)
    publish(output_dir, market_id, week_str)


def run_pipeline_for_market(
    market: dict,
    mode: str,
    week_start: date,
    generate_html: bool = False,
) -> RunContext:
    """
    Run the full (or partial) pipeline for one market.
    All errors are caught and recorded in RunContext — never bubbled up.
    Returns the RunContext with accumulated stats.
    """
    market_id = market["market_id"]
    week_str = f"{week_start.year}-{week_start.isocalendar()[1]:02d}"
    run = RunContext(market_id=market_id, week=week_str)

    logger.info(
        "[orchestrator] ══ START market=%s mode=%s week=%s run_id=%s ══",
        market_id, mode, week_str, run.run_id,
    )

    try:
        if mode in ("weekly", "ingest-only"):
            run_ingestion(market, run)

        if mode in ("weekly", "report-only"):
            run_analytics_and_report(market, week_start, run, generate_html)

    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        run.add_error(msg)
        logger.error("[orchestrator] FATAL for market %s: %s", market_id, msg, exc_info=True)

    finally:
        run.finish()
        logger.info(
            "[orchestrator] ══ DONE market=%s run_id=%s errors=%d ══",
            market_id, run.run_id, run.errors_count,
        )

    return run


def run_backfill(start: date, end: date, generate_html: bool = False) -> None:
    """
    Generate historical weekly reports from stored DB data.
    Loops over all ISO weeks in [start, end].
    Does NOT scrape new data.
    """
    current = _iso_week_start(start)
    today_week = _iso_week_start(date.today())

    while current <= end and current < today_week:
        for market in TARGET_MARKETS:
            market_id = market["market_id"]
            week_str = f"{current.year}-{current.isocalendar()[1]:02d}"
            run = RunContext(market_id=market_id, week=week_str)
            try:
                run_analytics_and_report(market, current, run, generate_html)
            except Exception as exc:  # noqa: BLE001
                run.add_error(str(exc))
            finally:
                run.finish()
        current += timedelta(weeks=1)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging(run_id: str = "", week: str = "") -> None:
    from config.settings import LOGS_DIR
    if run_id:
        # Per-run log at LOGS_DIR/run_<id>.log — easy for admin UI to find
        log_dir = LOGS_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"run_{run_id}.log"
    else:
        log_dir = LOGS_DIR / week if week else LOGS_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "pipeline.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _iso_week_start(d: date) -> date:
    """Return the Monday of the ISO week containing date d."""
    return d - timedelta(days=d.weekday())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Job Market Intelligence Orchestrator")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--mode",
        choices=["weekly", "ingest-only", "report-only", "crawl"],
        help="Pipeline mode to run",
    )
    group.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill historical weekly reports from stored data",
    )
    parser.add_argument("--start", help="Backfill start date YYYY-MM-DD")
    parser.add_argument("--end", help="Backfill end date YYYY-MM-DD")
    parser.add_argument(
        "--html", action="store_true", help="Also generate report.html (Mode B paste helper)"
    )
    parser.add_argument(
        "--max-runtime",
        type=int,
        metavar="MINUTES",
        help="Max runtime in minutes for crawl mode (default: unlimited)",
    )
    parser.add_argument(
        "--run-id",
        help="Pipeline monitor run ID (set automatically when launched from admin)",
    )
    return parser.parse_args()


def _should_recompute_diversity(args: argparse.Namespace) -> bool:
    """Diversity rank recompute runs after any mode that actually fetches new job data."""
    if args.backfill:
        return False
    return args.mode != "report-only"


def main() -> None:
    args = _parse_args()

    # Ensure DB + tables exist
    run_migrations()

    week_start = _iso_week_start(date.today())
    week_str = f"{week_start.year}-{week_start.isocalendar()[1]:02d}"

    # Determine run_id before logging so the log file is named per-run
    from src.pipeline_monitor import finish_run, start_run
    mode = args.mode if not args.backfill else "backfill"
    run_id = args.run_id if (hasattr(args, "run_id") and args.run_id) else start_run(mode)

    _setup_logging(run_id=run_id, week=week_str)

    try:
        stats = _run(args, week_start)
        if _should_recompute_diversity(args):
            recompute_diversity_ranks()
        finish_run(run_id, status="success", **stats)
    except Exception as exc:
        finish_run(run_id, status="failed", error=str(exc))
        raise


def _run(args, week_start) -> dict:
    if args.backfill:
        if not args.start or not args.end:
            print("--backfill requires --start YYYY-MM-DD and --end YYYY-MM-DD")
            sys.exit(1)
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
        logger.info("[orchestrator] BACKFILL mode: %s → %s", start_date, end_date)
        run_backfill(start_date, end_date, generate_html=args.html)
        return {}
    elif args.mode == "crawl":
        logger.info("[orchestrator] Starting CRAWL mode (Findwork full-catalogue)")
        from src.collectors.findwork_crawler import FindworkCrawler

        crawler = FindworkCrawler()
        market = TARGET_MARKETS[0]
        max_runtime_seconds = args.max_runtime * 60 if args.max_runtime else None

        try:
            crawler.crawl_forever(market, max_runtime_seconds=max_runtime_seconds)
        except KeyboardInterrupt:
            logger.info("[orchestrator] Crawler interrupted by user")
        except Exception as exc:
            logger.error("[orchestrator] Crawler crashed: %s", exc, exc_info=True)
            sys.exit(1)
        return {}
    else:
        totals = {"jobs_fetched": 0, "jobs_inserted": 0, "jobs_deduped": 0, "skills_extracted": 0}
        for market in TARGET_MARKETS:
            ctx = run_pipeline_for_market(
                market=market,
                mode=args.mode,
                week_start=week_start,
                generate_html=args.html,
            )
            totals["jobs_fetched"]    += ctx.jobs_fetched
            totals["jobs_inserted"]   += ctx.jobs_inserted
            totals["jobs_deduped"]    += ctx.jobs_deduped
            totals["skills_extracted"] += ctx.skills_extracted

        # After all markets processed, update Tracker Directory
        if args.mode in ("weekly", "report-only"):
            logger.info("[orchestrator] Exporting to Tracker Directory spreadsheet")
            try:
                from config.settings import (
                    TRACKER_SPREADSHEET_ID,
                    GOOGLE_SA_JSON_PATH,
                    TRACKER_DEPLOYMENT_BASE_URL,
                    TRACKER_TOKEN,
                    DB_PATH
                )
                result = export_directory(
                    tracker_spreadsheet_id=TRACKER_SPREADSHEET_ID,
                    google_sa_json_path=GOOGLE_SA_JSON_PATH,
                    tracker_deployment_url=TRACKER_DEPLOYMENT_BASE_URL,
                    tracker_token=TRACKER_TOKEN,
                    db_path=DB_PATH
                )
                if "error" in result:
                    logger.warning("[orchestrator] Tracker export skipped: %s", result["error"])
                else:
                    logger.info(
                        "[orchestrator] Tracker export complete: %d jobs exported",
                        result.get("total_jobs", 0)
                    )
                    if "countries" in result:
                        for country, count in result["countries"].items():
                            logger.info("[orchestrator]   %s: %d jobs", country, count)
            except Exception as exc:
                logger.warning(
                    "[orchestrator] Tracker export failed: %s", exc, exc_info=True
                )

        return totals


if __name__ == "__main__":
    main()
