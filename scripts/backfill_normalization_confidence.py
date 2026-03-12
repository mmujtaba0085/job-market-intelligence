"""
scripts/backfill_normalization_confidence.py
────────────────────────────────────────────
Backfill normalization_confidence scores for existing jobs.

This updates the confidence column based on current normalization rules.

Usage:
    python scripts/backfill_normalization_confidence.py
    python scripts/backfill_normalization_confidence.py --dry-run
"""

import sys
import sqlite3
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.title_normalizer import normalize_title, get_confidence_label
from src.storage.db import get_connection


def backfill_confidence_scores(batch_size: int = 1000, dry_run: bool = False):
    """
    Backfill normalization_confidence for all jobs.
    
    Args:
        batch_size: Number of jobs to process in each batch
        dry_run: If True, show what would be updated without committing
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get total count of jobs needing confidence scores
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM jobs 
        WHERE normalization_confidence IS NULL 
           OR normalization_confidence = 0.0
    """)
    total = cursor.fetchone()["count"]
    
    print(f"{'[DRY RUN] ' if dry_run else ''}Backfilling normalization_confidence for {total:,} jobs...")
    print(f"Batch size: {batch_size}")
    print()
    
    processed = 0
    updated = 0
    confidence_stats = {
        "high": 0,   # >= 0.9
        "medium": 0, # >= 0.6
        "low": 0,    # < 0.6
    }
    
    # Sample low-confidence results for review
    low_conf_samples = []
    max_samples = 20
    
    while processed < total:
        # Fetch batch
        cursor.execute("""
            SELECT job_id, title, normalized_title
            FROM jobs
            WHERE normalization_confidence IS NULL 
               OR normalization_confidence = 0.0
            LIMIT ?
        """, (batch_size,))
        
        batch = cursor.fetchall()
        if not batch:
            break
        
        # Re-normalize each title to get confidence
        for row in batch:
            job_id = row["job_id"]
            title = row["title"]
            current_normalized = row["normalized_title"]
            
            # Get current confidence score
            normalized, confidence = normalize_title(title)
            
            # Track confidence stats
            label = get_confidence_label(confidence)
            confidence_stats[label] += 1
            
            # Collect low-confidence samples
            if confidence < 0.6 and len(low_conf_samples) < max_samples and title != normalized:
                low_conf_samples.append({
                    "job_id": job_id,
                    "title": title,
                    "normalized": normalized,
                    "current_normalized": current_normalized,
                    "confidence": confidence,
                    "label": label,
                })
            
            # Update database (unless dry run)
            if not dry_run:
                cursor.execute(
                    "UPDATE jobs SET normalization_confidence = ? WHERE job_id = ?",
                    (confidence, job_id)
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
    
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Backfill complete: {updated:,} confidence scores updated")
    
    # Show confidence breakdown
    print(f"\nConfidence Analysis:")
    print(f"  High (>=90%):   {confidence_stats['high']:,} jobs ({confidence_stats['high']/total*100:.1f}%)")
    print(f"  Medium (>=60%): {confidence_stats['medium']:,} jobs ({confidence_stats['medium']/total*100:.1f}%)")
    print(f"  Low (<60%):     {confidence_stats['low']:,} jobs ({confidence_stats['low']/total*100:.1f}%)")
    
    # Show low-confidence samples
    if low_conf_samples:
        print(f"\nLow-Confidence Samples (first {len(low_conf_samples)}):")
        for sample in low_conf_samples:
            conf_pct = int(sample['confidence'] * 100)
            label_symbol = {
                "high": "[HIGH]",
                "medium": "[MED] ",
                "low": "[LOW] ",
            }[sample['label']]
            
            print(f"  {label_symbol} [{conf_pct}%] '{sample['title']}' -> '{sample['normalized']}'")
            if sample['current_normalized'] != sample['normalized']:
                print(f"           (currently: '{sample['current_normalized']}')")
    
    # Query low-confidence jobs for review
    cursor.execute("""
        SELECT 
            title,
            normalized_title,
            normalization_confidence,
            COUNT(*) as count
        FROM jobs
        WHERE normalization_confidence < 0.6
          AND title != normalized_title
        GROUP BY title, normalized_title
        ORDER BY count DESC
        LIMIT 10
    """)
    
    low_conf_jobs = cursor.fetchall()
    if low_conf_jobs:
        print(f"\nTop Low-Confidence Normalizations (needs review):")
        for row in low_conf_jobs:
            conf_pct = int(row["normalization_confidence"] * 100)
            print(f"  [{conf_pct}%] '{row['title']}' -> '{row['normalized_title']}' ({row['count']} jobs)")
    
    conn.close()
    
    if dry_run:
        print("\n[DRY RUN] This was a dry run. No changes were committed.")
        print("   Run without --dry-run to apply changes.")


def validate_confidence_scores():
    """
    Validate that confidence scores are properly set.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("Validating normalization_confidence column...\n")
    
    # Check 1: NULL or 0.0 values
    cursor.execute("""
        SELECT COUNT(*) as count 
        FROM jobs 
        WHERE normalization_confidence IS NULL 
           OR normalization_confidence = 0.0
    """)
    unset_count = cursor.fetchone()["count"]
    
    if unset_count == 0:
        print("[OK] All jobs have confidence scores")
    else:
        print(f"[WARN] Found {unset_count:,} jobs without confidence scores")
    
    # Check 2: Distribution
    cursor.execute("""
        SELECT 
            CASE 
                WHEN normalization_confidence >= 0.9 THEN 'High (>=90%)'
                WHEN normalization_confidence >= 0.6 THEN 'Medium (>=60%)'
                WHEN normalization_confidence > 0.0 THEN 'Low (<60%)'
                ELSE 'Unset (0.0)'
            END as confidence_level,
            COUNT(*) as count,
            ROUND(AVG(normalization_confidence) * 100, 1) as avg_pct
        FROM jobs
        GROUP BY confidence_level
        ORDER BY avg_pct DESC
    """)
    
    print("\nConfidence Distribution:")
    for row in cursor.fetchall():
        print(f"  {row['confidence_level']:20} {row['count']:6,} jobs (avg: {row['avg_pct']}%)")
    
    # Check 3: Low-confidence jobs needing review
    cursor.execute("""
        SELECT COUNT(DISTINCT title) as unique_titles
        FROM jobs
        WHERE normalization_confidence < 0.6
          AND title != normalized_title
    """)
    
    review_needed = cursor.fetchone()["unique_titles"]
    print(f"\nJobs needing review: {review_needed:,} unique low-confidence normalizations")
    
    if review_needed > 0:
        print("  Visit /admin/normalize-titles to review and fix these")
    
    conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Backfill normalization confidence scores")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without committing changes"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate confidence scores are properly set"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of jobs to process per batch (default: 1000)"
    )
    
    args = parser.parse_args()
    
    if args.validate:
        validate_confidence_scores()
    else:
        backfill_confidence_scores(
            batch_size=args.batch_size,
            dry_run=args.dry_run
        )
