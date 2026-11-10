"""The Triggers assistant (user's idea, 2026-11-02 night): in the GUI's Triggers tab the mission maker says in plain
words, in any language, what should happen; the model fills in that agent's event and action rows.

It only writes rows into the tab's draft (the rows the page sends, saved or not) and never saves: the maker reviews
them and presses "Save triggers" (the server then checks them as always). Its tools cannot touch the mission, DCS,
other agents or files. The same brain connection as the agents ("brain" settings); one conversation per agent while
the GUI runs ("New conversation" resets it).
"""
import json
import threading
from pathlib import Path

from instructor.brain import BrainError, create_brain
from instructor.tools import parameters
from shared import triggers
from shared.agents import GROUP_ROLES

INSTRUCTIONS = Path(__file__).resolve().parent / 'trigger_assistant.md'
NAMESPACE = 'triggers'
NAMESPACE_DESCRIPTION = "Change the rows of this agent's Triggers tab (not saved until the maker presses Save)."
MESSAGE_MAX = 2000
ROWS_MAX = 50
FLAG = {'type': 'string', 'description': 'Mission flag: a number or a name (letters, digits, _ or -)'}
INDEX = {'type': 'integer', 'description': 'Row number as shown, from 1'}
INTERNAL = {'type': 'boolean', 'description': 'true: an internal flag (agent to agent, nothing in the Mission Editor); '
                                             'false: a DCS mission flag'}


def required(properties: dict) -> dict:
    return parameters(properties, tuple(properties))


class DraftToolBox:
    """The assistant's tools over one draft {events, actions}; same interface as the agents' toolboxes."""
    definitions = [
        {'type': 'function', 'name': 'add_event',
         'description': 'Add an event row: when the flag turns on, the agent calls the pilot following the '
                        'instructions.',
         'parameters': required({'flag': FLAG, 'instructions': {
             'type': 'string', 'description': 'What the agent does, written to the agent'}, 'internal': INTERNAL})},
        {'type': 'function', 'name': 'add_condition_event',
         'description': 'Add an event row that fires when something happens to a squad (no flag, nothing in the '
                        'Mission Editor): the agent calls the pilot following the instructions.',
         'parameters': parameters({
             'when': {'type': 'string', 'enum': list(triggers.CONDITIONS),
                      'description': 'casualties: soldiers killed; enemies_seen: enemies come into sight; '
                                     'helicopter_seen: the pilot\'s helicopter comes into sight; zone_reached: the '
                                     'squad enters a mission zone; squad_lost: the squad is wiped out or the '
                                     'wingman shot down; damaged, fuel_low (bingo), out_of_weapons (winchester), '
                                     'airborne (took off): wingmen only'},
             'instructions': {'type': 'string', 'description': 'What the agent does, written to the agent'},
             'about': {'type': 'string', 'description': 'id of the squad agent it watches (from "squad_agents"); '
                                                        'leave out for this agent\'s own squad'},
             'zone': {'type': 'string', 'description': 'zone name, for zone_reached only'}},
             ('when', 'instructions'))},
        {'type': 'function', 'name': 'add_action',
         'description': 'Add an action row: a tool only this agent may use, which sets the flag to 1.',
         'parameters': required({'name': {'type': 'string', 'description': 'lowercase_words_with_underscores'},
                                   'flag': FLAG,
                                   'description': {'type': 'string', 'description': 'When the agent should use it'},
                                   'internal': INTERNAL})},
        {'type': 'function', 'name': 'remove_event', 'description': 'Remove an event row.',
         'parameters': required({'index': INDEX})},
        {'type': 'function', 'name': 'remove_action', 'description': 'Remove an action row.',
         'parameters': required({'index': INDEX})},
    ]

    def __init__(self, role: str):
        self.role = role
        self.reserved = triggers.role_tool_names(role)
        self.draft = {'events': [], 'actions': []}

    def tools(self) -> list[dict]:
        return [{'type': 'namespace', 'name': NAMESPACE, 'description': NAMESPACE_DESCRIPTION,
                 'tools': self.definitions}]

    def guide(self) -> str:
        return '\n'.join(f'- {d["name"]}: {d["description"]}' for d in self.definitions)

    def call(self, name: str, arguments: str) -> str:
        handler = {'add_event': self.add_event, 'add_condition_event': self.add_condition_event,
                   'add_action': self.add_action, 'remove_event': self.remove_event,
                   'remove_action': self.remove_action}.get(name.split('.')[-1])
        if not handler:
            return json.dumps({'error': f'unknown tool {name}'})
        try:
            return json.dumps(handler(**json.loads(arguments or '{}')), ensure_ascii=False)
        except Exception as exc:   # reported to the model
            return json.dumps({'error': f'{type(exc).__name__}: {exc}'})

    def rows(self) -> dict:
        return {'ok': True, 'events': self.draft['events'], 'actions': self.draft['actions']}

    def add_event(self, flag: str, instructions: str, internal: bool = False) -> dict:
        event = {'flag': str(flag).strip(), 'instructions': str(instructions).strip(), 'internal': internal is True}
        if not triggers.events({'role': self.role, 'events': [event]}):
            return {'ok': False, 'error': 'invalid: the flag must be letters, digits, _ or - and the instructions '
                                          'not empty'}
        if len(self.draft['events']) >= ROWS_MAX:
            return {'ok': False, 'error': 'too many rows'}
        self.draft['events'].append(event)
        return self.rows()

    def add_condition_event(self, when: str, instructions: str, about: str = '', zone: str = '') -> dict:
        found = triggers.events({'role': self.role, 'events': [{'when': when, 'instructions': instructions,
                                                                'about': about, 'zone': zone}]})
        if not found:
            return {'ok': False, 'error': f'invalid: when must be one of {", ".join(triggers.CONDITIONS)}, the '
                                          'instructions not empty, and zone_reached needs a zone'}
        if len(self.draft['events']) >= ROWS_MAX:
            return {'ok': False, 'error': 'too many rows'}
        self.draft['events'].append(condition_draft(found[0]))
        return self.rows()

    def add_action(self, name: str, flag: str, description: str, internal: bool = False) -> dict:
        action = {'name': str(name).strip(), 'flag': str(flag).strip(), 'description': str(description).strip(),
                  'internal': internal is True}
        if action['name'] in self.reserved:
            return {'ok': False, 'error': f'{action["name"]} is already one of the role\'s own tools'}
        if any(a.get('name') == action['name'] for a in self.draft['actions']):
            return {'ok': False, 'error': f'there is already an action called {action["name"]}'}
        if not triggers.actions({'role': self.role, 'actions': [action]}, self.reserved):
            return {'ok': False, 'error': 'invalid: the name must be lowercase_words (starting with a letter), the '
                                          'flag letters, digits, _ or -, and "when to use it" not empty'}
        if len(self.draft['actions']) >= ROWS_MAX:
            return {'ok': False, 'error': 'too many rows'}
        self.draft['actions'].append(action)
        return self.rows()

    def remove(self, kind: str, index: int) -> dict:
        rows = self.draft[kind]
        if not 1 <= int(index) <= len(rows):
            return {'ok': False, 'error': f'no row {index}: there are {len(rows)}'}
        rows.pop(int(index) - 1)
        return self.rows()

    def remove_event(self, index: int) -> dict:
        return self.remove('events', index)

    def remove_action(self, index: int) -> dict:
        return self.remove('actions', index)


