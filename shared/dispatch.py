"""Several agents on one frequency (user's design, 2026-11-03): who a phrase is for.

Like a real radio, everyone on the channel hears everything and the one called answers. The pilot names the agent at
the start of the call ("Tango, move to zone 1"): its name or one of its aliases (agent.json "aliases", e.g. a
callsign) among the first ADDRESS_WORDS words. When no agent is named, "all stations" words (locales/*.json
"radio.all_stations", every language at once: no words in code) call every agent on the channel, each answering in
turn (the voice queue speaks one clip at a time). Without a name, the
agent that last talked with the pilot on this channel (within FOLLOW_UP_S) answers; else nobody does, as on the air.
An agent alone on its frequency always answers, named or not (the behaviour before shared channels).

Matching is language-neutral: lowercase, no accents, no punctuation (shared/locale.normalize), and each word may be
slightly off (dictation errors such as "bueno" for "Buenos").
"""
import difflib
import time
from pathlib import Path

from shared.locale import ROOT, normalize

ADDRESS_WORDS = 6      # the callee is named at the start of a radio call
FOLLOW_UP_S = 60.0     # an unnamed phrase goes on with the agent that last talked on this channel
WORD_RATIO = 0.8       # difflib ratio for a word to count as the same (words of 4+ letters; shorter ones exact)


def radio_words(folder: Path = ROOT / 'locales') -> list[dict]:
    """Every locale's "radio" section (all_stations, numbers)."""
    import json
    sections = []
    for path in sorted(folder.glob('*.json')):
        try:
            sections.append(json.loads(path.read_text(encoding='utf-8')).get('radio') or {})
        except (OSError, ValueError):
            continue
    return sections


def all_stations_words(folder: Path = ROOT / 'locales') -> list[list[str]]:
    """Every locale's "radio.all_stations" phrases, normalized to word lists."""
    return [normalize(w).split() for section in radio_words(folder) for w in section.get('all_stations') or []
            if normalize(w)]


def number_words(folder: Path = ROOT / 'locales') -> dict[int, list[str]]:
    """A wingman's number as the pilot may say it, every language at once ("radio.numbers") and as a digit."""
    found: dict[int, list[str]] = {}
    for section in radio_words(folder):
        for number, words in (section.get('numbers') or {}).items():
            found.setdefault(int(number), [number]).extend(normalize(w) for w in words if normalize(w))
    return found


NUMBER_WORDS = number_words()


def names(agent: dict) -> list[list[str]]:
    """The words the pilot may call this agent by: its name, its aliases, and a wingman's number ("Two, cover me",
    "Colt 1-2" heard as "colt 1 2")."""
    found = [agent.get('name', ''), *(a for a in agent.get('aliases') or [] if isinstance(a, str))]
    if agent.get('role') == 'wingman':
        found += NUMBER_WORDS.get((agent.get('wingman') or {}).get('number'), [])
    return [normalize(n).split() for n in found if normalize(n)]


def same_word(heard: str, wanted: str) -> bool:
    if heard == wanted:
        return True
    if len(wanted) == 3 and heard.startswith(wanted) and len(heard) == 4:
        return True   # live 2026-11-06: "Axe" dictated as "Axel" went to the last speaker on the channel
    return min(len(heard), len(wanted)) >= 4 and difflib.SequenceMatcher(None, heard, wanted).ratio() >= WORD_RATIO


def position(words: list[str], phrase: list[str]) -> int | None:
    """Where phrase starts in words (first ADDRESS_WORDS only), or None."""
    for start in range(min(len(words), ADDRESS_WORDS)):
        part = words[start:start + len(phrase)]
        if len(part) == len(phrase) and all(same_word(h, w) for h, w in zip(part, phrase)):
            return start
    return None


def addressed(text: str, agents: list[dict], everyone: list[list[str]]) -> tuple[list[dict], str]:
    """The agents a phrase calls by name, in the order named, or all of them for "all stations". What comes first
    decides: "Tango, are you all ready?" is for Tango, "Colt flight, two bandits north" for the whole flight (not
    number two). Returns (agents, how): how is "name", "all" or "" (nobody named)."""
    words = normalize(text).split()
    named = []
    for agent in agents:
        at = [p for p in (position(words, n) for n in names(agent)) if p is not None]
        if at:
            named.append((min(at), agent))
    calls_all = [p for p in (position(words, phrase) for phrase in everyone) if p is not None]
    if calls_all and (not named or min(calls_all) < min(at for at, _ in named)):
        return list(agents), 'all'
    if named:
        return [agent for _, agent in sorted(named, key=lambda pair: pair[0])], 'name'
    return [], ''


class Dispatcher:
    """Remembers who last talked with the pilot on each channel, for phrases that name nobody."""

    def __init__(self, everyone: list[list[str]] | None = None):
        self.everyone = all_stations_words() if everyone is None else everyone
        self.last: dict[tuple, tuple[str, float]] = {}   # channel key -> (agent id, when)

    @staticmethod
    def key(agents: list[dict]) -> tuple:
        return tuple(sorted(a['id'] for a in agents))

    def talked(self, agents: list[dict], agent_id: str) -> None:
        self.last[self.key(agents)] = (agent_id, time.monotonic())

    def route(self, text: str, agents: list[dict]) -> tuple[list[dict], str]:
        """(who answers, how): how is "alone", "name", "all", "follow_up" or "" (nobody)."""
        if len(agents) <= 1:
            return list(agents), 'alone'
        chosen, how = addressed(text, agents, self.everyone)
        if chosen:
            return chosen, how
        last_id, at = self.last.get(self.key(agents), ('', 0.0))
        if time.monotonic() - at <= FOLLOW_UP_S:
            follow = [a for a in agents if a['id'] == last_id]
            if follow:
                return follow, 'follow_up'
        return [], ''
