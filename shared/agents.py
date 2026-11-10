"""Agents ("services" in the GUI): radio characters, each in its own folder agents/<id>/ (user's design, 2026-11-02).

  agents/<id>/agent.json   name, active, frequency_mhz + modulation, connection, voice (TTS), speech (STT)
  agents/<id>/mission.md   its system prompt: who it is and how it behaves (edited in the GUI's Mission tab); for a
                           wingman only its personality (its fixed rules: wingman/rules.md)
  agents/<id>/memory.json  a wingman's memory of the pilot (wingman/memory.py)

The frequency is the on-switch: when the pilot transmits on the radio tuned to an active agent's frequency, that agent
answers with its own folder (prompt, voice, dictation language), on that same frequency. Several agents may share a
frequency: the one the pilot names answers (shared/dispatch.py, "aliases" are other names). Roles: "instructor" and
"atc" (atc/); a commander will be another role with its own tools.

"frequency_mode": "airfield" (the ATC, user's choice 2026-11-02): instead of one frequency of its own, the agent
answers on every airfield's real ATC frequency; station_finder(freq_hz) (set by the voice loop from atc/desk.py) says
whether an airfield listens there. Agents with a fixed frequency are matched first.

The first run creates agents/instructor from the older single-instructor settings (settings.json "srs" frequency,
"voice", "speech") and agents/atc. The original mission of each role is kept in code folders (ROLE_MISSIONS) for
"restore".
"""
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from shared.settings import merge

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / 'agents'
ROLE_MISSIONS = {'instructor': ROOT / 'instructor' / 'brain_instructions.md', 'atc': ROOT / 'atc' / 'mission.md',
                 'squad': ROOT / 'squad' / 'mission.md', 'wingman': ROOT / 'wingman' / 'mission.md',
                 'vehicles': ROOT / 'vehicles' / 'mission.md', 'artillery': ROOT / 'artillery' / 'mission.md',
                 'ships': ROOT / 'ships' / 'mission.md', 'controller': ROOT / 'controller' / 'mission.md'}
# AI ground groups the agent commands (squad/desk.py, vehicles/desk.py, artillery/desk.py): agent.json "<role>":
# {groups, name}.
GROUND_ROLES = ('squad', 'vehicles', 'artillery', 'ships')   # ships: a surface group, same config
GROUP_ROLES = (*GROUND_ROLES, 'wingman')   # agents that follow a group of the mission (the GUI's "group")
AIRFIELD_MODE = 'airfield'
MISSION_FIELDS = ('name', 'tower', 'radio', 'aircraft', 'callsign', 'tool_guide')
ADDABLE_ROLES = ('instructor', 'squad', 'vehicles', 'artillery', 'ships', 'wingman',
                 'controller')   # "+ Add agent" in the GUI; the ATC is fixed and only one
# The air controller (controller/desk.py): its "group" in the GUI is its first radar group (agent.json "controller":
# {sensors, name}). Any agent may carry "frequency_sharing": "public" (the controller gives its frequency to any pilot)
# or "task" (default: only to a pilot whose task involves it).
SENSOR_ROLES = ('controller',)
WINGMAN_NUMBERS = (2, 3, 4)
WINGMAN_CHATTER = ('off', 'rare', 'normal')   # same as wingman/desk.py CHATTER_S
FIXED_ROLES = ('atc',)                    # name, role and tools not editable
GROUP_MAX = 64
MODULATIONS = ('AM', 'FM')
FREQUENCY_RANGE_MHZ = (30.0, 400.0)
TOLERANCE_HZ = 5000          # radios step in 5/25 kHz; anything closer is the same channel
AGENT_ID = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
VOICE_DEFAULTS = {'name': 'M1', 'speed': 1.05, 'steps': 8, 'silence_s': 0.3, 'volume': 1.0, 'output': 'srs'}


def default_instructor(settings: dict) -> dict:
    srs, voice, speech = settings.get('srs', {}), settings.get('voice', {}), settings.get('speech', {})
    frequencies = srs.get('frequencies_mhz') or [251.0]
    modulations = srs.get('modulations') or ['AM']
    agent_voice = {**VOICE_DEFAULTS, **{k: v for k, v in voice.items() if k in (*VOICE_DEFAULTS, 'language')}}
    agent_voice.setdefault('language', settings.get('language', 'en'))
    return {'name': 'Instructor', 'role': 'instructor', 'active': True,
            'frequency_mhz': float(frequencies[0]), 'modulation': modulations[0].upper(),
            'connection': 'default', 'voice': agent_voice,
            'speech': {'language': speech.get('language', 'en-US')}}


