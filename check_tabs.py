from src.storage.db import get_connection

c = get_connection()
r = c.execute("""
    SELECT assigned_sheet, COUNT(DISTINCT assigned_tab) as tab_count
    FROM sheets_staging 
    WHERE status='pending' AND exclude_from_upload=0 
    GROUP BY assigned_sheet
""").fetchall()

total_tabs = sum(row[1] for row in r)

print(f'Total unique tabs to process: {total_tabs}')
print('\nBy country:')
for row in r:
    print(f'  {row[0]}: {row[1]} tabs')

print(f'\n455 jobs / {total_tabs} tabs = ~{455/total_tabs:.1f} jobs per tab')
print(f'\nEstimated API calls: {total_tabs * 3} (create/clear/write) + 3 Overview tabs = ~{total_tabs * 3 + 3} total')
print(f'Estimated time (0.5s per call): {(total_tabs * 3 + 3) * 0.5 / 60:.1f} minutes')

c.close()
