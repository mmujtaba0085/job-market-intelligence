"""
generate_reports_only.py
────────────────────────
Generate markdown reports from existing weekly_metrics data.
Does NOT re-ingest jobs - uses current database state.

Usage:
    python generate_reports_only.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.markets import TARGET_MARKETS
from src.storage.db import get_connection, get_weekly_metrics, get_remote_ratio
from src.storage.models import WeeklyMetric
from src.reports.markdown_report import generate_markdown_report
from src.reports.charts_export import export_charts
from src.reports.chart_generator import generate_all_charts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)


def get_latest_week_for_market(market_id: str) -> tuple[str, list[dict]]:
    """Find the latest week with metrics data."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT DISTINCT week_start_date
            FROM weekly_metrics
            WHERE market_id = ?
            ORDER BY week_start_date DESC
            LIMIT 1
        """, (market_id,)).fetchone()
        
        if not row:
            return None, []
        
        week_start_date = row["week_start_date"]
        
        # Get metrics for this week
        metrics_rows = conn.execute("""
            SELECT *
            FROM weekly_metrics
            WHERE market_id = ? AND week_start_date = ?
            ORDER BY frequency DESC
        """, (market_id, week_start_date)).fetchall()
        
        return week_start_date, [dict(row) for row in metrics_rows]
    finally:
        conn.close()


def get_coverage_data(market_id: str, week_start: str) -> dict:
    """Get source coverage stats for the week."""
    # Convert week_start to week_id
    week_dt = datetime.fromisoformat(week_start)
    iso_year, iso_week, _ = week_dt.isocalendar()
    week_id = f"{iso_year}-{iso_week:02d}"
    
    conn = get_connection()
    try:
        # Get jobs by source
        sources = conn.execute("""
            SELECT source_name, COUNT(*) as count
            FROM jobs
            WHERE market_id = ? AND week_id = ?
            GROUP BY source_name
            ORDER BY count DESC
        """, (market_id, week_id)).fetchall()
        
        # Get jobs by location/country
        locations = conn.execute("""
            SELECT country, COUNT(*) as count
            FROM jobs
            WHERE market_id = ? AND week_id = ?
            GROUP BY country
            ORDER BY count DESC
            LIMIT 10
        """, (market_id, week_id)).fetchall()
        
        # Get top companies
        companies = conn.execute("""
            SELECT company, COUNT(*) as count
            FROM jobs
            WHERE market_id = ? AND week_id = ? AND company != ''
            GROUP BY company
            ORDER BY count DESC
            LIMIT 10
        """, (market_id, week_id)).fetchall()
        
        # Get remote type distribution
        remote_types = conn.execute("""
            SELECT remote_type, COUNT(*) as count
            FROM jobs
            WHERE market_id = ? AND week_id = ?
            GROUP BY remote_type
            ORDER BY count DESC
        """, (market_id, week_id)).fetchall()
        
        total_jobs = sum(row["count"] for row in sources)
        
        return {
            "source_breakdown": [{"source_name": row["source_name"], "job_count": row["count"], "pct": round(100.0 * row["count"] / total_jobs, 2) if total_jobs > 0 else 0} for row in sources],
            "top_locations": [{"name": row["country"], "count": row["count"]} for row in locations],
            "top_companies": [{"name": row["company"], "count": row["count"]} for row in companies],
            "remote_breakdown": {row["remote_type"].title(): row["count"] for row in remote_types},
            "total_jobs": total_jobs,
        }
    finally:
        conn.close()


def main():
    print("=" * 70)
    print("GENERATE REPORTS FROM EXISTING METRICS")
    print("=" * 70)
    
    for market in TARGET_MARKETS:
        market_id = market["market_id"]
        
        print(f"\n📊 Processing market: {market_id}")
        
        # Get latest week with metrics
        week_start_date, metrics_data = get_latest_week_for_market(market_id)
        
        if not week_start_date:
            print(f"   ⚠️  No metrics found for {market_id}")
            continue
        
        print(f"   Latest week: {week_start_date}")
        print(f"   Metrics: {len(metrics_data)} skills")
        
        # Convert to WeeklyMetric objects
        metrics = []
        for m in metrics_data:
            metric = WeeklyMetric(
                market_id=m["market_id"],
                week_start_date=datetime.fromisoformat(m["week_start_date"]).date(),
                week_number=m["week_number"],
                skill_name=m["skill_name"],
                category=m["category"],
                frequency=m["frequency"],
                growth_percentage=m["growth_percentage"],
                absolute_delta=m.get("absolute_delta", 0),
                mover_score=m.get("mover_score", 0.0),
                emerging_flag=bool(m["emerging_flag"]),
                declining_flag=bool(m["declining_flag"]),
            )
            metrics.append(metric)
        
        # Get coverage data
        coverage_data = get_coverage_data(market_id, week_start_date)
        
        # Get remote ratio
        week_dt = datetime.fromisoformat(week_start_date).date()
        week_end = week_dt
        remote_ratio = get_remote_ratio(market_id, week_start_date, week_end.isoformat())
        
        # Determine output directory
        iso_year, iso_week, _ = week_dt.isocalendar()
        week_str = f"{iso_year}-{iso_week:02d}"
        output_dir = Path(f"outputs/{market_id}/{week_str}")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate report
        print(f"   Generating report...")
        try:
            # Create a simple namespace object for coverage
            from types import SimpleNamespace
            coverage = SimpleNamespace(**coverage_data)
            coverage.sources_breakdown = [SimpleNamespace(**s) for s in coverage_data["source_breakdown"]]
            coverage.countries_breakdown = [SimpleNamespace(**l) for l in coverage_data["top_locations"]]
            coverage.companies_breakdown = [SimpleNamespace(**c) for c in coverage_data["top_companies"]]
            
            report_path = generate_markdown_report(
                market=market,
                week_start=week_dt,
                metrics=metrics,
                output_dir=output_dir,
                remote_ratio=remote_ratio,
                sources_used=[],  # Will be populated from coverage_data
                jobs_collected=coverage_data["total_jobs"],
                coverage_data=coverage,
                title_stats=None,
                trend_stats=None,
                category_stats=None,
                skill_pairs=[],
            )
            print(f"   ✅ Report generated: {report_path}")
            
            # Generate charts.json
            print(f"   Generating charts...")
            export_charts(
                metrics=metrics,
                output_dir=output_dir,
                coverage_data=coverage_data,
                co_occurrence={},  # No co-occurrence data for now
                trend_stats=None,
                category_stats=None,
            )
            
            # Generate PNG images from charts.json
            charts_json = output_dir / "charts.json"
            if charts_json.exists():
                chart_paths = generate_all_charts(charts_json)
                print(f"   ✅ Generated {len(chart_paths)} chart images")
            else:
                print(f"   ⚠️  charts.json not found, skipping image generation")
                
        except Exception as exc:
            print(f"   ❌ Error generating report: {exc}")
            logger.exception("Report generation failed")
    
    print("\n" + "=" * 70)
    print("✅ Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()
