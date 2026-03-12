"""
scripts/check_unknown_titles.py
────────────────────────────────
Check what titles are normalized to Unknown or have low confidence
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.db import get_connection

def main():
    conn = get_connection()
    cursor = conn.cursor()
    
    print("=" * 80)
    print("Unknown Titles")
    print("=" * 80)
    
    cursor.execute('SELECT title FROM jobs WHERE normalized_title = "Unknown" LIMIT 20')
    rows = cursor.fetchall()
    
    for row in rows:
        print(f"  - {row[0]}")
    
    print("\n" + "=" * 80)
    print("Sample Low Confidence Titles (random 30)")
    print("=" * 80)
    
    cursor.execute('''
        SELECT DISTINCT normalized_title 
        FROM jobs 
        WHERE normalization_confidence < 0.6 
        AND normalized_title != "Unknown" 
        ORDER BY RANDOM() 
        LIMIT 30
    ''')
    rows = cursor.fetchall()
    
    for row in rows:
        print(f"  - {row[0]}")
    
    conn.close()

if __name__ == "__main__":
    main()
