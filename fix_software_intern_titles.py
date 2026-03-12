import sqlite3
from src.title_normalizer import normalize_title

conn = sqlite3.connect('data/jobs.sqlite')
cursor = conn.cursor()

# Specific titles that should consolidate to "Software Engineer Intern"
titles_to_fix = [
    'Software Engineering Intern',
    'SWE Intern',
    'Software Development Intern',
    'Software Developer Intern',
    'Software Intern',
]

print('Fixing specific title normalizations...\n')

total_updated = 0
for title in titles_to_fix:
    normalized, confidence = normalize_title(title)
    
    cursor.execute('UPDATE jobs SET normalized_title = ? WHERE title = ?', (normalized, title))
    updated = cursor.rowcount
    total_updated += updated
    
    print(f'  "{title}" -> "{normalized}" ({updated} jobs updated)')

conn.commit()
conn.close()

print(f'\nTotal: {total_updated} jobs updated')
print('\nRunning verification...')

# Verify
conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("""
    SELECT normalized_title, COUNT(*) as count, COUNT(DISTINCT title) as variants
    FROM jobs
    WHERE normalized_title = 'Software Engineer Intern'
""")

row = cursor.fetchone()
if row:
    print(f'\n"Software Engineer Intern": {row["count"]} jobs from {row["variants"]} variants')

conn.close()
