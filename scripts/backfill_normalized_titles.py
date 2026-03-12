"""
scripts/backfill_normalized_titles.py
────────────────────────────────────
Backfill normalized_title for all existing jobs in the database.

Run once after adding the normalized_title column.

Usage:
    python scripts/backfill_normalized_titles.py
"""

import sys
import sqlite3
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.title_normalizer import normalize_title, get_confidence_label
from src.storage.db import get_connection


def backfill_normalized_titles(batch_size: int = 1000, dry_run: bool = False):
    """
    Backfill normalized_title for all existing jobs.
    
    Args:
        batch_size: Number of jobs to process in each batch
        dry_run: If True, show what would be updated without committing
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get total count of jobs needing normalization
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM jobs 
        WHERE normalized_title IS NULL OR normalized_title = '' OR normalized_title = title
    """)
    total = cursor.fetchone()["count"]
    
    print(f"{'[DRY RUN] ' if dry_run else ''}Backfilling normalized_title for {total:,} jobs...")
    print(f"Batch size: {batch_size}")
    print()
    
    processed = 0
    updated = 0
    confidence_stats = {
        "high": 0,
        "medium": 0,
        "low": 0,
    }
    
    # Sample results for preview
    samples = []
    max_samples = 20
    
    while processed < total:
        # Fetch batch
        cursor.execute("""
            SELECT job_id, title, normalized_title
            FROM jobs
            WHERE normalized_title IS NULL OR normalized_title = '' OR normalized_title = title
            LIMIT ?
        """, (batch_size,))
        
        batch = cursor.fetchall()
        if not batch:
            break
        
        # Normalize each title
        for row in batch:
            job_id = row["job_id"]
            title = row["title"]
            current_normalized = row["normalized_title"]
            
            # Normalize
            normalized, confidence = normalize_title(title)
            
            # Track confidence stats
            label = get_confidence_label(confidence)
            confidence_stats[label] += 1
            
            # Collect samples
            if len(samples) < max_samples and title != normalized:
                samples.append({
                    "job_id": job_id,
                    "title": title,
                    "normalized": normalized,
                    "confidence": confidence,
                    "label": label,
                })
            
            # Update database (unless dry run)
            if not dry_run:
                cursor.execute(
                    "UPDATE jobs SET normalized_title = ? WHERE job_id = ?",
                    (normalized, job_id)
                )
            
            updated += 1
            processed += 1
            
            # Progress indicator
            if processed % 1000 == 0:
                pct = (processed / total * 100)
                print(f"  Processed {processed:,}/{total:,} ({pct:.1f}%)")
        
        # Commit batch
        if not dry_run:
            conn.commit()
    
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Backfill complete: {updated:,} titles processed")
    
    # Show confidence breakdown
    print(f"\nConfidence Analysis:")
    print(f"  High (>=90%):   {confidence_stats['high']:,} jobs ({confidence_stats['high']/total*100:.1f}%)")
    print(f"  Medium (>=60%): {confidence_stats['medium']:,} jobs ({confidence_stats['medium']/total*100:.1f}%)")
    print(f"  Low (<60%):    {confidence_stats['low']:,} jobs ({confidence_stats['low']/total*100:.1f}%)")
    
    # Show sample normalizations
    if samples:
        print(f"\nSample Normalizations (first {len(samples)}):")
        for sample in samples:
            conf_pct = int(sample['confidence'] * 100)
            label_symbol = {
                "high": "[HIGH]",
                "medium": "[MED] ",
                "low": "[LOW] ",
            }[sample['label']]
            
            print(f"  {label_symbol} [{conf_pct}%] '{sample['title']}' -> '{sample['normalized']}'")
    
    # Show consolidation impact
    cursor.execute("""
        SELECT 
            COUNT(DISTINCT title) as unique_raw,
            COUNT(DISTINCT normalized_title) as unique_normalized
        FROM jobs
    """)
    
    stats = cursor.fetchone()
    reduction = (1 - stats["unique_normalized"] / stats["unique_raw"]) * 100
    
    print(f"\nConsolidation Impact:")
    print(f"  Unique raw titles:        {stats['unique_raw']:,}")
    print(f"  Unique normalized titles: {stats['unique_normalized']:,}")
    print(f"  Reduction:                {reduction:.1f}%")
    
    # Show top consolidations
    cursor.execute("""
        SELECT 
            normalized_title,
            COUNT(DISTINCT title) as variant_count,
            COUNT(*) as job_count
        FROM jobs
        WHERE normalized_title IS NOT NULL
        GROUP BY normalized_title
        HAVING variant_count > 1
        ORDER BY job_count DESC
        LIMIT 10
    """)
    
    consolidations = cursor.fetchall()
    if consolidations:
        print(f"\nTop Consolidations:")
        for row in consolidations:
            print(f"  '{row['normalized_title']}': {row['variant_count']} variants, {row['job_count']} jobs")
            
            # Fetch sample variants
            cursor.execute("""
                SELECT DISTINCT title
                FROM jobs
                WHERE normalized_title = ?
                ORDER BY title
                LIMIT 5
            """, (row['normalized_title'],))
            
            variants = [v['title'] for v in cursor.fetchall()]
            for i, variant in enumerate(variants[:3]):
                print(f"    ├─ {variant}")
            if len(variants) > 3:
                print(f"    └─ ... and {row['variant_count'] - 3} more")
    
    conn.close()
    
    if dry_run:
        print("\n[DRY RUN] This was a dry run. No changes were committed.")
        print("   Run without --dry-run to apply changes.")


