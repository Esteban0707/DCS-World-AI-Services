"""The black box: everything that happens in one flight, written to logs/sessions/<start>_<aircraft>.jsonl.

Like a flight data recorder, it is read after the flight: the instructor in the tower does not see the cockpit live,
but the debriefing reads this file (switch changes, telemetry, what pilot and instructor said) and compares it with
the manual and DCS's procedures. One file per time the pilot enters an aircraft.

Each line is one JSON record with "kind":
- start: aircraft and module; start_switches: every switch's starting position (after the first snapshot)
- switch: a switch change from shared/cockpit_summary.py (name, old, new, verified)
- start_controls / control: the raw recorder (user's request 2026-11-01, "a recorder in the aircraft for everything I
  touch"): every clickable cockpit control of the module (controls.json, including radio/TACAN/IFF wheels that have
  no labels yet), its raw argument value once it settles, with the label when positions.json knows one. Starting
  values first, then each change: the evidence to verify the switch labels without answering in a console.
- flight: telemetry sample in pilot units (every TELEMETRY_S of model time) plus lat/lon
- radio: the radio selected for the instructor changed (name, MHz, modulation)
- callsign: the aircraft's callsign from the mission (GUI hook)
- groups: the agents' DCS groups every GROUPS_S (exists, alive, lat/lon, speed, DCS's troop count aboard) and who
  carries whom (state.transport): the evidence of what a squad really did (live 2026-11-05: "Tango got out" was
  said while Tango was still aboard Bison, and nothing in the logs could tell)
- pilot / instructor: what was said on the radio
- end: the pilot left the aircraft
"model_time" is DCS model time in seconds; "time" is wall-clock time.
"""
import json
import threading
import time
from pathlib import Path

from instructor.brain_context import flight_context, pilot_radio
from shared.cockpit import CockpitState
from shared.cockpit_summary import CockpitSummary, load_positions
from shared.modules import find_module

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / 'logs' / 'sessions'
TELEMETRY_S = 5.0
STARTING_STATE_S = 2.0    # switches seen in this first span of model time count as the starting state
SETTLE_S = 0.3            # a control's value is recorded once it has stayed put this long (model time)
STILL = 0.002             # value changes smaller than this are the control standing still
RECORD_CHANGE = 0.01      # a settled value is recorded when it differs this much from the last recorded one
GROUPS_S = 10.0           # wall-clock seconds between two "groups" records


def group_records(groups: dict | None) -> dict:
    """The agents' groups, small: what the debriefing needs to tell where each one was and who was aboard."""
    records = {}
    for name, group in (groups or {}).items():
        if not isinstance(group, dict):
            continue
        record = {k: group[k] for k in ('exists', 'alive', 'size') if k in group}
        record.update({k: round(group[k], 5) for k in ('lat', 'lon') if isinstance(group.get(k), (int, float))})
        if isinstance(group.get('speed_ms'), (int, float)):
            record['speed_ms'] = round(group['speed_ms'], 1)
        ground = group.get('ground') if isinstance(group.get('ground'), dict) else {}
        if 'descent_on_board' in ground:
            record['descent_on_board'] = ground['descent_on_board']
        records[name] = record
    return records


def control_names(module: Path | None) -> tuple[dict[int, str], set[int]]:
    """Cockpit argument -> control name for every clickable control of the module (controls.json hints), and the
    arguments of push buttons."""
    path = module / 'controls.json' if module else None
    if not path or not path.exists():
        return {}, set()
    names, buttons = {}, set()
    for control in json.loads(path.read_text(encoding='utf-8'))['controls']:
        name = (control.get('hint') or control.get('id') or '').split(',')[0].strip()
        for arg, kind in zip(control.get('arg') or [], control.get('class') or []):
            names.setdefault(int(arg), name)
            if kind == 'class_type.BTN':
                buttons.add(int(arg))
    return names, buttons


class ControlRecorder:
    """Raw cockpit controls: records each one's value once it settles (the export sends only changes, plus a full
    snapshot every few seconds, so settling is checked on every message, not only when the argument arrives).
    Push buttons are recorded at once: a press is shorter than the settling time."""

    def __init__(self, names: dict[int, str], buttons: set[int] = frozenset()):
        self.names, self.buttons = names, buttons
        self.recorded: dict[int, float] = {}
        self.moving: dict[int, tuple[float, float]] = {}    # arg -> (value, model time it got there)

    def update(self, args: dict, now: float) -> None:
        for key, value in args.items():
            arg = int(key)
            if arg not in self.names or not isinstance(value, (int, float)):
                continue
            moving = self.moving.get(arg)
            if moving is None or abs(value - moving[0]) > STILL:
                self.moving[arg] = (value, now)

    def settled(self, now: float, quiet: bool = False) -> list[tuple[int, float | None, float]]:
        """(arg, previous recorded value or None, new value) for each control that settled at a new value; with
        quiet, the values are only remembered (the starting state)."""
        changes = []
        for arg, (value, since) in list(self.moving.items()):
            if now - since < SETTLE_S and arg not in self.buttons:
                continue
            del self.moving[arg]
            old = self.recorded.get(arg)
            if old is not None and abs(value - old) < RECORD_CHANGE:
                continue
            self.recorded[arg] = value
            # A control seen for the first time shows its starting value, not a touch: the export's full snapshot
            # comes every 5 s, after the starting span has closed (seen live 2026-11-01: dozens of "None ->" records).
            if not quiet and old is not None:
                changes.append((arg, old, value))
        return changes


