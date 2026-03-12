"""Quick test script for JSearch API"""
import requests

API_KEY = "***REMOVED-LEAKED-KEY***"

headers = {
    "X-RapidAPI-Key": API_KEY,
    "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
}

params = {
    "query": "python developer",
    "num_pages": "1"
}

print("Testing JSearch API...")
print(f"Endpoint: https://jsearch.p.rapidapi.com/search")
print(f"Query: {params['query']}")
print(f"Timeout: 30 seconds\n")

try:
    resp = requests.get(
        "https://jsearch.p.rapidapi.com/search",
        headers=headers,
        params=params,
        timeout=30
    )
    
    print(f"✓ Status Code: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        jobs = data.get("data", [])
        print(f"✓ Jobs Found: {len(jobs)}")
        
        if jobs:
            print("\nFirst 3 jobs:")
            for i, job in enumerate(jobs[:3], 1):
                print(f"  {i}. {job.get('job_title', 'N/A')} at {job.get('employer_name', 'N/A')}")
                print(f"     Location: {job.get('job_city', 'N/A')}, {job.get('job_country', 'N/A')}")
        else:
            print("⚠ No jobs returned in response")
    else:
        print(f"✗ Error: {resp.status_code}")
        print(f"Response: {resp.text[:500]}")
        
except requests.exceptions.Timeout:
    print("✗ Request timed out (>10 seconds)")
except requests.exceptions.RequestException as e:
    print(f"✗ Request failed: {e}")
