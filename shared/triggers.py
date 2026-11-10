"""Triggers: the link between a mission's flags and the agents (user's design, 2026-11-02 night).

The mission maker's whole job in the editor is a zone and a trigger ("unit Paco in Zone 1 -> FLAG 10 ON"); the
agent's side lives in its agent.json, written by the GUI's Triggers tab:

  "events":  [{"flag": "10", "instructions": "If you see the helicopter, call the pilot and tell them where it is."}]
      When flag 10 turns on (0 -> anything else), the agent reads the instructions with its own situation (only what
      it can see) and makes the call in character; it never invents what it cannot see.
  "actions": [{"name": "smoke_ready", "description": "Use it when the pilot says the smoke is in sight.",
               "flag": "20", "value": 1}]
      A tool the model may use (namespace "mission"); it sets flag 20 and the mission's own triggers act on it.

Safety (same fixed-list rule as the task port): the hook reads only the flags some agent listens to and sets only
the flags some agent's actions list; an agent sets only its own. The ATC is fixed: no triggers.

Internal flags (user's idea, 2026-11-03): an event or action row marked "internal": true uses a flag that lives in
this program only (InternalFlags), never in DCS: one agent's action turns it on and another agent's event fires at
once, on any frequency, with no Mission Editor trigger. They start at 0 when the program starts. An internal flag and
a DCS flag of the same name are different flags.

Condition events (user's design, 2026-11-03): an event row with "when" instead of "flag" fires when something happens
to a squad, seen by our code in the mission data, with no Mission Editor trigger:
  {"when": "casualties" | "enemies_seen" | "helicopter_seen" | "zone_reached" | "squad_lost",
   "zone": "Z1" (zone_reached only), "about": "<squad agent id>" (missing: the agent itself), "instructions": "..."}
The agent gets what happened as the event's "cause" (e.g. "Tango's squad lost 2 soldiers (2 of 4 left)").
"""
import json
import re
import threading
import time

from client.dcs_tasks import FLAG_NAME
from instructor.geo import simplify
from instructor.tools import NO_ARGUMENTS

ACTION_NAME = re.compile(r'^[a-z][a-z0-9_]{0,39}$')   # a tool name the model sees
NAMESPACE = 'mission'
NAMESPACE_DESCRIPTION = "Actions in the mission's story that this agent may take."
FIXED_ROLES = ('atc',)
LISTS_EVERY_S = 5.0     # the hook forgets the lists when DCS restarts: say them again
FLAGS_STALE_S = 5.0
REPLY_WAIT_S = 3.0
TEXT_MAX = 1000
# Condition events (user's design, 2026-11-03): "when this happens" to a squad, seen by our code in the mission data
# (squad/desk.py facts), no flag and no model needed to notice it. Each fires on a change, never on the first reading.
CONDITIONS = ('casualties', 'enemies_seen', 'helicopter_seen', 'zone_reached', 'squad_lost',
              'damaged', 'fuel_low', 'out_of_weapons', 'airborne')   # the last four: wingmen (wingman/desk.py)
DAMAGE_STEP = 0.05            # "damaged" fires for a new hit of at least 5 % of the aircraft's life
REQUESTS_MAX = 5              # the pilot's "tell me when..." asks an agent keeps
REQUEST_TTL_S = 1800.0
CONDITION_COOLDOWN_S = 60.0   # a row fires again only after this (enemies or the helicopter flickering in and out)
ZONE_NAME_MAX = 60
AGENT_ID = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')   # as shared/agents.py


def events(agent: dict | None) -> list[dict]:
    """The agent's valid events, flag rows and condition rows ("when"); anything malformed is left out (the GUI
    checks before saving)."""
    if not agent or agent.get('role') in FIXED_ROLES:
        return []
    found = []
    for event in agent.get('events') or []:
        if not (isinstance(event, dict) and str(event.get('instructions') or '').strip()):
            continue
        instructions = str(event['instructions']).strip()[:TEXT_MAX]
        if 'when' in event:
            condition = condition_row(event, instructions)
            if condition:
                found.append(condition)
        elif FLAG_NAME.match(str(event.get('flag', ''))):
            found.append({'flag': str(event['flag']), 'instructions': instructions,
                          'internal': event.get('internal') is True})
    return found


