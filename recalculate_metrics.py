"""
recalculate_metrics.py
──────────────────────
Recalculate weekly_metrics for all weeks in the database.
Uses existing jobs/skills data without re-ingestion.

This script:
1. Finds all unique week_ids in the jobs table
2. For each week, calculates metrics using compute_weekly_metrics
3. Properly calculates growth vs prior weeks
4. Updates weekly_metrics table
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from src.analytics.weekly_metrics import compute_weekly_metrics
from config.markets import TARGET_MARKETS


def get_all_week_ids(db_path: Path) -> list[tuple[str, str]]:
    """
    Get all unique (market_id, week_id) combinations from jobs table.
    Returns sorted by week_id ascending (oldest first).
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    rows = cursor.execute("""
        SELECT DISTINCT market_id, week_id
        FROM jobs
        WHERE week_id IS NOT NULL AND week_id != 'unknown'
        ORDER BY week_id ASC
    """).fetchall()
    
    conn.close()
    return rows


def week_id_to_date(week_id: str) -> datetime:
    """
    Convert week_id (YYYY-WW) to the Monday of that ISO week.
    
    Args:
        week_id: String like "2026-09"
    
    Returns:
        datetime object for the Monday of that week
    """
    year, week = week_id.split("-")
    year = int(year)
    week = int(week)
    
    # Get January 4th of the year (always in week 1)
    jan4 = datetime(year, 1, 4)
    # Find Monday of week 1
    week1_monday = jan4 - timedelta(days=jan4.weekday())
    # Add weeks
    target_monday = week1_monday + timedelta(weeks=week - 1)
    
    return target_monday


def main():
    db_path = Path("data/jobs.sqlite")
    
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return
    
    print("=" * 70)
    print("WEEKLY METRICS RECALCULATION")
    print("=" * 70)
    
    # Get all week_ids
    week_data = get_all_week_ids(db_path)
    
    if not week_data:
        print("\n❌ No week_id data found in jobs table!")
        print("   Run fix_week_ids.py first to populate week_id column.")
        return
    
    print(f"\n📊 Found {len(week_data)} market-week combinations")
    
    # Group by market
    market_weeks = {}
    for market_id, week_id in week_data:
        if market_id not in market_weeks:
            market_weeks[market_id] = []
        market_weeks[market_id].append(week_id)
    
    print(f"\n📈 Markets: {list(market_weeks.keys())}")
    
    # Process each market
    total_processed = 0
    total_metrics = 0
    
    for market_id, week_ids in market_weeks.items():
        print(f"\n{'=' * 70}")
        print(f"Processing: {market_id}")
        print(f"{'=' * 70}")
        print(f"Weeks to process: {len(week_ids)}")
        print(f"Range: {week_ids[0]} → {week_ids[-1]}")
        
        # Process each week in chronological order
        for i, week_id in enumerate(week_ids, 1):
            try:
                # Convert week_id to Monday date
                week_start = week_id_to_date(week_id).date()
                
                print(f"\n[{i}/{len(week_ids)}] Week {week_id} ({week_start})...", end=" ")
                
                # Compute metrics for this week
                metrics = compute_weekly_metrics(market_id, week_start)
                
                total_processed += 1
                total_metrics += len(metrics)
                
                print(f"✅ {len(metrics)} skill metrics")
                
            except Exception as exc:
                print(f"❌ Error: {exc}")
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"✅ Processed {total_processed} weeks")
    print(f"✅ Generated {total_metrics:,} skill metrics")
    print(f"📊 Average {total_metrics // total_processed if total_processed > 0 else 0} skills per week")
    print("=" * 70)
    
    # Show sample of latest week
    print("\n" + "=" * 70)
    print("SAMPLE: Latest Week Metrics")
    print("=" * 70)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    latest_week = cursor.execute("""
        SELECT DISTINCT week_start_date 
        FROM weekly_metrics 
        ORDER BY week_start_date DESC 
        LIMIT 1
    """).fetchone()
    
    if latest_week:
        latest_week_date = latest_week[0]
        
        rows = cursor.execute("""
            SELECT skill_name, frequency, growth_percentage, absolute_delta, emerging_flag
            FROM weekly_metrics
            WHERE week_start_date = ?
            ORDER BY frequency DESC
            LIMIT 10
        """, (latest_week_date,)).fetchall()
        
        print(f"\nWeek: {latest_week_date}")
        print(f"\n{'Skill':<25} {'Freq':>6} {'Growth':>10} {'Delta':>8} {'Emerging':>10}")
        print("-" * 70)
        
        for row in rows:
            emerging = "🔥 YES" if row['emerging_flag'] else "—"
            print(f"{row['skill_name']:<25} {row['frequency']:>6} {row['growth_percentage']:>9.1f}% "
                  f"{row['absolute_delta']:>8} {emerging:>10}")
    
    conn.close()
    
    print("\n" + "=" * 70)
    print("✅ Done! You can now run:")
    print("   - python -m src.orchestrator --mode weekly (to generate reports)")
    print("   - python web_viewer.py (to view dashboard)")
    print("=" * 70)


if __name__ == "__main__":
    main()
