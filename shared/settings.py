"""settings.json: read by every part of the program, written by the GUI (gui/server.py).

Writes go through update(): a deep merge of the changed keys, written to a temporary file and moved into place, so a
crash never leaves half a file and keys the GUI does not know about are kept.
"""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / 'settings.json'
_lock = threading.Lock()


def load(path: Path = PATH) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def merge(base: dict, changes: dict) -> dict:
    """base with changes applied; nested dicts are merged, everything else replaced."""
    result = dict(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def update(changes: dict, path: Path = PATH) -> dict:
    """Apply changes to the file and return the whole new settings."""
    with _lock:
        settings = merge(load(path), changes)
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(temp, path)
        return settings