def flag_events(agent: dict | None) -> list[dict]:
    return [e for e in events(agent) if 'flag' in e]


def condition_events(agent: dict | None) -> list[dict]:
    return [e for e in events(agent) if 'when' in e]


def condition_row(event: dict, instructions: str) -> dict | None:
    """{when, instructions, about?, zone?}: about is another squad agent's id ('' or missing: the agent itself)."""
    when, about, zone = event.get('when'), str(event.get('about') or ''), str(event.get('zone') or '').strip()
    if when not in CONDITIONS or (about and not AGENT_ID.match(about)):
        return None
    if when == 'zone_reached' and not (zone and len(zone) <= ZONE_NAME_MAX):
        return None
    row = {'when': when, 'instructions': instructions}
    if about:
        row['about'] = about
    if when == 'zone_reached':
        row['zone'] = zone
    return row


def actions(agent: dict | None, reserved: tuple = ()) -> list[dict]:
    """The agent's valid actions. reserved: its role's own tool names (an action cannot take one)."""
    if not agent or agent.get('role') in FIXED_ROLES:
        return []
    found, names = [], set()
    for action in agent.get('actions') or []:
        if not isinstance(action, dict):
            continue
        name, flag = str(action.get('name', '')), str(action.get('flag', ''))
        if not (ACTION_NAME.match(name) and FLAG_NAME.match(flag) and str(action.get('description') or '').strip()):
            continue
        if name in reserved or name in names:
            continue
        try:
            value = int(action.get('value', 1))
        except (TypeError, ValueError):
            continue
        names.add(name)
        found.append({'name': name, 'description': str(action['description']).strip()[:TEXT_MAX], 'flag': flag,
                      'value': value, 'internal': action.get('internal') is True})
    return found


def role_tools(role: str) -> list[dict]:
    """The tools a role already has (name, description): what the agent can do without any trigger."""
    if role == 'squad':
        from squad.tools import SquadToolBox
        return SquadToolBox.definitions
    if role == 'vehicles':
        from vehicles.tools import VehicleToolBox
        return VehicleToolBox.definitions
    if role == 'artillery':
        from artillery.tools import ArtilleryToolBox
        return ArtilleryToolBox.definitions
    if role == 'ships':
        from ships.tools import ShipToolBox
        return ShipToolBox.definitions
    if role == 'controller':
        from controller.tools import ControllerToolBox
        return ControllerToolBox.definitions
    if role == 'instructor':
        from instructor.tools import ToolBox
        return ToolBox.definitions
    if role == 'wingman':
        from wingman.tools import WingmanToolBox
        return WingmanToolBox.definitions
    return []


def role_tool_names(role: str) -> tuple:
    """The tool names a role already has: an action may not take one."""
    return tuple(d['name'] for d in role_tools(role))


def check(agent: dict, reserved: tuple = ()) -> None:
    """For the GUI before saving: ValueError names the first bad entry ("events.0", "actions.1")."""
    for kind in ('events', 'actions'):
        if not isinstance(agent.get(kind) or [], list):
            raise ValueError(kind)
        if agent.get(kind) and agent.get('role') in FIXED_ROLES:
            raise ValueError(kind)
    for i, entry in enumerate(agent.get('events') or []):
        if not events({'events': [entry]}):
            raise ValueError(f'events.{i}')
    names = set()
    for i, entry in enumerate(agent.get('actions') or []):
        if not actions({'actions': [entry]}, reserved) or entry['name'] in names:
            raise ValueError(f'actions.{i}')
        names.add(entry['name'])


class InternalFlags:
    """The internal flags: set by agents' actions, read by agents' events; DCS never sees them."""

    def __init__(self):
        self.lock = threading.Lock()
        self.values: dict[str, int] = {}
        self.turned_on: dict[str, str] = {}   # since the last drain(): flag -> what turned it on

    def set(self, name: str, value: int = 1, cause: str = '') -> None:
        with self.lock:
            was = self.values.get(name, 0)
            self.values[name] = value
            if not was and value:
                self.turned_on[name] = cause

    def drain(self) -> dict[str, str]:
        with self.lock:
            names, self.turned_on = self.turned_on, {}
            return names


