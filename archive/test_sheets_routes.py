"""
Quick test to verify Google Sheets routes are working
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import web_viewer

if __name__ == "__main__":
    print("Testing Google Sheets Integration Routes...")
    print("=" * 60)
    
    client = web_viewer.app.test_client()
    
    # Test staging route
    resp1 = client.get('/admin/sheets_staging')
    status1 = "✓ OK" if resp1.status_code == 200 else f"✗ ERROR ({resp1.status_code})"
    print(f"1. /admin/sheets_staging           {status1}")
    
    # Test analytics route
    resp2 = client.get('/admin/sheets_analytics')
    status2 = "✓ OK" if resp2.status_code == 200 else f"✗ ERROR ({resp2.status_code})"
    print(f"2. /admin/sheets_analytics         {status2}")
    
    # Test API endpoints
    resp3 = client.get('/sheets/track?country=Canada&tab=Test&sheet_id=123&gid=0')
    status3 = "✓ OK" if resp3.status_code == 302 else f"✗ ERROR ({resp3.status_code})"
    print(f"3. /sheets/track (redirect)        {status3}")
    
    print("=" * 60)
    
    if all([resp1.status_code == 200, resp2.status_code == 200, resp3.status_code == 302]):
        print("✓ All routes working!")
        print("\nStart the server and visit:")
        print("  • http://localhost:5000/admin/sheets_staging")
        print("  • http://localhost:5000/admin/sheets_analytics")
    else:
        print("✗ Some routes have errors")
