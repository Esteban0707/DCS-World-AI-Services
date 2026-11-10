"""What the tower sees and remembers, to call the pilot on its own like a real tower.

Adapted from DCS-ATC (atc/tower.py; copied and rewritten with the user's permission, 2026-11-02). Code detects the
situation and the call is a fixed phrase in the radio language (locales "atc"), so it is fast and exact. The tower's
memory (Ledger) records its own decisions, not the words of its replies, so it works in any language.

Situations:
- runway_free / number_two: the pilot is holding at the holding point because of traffic, and the runway is now free,
  or a different arrival is now the reason to keep holding.
- unannounced_taxi: the pilot taxis at the field with no taxi clearance from the tower.
- runway_incursion: the pilot enters the runway in use with no takeoff clearance from the tower.
- departed: the pilot the tower cleared is airborne and climbing away.
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass

from atc.phrases import Speech

UNANNOUNCED_TAXI_KT = 3
UNANNOUNCED_TAXI_S = 10      # moving this long without a clearance before the tower asks
DEPARTED_CALL_AFTER_S = 20   # airborne this long after a takeoff the tower cleared -> it releases the pilot
CALLED_STATION_MIN_RATIO = 0.8


@dataclass
class Ledger:
    """What the tower has given the pilot in the current departure (reset after each flight)."""
    taxi_cleared: bool = False
    takeoff_cleared: bool = False
    hold_key: str | None = None   # takeoff held for traffic: that traffic's key (ground.traffic_key)

    def record(self, kind: str, decision: dict | None):
        """After the tower gave the pilot `decision` for 'taxi' or 'takeoff' (atc/ground.py)."""
        if not decision:
            return
        if decision.get('status') == 'cleared':
            if kind == 'taxi':
                self.taxi_cleared = True
            elif kind == 'takeoff':
                self.takeoff_cleared, self.taxi_cleared, self.hold_key = True, True, None
        elif kind == 'takeoff' and decision.get('status') == 'hold' and decision.get('traffic_key'):
            self.hold_key = decision['traffic_key']


@dataclass
class View:
    """One look from the tower."""
    t: float                    # model time
    on_ground: bool
    landed: bool                # after a landing, until parked
    ground_speed_kt: float
    at_departure_end: bool
    on_runway: bool             # on the runway in use, not rolling
    takeoff: dict               # ground.takeoff_clearance() now


class Watch:
    """Turns successive views into situations, each called once."""

    def __init__(self):
        self.moving_since = None
        self.taxi_asked = False
        self.incursion_called = False

    def update(self, view: View, ledger: Ledger) -> str | None:
        if not view.on_ground:
            self.moving_since, self.incursion_called = None, False
            return None
        if view.on_runway and not ledger.takeoff_cleared and not view.landed:
            if not self.incursion_called:
                self.incursion_called = True
                return 'runway_incursion'
        elif not view.on_runway:
            self.incursion_called = False
        if view.ground_speed_kt >= UNANNOUNCED_TAXI_KT and not view.landed and not ledger.taxi_cleared:
            self.moving_since = view.t if self.moving_since is None else self.moving_since
            if not self.taxi_asked and view.t - self.moving_since >= UNANNOUNCED_TAXI_S:
                self.taxi_asked = True
                return 'unannounced_taxi'
        else:
            self.moving_since = None
        # Holding for traffic: call when the runway is free, or when a different arrival is now the reason.
        if ledger.hold_key and view.at_departure_end and not view.on_runway:
            card = view.takeoff
            if card.get('status') == 'cleared':
                ledger.hold_key = None
                return 'runway_free'
            key = card.get('traffic_key') if card.get('status') == 'hold' else None
            if key and key.endswith(' final') and key.rsplit(' ', 1)[0] != ledger.hold_key.rsplit(' ', 1)[0]:
                ledger.hold_key = key
                return 'number_two'
        return None


def phrase(name: str, speech: Speech, runway: str, takeoff: dict, traffic: str | None) -> str:
    """The fixed phrase of a situation, in the radio language. `runway` is spoken ("runway two seven")."""
    if name == 'departed':
        return speech.say('departed')
    if name == 'runway_free':
        return speech.say('runway_free', clearance=takeoff.get('say', speech.say('hold_position')))
    if name == 'number_two':
        return speech.say('keep_holding', traffic=takeoff.get('traffic') or speech.say('runway_occupied'))
    if name == 'unannounced_taxi':
        return speech.say('unannounced_taxi')
    if name == 'runway_incursion':
        if traffic:
            return speech.say('runway_incursion_traffic', runway=runway, traffic=traffic)
        return speech.say('runway_incursion', runway=runway)
    raise ValueError(name)


SOUND_RULES = [  # dictation spells the same sounds differently: compare how words sound, not how they are spelt
    (r'qu', 'k'), (r'c([eiy])', r's\1'), (r'[cqg]', 'k'), (r'kh|ch', 'k'), (r'h', ''), (r'y', 'i'), (r'v', 'b'),
    (r'z', 's'), (r'(.)\1+', r'\1'),
]


def sound(text: str) -> str:
    """'Kolkhi' and the dictated 'colgi' / 'colky' -> 'kolki'."""
    text = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if unicodedata.category(c) != 'Mn')
    for pattern, replacement in SOUND_RULES:
        text = re.sub(pattern, replacement, text)
    return text


def called_station(heard: str, own_names: list[str], other_names: list[str]) -> str | None:
    """The other airfield the pilot named, if any ("Kobuleti, Enfield 1-1, ..." on Kolkhi's frequency), matched by
    sound because dictation mangles names: a word counts only if it is closer to another airfield than to this one."""
    def parts(names):
        return {sound(p) for name in names for p in re.split(r'[\s\-]+', name) if len(p) >= 4}

    own, others = parts(own_names), {}
    for name in other_names:
        for part in parts([name]):
            others.setdefault(part, name)
    best, best_ratio = None, CALLED_STATION_MIN_RATIO
    for word in re.findall(r'\w{4,}', sound(heard)):
        own_ratio = max((difflib.SequenceMatcher(None, word, p).ratio() for p in own), default=0)
        for part, name in others.items():
            ratio = difflib.SequenceMatcher(None, word, part).ratio()
            if ratio >= best_ratio and ratio > own_ratio:
                best, best_ratio = name, ratio
    return best
