"""Test all market keywords for JSearch"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from config.settings import JSEARCH_API_KEY

if not JSEARCH_API_KEY:
    raise SystemExit("JSEARCH_API_KEY not set. Copy .env.example to .env and add your RapidAPI key.")

API_KEY = JSEARCH_API_KEY

headers = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
}

# Your actual market keywords
keywords = [
    "machine learning",
    "deep learning",
    "computer vision",
    "natural language processing",
    "nlp",
    "large language model",
    "llm",
    "mlops",
    "data scientist"
]

print("Testing JSearch API with all market keywords...\n")
print("=" * 70)

results = []

for keyword in keywords:
    params = {
        "query": keyword + " United States",
        "num_pages": "1",
        "date_posted": "week"  # Last 7 days
    }
    
    try:
        print(f"Testing: '{keyword}'...", end=" ")
        
        resp = requests.get(
            "https://jsearch.p.rapidapi.com/search",
            headers=headers,
            params=params,
            timeout=30
        )
        
        if resp.status_code == 200:
            jobs_count = len(resp.json().get("data", []))
            print(f"✓ {jobs_count} jobs")
            results.append((keyword, jobs_count, "SUCCESS"))
        elif resp.status_code == 403:
            print(f"✗ 403 FORBIDDEN (API key issue)")
            results.append((keyword, 0, "403 FORBIDDEN"))
        elif resp.status_code == 429:
            print(f"✗ 429 RATE LIMITED")
            results.append((keyword, 0, "RATE LIMITED"))
        else:
            print(f"✗ {resp.status_code}")
            results.append((keyword, 0, f"HTTP {resp.status_code}"))
            
    except requests.exceptions.Timeout:
        print(f"✗ TIMEOUT (>30s)")
        results.append((keyword, 0, "TIMEOUT"))
    except Exception as e:
        print(f"✗ ERROR: {str(e)[:50]}")
        results.append((keyword, 0, "ERROR"))
    
    # Rate limiting - wait 2 seconds between requests
    time.sleep(2)

print("\n" + "=" * 70)
print("\nSUMMARY:")
print("-" * 70)

total_jobs = 0
successful = 0

for keyword, count, status in results:
    if status == "SUCCESS":
        print(f"✓ {keyword:30} → {count:3} jobs")
        total_jobs += count
        successful += 1
    else:
        print(f"✗ {keyword:30} → {status}")

print("-" * 70)
print(f"\nTotal: {successful}/{len(keywords)} keywords successful")
print(f"Total jobs found: {total_jobs}")
print("\nRecommendation: Increase timeout in jsearch_collector.py from 20 to 30 seconds")
