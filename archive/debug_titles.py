import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

conn = sqlite3.connect('data/jobs.sqlite')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("""
    SELECT title, normalized_title, COUNT(*) as count 
    FROM jobs 
    WHERE title LIKE '%Software%Intern%' OR title LIKE '%SWE%Intern%' 
    GROUP BY title, normalized_title 
    ORDER BY count DESC 
    LIMIT 15
""")

rows = cursor.fetchall()
print('Title'.ljust(45) + 'Normalized Title'.ljust(45) + 'Count')
print('-' * 95)
for row in rows:
    print(row['title'][:43].ljust(45) + row['normalized_title'][:43].ljust(45) + str(row['count']))

print('\n\nChecking exact mapping for "Software Engineering Intern":')
from src.title_normalizer import normalize_title
test_titles = [
    "Software Engineering Intern",
    "SWE Intern",
    "Software Development Intern",
    "Software Developer Intern",
    "software engineer intern",
]

for title in test_titles:
    normalized, confidence = normalize_title(title)
    print(f"  '{title}' → '{normalized}' (confidence: {confidence:.2f})")

conn.close()
