"""Cockpit switch summary: raw cockpit arguments -> readable switch changes ("Battery Switch: OFF -> BATT").

Uses the active module's positions.json (tools/build_positions.py). Plain code, no model. A switch is reported when
its argument settles near one of its positions, so values caught mid-animation (e.g. 0.32 on the way from 0 to 1)
are not reported. The first value seen of each switch is the starting state, not a change. Buttons are reported
when pressed only.
"""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SwitchChange:
    arg: int
    name: str
    old: str | None     # None: first value seen (starting state)
    new: str
    verified: bool

    def text(self) -> str:
        return f'{self.name}: {self.new}' if self.old is None else f'{self.name}: {self.old} -> {self.new}'


def load_positions(module_dir: Path | None) -> dict:
    path = module_dir / 'positions.json' if module_dir else None
    if not path or not path.exists():
        return {}
    return {int(arg): entry for arg, entry in json.loads(path.read_text(encoding='utf-8'))['args'].items()
            if entry['positions']}


class CockpitSummary:
    def __init__(self, positions: dict):
        self.positions = positions
        self.labels: dict[int, str] = {}     # last settled label per argument
        self.tolerance = {arg: tolerance(entry) for arg, entry in positions.items()}

    def label(self, arg: int, value: float) -> str | None:
        entry = self.positions[arg]
        nearest = min(entry['positions'], key=lambda p: abs(p['value'] - value))
        return nearest['label'] if abs(nearest['value'] - value) <= self.tolerance[arg] else None

    def update(self, args: dict) -> list[SwitchChange]:
        """Changes from one export message's arguments ({arg: value}, keys may be strings)."""
        changes = []
        for key, value in args.items():
            arg = int(key)
            if arg not in self.positions:
                continue
            label = self.label(arg, value)
            old = self.labels.get(arg)
            if label is None or label == old:
                continue
            self.labels[arg] = label
            entry = self.positions[arg]
            if entry['kind'] == 'button' and (label != 'PRESSED' or old is None):
                continue
            changes.append(SwitchChange(arg, entry['name'], old, label, entry['verified']))
        return changes

    def state(self) -> dict[str, str]:
        """Every switch's current position by name (buttons left out)."""
        return {self.positions[arg]['name']: label for arg, label in sorted(self.labels.items())
                if self.positions[arg]['kind'] != 'button'}


def tolerance(entry: dict) -> float:
    values = sorted(p['value'] for p in entry['positions'])
    gaps = [b - a for a, b in zip(values, values[1:])]
    return min(gaps) / 4 if gaps else 0.05