class BlackBox:
    def __init__(self, folder: Path = SESSIONS):
        self.folder = folder
        self.file = None
        self.path: Path | None = None
        self.summary: CockpitSummary | None = None
        self.controls = ControlRecorder({})
        self.started_at = 0.0
        self.last_sample = None
        self.last_radio = None
        self.last_callsign = None
        self.last_groups = None
        self.starting = False
        self.lock = threading.Lock()

    def write(self, kind: str, model_time: float, **data) -> None:
        line = json.dumps({'kind': kind, 'time': round(time.time(), 2), 'model_time': round(model_time, 2), **data},
                          ensure_ascii=False)
        with self.lock:   # the cockpit loop and the radio answers write from different threads
            if self.file:
                self.file.write(line + '\n')
                self.file.flush()

    def on_cockpit(self, kind: str, message: dict, state: CockpitState) -> None:
        """Feed every export message after CockpitState.apply (kind is what apply returned)."""
        if kind == 'aircraft':
            self.start(state)
        elif kind == 'left_aircraft':
            self.stop(state.time)
            return
        if not self.file:
            return
        if self.starting and state.time - self.started_at > STARTING_STATE_S:
            self.starting = False
            self.write('start_switches', state.time, switches=self.summary.state())
            # Whatever the controls showed before this message is the starting state.
            self.controls.settled(float('inf'), quiet=True)
            self.write('start_controls', state.time, controls={
                str(arg): {'control': self.controls.names[arg], 'value': round(value, 3), **self.label_of(arg, value)}
                for arg, value in sorted(self.controls.recorded.items())})
        if message.get('args'):
            self.controls.update(message['args'], state.time)
        if not self.starting:
            for arg, old, new in self.controls.settled(state.time):
                self.write('control', state.time, arg=arg, control=self.controls.names[arg],
                           old=None if old is None else round(old, 3), new=round(new, 3), **self.label_of(arg, new))
        if message.get('args'):
            changes = self.summary.update(message['args'])
            if not self.starting:   # during the first snapshot the summary only fills in the starting state
                for change in (c for c in changes if c.old is not None):   # first seen = starting position
                    self.write('switch', state.time, name=change.name, old=change.old, new=change.new,
                               arg=change.arg, verified=change.verified)
        if state.flight and (self.last_sample is None or state.time - self.last_sample >= TELEMETRY_S):
            self.last_sample = state.time
            position = {k: round(state.flight[k], 5) for k in ('lat', 'lon') if isinstance(state.flight.get(k), float)}
            self.write('flight', state.time, **flight_context(state.flight), **position)
        radio = pilot_radio(state)
        if radio != self.last_radio:
            self.last_radio = radio
            self.write('radio', state.time, radio=radio)
        if state.callsign and state.callsign != self.last_callsign:
            self.last_callsign = state.callsign
            self.write('callsign', state.time, callsign=state.callsign)
        if kind == 'groups' and state.groups and \
                (self.last_groups is None or time.monotonic() - self.last_groups >= GROUPS_S):
            self.last_groups = time.monotonic()
            self.write('groups', state.time, groups=group_records(state.groups), transport=state.transport)

    def start(self, state: CockpitState) -> None:
        self.stop(state.time)
        module = find_module(state.aircraft)
        self.summary = CockpitSummary(load_positions(module))
        self.controls = ControlRecorder(*control_names(module))
        self.folder.mkdir(parents=True, exist_ok=True)
        stem = f'{time.strftime("%Y%m%d-%H%M%S")}_{state.aircraft}'
        self.path = self.folder / f'{stem}.jsonl'
        number = 1
        while self.path.exists():   # two sessions started within the same second
            number += 1
            self.path = self.folder / f'{stem}_{number}.jsonl'
        self.file = self.path.open('a', encoding='utf-8')
        self.started_at, self.last_sample, self.last_radio, self.starting = state.time, None, None, True
        self.last_callsign = None
        self.write('start', state.time, aircraft=state.aircraft, module=module.name if module else None)

    def stop(self, model_time: float) -> None:
        if self.file:
            self.write('end', model_time)
            with self.lock:
                self.file.close()
                self.file = None

    def label_of(self, arg: int, value: float) -> dict:
        """The position label positions.json gives this value, if any, and whether it was verified."""
        if arg not in self.summary.positions:
            return {}
        label = self.summary.label(arg, value)
        return {'label': label, 'verified': self.summary.positions[arg]['verified']} if label else {}

    def said(self, who: str, text: str, model_time: float) -> None:
        self.write(who, model_time, text=text)
