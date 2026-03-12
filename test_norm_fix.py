"""Test title normalization"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.title_normalizer import normalize_title

tests = [
    'Full - Stack Software Engineer',
    'Co - Op',
    'On - Site Medical Interpreter',
    '#ls359 - Engineer',
    'REF123 - Manager'
]

for t in tests:
    normalized, conf = normalize_title(t)
    print(f"{t:40} -> {normalized}")