class FlagWatch:
    """Keeps the hook's flag lists (every active agent's) and tells which events fired: a watched flag that went from
    0 to anything else. The first value seen of a DCS flag is only the baseline (a flag already on when the program
    starts or the hook comes back does not fire again). Internal flags fire on every 0 -> on (they start at 0)."""

    def __init__(self, agents, state, tasks, internal: InternalFlags | None = None):
        self.agents, self.state, self.tasks = agents, state, tasks
        self.internal = internal or InternalFlags()
        self.values: dict[str, float] = {}
        self.seen: dict | None = None   # the last flags message handled
        self.sent_lists = None
        self.next_lists = 0.0

    def lists(self) -> tuple[list[str], list[str]]:
        """The DCS flags only (internal ones never go to the hook)."""
        watched, settable = [], []
        for agent in self.agents.active():
            for event in flag_events(agent):
                if not event['internal'] and event['flag'] not in watched:
                    watched.append(event['flag'])
            for action in actions(agent):
                if not action['internal'] and action['flag'] not in settable:
                    settable.append(action['flag'])
        return watched, settable

    def tick(self) -> list[tuple[str, dict]]:
        """Called every couple of seconds; returns [(agent id, event)] that fired since the last call."""
        fired = self.fired(self.internal.drain(), internal=True)
        return fired + self.dcs_tick()

    def fired(self, turned_on: dict, internal: bool) -> list[tuple[str, dict]]:
        """turned_on: flag -> what turned it on (an internal flag's agent and action; '' for a DCS flag), given to the
        event as "cause" so the agent knows what happened (live 2026-11-03: Victor, told only "Tango confirmed",
        could not know it)."""
        return [(agent['id'], {**event, 'cause': turned_on[event['flag']]} if turned_on[event['flag']] else event)
                for agent in self.agents.active() for event in flag_events(agent)
                if event['internal'] == internal and event['flag'] in turned_on]

    def dcs_tick(self) -> list[tuple[str, dict]]:
        watched, settable = self.lists()
        now = time.monotonic()
        if self.tasks and (watched or settable or self.sent_lists) and \
                (now >= self.next_lists or (watched, settable) != self.sent_lists):
            self.next_lists = now + LISTS_EVERY_S
            try:
                self.tasks.flags(watched, settable)
                self.sent_lists = (watched, settable)
            except (OSError, ValueError):
                pass
        at, flags = self.state.flags_at, self.state.flags
        # A new message is a new dict (kept here, so its identity is never reused while compared).
        if at is None or flags is None or flags is self.seen or now - at > FLAGS_STALE_S:
            return []
        self.seen = flags
        turned_on = {name for name, value in flags.items()
                     if name in self.values and not self.values[name] and number(value)}
        self.values.update({name: number(value) for name, value in flags.items()})
        return self.fired(dict.fromkeys(turned_on, ''), internal=False)


class ConditionWatch:
    """The condition events: compares each squad's facts (desk.facts(): None while unknown or aboard) with the last
    ones and fires the rows whose condition just became true. desk_of(agent id) -> its desk or None."""

    def __init__(self, agents, desk_of):
        self.agents, self.desk_of = agents, desk_of
        self.last: dict[str, dict] = {}         # squad agent id -> its facts at the last tick
        self.fired_at: dict[tuple, float] = {}  # (agent id, row) -> when it last fired

    def tick(self) -> list[tuple[str, dict]]:
        """At most one event per agent per tick: conditions that come together (live 2026-11-03: Victor's "soldier
        killed" and "enemies seen" at once made two calls back to back) go out as one call."""
        rows = [(agent, event) for agent in self.agents.active() for event in condition_events(agent)]
        for agent in self.agents.active():   # the pilot's "tell me when..." (squad tool tell_pilot_when)
            desk = self.desk_of(agent['id'])
            rows += [(agent, request) for request in (desk.requests_due() if hasattr(desk, 'requests_due') else [])]
        if not rows:
            return []
        changes = {}
        for squad_id in {event.get('about') or agent['id'] for agent, event in rows}:
            desk = self.desk_of(squad_id)
            facts = desk.facts() if desk and hasattr(desk, 'facts') else None
            if facts is None:
                continue
            before, self.last[squad_id] = self.last.get(squad_id), facts
            if before is not None:
                changes[squad_id] = (before, facts)
        fired, now = {}, time.monotonic()
        for agent, event in rows:
            squad_id = event.get('about') or agent['id']
            if squad_id not in changes:
                continue
            cause = happened(event, *changes[squad_id])
            key = (agent['id'], json.dumps({k: event[k] for k in ('when', 'about', 'zone', 'instructions')
                                            if k in event}, sort_keys=True))
            if cause and now - self.fired_at.get(key, -CONDITION_COOLDOWN_S) >= CONDITION_COOLDOWN_S:
                self.fired_at[key] = now
                if 'request' in event:   # asked once, told once
                    self.desk_of(agent['id']).request_done(event)
                squad = self.agents.get(squad_id) or {}
                unit = getattr(self.desk_of(squad_id), 'UNIT', 'squad')   # "squad", or a wingman's "aircraft"
                whose = f'your {unit}' if squad_id == agent['id'] else f'{squad.get("name") or squad_id}\'s {unit}'
                fired.setdefault(agent['id'], []).append({**event, 'cause': f'{whose} {cause}'})
        return [(agent_id, merged(events)) for agent_id, events in fired.items()]


