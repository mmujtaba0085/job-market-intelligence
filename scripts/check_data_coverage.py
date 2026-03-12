"""
scripts/check_data_coverage.py
───────────────────────────────
Data validation script to assess coverage before building analytics.

Checks:
- Salary data coverage percentage
- Title diversity (top 50 titles)
- Country/location distribution
- Remote type breakdown
- Company posting frequencies
- Source contribution

Run before implementing Phase 2/3 analytics to guide prioritization.

Usage:
  python scripts/check_data_coverage.py [market_id]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import DB_PATH


def get_db():
    """Get database connection."""
    return sqlite3.connect(DB_PATH)


def check_salary_coverage(market_id: str = "ai_ml_global"):
    """Check percentage of jobs with salary data."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total_jobs,
            SUM(CASE WHEN salary_min IS NOT NULL THEN 1 ELSE 0 END) as jobs_with_salary,
            ROUND(100.0 * SUM(CASE WHEN salary_min IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2) as coverage_pct
        FROM jobs 
        WHERE market_id = ?
    """, (market_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    return {
        "total_jobs": result[0],
        "jobs_with_salary": result[1],
        "coverage_pct": result[2] or 0.0
    }


def get_title_diversity(market_id: str = "ai_ml_global", limit: int = 50):
    """Get top N titles by frequency."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT title, COUNT(*) as freq 
        FROM jobs 
        WHERE market_id = ?
        GROUP BY title 
        ORDER BY freq DESC 
        LIMIT ?
    """, (market_id, limit))
    
    titles = cursor.fetchall()
    conn.close()
    
    return [{"title": t[0], "frequency": t[1]} for t in titles]


def get_country_distribution(market_id: str = "ai_ml_global"):
    """Get job distribution by country."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT country, COUNT(*) as jobs 
        FROM jobs 
        WHERE market_id = ?
        GROUP BY country 
        ORDER BY jobs DESC
    """, (market_id,))
    
    countries = cursor.fetchall()
    conn.close()
    
    total = sum(c[1] for c in countries)
    return [
        {"country": c[0], "jobs": c[1], "pct": round(100.0 * c[1] / total, 2) if total > 0 else 0}
        for c in countries
    ]


def get_remote_breakdown(market_id: str = "ai_ml_global"):
    """Get remote type distribution."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT remote_type, COUNT(*) as jobs 
        FROM jobs 
        WHERE market_id = ?
        GROUP BY remote_type 
        ORDER BY jobs DESC
    """, (market_id,))
    
    remote_types = cursor.fetchall()
    conn.close()
    
    total = sum(r[1] for r in remote_types)
    return [
        {"remote_type": r[0], "jobs": r[1], "pct": round(100.0 * r[1] / total, 2) if total > 0 else 0}
        for r in remote_types
    ]


def get_company_distribution(market_id: str = "ai_ml_global", limit: int = 20):
    """Get top companies by job postings."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT company, COUNT(*) as jobs 
        FROM jobs 
        WHERE market_id = ?
        GROUP BY company 
        ORDER BY jobs DESC 
        LIMIT ?
    """, (market_id, limit))
    
    companies = cursor.fetchall()
    conn.close()
    
    return [{"company": c[0], "jobs": c[1]} for c in companies]


def get_source_distribution(market_id: str = "ai_ml_global"):
    """Get job distribution by source."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT source_name, COUNT(*) as jobs 
        FROM jobs 
        WHERE market_id = ?
        GROUP BY source_name 
        ORDER BY jobs DESC
    """, (market_id,))
    
    sources = cursor.fetchall()
    conn.close()
    
    total = sum(s[1] for s in sources)
    return [
        {"source": s[0], "jobs": s[1], "pct": round(100.0 * s[1] / total, 2) if total > 0 else 0}
        for s in sources
    ]


def print_report(market_id: str = "ai_ml_global"):
    """Print comprehensive data coverage report."""
    print("=" * 80)
    print(f"DATA COVERAGE REPORT — {market_id}")
    print("=" * 80)
    
    # Salary coverage
    print("\n📊 SALARY DATA COVERAGE")
    print("-" * 80)
    salary_stats = check_salary_coverage(market_id)
    print(f"Total jobs:        {salary_stats['total_jobs']:,}")
    print(f"Jobs with salary:  {salary_stats['jobs_with_salary']:,}")
    print(f"Coverage:          {salary_stats['coverage_pct']}%")
    
    if salary_stats['coverage_pct'] >= 30:
        print("✅ Sufficient for Phase 3 salary analytics")
    else:
        print("⚠️  Low coverage - Phase 3 salary analytics should be deprioritized")
    
    # Remote type breakdown
    print("\n🏠 REMOTE TYPE BREAKDOWN")
    print("-" * 80)
    remote_data = get_remote_breakdown(market_id)
    for item in remote_data:
        print(f"{item['remote_type']:15s} {item['jobs']:>6,} jobs ({item['pct']:>5.1f}%)")
    
    # Country distribution
    print("\n🌍 COUNTRY DISTRIBUTION (Top 10)")
    print("-" * 80)
    country_data = get_country_distribution(market_id)
    for i, item in enumerate(country_data[:10], 1):
        print(f"{i:2d}. {item['country']:30s} {item['jobs']:>6,} jobs ({item['pct']:>5.1f}%)")
    
    # Source distribution
    print("\n🔗 SOURCE DISTRIBUTION")
    print("-" * 80)
    source_data = get_source_distribution(market_id)
    for item in source_data:
        print(f"{item['source']:20s} {item['jobs']:>6,} jobs ({item['pct']:>5.1f}%)")
    
    # Company distribution
    print("\n🏢 TOP COMPANIES (Top 10)")
    print("-" * 80)
    company_data = get_company_distribution(market_id, limit=10)
    for i, item in enumerate(company_data, 1):
        print(f"{i:2d}. {item['company']:40s} {item['jobs']:>5,} jobs")
    
    # Title diversity
    print("\n💼 TITLE DIVERSITY (Top 20)")
    print("-" * 80)
    title_data = get_title_diversity(market_id, limit=20)
    for i, item in enumerate(title_data, 1):
        print(f"{i:2d}. {item['title']:50s} {item['frequency']:>5,}")
    
    unique_titles = len(get_title_diversity(market_id, limit=1000))
    print(f"\nUnique titles in database: {unique_titles:,}")
    
    # Recommendations
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    
    if salary_stats['coverage_pct'] >= 30:
        print("✅ Phase 3: Implement salary analytics (good coverage)")
    else:
        print("⏭️  Phase 3: Skip or deprioritize salary analytics (low coverage)")
    
    if unique_titles > 50:
        print("✅ Phase 2: Title normalization recommended (high diversity)")
    else:
        print("ℹ️  Phase 2: Title normalization optional (low diversity)")
    
    if salary_stats['total_jobs'] > 0:
        print("✅ Phase 1: Coverage metrics ready to implement")
    else:
        print("⚠️  No data in database - run orchestrator first")
    
    print("=" * 80)


if __name__ == "__main__":
    market_id = sys.argv[1] if len(sys.argv) > 1 else "ai_ml_global"
    try:
        print_report(market_id)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
