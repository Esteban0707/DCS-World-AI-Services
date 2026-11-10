"""User-facing text lives in locales/<lang>.json, never in code."""
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_locale(language: str) -> dict:
    path = ROOT / 'locales' / f'{language}.json'
    if not path.exists():
        path = ROOT / 'locales' / 'en.json'
    return json.loads(path.read_text(encoding='utf-8'))


def normalize(text: str) -> str:
    """Lowercase, no accents, no punctuation: for matching spoken commands."""
    text = unicodedata.normalize('NFKD', text.lower())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w\s]', ' ', text).split())
