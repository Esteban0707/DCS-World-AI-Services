"""A wingman's memory of its pilot (user's design, 2026-11-04): a small file per agent, agents/<id>/memory.json,
{"pilot": [lines], "flights": [lines]}, kept by the model with its remember / forget tools, shown and edited in the
GUI (Mission tab), and given to the model with its fixed rules on every turn (wingman/rules.md).

"pilot": what it knows of the pilot as a pilot (name, nickname, habits, shared jokes); never trimmed by itself: when
full, the model must forget a line first. "flights": one line per flight, dated; the oldest go first.
"""
import json
import time
from pathlib import Path

from instructor import geo
from shared.agents import write_json

FILE = 'memory.json'
PILOT_KEPT = 40
FLIGHTS_KEPT = 12
LINE_MAX = 200
KINDS = ('pilot', 'flight')
RULES = Path(__file__).resolve().parent / 'rules.md'   # the wingman's fixed rules, after the user's personality
KIND_RULES = Path(__file__).resolve().parent / 'kinds'   # <kind>.md: how an airplane / a helicopter flies the job
HISTORY_TURNS = 20   # the wingman keeps more of the talk than other agents: it flies the whole flight with the pilot


def brain_appendix(memory: 'Memory', playbook=None):
    """What the brain adds after the agent's mission (instructor/brain.py "appendix"): the rules, those of its kind of
    aircraft (the playbook's kind: airplane / helicopter), the playbook's index and its open pages
    (wingman/playbook.py), then the memory."""
    def appendix() -> str:
        kind = playbook.kind() if playbook else 'airplane'
        kind_rules = KIND_RULES / f'{kind}.md'
        parts = [RULES.read_text(encoding='utf-8').strip(),
                 kind_rules.read_text(encoding='utf-8').strip() if kind_rules.exists() else '',
                 playbook.prompt() if playbook else '', memory.prompt()]
        return '\n\n'.join(p for p in parts if p)
    return appendix


def clean(lines) -> list[str]:
    if not isinstance(lines, list):
        return []
    return [' '.join(str(line).split())[:LINE_MAX] for line in lines if str(line).strip()]


class Memory:
    def __init__(self, folder: Path):
        self.path = folder / FILE

    def load(self) -> dict:
        """Read on every use: the GUI may have edited it meanwhile."""
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        return {'pilot': clean(data.get('pilot'))[:PILOT_KEPT], 'flights': clean(data.get('flights'))[-FLIGHTS_KEPT:]}

    def save(self, pilot, flights) -> dict:
        """The GUI's save: the lines as written (one per line), within the limits."""
        data = {'pilot': clean(pilot)[:PILOT_KEPT], 'flights': clean(flights)[-FLIGHTS_KEPT:]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, data)
        return data

    def remember(self, text: str, about: str = 'pilot') -> dict:
        line = ' '.join(str(text or '').split())[:LINE_MAX]
        if not line:
            return {'ok': False, 'detail': 'nothing to remember'}
        if about not in KINDS:
            return {'ok': False, 'detail': f'about must be one of {", ".join(KINDS)}'}
        data = self.load()
        if about == 'flight':
            data['flights'] = (data['flights'] + [f'{time.strftime("%Y-%m-%d")}: {line}'])[-FLIGHTS_KEPT:]
        else:
            if any(geo.simplify(old) == geo.simplify(line) for old in data['pilot']):
                return {'ok': True, 'detail': 'you already remember that'}
            if len(data['pilot']) >= PILOT_KEPT:
                return {'ok': False, 'detail': f'your memory of the pilot is full ({PILOT_KEPT} lines): forget '
                                               'something less important first'}
            data['pilot'].append(line)
        self.save(data['pilot'], data['flights'])
        return {'ok': True, 'detail': 'remembered'}

    def forget(self, text: str) -> dict:
        """The line that is, or contains, these words (one only)."""
        wanted = geo.simplify(text or '')
        data = self.load()
        lines = [(kind, line) for kind in ('pilot', 'flights') for line in data[kind]]
        found = [(k, l) for k, l in lines if geo.simplify(l) == wanted] or \
                [(k, l) for k, l in lines if wanted and wanted in geo.simplify(l)]
        if not found:
            return {'ok': False, 'detail': 'no such line in your memory'}
        if len(found) > 1:
            return {'ok': False, 'detail': 'several lines match: ' + ' | '.join(l for _, l in found[:5])}
        kind, line = found[0]
        data[kind].remove(line)
        self.save(data['pilot'], data['flights'])
        return {'ok': True, 'detail': f'forgotten: {line}'}

    def prompt(self) -> str:
        data = self.load()
        if not data['pilot'] and not data['flights']:
            return 'Your memory: empty. This may be your first flight together: get to know the pilot.'
        parts = ['Your memory of the pilot:']
        parts += [f'- {line}' for line in data['pilot']] or ['- (nothing yet)']
        if data['flights']:
            parts += ['Earlier flights together:'] + [f'- {line}' for line in data['flights']]
        return '\n'.join(parts)
