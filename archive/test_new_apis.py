"""Quick test script for new API endpoints."""
import requests

print("Testing new job API endpoints...\n")
print("=" * 80)

# Test Himalayas JSON API
print("\n1. Testing Himalayas JSON API")
print("-" * 80)
try:
    resp = requests.get("https://himalayas.app/jobs/api", params={"limit": 5, "offset": 0}, timeout=10)
    print(f"   Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        jobs_count = len(data.get("jobs", []))
        total = data.get("totalCount", 0)
        print(f"   ✅ SUCCESS - {jobs_count} jobs returned, total available: {total}")
    else:
        print(f"   ❌ FAILED - Response: {resp.text[:200]}")
except Exception as e:
    print(f"   ❌ ERROR - {type(e).__name__}: {e}")

# Test Himalayas RSS
print("\n2. Testing Himalayas RSS Feed")
print("-" * 80)
try:
    resp = requests.get("https://himalayas.app/jobs/feed", timeout=10)
    print(f"   Status: {resp.status_code}")
    if resp.status_code == 200:
        print(f"   ✅ SUCCESS - {len(resp.content)} bytes received")
        print(f"   Content type: {resp.headers.get('content-type')}")
    else:
        print(f"   ❌ FAILED - Response: {resp.text[:200]}")
except Exception as e:
    print(f"   ❌ ERROR - {type(e).__name__}: {e}")

# Test Jobicy API
print("\n3. Testing Jobicy JSON API")
print("-" * 80)
try:
    resp = requests.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 5, "tag": "python"}, timeout=10)
    print(f"   Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        print(f"   ✅ SUCCESS - {len(jobs)} jobs returned")
        if jobs:
            print(f"   Sample job: {jobs[0].get('jobTitle', 'N/A')}")
    else:
        print(f"   ❌ FAILED - Response: {resp.text[:200]}")
except Exception as e:
    print(f"   ❌ ERROR - {type(e).__name__}: {e}")

# Test HireWeb3 RSS
print("\n4. Testing HireWeb3 RSS Feed")
print("-" * 80)
try:
    resp = requests.get("https://hireweb3.io/job/rss", timeout=10)
    print(f"   Status: {resp.status_code}")
    if resp.status_code == 200:
        print(f"   ✅ SUCCESS - {len(resp.content)} bytes received")
        print(f"   Content type: {resp.headers.get('content-type')}")
    else:
        print(f"   ❌ FAILED - Response: {resp.text[:200]}")
except Exception as e:
    print(f"   ❌ ERROR - {type(e).__name__}: {e}")

print("\n" + "=" * 80)
print("Test complete!")
