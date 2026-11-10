"""The internal net: agents send each other text messages (user's plan, 2026-11-07; the base for artillery calls for
fire, officers and the commander).

The net is not the radio: the pilot never hears it, no voice, no SRS, no radio range (a command net between the
mission's own units). Every active agent but the ATC (fixed) is on it. An agent sends with its tool send_message
(namespace "net"); the recipient gets the message as an event in its own turn (instructor/voice_loop.py
Responder.net_call):
- a message: the recipient may act with its tools and its answer goes back to the sender on the net (a reply), or it
  answers SILENT;
- a reply: the sender may call the pilot on the radio about it, or stay SILENT; to go on it sends a new message.
The net decides which it is: what comes from an agent that was asked something is its reply, however it was sent.
Realism: a message carries only the words its sender wrote; nobody learns anything else through the net.
Loops are cut by hops (each message answered or forwarded counts one more; MAX_HOPS) and a per-sender rate.
"""
import json
import threading
import time

from client.dictation import log
from instructor.tools import parameters
from shared.agents import FIXED_ROLES
from shared.dispatch import names, same_word
from shared.locale import normalize

NAMESPACE = 'net'
NAMESPACE_DESCRIPTION = 'The internal net: text messages with the other agents (the pilot does not hear it).'
TEXT_MAX = 400
MAX_HOPS = 6              # messages back and forth from one first message before the net refuses more
PER_MINUTE = 6            # messages one agent may send in 60 s
ANSWER_WINDOW_S = 600.0   # what comes back from an agent asked within this time is its reply
ROLE_WORDS = {'instructor': 'instructor', 'squad': 'infantry squad', 'vehicles': 'ground vehicles',
              'wingman': 'wingman aircraft', 'artillery': 'artillery battery', 'ships': 'ships',
              'controller': 'air controller (AWACS)'}
DEFINITION = {
    'type': 'function', 'name': 'send_message',
    'description': 'Send a text message to another agent on the internal net: to ask it for something, tell it '
                   'something it needs, or answer it. The pilot does not hear the net (tell the pilot yourself on the '
                   'radio when they should know). Write only what you know yourself; the other agent knows nothing '
                   'else. to: the agent\'s name; text: your message, short, in English. Its answer comes back to you '
                   'later on the net. Returns ok and what was done.',
    'parameters': parameters({'to': {'type': 'string'}, 'text': {'type': 'string'}}, ('to', 'text')),
}
# A call for fire (user, 2026-11-07: "who gives the artillery the point, and how"): the observer names what it sees and
# our code turns its own sighting into the point, attached to the message; the model never handles coordinates.
CALL_FOR_FIRE = {
    'type': 'function', 'name': 'call_for_fire',
    'description': 'Ask an artillery battery on the net to fire on a target you see: its exact point goes with the '
                   'message, from your own eyes. Corrections after the rounds fall are made by the battery '
                   '("last" with add / drop / left / right): tell it with send_message. Returns ok and what was '
                   'done.',
    'parameters': parameters({
        'battery': {'type': 'string', 'description': 'the artillery agent\'s name'},
        'target': {'type': 'string',
                   'description': 'an enemy group you see or saw lately (by its name in your situation), a smoke you '
                                  'see, a mission zone or map mark, "here" (your own position, with offset_m and '
                                  'toward), or coordinates the pilot gave'},
        'description': {'type': 'string', 'description': 'what the target is, in a few words (e.g. "three tanks in '
                                                         'the open"), in English'},
        'offset_m': {'type': 'number', 'description': 'move the point this many metres (0: none)'},
        'toward': {'type': 'string', 'description': 'which way offset_m goes: a compass direction'}},
        ('battery', 'target', 'description'))}
ARTILLERY_ROLES = ('artillery', 'ships')   # they take calls for fire
# A request for air support (user, 2026-11-08: "Axe sees enemies and asks Magic for air support"): like a call for
# fire, the unit names what it sees and our code attaches its own sighting as the point; the controller puts it on its
# task board (controller/desk.py receive_target) and later hands a pilot over to the unit.
REQUEST_AIR_SUPPORT = {
    'type': 'function', 'name': 'request_air_support',
    'description': 'Ask the air controller on the net for aircraft to attack a target you see: its point goes with '
                   'the request, from your own eyes, and you become the contact the pilot is sent to (he will call '
                   'you on your frequency). Returns ok and what was done.',
    'parameters': parameters({
        'controller': {'type': 'string', 'description': 'the air controller\'s name'},
        'target': CALL_FOR_FIRE['parameters']['properties']['target'],
        'description': {'type': 'string', 'description': 'what the target is and what you need, in a few words '
                                                         '(e.g. "four tanks advancing on us"), in English'},
        'urgent': {'type': 'boolean', 'description': 'true when you are under attack or in danger'}},
        ('controller', 'target', 'description'))}
