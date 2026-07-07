#!/usr/bin/env python3
"""Test the export endpoint."""

import requests
import sys

try:
    print("Testing http://localhost:5000/export/jobs ...")
    response = requests.get("http://localhost:5000/export/jobs", timeout=10)
    
    print(f"Status Code: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")
    print(f"Content-Disposition: {response.headers.get('Content-Disposition')}")
    print(f"Response length: {len(response.content)} bytes")
    
    if response.status_code == 200:
        print("\n✅ Export endpoint is working!")
        # Show first 200 characters of response
        print(f"\nFirst 200 chars of CSV:\n{response.text[:200]}...")
    else:
        print(f"\n❌ Export failed with status {response.status_code}")
        print(f"Response: {response.text[:500]}")
        
except requests.exceptions.ConnectionError:
    print("❌ Cannot connect to http://localhost:5000")
    print("Make sure the web server is running with: python web_viewer.py")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
