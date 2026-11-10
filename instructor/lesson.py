"""Run a converted DCS training lesson (modules/<aircraft>/training/*.json) against the live cockpit.

The lesson is the training mission's own trigger logic: rules with conditions (cockpit argument ranges, flags,
timers...) and actions (instructor text, flags, cockpit highlights, waiting for the pilot). This engine evaluates
the conditions it can see from the cockpit export and reports what the instructor should do as events; it does not
speak or call any model itself.

Conditions that depend on the training mission's own map (zones, other units) cannot be judged outside that
mission: they are treated as false and reported once, so a lesson pauses there instead of guessing.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from shared.cockpit import CockpitState

UNSUPPORTED = 'unsupported'


@dataclass
class Event:
    kind: str                  # say | highlight | unhighlight | wait | resume | finished | unsupported
    text: str = ''
    controls: list = field(default_factory=list)
    rule: str = ''


class Lesson:
    def __init__(self, path: Path, language: str):
        data = json.loads(path.read_text(encoding='utf-8'))
        self.name = data['description'].get(language, '').split('\n')[0].strip() or path.stem
        self.rules = data['rules']
        self.language = language
        self.flags: dict[int, float] = {}
        self.flag_set_at: dict[int, float] = {}
        self.fired: set[int] = set()
        self.highlights: dict[int, str] = {}       # highlight id -> control name
        self.waiting_flag: int | None = None       # set when the pilot says continue
        self.start_time: float | None = None
        self.finished = False
        self.last_text = ''
        self.reported: set[str] = set()

    # --- pilot input -------------------------------------------------------------------------------------------
    def pilot_continue(self, now: float) -> list[Event]:
        if self.waiting_flag is None:
            return []
        self._set_flag(self.waiting_flag, 1, now)
        self.waiting_flag = None
        return [Event('resume')]

    # --- evaluation --------------------------------------------------------------------------------------------
    def tick(self, state: CockpitState) -> list[Event]:
        if self.finished:
            return []
        now = state.time
        if self.start_time is None:
            self.start_time = now
        events = []
        for rule in self.rules:
            once = rule['type'] != 'triggerContinious'
            if once and rule['index'] in self.fired:
                continue
            if self._conditions_hold(rule, state, events):
                self.fired.add(rule['index'])
                events.extend(self._run_actions(rule, now))
                if self.finished:
                    break
        return events

    def _conditions_hold(self, rule: dict, state: CockpitState, events: list) -> bool:
        # DCS rules: conditions are ANDed; an "or" entry starts a new alternative group.
        groups, current = [], []
        for condition in rule['conditions']:
            if condition['predicate'] == 'or':
                groups.append(current)
                current = []
            else:
                current.append(condition)
        groups.append(current)
        for group in groups:
            results = [self._condition(c, state) for c in group]
            if False in results:
                continue
            # Only report an unsupported condition when it is the one thing blocking this rule.
            if UNSUPPORTED in results:
                key = f'{rule["index"]}'
                if key not in self.reported:
                    self.reported.add(key)
                    names = sorted({c['predicate'] for c, r in zip(group, results) if r == UNSUPPORTED})
                    events.append(Event('unsupported', text=', '.join(names), rule=rule['name']))
                continue
            if all(results):
                return True
        return False

    def _condition(self, c: dict, state: CockpitState):
        p, now, flight = c['predicate'], state.time, state.flight
        if p == 'c_argument_in_range':
            value = state.args.get(c['arg'])
            return value is not None and c['min'] <= value <= c['max']
        if p == 'c_flag_is_true':
            return bool(self.flags.get(c['flag']))
        if p == 'c_flag_is_false':
            return not self.flags.get(c['flag'])
        if p == 'c_time_since_flag':
            return bool(self.flags.get(c['flag'])) and now - self.flag_set_at[c['flag']] >= c['seconds']
        if p == 'c_time_after':
            return now - self.start_time >= c['seconds']
        if p == 'c_flag_equals':
            return self.flags.get(c['flag'], 0) == c.get('value')
        if p in ('c_unit_speed_lower', 'c_unit_speed_higher'):
            # Mission editor unit speed is in m/s; TAS stands in for ground speed (no wind on the ground matters).
            speed = flight.get('tas_ms')
            if speed is None:
                return False
            return speed < c['speedU'] if p.endswith('lower') else speed > c['speedU']
        if p in ('c_unit_altitude_lower', 'c_unit_altitude_higher'):
            altitude = flight.get('alt_msl_m')
            if altitude is None:
                return False
            return altitude < c['altitude'] if p.endswith('lower') else altitude > c['altitude']
        if p == 'c_unit_alive':
            return state.aircraft is not None
        if p == 'c_unit_damaged':
            return False
        return UNSUPPORTED

    def _set_flag(self, flag: int, value: float, now: float) -> None:
        if value and not self.flags.get(flag):
            self.flag_set_at[flag] = now
        self.flags[flag] = value

    def _run_actions(self, rule: dict, now: float) -> list[Event]:
        events = []
        for a in rule['actions']:
            p = a['predicate']
            if p in ('a_out_text_delay', 'a_out_text_delay_g') and a.get('text'):
                text = a['text'].get(self.language) or a['text'].get('en') or next(iter(a['text'].values()))
                self.last_text = text
                events.append(Event('say', text=text, rule=rule['name']))
            elif p == 'a_set_flag':
                self._set_flag(a['flag'], a.get('value', 1), now)
            elif p == 'a_set_flag_value':
                self._set_flag(a['flag'], a.get('value', 0), now)
            elif p == 'a_clear_flag':
                self.flags[a['flag']] = 0
            elif p == 'a_cockpit_highlight':
                self.highlights[a['highlight_id']] = a.get('control') or a.get('element_name', '')
                events.append(Event('highlight', controls=[self.highlights[a['highlight_id']]], rule=rule['name']))
            elif p == 'a_cockpit_remove_highlight':
                control = self.highlights.pop(a['highlight_id'], None)
                if control:
                    events.append(Event('unhighlight', controls=[control], rule=rule['name']))
            elif p == 'c_start_wait_for_user':
                self.waiting_flag = a['flag']
                events.append(Event('wait', rule=rule['name']))
            elif p == 'c_stop_wait_for_user':
                self.waiting_flag = None
            elif p == 'a_end_mission':
                self.finished = True
                events.append(Event('finished', rule=rule['name']))
        return events

    def context(self, state: CockpitState) -> dict:
        """What the instructor is doing right now, for the model when the pilot asks something free."""
        return {
            'lesson': self.name,
            'last_instruction': self.last_text,
            'highlighted_controls': sorted(self.highlights.values()),
            'waiting_for_pilot': self.waiting_flag is not None,
            'aircraft': state.aircraft,
            'flight': state.flight,
        }