CONTROLLER_ROLES = ('controller',)


def on_net(agent: dict | None) -> bool:
    return bool(agent and agent.get('active') and agent.get('role') not in FIXED_ROLES)


class Net:
    """deliver(agent id, event) hands a message to its recipient (voice_loop: the Router's Responder.event);
    on_message(sender name, recipient name, text, kind) shows it (the GUI's Status, the black box)."""

    def __init__(self, agents, deliver=None, on_message=None):
        self.agents, self.deliver, self.on_message = agents, deliver, on_message
        self.lock = threading.Lock()
        self.sent_at: dict[str, list[float]] = {}   # sender id -> times of its last messages
        self.asked: dict[tuple[str, str], float] = {}   # (sender id, recipient id) -> when, until it is answered

    def members(self, exclude: str = '') -> list[dict]:
        return [a for a in self.agents.active() if on_net(a) and a['id'] != exclude]

    def find(self, to: str, exclude: str = '') -> dict | None:
        """The agent called `to`: its id, its whole name or an alias, a little off allowed (shared/dispatch.py)."""
        members = self.members(exclude)
        wanted = normalize(str(to or '')).split()
        for agent in members:
            if agent['id'] == str(to).strip().lower():
                return agent
        for agent in members:
            for name in names(agent):
                if len(name) == len(wanted) and all(same_word(h, w) for h, w in zip(wanted, name)):
                    return agent
        return None

    def send(self, sender_id: str, to: str, text: str, hops: int = 0, target: dict | None = None,
             notice: bool = False) -> dict:
        """What an agent sends to one that asked it something (within ANSWER_WINDOW_S) is its reply, whichever way it
        was sent (live 2026-11-07: Tango answered Bison with send_message, and Bison's call to the pilot went back
        to Tango on the net); anything else is a message. notice: a report made by our code (a battery's "shot"),
        answered like a reply (to the pilot or SILENT), never back on the net. target: {lat, lon, what, observer}
        attached to a call for fire."""
        sender = self.agents.get(sender_id)
        text = ' '.join(str(text or '').split())[:TEXT_MAX]
        if not on_net(sender):
            return {'ok': False, 'detail': 'you are not on the internal net'}
        if not text:
            return {'ok': False, 'detail': 'empty message'}
        recipient = self.find(to, exclude=sender_id)
        if recipient is None:
            members = ', '.join(a['name'] for a in self.members(sender_id)) or 'nobody'
            return {'ok': False, 'detail': f'nobody called {to} on the net (on the net now: {members})'}
        if hops > MAX_HOPS:
            log({'event': 'net_refused', 'from': sender_id, 'to': recipient['id'], 'hops': hops, 'text': text})
            return {'ok': False, 'detail': 'too many messages back and forth: decide yourself now'}
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.sent_at.get(sender_id, []) if now - t < 60.0]
            if len(recent) >= PER_MINUTE:
                return {'ok': False, 'detail': 'too many messages this minute: wait for the answers'}
            self.sent_at[sender_id] = recent + [now]
            if notice:
                kind = 'notice'
            else:
                asked_at = self.asked.pop((recipient['id'], sender_id), None)
                kind = 'reply' if asked_at is not None and now - asked_at <= ANSWER_WINDOW_S else 'message'
            if kind == 'message':
                self.asked[(sender_id, recipient['id'])] = now
        log({'event': 'net', 'from': sender_id, 'to': recipient['id'], 'kind': kind, 'hops': hops, 'text': text,
             **({'target': target} if target else {})})
        print(f'NET {sender["name"]} -> {recipient["name"]}: {text}')
        if self.on_message:
            self.on_message(sender['name'], recipient['name'], text, kind)
        if self.deliver:
            event = {'net': kind, 'from': sender_id, 'from_name': sender['name'], 'text': text, 'hops': hops}
            if target:
                event['target'] = target
            self.deliver(recipient['id'], event)
        if kind != 'message':
            return {'ok': True, 'detail': f'your answer was sent to {recipient["name"]}'}
        return {'ok': True, 'detail': f'sent to {recipient["name"]}; its answer comes to you on the net'}