def merged(events: list[dict]) -> dict:
    """Several condition events of one agent as one call."""
    if len(events) == 1:
        return events[0]
    return {'when': '+'.join(e['when'] for e in events),
            'instructions': '\n'.join(f'- {e["instructions"]}' for e in events),
            'cause': '; '.join(e['cause'] for e in events)}


def happened(event: dict, before: dict, now: dict) -> str:
    """What just happened for a condition row, as words for the agent ('' = nothing)."""
    when = event['when']
    if when == 'squad_lost':
        return 'was wiped out' if now.get('lost') and not before.get('lost') else ''
    if now.get('lost'):
        return ''
    if when == 'casualties':
        lost = (before.get('alive') or 0) - (now.get('alive') or 0)
        members = now.get('members') or 'soldiers'   # a vehicles agent loses vehicles (vehicles/desk.py facts)
        return f'lost {lost} {members} ({now.get("alive")} of {now.get("size")} left)' if lost > 0 else ''
    if when == 'enemies_seen':
        return 'spotted enemies' if now.get('enemies') and not before.get('enemies') else ''
    if when == 'helicopter_seen':
        return 'has the helicopter in sight' if now.get('helicopter') and not before.get('helicopter') else ''
    if when == 'zone_reached':
        zone = simplify(event['zone'])
        inside = lambda facts: zone in (facts.get('zones') or ())
        return f'reached zone {event["zone"]}' if inside(now) and not inside(before) else ''
    if when == 'damaged':
        hit = (now.get('damage') or 0) - (before.get('damage') or 0)
        return f'was hit (damage now {round(now["damage"] * 100)} %)' if hit >= DAMAGE_STEP else ''
    if when == 'fuel_low':
        return f'is low on fuel ({round((now.get("fuel") or 0) * 100)} % left)' \
            if now.get('fuel_low') and not before.get('fuel_low') else ''
    if when == 'out_of_weapons':
        return 'has no weapons left' if now.get('out_of_weapons') and not before.get('out_of_weapons') else ''
    if when == 'airborne':
        return 'took off and is airborne' if now.get('airborne') and not before.get('airborne') else ''
    return ''


