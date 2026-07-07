import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.title_normalizer import normalize_title

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Test a specific job with "Software Engineering Intern"
cursor.execute('SELECT job_id, title, normalized_title FROM jobs WHERE title = "Software Engineering Intern" LIMIT 1')
row = cursor.fetchone()

if row:
    print(f'Job {row["job_id"]}:')
    print(f'  title = "{row["title"]}"')
    print(f'  normalized_title = "{row["normalized_title"]}"') 
    
    normalized, conf = normalize_title(row['title'])
    print(f'\nTest normalization:')
    print(f'  "{row["title"]}" -> "{normalized}"')
    print(f'  Confidence: {conf:.2f}')
    print(f'  Should update: {normalized != row["normalized_title"]}')
    
    if normalized != row["normalized_title"]:
        print(f'\n[ACTION] Would update to "{normalized}"')
        # Actually update it
        cursor.execute('UPDATE jobs SET normalized_title = ? WHERE job_id = ?', (normalized, row['job_id']))
        conn.commit()
        print('[SUCCESS] Updated!')
else:
    print('No jobs found with title "Software Engineering Intern"')

# Check how many jobs with this title exist
cursor.execute('SELECT COUNT(*) as count FROM jobs WHERE title = "Software Engineering Intern"')
count = cursor.fetchone()['count']
print(f'\nTotal jobs with title "Software Engineering Intern": {count}')

# Check how many have correct normalized_title
cursor.execute('SELECT COUNT(*) as count  FROM jobs WHERE title = "Software Engineering Intern" AND normalized_title = "Software Engineer Intern"')
correct = cursor.fetchone()['count']
print(f'Jobs correctly normalized to "Software Engineer Intern": {correct}')

conn.close()