class NetToolBox:
    """The role's toolbox (base) plus send_message, and call_for_fire for an agent with eyes (its desk has
    target_point: squads, vehicles, wingmen) while a battery is on the net. Same interface as the role toolboxes.
    hops: set by the Responder while it answers a net message, so what it sends from there counts one more."""

    def __init__(self, base, net: Net, agent_id: str, desk=None):
        self.base, self.net, self.agent_id = base, net, agent_id
        self.desk = desk if hasattr(desk, 'target_point') else None
        self.hops = 0

    def __getattr__(self, name):   # the base's own attributes (playbook, desk, apply...)
        return getattr(self.base, name)

    @property
    def remembered(self) -> tuple:
        return tuple(getattr(self.base, 'remembered', ())) + (DEFINITION['name'], CALL_FOR_FIRE['name'],
                                                              REQUEST_AIR_SUPPORT['name'])

    def batteries(self) -> list[dict]:
        return [a for a in self.net.members(self.agent_id) if a.get('role') in ARTILLERY_ROLES] if self.desk else []

    def controllers(self) -> list[dict]:
        return [a for a in self.net.members(self.agent_id) if a.get('role') in CONTROLLER_ROLES] if self.desk else []

    def definitions(self) -> list[dict]:
        """The tools with who is on the net now (agents come and go in the GUI)."""
        members = ', '.join(f'{a["name"]} ({ROLE_WORDS.get(a.get("role"), a.get("role"))})'
                            for a in self.net.members(self.agent_id)) or 'nobody'
        found = [{**DEFINITION, 'description': f'{DEFINITION["description"]} On the net now: {members}.'}]
        batteries = ', '.join(a['name'] for a in self.batteries())
        if batteries:
            found.append({**CALL_FOR_FIRE, 'description': f'{CALL_FOR_FIRE["description"]} Batteries: {batteries}.'})
        controllers = ', '.join(a['name'] for a in self.controllers())
        if controllers:
            found.append({**REQUEST_AIR_SUPPORT,
                          'description': f'{REQUEST_AIR_SUPPORT["description"]} Air controllers: {controllers}.'})
        return found

    def tools(self) -> list[dict]:
        tools = list(self.base.tools()) if self.base else []
        return tools + [{'type': 'namespace', 'name': NAMESPACE, 'description': NAMESPACE_DESCRIPTION,
                         'tools': self.definitions()}]

    def guide(self) -> str:
        lines = [self.base.guide()] if self.base else []
        lines += [f'- {d["name"]}: {d["description"]}' for d in self.definitions()]
        return '\n'.join(line for line in lines if line)

    def call(self, name: str, arguments: str) -> str:
        prefix, _, leaf = name.rpartition('.')
        if leaf in (DEFINITION['name'], CALL_FOR_FIRE['name'], REQUEST_AIR_SUPPORT['name']) and \
                prefix in ('', NAMESPACE):
            try:
                args = json.loads(arguments or '{}')
                if leaf == CALL_FOR_FIRE['name']:
                    result = self.call_for_fire(**args)
                elif leaf == REQUEST_AIR_SUPPORT['name']:
                    result = self.request_air_support(**args)
                else:
                    result = self.net.send(self.agent_id, args.get('to', ''), args.get('text', ''), hops=self.hops)
            except (TypeError, ValueError, AttributeError) as exc:
                result = {'error': f'{type(exc).__name__}: {exc}'}
            return json.dumps(result, ensure_ascii=False)
        if self.base:
            return self.base.call(name, arguments)
        return json.dumps({'error': f'unknown tool {name}'})

    def call_for_fire(self, battery: str, target: str, description: str, offset_m: float = 0,
                      toward: str = '') -> dict:
        batteries = self.batteries()
        if not batteries:
            return {'ok': False, 'detail': 'no artillery battery on the net'}
        recipient = self.net.find(battery, exclude=self.agent_id)
        if recipient is None or recipient not in batteries:
            return {'ok': False, 'detail': f'no battery called {battery} (batteries: '
                                           f'{", ".join(a["name"] for a in batteries)})'}
        found = self.desk.target_point(target, float(offset_m or 0), toward or '')
        if isinstance(found, str):
            return {'ok': False, 'detail': found}
        point, observer = found
        what = ' '.join(str(description or target).split())[:120]
        text = f'Fire mission: {what} (target point attached).'
        result = self.net.send(self.agent_id, recipient['id'], text, hops=self.hops,
                               target={'lat': point[0], 'lon': point[1], 'what': what, 'observer': observer})
        return result

    def request_air_support(self, controller: str, target: str, description: str, urgent: bool = False,
                            offset_m: float = 0, toward: str = '') -> dict:
        controllers = self.controllers()
        if not controllers:
            return {'ok': False, 'detail': 'no air controller on the net'}
        recipient = self.net.find(controller, exclude=self.agent_id)
        if recipient is None or recipient not in controllers:
            return {'ok': False, 'detail': f'no air controller called {controller} (controllers: '
                                           f'{", ".join(a["name"] for a in controllers)})'}
        found = self.desk.target_point(target, float(offset_m or 0), toward or '')
        if isinstance(found, str):
            return {'ok': False, 'detail': found}
        point, observer = found
        what = ' '.join(str(description or target).split())[:120]
        text = f'Request air support{" (urgent)" if urgent else ""}: {what} (target point attached).'
        return self.net.send(self.agent_id, recipient['id'], text, hops=self.hops,
                             target={'lat': point[0], 'lon': point[1], 'what': what, 'observer': observer,
                                     'kind': 'air_support', 'urgent': bool(urgent)})