class PilotRequests:
    """The pilot's "tell me when..." for a desk (squad/desk.py, wingman/desk.py: tool tell_pilot_when): a condition
    row of this run only, for the agent's own unit, fired once by ConditionWatch. The desk has facts() and
    self.requests = []."""

    def tell_pilot_when(self, condition: str, request: str, zone: str = '') -> dict:
        rows = events({'events': [{'when': condition, 'zone': zone, 'instructions': str(request)}]})
        if not rows or condition == 'squad_lost':
            return {'ok': False, 'detail': 'unknown condition (or zone_reached without a zone)'}
        facts = self.facts() or {}
        already = {'helicopter_seen': facts.get('helicopter'), 'enemies_seen': facts.get('enemies'),
                   'fuel_low': facts.get('fuel_low'), 'out_of_weapons': facts.get('out_of_weapons'),
                   'airborne': facts.get('airborne'),
                   'zone_reached': simplify(zone) in (facts.get('zones') or ())}.get(condition)
        if already:
            return {'ok': False, 'detail': 'that is already true now: tell the pilot now'}
        now = time.monotonic()
        self.requests = [r for r in self.requests if r['until'] > now][-(REQUESTS_MAX - 1):]
        self.requests.append({**rows[0], 'request': str(request)[:200], 'until': now + REQUEST_TTL_S,
                              'instructions': f'The pilot asked you: "{str(request)[:200]}". It has happened now: '
                                              'call the pilot and tell them, with what you see.'})
        return {'ok': True, 'detail': 'you will be told when it happens'}

    def requests_due(self) -> list[dict]:
        now = time.monotonic()
        self.requests = [r for r in self.requests if r['until'] > now]
        return self.requests

    def request_done(self, row: dict) -> None:
        self.requests = [r for r in self.requests if r is not row]


def number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class TriggerToolBox:
    """An agent's role toolbox plus one tool per action of its own (read live from its agent.json, so a change in the
    GUI applies at once). Same interface as the role toolboxes."""

    def __init__(self, base, agents, agent_id: str, tasks, state, internal: InternalFlags | None = None):
        self.base, self.agents, self.agent_id, self.tasks, self.state = base, agents, agent_id, tasks, state
        self.internal = internal

    @property
    def remembered(self) -> tuple:
        """The tools whose calls the brain keeps in its history: the role's orders and every action."""
        return tuple(getattr(self.base, 'remembered', ())) + tuple(a['name'] for a in self.actions())

    def reserved(self) -> tuple:
        return tuple(d['name'] for d in getattr(self.base, 'definitions', []))

    def actions(self) -> list[dict]:
        return actions(self.agents.get(self.agent_id), self.reserved())

    def tools(self) -> list[dict]:
        tools = list(self.base.tools()) if self.base else []
        own = self.actions()
        if own:
            tools.append({'type': 'namespace', 'name': NAMESPACE, 'description': NAMESPACE_DESCRIPTION,
                          'tools': [{'type': 'function', 'name': a['name'],
                                     'description': a['description'] + ' Returns ok and what the mission answered.',
                                     'parameters': NO_ARGUMENTS} for a in own]})
        return tools

    def guide(self) -> str:
        lines = [self.base.guide()] if self.base else []
        lines += [f'- {a["name"]}: {a["description"]}' for a in self.actions()]
        return '\n'.join(line for line in lines if line)

    def call(self, name: str, arguments: str) -> str:
        prefix, _, leaf = name.rpartition('.')
        action = next((a for a in self.actions() if a['name'] == leaf), None) if prefix in ('', NAMESPACE) else None
        if action:
            return json.dumps(self.run(action), ensure_ascii=False)
        if self.base:
            return self.base.call(name, arguments)
        return json.dumps({'error': f'unknown tool {name}'})

    def run(self, action: dict) -> dict:
        if action['internal']:   # another agent's event reacts; DCS is not involved
            if self.internal is None:
                return {'ok': False, 'detail': 'internal flags are not available'}
            agent = self.agents.get(self.agent_id) or {}
            cause = f'{agent.get("name") or self.agent_id} used its action {action["name"]}, which it uses ' \
                    f'when: {action["description"]}'
            self.internal.set(action['flag'], action['value'], cause)
            return {'ok': True, 'detail': 'done'}
        if not self.tasks:
            return {'ok': False, 'detail': 'no connection to the mission'}
        try:
            request_id = self.tasks.set_flag(action['flag'], action['value'])
        except (OSError, ValueError) as exc:
            return {'ok': False, 'detail': str(exc)}
        deadline = time.monotonic() + REPLY_WAIT_S
        while time.monotonic() < deadline:
            reply = self.state.replies.get(request_id)
            if reply:
                ok = bool(reply.get('ok'))
                return {'ok': ok, 'detail': 'done' if ok else reply.get('detail', 'refused')}
            time.sleep(0.05)
        return {'ok': False, 'detail': 'the mission did not answer (is DCS running with our hook?)'}
