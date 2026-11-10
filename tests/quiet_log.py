"""Imported by the tests that make the program log (client/dictation.py log): the log goes to a temporary file, so the
real logs/dictation.jsonl only holds what happened in flights."""
import tempfile
from pathlib import Path

from client import dictation

dictation.LOG = Path(tempfile.mkdtemp(prefix='dcs-instructor-tests-')) / 'dictation.jsonl'