def condition_draft(row: dict) -> dict:
    """A condition row as the page shows it: every field present ('' when unused)."""
    return {'when': str(row.get('when') or '')[:40], 'about': str(row.get('about') or '')[:40],
            'zone': str(row.get('zone') or '')[:triggers.ZONE_NAME_MAX],
            'instructions': str(row.get('instructions') or '')[:triggers.TEXT_MAX]}


def clean_draft(draft) -> dict:
    """The page's rows as they are (they may be unfinished): only the known fields, as text."""
    draft = draft if isinstance(draft, dict) else {}
    keys = {'events': ('flag', 'instructions'), 'actions': ('name', 'flag', 'description')}
    return {kind: [condition_draft(row) if 'when' in row else
                   {**{k: str(row.get(k) or '')[:triggers.TEXT_MAX] for k in fields},
                    'internal': row.get('internal') is True}
                   for row in (draft.get(kind) or [])[:ROWS_MAX] if isinstance(row, dict)]
            for kind, fields in keys.items()}


class TriggerAssistant:
    """One conversation per agent; ask() is called from the GUI's request threads."""

    def __init__(self, settings_reader, agents, brain_factory=create_brain):
        self.settings_reader, self.agents, self.brain_factory = settings_reader, agents, brain_factory
        self.sessions: dict[str, tuple] = {}   # agent id -> (brain, toolbox)
        self.lock = threading.Lock()

    def reset(self, agent_id: str) -> None:
        with self.lock:
            self.sessions.pop(agent_id, None)

    def session(self, agent: dict) -> tuple:
        with self.lock:
            if agent['id'] not in self.sessions:
                box = DraftToolBox(agent.get('role', ''))
                brain = self.brain_factory(self.settings_reader(), box, 'none (this is the GUI)',
                                           INSTRUCTIONS.read_text(encoding='utf-8'))
                self.sessions[agent['id']] = (brain, box)
            return self.sessions[agent['id']]

    def situation(self, agent: dict, draft: dict) -> dict:
        numbered = {kind: [{'row': i + 1, **row} for i, row in enumerate(rows)] for kind, rows in draft.items()}
        others = [{'agent': a['name'], 'events': triggers.events(a),
                   'actions': [{k: x[k] for k in ('name', 'flag', 'internal', 'description')}
                               for x in triggers.actions(a)]}
                  for a in self.agents.all() if a['id'] != agent['id'] and a.get('role') not in triggers.FIXED_ROLES]
        squads = [{'id': a['id'], 'name': a['name'], **({'this_agent': True} if a['id'] == agent['id'] else {})}
                  for a in self.agents.all() if a.get('role') in GROUP_ROLES]
        return {'agent': {'name': agent['name'], 'role': agent.get('role')},
                'role_tools': [{'name': d['name'], 'description': d['description']}
                               for d in triggers.role_tools(agent.get('role', ''))],
                **numbered, 'other_agents': others, 'squad_agents': squads}

    def ask(self, agent_id: str, message: str, draft) -> dict:
        """{reply, draft} or {error: kind}; the draft comes back changed only by the model's tools."""
        agent = self.agents.get(agent_id)
        if agent is None:
            raise ValueError('agent')
        if agent.get('role') in triggers.FIXED_ROLES:
            raise ValueError('fixed_agent')
        message = str(message or '').strip()
        if not message or len(message) > MESSAGE_MAX:
            raise ValueError('message')
        brain, box = self.session(agent)
        if hasattr(brain, 'agent_name'):
            brain.agent_name = agent['name']
        box.draft = clean_draft(draft)
        try:
            reply = brain.ask(message, self.situation(agent, box.draft), '')
        except BrainError as exc:
            return {'error': exc.kind, 'detail': str(exc), 'draft': box.draft}
        return {'reply': reply, 'draft': box.draft, 'tools': getattr(brain, 'last_tools', [])}