def default_atc(settings: dict) -> dict:
    """The ATC: on every airfield's real frequency, its own voice, the same language as the instructor."""
    speech = settings.get('speech', {})
    language = settings.get('voice', {}).get('language') or settings.get('language', 'en')
    return {'name': 'ATC', 'role': 'atc', 'active': True, 'frequency_mode': AIRFIELD_MODE,
            'frequency_mhz': 0.0, 'modulation': 'AM', 'connection': 'default',
            'voice': {**VOICE_DEFAULTS, 'name': 'M2', 'speed': 1.15, 'language': language},
            'speech': {'language': speech.get('language', 'en-US')},
            'atc': {'pressure': 'inhg', 'runway_protect_nm': 5.0, 'station_names': {}}}


def default_squad(settings: dict, group: str = 'Buenos', role: str = 'squad') -> dict:
    """A squad the pilot carries (or, role "vehicles", a group of ground vehicles): the AI ground group of that name
    in the mission, on its own radio."""
    speech = settings.get('speech', {})
    language = settings.get('voice', {}).get('language') or settings.get('language', 'en')
    return {'name': group, 'role': role, 'active': True, 'frequency_mhz': 255.0, 'modulation': 'AM',
            'connection': 'default', 'voice': {**VOICE_DEFAULTS, 'name': 'M3', 'speed': 1.1, 'language': language},
            'speech': {'language': speech.get('language', 'en-US')}, role: {'groups': [group], 'name': group}}


def default_wingman(settings: dict, number: int = 2, group: str = '') -> dict:
    """A wingman: its own AI aircraft group in the mission (late activation, started by radio: wingman/desk.py),
    called by name or by its number in the flight, on the flight's frequency (user's example: 316.000)."""
    speech = settings.get('speech', {})
    language = settings.get('voice', {}).get('language') or settings.get('language', 'en')
    return {'name': group or f'Number {number}', 'role': 'wingman', 'active': True,
            'frequency_mhz': 316.0, 'modulation': 'AM', 'connection': 'default',
            'voice': {**VOICE_DEFAULTS, 'name': 'M4', 'speed': 1.15, 'language': language},
            'speech': {'language': speech.get('language', 'en-US')},
            'wingman': {'number': number, 'group': group, 'chatter': 'rare'}}


def default_controller(settings: dict, group: str = 'Magic') -> dict:
    """An air controller (controller/desk.py): its first radar group (an AWACS) from the GUI, more in agent.json."""
    speech = settings.get('speech', {})
    language = settings.get('voice', {}).get('language') or settings.get('language', 'en')
    return {'name': 'Magic', 'role': 'controller', 'active': True, 'frequency_mhz': 270.0, 'modulation': 'AM',
            'connection': 'default', 'voice': {**VOICE_DEFAULTS, 'name': 'M5', 'speed': 1.15, 'language': language},
            'speech': {'language': speech.get('language', 'en-US')},
            'controller': {'sensors': [group], 'name': 'Magic', 'tasks': []}}


def checked_number(number) -> int:
    """A wingman's number in the flight: 2, 3 or 4; ValueError('number') otherwise."""
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise ValueError('number') from None
    if number not in WINGMAN_NUMBERS:
        raise ValueError('number')
    return number


def same_channel(a: dict, b: dict) -> bool:
    """Two fixed-frequency agents on the same radio channel."""
    return not airfield_mode(a) and not airfield_mode(b) and a.get('modulation') == b.get('modulation') and \
        abs(float(a.get('frequency_mhz', 0)) - float(b.get('frequency_mhz', 0))) * 1e6 <= TOLERANCE_HZ


def checked_group(group) -> str:
    """A squad's group as the Mission Editor names it; ValueError('group') if empty, too long or with control chars."""
    group = str(group or '').strip()
    if not 0 < len(group) <= GROUP_MAX or any(ord(c) < 32 for c in group):
        raise ValueError('group')
    return group


def airfield_mode(agent: dict) -> bool:
    return agent.get('frequency_mode') == AIRFIELD_MODE


def fill_mission(template: str, values: dict) -> str:
    """The mission with its {fields} filled; only the known fields, so any other braces the user writes stay."""
    for field in MISSION_FIELDS:
        template = template.replace('{' + field + '}', str(values.get(field, '')))
    return template.strip() + '\n'


def channel_label(agent: dict) -> str:
    if airfield_mode(agent):
        return 'airfield frequencies'
    return f'{agent["frequency_mhz"]:.3f} {agent["modulation"]}'


