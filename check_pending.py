from src.storage.db import get_connection

conn = get_connection()
cursor = conn.execute('SELECT COUNT(*) FROM sheets_staging WHERE status = \'pending\' AND exclude_from_upload = 0')
pending = cursor.fetchone()[0]

cursor = conn.execute('SELECT assigned_sheet, COUNT(*) FROM sheets_staging WHERE status = \'pending\' AND exclude_from_upload = 0 GROUP BY assigned_sheet')
by_country = cursor.fetchall()

cursor = conn.execute("""
    SELECT assigned_sheet, COUNT(DISTINCT assigned_tab) as tab_count
    FROM sheets_staging 
    WHERE status='pending' AND exclude_from_upload=0 
    GROUP BY assigned_sheet
""")
tabs_by_country = cursor.fetchall()
total_tabs = sum(row[1] for row in tabs_by_country)

print(f'Pending jobs to upload: {pending}')
print('\nBy country:')
for row in by_country:
    print(f'  {row[0]}: {row[1]} jobs')

print(f'\nTotal unique tabs to process: {total_tabs}')
print('Tabs by country:')
for row in tabs_by_country:
    print(f'  {row[0]}: {row[1]} tabs')

print(f'\nEstimated API calls: {total_tabs * 3} (create/clear/write) + 3 Overview tabs = ~{total_tabs * 3 + 3} total')
print(f'Estimated time (0.5s per call): {(total_tabs * 3 + 3) * 0.5 / 60:.1f} minutes')

conn.close()