def validate_backfill():
    """
    Validate that backfill was successful.
    Check for NULL values and analyze normalization quality.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("Validating normalized_title column...\n")
    
    # Check 1: NULL values
    cursor.execute("SELECT COUNT(*) as count FROM jobs WHERE normalized_title IS NULL")
    null_count = cursor.fetchone()["count"]
    
    if null_count == 0:
        print("[OK] No NULL values found")
    else:
        print(f"[ERROR] Found {null_count:,} NULL values (should be 0)")
    
    # Check 2: Empty strings
    cursor.execute("SELECT COUNT(*) as count FROM jobs WHERE normalized_title = ''")
    empty_count = cursor.fetchone()["count"]
    
    if empty_count == 0:
        print("[OK] No empty strings found")
    else:
        print(f"[WARN] Found {empty_count:,} empty strings")
    
    # Check 3: Normalization coverage
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN title != normalized_title THEN 1 ELSE 0 END) as changed
        FROM jobs
    """)
    row = cursor.fetchone()
    total = row["total"]
    changed = row["changed"]
    coverage = (changed / total * 100) if total > 0 else 0
    
    print(f"\nNormalization Coverage:")
    print(f"  Total jobs:    {total:,}")
    print(f"  Normalized:    {changed:,} ({coverage:.1f}%)")
    print(f"  Unchanged:     {total - changed:,} ({100 - coverage:.1f}%)")
    
    if coverage >= 30:
        print(f"[OK] Coverage is good (>= 30%)")
    else:
        print(f"[WARN] Coverage is low (<30%), consider adding more normalization rules")
    
    # Check 4: Case-only differences (should be normalized)
    cursor.execute("""
        SELECT title, normalized_title, COUNT(*) as count
        FROM jobs
        WHERE LOWER(title) = LOWER(normalized_title) 
          AND title != normalized_title
        GROUP BY title, normalized_title
        ORDER BY count DESC
        LIMIT 5
    """)
    
    case_issues = cursor.fetchall()
    if case_issues:
        print(f"\n[WARN] Found {len(case_issues)} case-only differences (should be normalized):")
        for row in case_issues:
            print(f"  '{row['title']}' -> '{row['normalized_title']}' ({row['count']} jobs)")
    else:
        print("\n[OK] No case-only differences found")
    
    conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Backfill normalized_title column")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without committing changes"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate backfill was successful"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of jobs to process per batch (default: 1000)"
    )
    
    args = parser.parse_args()
    
    if args.validate:
        validate_backfill()
    else:
        backfill_normalized_titles(
            batch_size=args.batch_size,
            dry_run=args.dry_run
        )