class AgentStore:
    """The agents on disk, cached; reload() after a change. Thread-safe for the GUI and the voice loop."""

    def __init__(self, folder: Path = AGENTS_DIR, settings: dict | None = None):
        self.folder = folder
        self.lock = threading.Lock()
        self.agents: dict[str, dict] = {}
        self.station_finder = None   # freq_hz -> airfield name or None, for "airfield" mode agents
        if settings is not None and not self.folder.exists():
            self.create('instructor', default_instructor(settings))
            self.create('atc', default_atc(settings))
        self.reload()

    def reload(self) -> None:
        agents = {}
        if self.folder.exists():
            for path in sorted(self.folder.glob('*/agent.json')):
                try:
                    agent = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError) as exc:
                    print(f'[agents] {path}: {exc}')
                    continue
                agents[path.parent.name] = {**agent, 'id': path.parent.name}
        with self.lock:
            self.agents = agents

    def all(self) -> list[dict]:
        with self.lock:
            return list(self.agents.values())

    def get(self, agent_id: str) -> dict | None:
        with self.lock:
            return self.agents.get(agent_id)

    def active(self) -> list[dict]:
        return [a for a in self.all() if a.get('active')]

    def default(self) -> dict | None:
        """The agent that answers when the radio is not known (DCS or SRS not running): the first active one."""
        active = self.active()
        return next((a for a in active if a.get('role') == 'instructor'), active[0] if active else None)

    def on_frequency(self, freq_hz: float, modulation: int) -> dict | None:
        """The active agent on this radio channel (modulation as SRS numbers it: 0 AM, 1 FM): a fixed-frequency agent
        first, else an "airfield" agent when an airfield listens on that frequency (any modulation: the airfield's
        VHF-low channel is heard on FM radios too)."""
        active = self.active()
        for agent in active:
            if not airfield_mode(agent) and abs(freq_hz - agent['frequency_mhz'] * 1e6) <= TOLERANCE_HZ and \
                    modulation == MODULATIONS.index(agent['modulation']):
                return agent
        finder = self.station_finder
        if finder and finder(freq_hz):
            return next((a for a in active if airfield_mode(a)), None)
        return None

    def on_channel(self, agent: dict) -> list[dict]:
        """Every active agent sharing this agent's frequency (itself included; shared/dispatch.py picks who answers).
        An "airfield" agent is alone on its channels."""
        if airfield_mode(agent):
            return [agent]
        mates = [a for a in self.active() if same_channel(a, agent)]
        return mates if any(a['id'] == agent.get('id') for a in mates) else [agent, *mates]

    # --- files ------------------------------------------------------------------------------------------------

    def path(self, agent_id: str) -> Path:
        if not AGENT_ID.match(agent_id):
            raise ValueError('agent')
        return self.folder / agent_id

    def add(self, role: str, name: str, frequency_mhz: float, modulation: str, settings: dict,
            group: str = '', number=2) -> dict:
        """A new agent from the GUI's "+ Add agent": its role's defaults (voice, mission) with this name and
        frequency, switched on. A squad and a wingman need the name of their AI group in the mission (the editor's
        group name); a wingman also its number in the pilot's flight (2-4)."""
        if role not in ADDABLE_ROLES:
            raise ValueError('role')
        name = name.strip()
        if role in GROUND_ROLES:
            agent = default_squad(settings, checked_group(group), role)
            agent[role]['name'] = name
        elif role == 'wingman':
            agent = default_wingman(settings, checked_number(number), checked_group(group))
        elif role in SENSOR_ROLES:
            agent = default_controller(settings, checked_group(group))
            agent['controller']['name'] = name
        else:
            agent = default_instructor(settings)
        agent.update({'name': name, 'active': True, 'frequency_mhz': round(float(frequency_mhz), 3),
                      'modulation': modulation})
        agent_id = self.new_id(name)
        self.check_channel(agent_id, agent)
        self.create(agent_id, agent)
        self.reload()
        return self.get(agent_id)

    def remove(self, agent_id: str) -> Path:
        """The GUI's "Delete agent": its folder is moved, not erased, to backups/agents/<id>-<time> (put it back in
        agents/ to restore it). The ATC is fixed and stays."""
        agent = self.get(agent_id)
        if agent is None:
            raise ValueError('agent')
        if agent.get('role') in FIXED_ROLES:
            raise ValueError('fixed_agent')
        removed = self.folder.parent / 'backups' / 'agents' / f'{agent_id}-{time.strftime("%Y%m%d-%H%M%S")}'
        removed.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.path(agent_id)), str(removed))
        self.reload()
        return removed

    def new_id(self, name: str) -> str:
        """A folder name from the agent's name: "Los Buenos 2" -> "los-buenos-2"; unique."""
        base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:24].strip('-')
        if not base or not base[0].isalpha():
            base = f'agent-{base}'.strip('-')
        agent_id, n = base, 2
        while self.get(agent_id) is not None or (self.folder / agent_id).exists():
            agent_id, n = f'{base}-{n}', n + 1
        return agent_id

    def create(self, agent_id: str, agent: dict) -> None:
        folder = self.path(agent_id)
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / 'agent.json', agent)
        original = ROLE_MISSIONS.get(agent.get('role', ''))
        if original and not (folder / 'mission.md').exists():
            (folder / 'mission.md').write_text(original.read_text(encoding='utf-8'), encoding='utf-8')

    def update(self, agent_id: str, changes: dict) -> dict:
        """Merge checked changes into agent.json; ValueError names the first bad value."""
        agent = self.get(agent_id)
        if agent is None:
            raise ValueError('agent')
        if agent.get('role') in FIXED_ROLES and changes.get('name', agent['name']) != agent['name']:
            raise ValueError('fixed_name')
        stored = {k: v for k, v in agent.items() if k != 'id'}
        role = agent.get('role')
        if role in GROUND_ROLES and 'name' in changes:   # the name is who the squad says it is
            changes = merge(changes, {role: {'name': changes['name']}})
        if role in SENSOR_ROLES and 'name' in changes:
            changes = merge(changes, {role: {'name': changes['name']}})
        if 'group' in changes:   # a squad's or wingman's group in the mission (renamed in the Mission Editor)
            group = checked_group(changes['group']) if role in (*GROUP_ROLES, *SENSOR_ROLES) else None
            if group is None:
                raise ValueError('group')
            if role in GROUND_ROLES:
                own = {role: {'groups': [group]}}
            elif role in SENSOR_ROLES:   # the GUI's group is the first sensor; the others stay
                others = list((agent.get(role) or {}).get('sensors') or [])[1:]
                own = {role: {'sensors': [group, *[s for s in others if s != group]]}}
            else:
                own = {'wingman': {'group': group}}
            changes = merge({k: v for k, v in changes.items() if k != 'group'}, own)
        if 'number' in changes:   # a wingman's number in the pilot's flight
            if agent.get('role') != 'wingman':
                raise ValueError('number')
            changes = merge({k: v for k, v in changes.items() if k != 'number'},
                            {'wingman': {'number': checked_number(changes['number'])}})
        if 'chatter' in changes:   # how often a wingman talks on its own in calm moments (wingman/desk.py)
            if agent.get('role') != 'wingman' or changes['chatter'] not in WINGMAN_CHATTER:
                raise ValueError('chatter')
            changes = merge({k: v for k, v in changes.items() if k != 'chatter'},
                            {'wingman': {'chatter': changes['chatter']}})
        new = merge(stored, changes)
        self.check_channel(agent_id, new)
        write_json(self.path(agent_id) / 'agent.json', new)
        self.reload()
        return self.get(agent_id)

    def check_channel(self, agent_id: str, agent: dict) -> None:
        if airfield_mode(agent):
            if agent.get('active') and any(o['id'] != agent_id and o.get('active') and airfield_mode(o)
                                           for o in self.all()):
                raise ValueError('frequency_taken')   # one agent answers for the airfields
            return
        low, high = FREQUENCY_RANGE_MHZ
        if not low <= float(agent['frequency_mhz']) <= high:
            raise ValueError('frequency_mhz')
        if agent['modulation'] not in MODULATIONS:
            raise ValueError('modulation')
        # Agents may share a frequency (2026-11-03): the pilot calls the one he wants by name (shared/dispatch.py).

    def mission(self, agent_id: str) -> str:
        path = self.path(agent_id) / 'mission.md'
        if path.exists():
            return path.read_text(encoding='utf-8')
        return self.original_mission(agent_id)

    def original_mission(self, agent_id: str) -> str:
        original = ROLE_MISSIONS.get((self.get(agent_id) or {}).get('role', ''))
        return original.read_text(encoding='utf-8') if original else ''

    def save_mission(self, agent_id: str, text: str) -> None:
        if self.get(agent_id) is None:
            raise ValueError('agent')
        if not text.strip():
            raise ValueError('mission')
        write_text(self.path(agent_id) / 'mission.md', text)

    def restore_mission(self, agent_id: str) -> str:
        text = self.original_mission(agent_id)
        self.save_mission(agent_id, text)
        return text


def write_json(path: Path, data: dict) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def write_text(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text, encoding='utf-8')
    os.replace(temp, path)
