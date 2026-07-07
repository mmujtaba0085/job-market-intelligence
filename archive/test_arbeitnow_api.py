"""Test Arbeitnow API to see timestamp format."""
import requests
import json

url = "https://www.arbeitnow.com/api/job-board-api"
params = {"page": "1"}

print("=" * 80)
print("Testing Arbeitnow API timestamp format")
print("=" * 80)

try:
    resp = requests.get(url, params=params, timeout=30)
    
    if resp.status_code == 200:
        data = resp.json()
        jobs = data.get("data", [])
        
        if jobs:
            print(f"\nFound {len(jobs)} jobs on page 1\n")
            
            # Check first 3 jobs
            for idx, job in enumerate(jobs[:3], 1):
                print(f"Job {idx}:")
                print(f"  Title: {job.get('title', 'N/A')}")
                print(f"  Company: {job.get('company_name', 'N/A')}")
                created_at = job.get('created_at')
                print(f"  created_at: {created_at} (type: {type(created_at).__name__})")
                
                # Try to identify format
                if isinstance(created_at, int):
                    import datetime
                    # Try as Unix timestamp
                    dt = datetime.datetime.fromtimestamp(created_at, tz=datetime.timezone.utc)
                    print(f"  → Converted: {dt.isoformat()} → {dt.strftime('%Y-%m-%d')}")
                elif isinstance(created_at, str):
                    print(f"  → String format detected")
                print()
        else:
            print("No jobs found in response")
    else:
        print(f"HTTP {resp.status_code}: {resp.text[:200]}")

except Exception as e:
    print(f"Error: {e}")
