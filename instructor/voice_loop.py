"""Voice loop: SRS + push-to-talk dictation + cockpit data + brain; the instructor answers on the radio.

Used by DCS-Instructor.bat (no console: tools/launcher.py) and utilities/DICTATION_TEST.cmd (console). With a brain configured
("brain.provider", e.g. chatgpt) the pilot's words go to the model with the situation (tower_view.py: who, which tower, phase; the
tower's view is a tool) and the answer is spoken; the black box records everything; with "none" (or repeat=True) the instructor repeats what it understood, as a dictation check. With
"stop_when_window_closed", the loop ends once the Chrome dictation window has been closed (no page connected for
WINDOW_GONE_S).
"""
import json
import queue
import threading
import time
from pathlib import Path

from artillery.desk import ArtilleryDesk
from artillery.tools import ArtilleryToolBox
from atc.desk import Desk
from atc.tools import AtcToolBox
from client.dictation import create_dictation, log
from controller.desk import ControllerDesk
from controller.tools import ControllerToolBox
from ships.desk import ShipDesk
from ships.tools import ShipToolBox
from squad.desk import SquadDesk
from squad.tools import SquadToolBox
from vehicles.desk import VehicleDesk
from vehicles.tools import VehicleToolBox
from wingman.desk import WingmanDesk
from wingman.memory import HISTORY_TURNS as WINGMAN_HISTORY_TURNS, Memory, brain_appendix
from wingman.tools import WingmanToolBox
from gui.status import Live
from instructor.blackbox import BlackBox
from instructor.brain import BRAIN_ERROR_TEXT, BrainError, NullBrain, create_brain
from instructor.tools import ToolBox
from instructor.tower_view import TowerView
from shared.agents import GROUP_ROLES, SENSOR_ROLES, AgentStore, channel_label
from shared.cockpit import CockpitState, CockpitStream
from shared.dispatch import Dispatcher
from shared.locale import load_locale
from shared.net import Net, NetToolBox
from shared.radio_change import RadioToolBox
from shared.triggers import ConditionWatch, FlagWatch, InternalFlags, TriggerToolBox
from shared.radio import describe, selected_radio, transmit_radio

ROOT = Path(__file__).resolve().parent.parent
WINDOW_GONE_S = 1.0     # on top of up to KEEPALIVE_S for the server to notice (client/chrome_dictation.py)
CLOSE_DELIVERY_S = 0.5  # time for the "close" event to reach the windows still open before the process ends
GUI_WINDOW_SIZE = (1280, 860)
# Radio panel arguments logged with every radio change (diagnostic). F-5E AN/ARC-164, names from controls.json:
# 300 preset channel, 307 frequency mode MANUAL/PRESET/GUARD, 311 function OFF/MAIN/BOTH/ADF, manual frequency knobs
# 327 (100 MHz; SRS reads its "A" position as HAVE QUICK), 328 (10), 329 (1), 330 (0.1), 331 (0.025 MHz).
RADIO_ARGS = {'F-5E-3': (300, 307, 311, 327, 328, 329, 330, 331)}
NO_TRANSMISSION = '(no transmission from the pilot)'   # history marker before a call the agent made on its own
OWN_CALLS_EVERY_S = 2.0   # how often agents that call on their own (the ATC) look at the situation
# A mission event (shared/triggers.py): the model makes the call from the mission maker's instructions.
EVENT_PROMPT = ('(Not the pilot speaking: a mission event you were told to react to.) Your instructions for it: '
                '{instructions}\nMake that call to the pilot on the radio now, in character, from what your situation '
                'shows. Never invent what you cannot see; if the instructions depend on something you cannot see, say '
                'so plainly. Speak the language the pilot has been speaking to you; if they have not spoken yet, '
                'language code "{language}".')
EVENT_CAUSE = 'What turned this event on (it really happened: you may rely on it):'
# A wingman's callouts and quiet time (wingman/desk.py reports(), rules in wingman/rules.md).
REPORT_PROMPT = ('(Not the leader speaking: a report. This just happened to you, it is real:) {report}\nCall it to '
                 'the leader on the radio now, short and in character, in your own words as a real crew would say '
                 'it, not a status readout; do not repeat what was just said on this frequency. Speak the language '
                 'the leader has been speaking to you; if they have not spoken yet, language code "{language}".')
QUIET_PROMPT = ('(Not the leader speaking: quiet time. {report}.) Say something of your own about the flight, the '
                'mission or the game, in character, or answer exactly SILENT to say nothing. Speak the language the '
                'leader has been speaking to you; if they have not spoken yet, language code "{language}".')
FIX_PROMPT = ('(Not the leader speaking: a problem with your aircraft, for you alone; the leader must not hear of '
              'it.) {report}\nFix it now with your tools: give your current order again, or another one that gets '
              'you where the leader wants you (fly_to, orbit, follow). Then answer exactly SILENT.')
# The internal net (shared/net.py): a message is answered back on the net; a reply may become a call to the pilot.
NET_PROMPT = ('(Not the pilot speaking: a text message to you on the internal net. The pilot does not hear the net.) '
              '{sender}: {text}\nDo what it asks with your tools if you can and your role should; never invent what '
              'you cannot see. Then answer {sender} with your words here (not with send_message): they go back to '
              '{sender} on the net, not on the radio; short, in English, what you did, what you see or why not. Answer '
              'exactly SILENT if no answer is needed.')
NET_REPLY_PROMPT = ('(Not the pilot speaking: {sender} answered you on the internal net. The pilot does not hear the '
                    'net.) {sender}: {text}\nIf the pilot should know this now, call them on the radio with your words '
                    'here, short and in your own words. Do not acknowledge on the net; use send_message only to ask '
                    '{sender} something more. Otherwise answer exactly SILENT. Speak the language the pilot has been '
                    'speaking to you; if they have not spoken yet, language code "{language}".')
NET_TARGET = ('{sender} attached the target point from its own sighting: it is target {target} in your situation '
              '(targets_from_observers); fire_mission with target "{target}" fires there.')
NET_TARGET_QUEUED = ('{sender} attached the target point from its own sighting: target {target}. Your battery is busy, '
                     'so it is already queued as number {position} (fire_queue in your situation) and will be fired '
                     'by itself after the current mission, with the same rounds and spread: do not call fire_mission '
                     'for it; tell {sender} it is queued.')
SILENT = 'SILENT'
EVENT_WAIT_PTT_S = 20.0
EVENT_HOLD_S = 600.0      # an event call out of radio range waits this long for the pilot to come back in range
EVENT_HOLD_POLL_S = 2.0   # an event call waits while the pilot transmits, at most this long


def load_settings() -> dict:
    return json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))


class Responder:
    """Answers the pilot one phrase at a time, in order, off the dictation thread (a model call takes seconds)."""

    def __init__(self, brain, locale: dict, language: str, state: CockpitState, speak, show, blackbox=None,
                 situation=dict, live=None, name: str = 'INSTRUCTOR', role: str = 'instructor', prefilter=None):
        """situation(): sent with each question (TowerView.situation, atc Desk.situation). live: the GUI's Live, shows
        the exchanges. name: the agent answering (logs and the GUI); role: who speaks in the black box.
        prefilter(text): before the brain; '' = nobody answers (e.g. no airfield on that frequency), a text = that
        fixed reply, None = the brain answers."""
        self.brain, self.locale, self.language, self.state = brain, locale, language, state
        self.speak, self.show, self.blackbox, self.situation = speak, show, blackbox, situation
        self.live, self.name, self.role, self.prefilter = live, name, role, prefilter
        self.answering = False
        self.last_activity = time.monotonic()   # the pilot spoke to it or it transmitted (a wingman's quiet time)
        self.desk = None   # the role's desk (atc/desk.py, squad/desk.py), set by run()
        self.on_air = None   # reply -> None after each transmission: the Router tells the others on the frequency
        self.agent_id, self.net, self.net_box = '', None, None   # the internal net (shared/net.py), set by run()
        self.phrases: queue.Queue = queue.Queue()   # the pilot's phrases ((text, record)) and mission events (dict)
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def busy(self) -> bool:
        """The pilot's words are waiting or being answered: the agent does not call on its own now."""
        return self.answering or not self.phrases.empty()

    def heard(self, text: str, record: bool = True) -> None:
        """record=False: the same phrase was already shown and recorded for another agent (a call to all stations)."""
        if record:
            print(f'PILOT: {text}')
        self.phrases.put((text, record))

    def event(self, event: dict) -> None:
        """A mission event for this agent (shared/triggers.py): answered in turn with the pilot's phrases."""
        if 'net' in event:
            pass   # the net printed it already
        elif 'report' in event:
            print(f'{"QUIET" if event.get("quiet") else "REPORT"} for {self.name}: {event["report"]}')
        elif 'when' in event:
            print(f'EVENT for {self.name}: {event.get("cause") or event["when"]}')
        else:
            print(f'EVENT for {self.name}: {"internal " if event.get("internal") else ""}flag {event.get("flag")}')
        self.phrases.put(event)

    def _run(self) -> None:
        while True:
            item = self.phrases.get()
            self.answering = True
            try:
                if isinstance(item, dict) and 'net' in item:
                    self.net_call(item)
                elif isinstance(item, dict):
                    self.event_call(item)
                else:
                    self.answer(*item)
            except Exception as exc:   # one failed answer must not end this agent's thread
                log({'event': 'error', 'where': 'responder', 'agent': self.name, 'error': f'{type(exc).__name__}: {exc}'})
                print(f'[{self.name}] failed: {exc}')
            finally:
                self.answering = False

    def event_call(self, event: dict) -> str:
        """The agent calls the pilot because a mission flag turned on: the model phrases the mission maker's
        instructions in character, from its own situation. Never while the pilot transmits."""
        silent = getattr(self.desk, 'silent', None)
        if silent and silent(event):   # a wingman shot down: no calls from the beyond (only its last one)
            log({'event': 'silent', 'agent': self.name, 'report': event.get('report'), 'when': event.get('when')})
            return ''
        deadline = time.monotonic() + EVENT_WAIT_PTT_S
        while (self.state.radio or {}).get('ptt') and time.monotonic() < deadline:
            time.sleep(0.2)
        reach = getattr(self.desk, 'can_reach_pilot', None)
        if reach and not reach():   # out of radio range (a squad): the call waits until the pilot can be reached
            self.hold(event)
            return ''
        instructions = event.get('instructions', event.get('report', ''))
        if isinstance(self.brain, NullBrain):
            if event.get('quiet') or event.get('fix'):
                return ''
            reply, seconds = instructions, None   # no model: read the instructions out, as a check
        else:
            context = self.situation()
            try:
                if event.get('fix'):
                    prompt = FIX_PROMPT.format(report=event['report'])
                elif 'report' in event:
                    prompt = (QUIET_PROMPT if event.get('quiet') else REPORT_PROMPT).format(
                        report=event['report'], language=self.language)
                else:
                    prompt = EVENT_PROMPT.format(instructions=instructions, language=self.language)
                if event.get('cause'):   # an internal flag: which agent's action turned it on
                    prompt = f'{prompt}\n{EVENT_CAUSE} {event["cause"]}'
                reply = self.brain.ask(prompt, context, self.language)
            except BrainError as exc:
                log({'event': 'brain_error', 'provider': self.brain.name, 'mission_event': event, 'kind': exc.kind,
                     'code': exc.code, 'error': str(exc)})
                print(f'[brain error] {exc}')
                return ''
            seconds = getattr(self.brain, 'last_seconds', 0.0)
            log({'event': 'report' if 'report' in event else 'mission_event', 'agent': self.name,
                 'flag': event.get('flag'), 'when': event.get('when'), 'cause': event.get('cause', ''),
                 'report': event.get('report'), 'quiet': event.get('quiet', False), 'answer': reply,
                 'seconds': round(seconds, 2), 'tools': getattr(self.brain, 'last_tools', []),
                 'calls': getattr(self.brain, 'last_calls', []), 'context': context})
            # Quiet time: it chose to say nothing. A fix is never transmitted, whatever it answered.
            if reply.strip().strip('.').upper() == SILENT or event.get('fix'):
                print(f'{self.name.upper()}: (stays silent)')
                return ''
        self.transmit(reply, seconds)
        return reply

    def hold(self, event: dict) -> None:
        """Keep an event call while the pilot is out of radio range; make it once back in range (EVENT_HOLD_S)."""
        log({'event': 'event_held', 'agent': self.name, 'flag': event.get('flag'), 'when': event.get('when'),
             'cause': event.get('cause', '')})
        print(f'[{self.name}] out of radio range: the call waits')

        def wait():
            deadline = time.monotonic() + EVENT_HOLD_S
            while time.monotonic() < deadline:
                time.sleep(EVENT_HOLD_POLL_S)
                if self.desk.can_reach_pilot():
                    self.phrases.put(event)
                    return
            log({'event': 'event_dropped', 'agent': self.name, 'flag': event.get('flag'), 'when': event.get('when')})

        threading.Thread(target=wait, daemon=True).start()

    def net_call(self, event: dict) -> str:
        """A message or a reply on the internal net (shared/net.py). A message's answer goes back to its sender on the
        net; a reply's answer, unless SILENT, is a call to the pilot on the radio. A killed agent answers nothing."""
        silent = getattr(self.desk, 'silent', None)
        if (silent and silent(event)) or isinstance(self.brain, NullBrain):
            log({'event': 'net_unanswered', 'agent': self.name, 'from': event.get('from'), 'text': event.get('text')})
            return ''
        message = event['net'] == 'message'   # a reply or a notice (a battery's "shot") may go to the pilot
        prompt = (NET_PROMPT if message else NET_REPLY_PROMPT).format(
            sender=event.get('from_name', '?'), text=event.get('text', ''), language=self.language)
        receive = getattr(self.desk, 'receive_target', None)
        target_id = receive(event) if receive and event.get('target') else None
        if target_id and hasattr(self.desk, 'target_words'):   # a request for air support: on the controller's board
            prompt += f'\n{self.desk.target_words(event, target_id)}'
        elif target_id:   # a call for fire: the observer's point, kept by the battery's desk (queued when it is busy)
            position = self.desk.queued_position(target_id) if hasattr(self.desk, 'queued_position') else None
            words = NET_TARGET_QUEUED if position else NET_TARGET
            prompt += f'\n{words.format(sender=event.get("from_name", "?"), target=target_id, position=position)}'
        context = self.situation()
        if self.net_box is not None:
            self.net_box.hops = event.get('hops', 0) + 1   # what it sends from here goes on this exchange
        try:
            reply = self.brain.ask(prompt, context, self.language)
        except BrainError as exc:
            log({'event': 'brain_error', 'provider': self.brain.name, 'net': event, 'kind': exc.kind,
                 'code': exc.code, 'error': str(exc)})
            print(f'[brain error] {exc}')
            return ''
        finally:
            if self.net_box is not None:
                self.net_box.hops = 0
        seconds = getattr(self.brain, 'last_seconds', 0.0)
        log({'event': 'net_answer', 'agent': self.name, 'from': event.get('from'), 'kind': event['net'],
             'text': event.get('text'), 'answer': reply, 'seconds': round(seconds, 2),
             'tools': getattr(self.brain, 'last_tools', []), 'calls': getattr(self.brain, 'last_calls', []),
             'context': context})
        if reply.strip().strip('.').upper() == SILENT:
            print(f'{self.name.upper()}: (no answer on the net)' if message else f'{self.name.upper()}: (stays silent)')
            return ''
        if message:
            if self.net:
                self.net.send(self.agent_id, event.get('from', ''), reply, hops=event.get('hops', 0) + 1)
            return reply
        deadline = time.monotonic() + EVENT_WAIT_PTT_S
        while (self.state.radio or {}).get('ptt') and time.monotonic() < deadline:
            time.sleep(0.2)
        reach = getattr(self.desk, 'can_reach_pilot', None)
        if reach and not reach():
            log({'event': 'reply_not_heard', 'agent': self.name, 'net': event, 'answer': reply})
            print(f'{self.name.upper()} (out of the pilot\'s range): {reply}')
            return reply
        self.transmit(reply, seconds)
        return reply

    def answer(self, text: str, record: bool = True) -> str:
        self.last_activity = time.monotonic()
        if self.blackbox and record:
            self.blackbox.said('pilot', text, self.state.time)
        if self.live and record:
            self.live.said('pilot', text)
        seconds = None
        fixed = self.prefilter(text) if self.prefilter else None
        if fixed == '':
            log({'event': 'unanswered', 'agent': self.name, 'question': text, 'context': self.situation()})
            print(f'{self.name.upper()}: (nobody answers)')
            return ''
        if fixed:
            reply = fixed
            self.brain.remember(text, reply)
            log({'event': 'fixed_reply', 'agent': self.name, 'question': text, 'answer': reply})
        elif isinstance(self.brain, NullBrain):
            reply = text   # no model: repeat what was understood
        else:
            context = self.situation()
            try:
                reply = self.brain.ask(text, context, self.language)
                seconds = getattr(self.brain, 'last_seconds', 0.0)
                tools = getattr(self.brain, 'last_tools', [])
                log({'event': 'brain', 'agent': self.name, 'provider': self.brain.name, 'model': getattr(self.brain, 'model', ''),
                     'question': text, 'answer': reply, 'seconds': round(seconds, 2), 'tools': tools,
                     'calls': getattr(self.brain, 'last_calls', []), 'context': context})
                print(f'[brain {self.brain.name} {seconds:.1f} s{", tools: " + ", ".join(tools) if tools else ""}]')
            except BrainError as exc:
                log({'event': 'brain_error', 'provider': self.brain.name, 'question': text, 'kind': exc.kind,
                     'code': exc.code, 'error': str(exc)})
                print(f'[brain error] {exc}')
                reply = self.locale[BRAIN_ERROR_TEXT.get(exc.kind, 'brain_error')]
        reach = getattr(self.desk, 'can_reach_pilot', None)
        if reach and not reach():
            # It heard the pilot (the aircraft's radio reaches farther) and acted, but its own radio does not reach
            # back: the pilot hears nothing (user's design, 2026-11-03).
            log({'event': 'reply_not_heard', 'agent': self.name, 'question': text, 'answer': reply})
            print(f'{self.name.upper()} (out of the pilot\'s range): {reply}')
            return reply
        self.transmit(reply, seconds)
        return reply

    def transmit(self, reply: str, seconds: float | None = None) -> None:
        self.last_activity = time.monotonic()
        print(f'{self.name.upper()}: {reply}')
        if self.blackbox:
            self.blackbox.said(self.role, reply, self.state.time)
        if self.live:
            self.live.said('agent', reply, seconds, self.name)
        self.show(reply)
        self.speak(reply)
        if self.on_air:
            self.on_air(reply)

    def call(self, text: str) -> None:
        """A call the agent makes on its own (the ATC's tower calls), kept in its brain's history."""
        self.brain.remember(NO_TRANSMISSION, text)
        log({'event': 'own_call', 'agent': self.name, 'answer': text})
        self.transmit(text)


class Router:
    """One Responder per agent (shared/agents.py), each with its own brain (mission, radio, history); a phrase goes to
    the agent on whose frequency the pilot spoke. Several agents may share a frequency: the one the pilot names answers
    (shared/dispatch.py) and the others hear it all. Agents are read live, so the GUI's changes apply at once."""

    def __init__(self, agents, make_responder, dispatcher: Dispatcher | None = None):
        self.agents, self.make_responder = agents, make_responder
        self.dispatcher = dispatcher or Dispatcher()
        self.responders: dict[str, Responder] = {}
        self.lock = threading.Lock()
        self.forget = None   # agent_id -> None: drops what run() keeps for a deleted agent

    def heard(self, text: str, agent: dict | None = None) -> None:
        agent_id = (agent or {}).get('id') or (self.agents.default() or {}).get('id')
        agent = self.agents.get(agent_id) if agent_id else None
        if agent is None:
            print(f'PILOT (no agent): {text}')
            return
        mates = self.agents.on_channel(agent)
        chosen, how = self.dispatcher.route(text, mates)
        if len(mates) > 1:
            log({'event': 'dispatch', 'heard': text, 'channel': [a['id'] for a in mates],
                 'to': [a['id'] for a in chosen], 'how': how})
        if not chosen:
            print(f'PILOT (nobody named on {channel_label(agent)}): {text}')
        for number, callee in enumerate(chosen):
            self.responder(callee['id']).heard(text, record=number == 0)
        if len(chosen) == 1:
            self.dispatcher.talked(mates, chosen[0]['id'])
        called = ', '.join(a['name'] for a in chosen) or 'nobody named'
        for mate in mates:
            if mate not in chosen:
                self.responder(mate['id']).brain.overheard(f'Pilot to {called}: {text}')

    def on_air(self, agent_id: str, reply: str) -> None:
        """An agent transmitted: the others on its frequency heard it, and the pilot's next unnamed call is for it."""
        agent = self.agents.get(agent_id)
        if agent is None:
            return
        mates = self.agents.on_channel(agent)
        if len(mates) < 2:
            return
        self.dispatcher.talked(mates, agent_id)
        for mate in mates:
            if mate['id'] != agent_id:
                self.responder(mate['id']).brain.overheard(f'{agent["name"]}: {reply}')

    def responder(self, agent_id: str) -> Responder:
        with self.lock:
            if agent_id not in self.responders:
                responder = self.make_responder(agent_id)
                responder.on_air = lambda reply: self.on_air(agent_id, reply)
                self.responders[agent_id] = responder
            return self.responders[agent_id]

    def refresh(self) -> None:
        """After a change in the GUI: each brain gets its agent's current mission and channel."""
        with self.lock:
            for agent_id, responder in self.responders.items():
                agent = self.agents.get(agent_id)
                if agent is None:
                    continue
                responder.name = agent['name']   # renamed in the GUI: logs, the Status screen
                if hasattr(responder.brain, 'instructions'):
                    responder.brain.instructions = self.agents.mission(agent_id)
                    responder.brain.radio = channel_label(agent)
                    responder.brain.agent_name = agent['name']
                desk = getattr(responder, 'desk', None)
                if desk is not None and agent.get('role') in (*GROUP_ROLES, *SENSOR_ROLES):
                    desk.configure(agent)

    def remove(self, agent_id: str) -> None:
        """An agent deleted in the GUI: no answers from it any more (its frequency is free)."""
        with self.lock:
            self.responders.pop(agent_id, None)
        if self.forget:
            self.forget(agent_id)   # its desk too: a new agent may take the same folder name

    def brains(self) -> list:
        with self.lock:
            return [r.brain for r in self.responders.values()]


def run(settings: dict, voice=True, open_srs=False, stop_when_window_closed=False, repeat=False, gui=False) -> None:
    """gui: serve the local web GUI (gui/server.py) with this instructor's live state; opens its window when
    "gui.open_on_start" is set. With stop_when_window_closed, closing the GUI window (or the dictation window) stops
    the whole program."""
    if 'ptt' not in settings:
        raise SystemExit('no push-to-talk button yet: run utilities\\PTT_PICK.cmd first')
    speaker, dictation = None, None
    locale = load_locale(settings['language'])
    state = CockpitState()
    blackbox = BlackBox()
    tower = TowerView(state, lambda: blackbox.summary)
    toolbox = ToolBox(tower)
    agents = AgentStore(settings=settings)
    print('agents: ' + ', '.join(f'{a["name"]} {channel_label(a)}{"" if a.get("active") else " (off)"}'
                                 for a in agents.all()))
    live = Live()
    live.agents = agents

    # Ports first, so a clash fails at once, before SRS or the voice are started. The cockpit stream gives the radio
    # data for the instructor frequency gate (only one program can read it: close INSTRUCTOR/COCKPIT_TEST first).
    # Without DCS the gate has no data and listens anyway.
    try:
        stream = CockpitStream(settings['cockpit_port'], timeout=0.5)
    except OSError as exc:
        raise SystemExit(f'cockpit port {settings["cockpit_port"]} is in use: another instructor or test is running '
                         f'({exc})') from exc
    window = None
    if gui:
        from gui.server import DEFAULT_PORT, Gui
        gui_settings = settings.get('gui', {})
        try:
            window = Gui(live, gui_settings.get('port', DEFAULT_PORT))
        except OSError as exc:
            raise SystemExit(f'GUI port {gui_settings.get("port", DEFAULT_PORT)} is in use ({exc})') from exc
    # Agents with a desk of their own: the ATC (role "atc", atc/desk.py) answers on the airfields' own frequencies,
    # so the gate asks its desk whether an airfield listens on the pilot's frequency; a squad (role "squad",
    # squad/desk.py) commands its AI group in the mission. Both reach DCS through its patched radio menu
    # (client/dcs_bridge.py) and the squad through our hook too (client/dcs_tasks.py); harmless when not patched.
    from client.dcs_bridge import NativeBridge
    from client.dcs_tasks import TaskChannel
    bridge, tasks = NativeBridge(log=log), TaskChannel(log=log)
    # Which vehicle types carry troops, from DCS's own unit files (shared/cargo.py; scanned once, in the background).
    from shared.cargo import CargoCapacities
    cargo = CargoCapacities((settings.get('dcs') or {}).get('install'), log=log)
    # NATO names of the radars a controller hears, from DCS's own table (shared/reporting_names.py).
    from shared.reporting_names import ReportingNames
    reporting_names = ReportingNames((settings.get('dcs') or {}).get('install'))
    internal = InternalFlags()   # Triggers rows marked internal: agent -> agent, DCS never sees them

    def net_shown(sender: str, recipient: str, text: str, kind: str) -> None:
        """A net message on the GUI's Status and in the black box (the pilot does not hear it)."""
        live.said('net', text, None, f'{sender} > {recipient}')
        blackbox.write('net', state.time, sender=sender, to=recipient, net=kind, text=text)

    # The internal net (shared/net.py): messages go into the recipient's turn, like a mission event.
    net = Net(agents, deliver=lambda agent_id, event: router.responder(agent_id).event(event), on_message=net_shown)
    desks = {}

    def make_desk(a: dict):
        """The agent's desk, made once (also for an agent added from the GUI while running)."""
        if a['id'] not in desks:
            if a.get('role') == 'atc':
                desks[a['id']] = Desk(state, (a.get('voice') or {}).get('language') or settings['language'],
                                      a.get('atc'))
                desks[a['id']].native = bridge.send
            elif a.get('role') == 'squad':
                desks[a['id']] = SquadDesk(state, a, tasks, bridge)
            elif a.get('role') == 'vehicles':
                desks[a['id']] = VehicleDesk(state, a, tasks, bridge, cargo)
            elif a.get('role') in ('artillery', 'ships'):
                desks[a['id']] = (ShipDesk if a['role'] == 'ships' else ArtilleryDesk)(state, a, tasks, bridge, cargo)
                desks[a['id']].net = net   # its "shot" / "rounds complete" go to the observer on the net
            elif a.get('role') == 'wingman':
                desks[a['id']] = WingmanDesk(state, a, tasks)
            elif a.get('role') == 'controller':   # its radars, the task board; other agents' frequencies
                desks[a['id']] = ControllerDesk(state, a, tasks, agents, reporting_names)
        return desks.get(a['id'])

    for a in agents.all():
        make_desk(a)
    atc_desk = next((d for d in desks.values() if isinstance(d, Desk)), None)
    if atc_desk:
        agents.station_finder = lambda freq_hz: atc_desk.station_on(freq_hz) is not None

    def make_responder(agent_id: str) -> Responder:
        agent = agents.get(agent_id)
        desk = make_desk(agent)
        # A wingman: its memory of the pilot (agents/<id>/memory.json), its fixed rules after the personality, and a
        # longer history (wingman/memory.py).
        memory = Memory(agents.path(agent_id)) if isinstance(desk, WingmanDesk) else None
        box = AtcToolBox(desk) if isinstance(desk, Desk) else ControllerToolBox(desk) \
            if isinstance(desk, ControllerDesk) else WingmanToolBox(desk, memory) \
            if isinstance(desk, WingmanDesk) else ShipToolBox(desk) if isinstance(desk, ShipDesk) \
            else ArtilleryToolBox(desk) if isinstance(desk, ArtilleryDesk) \
            else VehicleToolBox(desk) if isinstance(desk, VehicleDesk) else SquadToolBox(desk) if desk else toolbox
        playbook = getattr(box, 'playbook', None)   # a wingman's playbook (wingman/playbook.py)
        radio_box = net_box = None
        if not isinstance(desk, Desk):   # the ATC is fixed; the others get a tool per Triggers action of their own
            box = TriggerToolBox(box, agents, agent_id, tasks, state, internal)
            # ...and may change frequency when the pilot says so (shared/radio_change.py)...
            box = radio_box = RadioToolBox(box, agents, agent_id)
            # ...and send messages to the other agents on the internal net (shared/net.py).
            # A unit with eyes may call for fire (call_for_fire, while a battery is on the net).
            box = net_box = NetToolBox(box, net, agent_id, desk)
        extra = {'appendix': brain_appendix(memory, playbook), 'history_turns': WINGMAN_HISTORY_TURNS} \
            if memory else {}
        brain = NullBrain() if repeat else create_brain(settings, box, channel_label(agent), agents.mission(agent_id),
                                                        **extra)
        if hasattr(brain, 'agent_name'):
            brain.agent_name = agent['name']
        print(f'{agent["name"]}: brain {brain.name} {getattr(brain, "model", "")}'.rstrip())
        # The agent is read again for every phrase, so voice changes made in the GUI apply at once. The ATC transmits
        # on the frequency the pilot called it on, named after the station.
        if isinstance(desk, Desk):
            speak = lambda text: speaker and speaker.say(text, desk.channel(agents.get(agent_id)))
        else:
            def speak(text: str) -> None:
                if speaker:
                    speaker.say(text, agents.get(agent_id))   # on the frequency it had when it was asked
                move_frequency(radio_box, brain, agent_id)
        language = (agent.get('voice') or {}).get('language') or settings['language']   # its voice's language
        responder = Responder(brain, locale, language, state, speak=speak,
                              show=lambda text: dictation.show(text), blackbox=blackbox,
                              situation=desk.situation if desk else tower.situation, live=live, name=agent['name'],
                              role=agent.get('role', 'instructor'), prefilter=getattr(desk, 'prefilter', None))
        responder.desk = desk
        responder.agent_id, responder.net, responder.net_box = agent_id, net, net_box
        return responder

    router = Router(agents, make_responder)
    router.forget = lambda agent_id: desks.pop(agent_id, None)
    live.router = router
    flag_watch = FlagWatch(agents, state, tasks, internal)
    condition_watch = ConditionWatch(agents, desks.get)   # "when this happens" rows: read from the squads' desks
    for agent in agents.active():
        router.responder(agent['id'])   # ready before the first press (a sign-in problem shows at start)
    dictation = create_dictation(settings, locale, on_text=router.heard,
                                 on_status=lambda kind, detail: print(f'[{kind}] {detail}'.rstrip()),
                                 radio_state=lambda: state.radio, agents=agents)
    if not dictation:
        raise SystemExit('dictation is off: set "speech.provider" in settings.json')
    if voice:
        from instructor.voice import Voice
        speaker = Voice(settings)
        speaker.log = log   # each clip: synthesis and SRS times, what ExternalAudio said (logs/dictation.jsonl)
        if open_srs:
            open_srs_apps(speaker)
    ptt = settings['ptt']
    gate = 'on an agent\'s frequency' if ptt.get('instructor_frequency_only') else 'on any frequency'
    print(f'{dictation.name} dictation: hold {ptt["joystick"]} button {ptt["button"]} and speak ({gate})')
    dictation.start()
    live.state, live.tower, live.dictation, live.voice = state, tower, dictation, speaker
    if window:
        window.start()
        print(f'GUI: {window.url}')
        if settings.get('gui', {}).get('open_on_start', True):
            from client.chrome_dictation import open_window
            open_window(window.url, size=GUI_WINDOW_SIZE)

    windows = [w for w in (dictation if hasattr(dictation, 'events') else None, window) if w is not None]
    watches = {id(w): WindowWatch() for w in windows}
    shown, radar_seen, calibration_seen = None, None, {}
    next_own_calls = 0.0
    try:
        while True:
            if live.stop.is_set():
                print('stop asked from the GUI: stopping')
                return
            message = stream.receive()
            if message:
                live.last_message_at = time.monotonic()
                kind = state.apply(message)
                # The radio of the last push-to-talk button pressed (ptt.radio2 = radio 2), or the aircraft's own.
                ptt_radio = getattr(getattr(dictation, 'gate', None), 'ptt_radio', 1)
                if state.radio and ('selected' not in state.radio or ptt_radio >= 2):
                    state.radio = transmit_radio(state.radio, ptt_radio)
                blackbox.on_cockpit(kind, message, state)
                tower.on_flight()
                if kind == 'aircraft' and blackbox.path:
                    print(f'black box: {blackbox.path.name}')
                if kind == 'native' and message.get('id') in bridge.quiet_ids:
                    bridge.quiet_ids.discard(message.get('id'))
                elif kind == 'native':
                    print(f'DCS native command {message.get("id")}: {message.get("status")} {message.get("detail", "")}')
                    log({'event': 'native_result', **message})
                if kind == 'radar':
                    radar_seen = log_radar_change(tower, state, radar_seen)
                    log_radar_calibration(state, calibration_seen)
                shown = log_radio_change(state, shown)
            if time.monotonic() >= next_own_calls:
                next_own_calls = time.monotonic() + OWN_CALLS_EVERY_S
                if desks:
                    own_calls(desks, router, agents, state)
                mission_events(flag_watch, router)
                mission_events(condition_watch, router)
            if stop_when_window_closed:
                for w in windows:
                    if watches[id(w)].closed(len(w.events.clients)):
                        print(f'{"GUI" if w is window else "dictation"} window closed: stopping')
                        return
    finally:
        for w in windows:
            w.events.push('close')   # the other window closes itself
        time.sleep(CLOSE_DELIVERY_S)


def move_frequency(radio_box, brain, agent_id: str) -> None:
    """After an agent's transmission: the frequency change it agreed to (change_frequency), saved like a GUI change;
    its brain's own radio line follows."""
    if radio_box is None:
        return
    try:
        moved = radio_box.apply()
    except ValueError as exc:   # the store refused it (checked before, so unlikely)
        log({'event': 'error', 'where': 'frequency change', 'agent': agent_id, 'error': str(exc)})
        return
    if moved:
        label = channel_label(moved)
        if hasattr(brain, 'radio'):
            brain.radio = label
        log({'event': 'frequency_change', 'agent': agent_id, 'radio': label})
        print(f'{moved["name"]}: now on {label}')


def own_calls(desks: dict, router: Router, agents: AgentStore, state: CockpitState) -> None:
    """The agents' calls of their own (desk.tick: the ATC's tower calls; a squad keeps its group reported): never
    while the pilot transmits or the agent is still answering."""
    transmitting = bool((state.radio or {}).get('ptt'))
    for agent_id, desk in list(desks.items()):   # the GUI may add one meanwhile
        agent = agents.get(agent_id)
        if not agent or not agent.get('active'):
            continue
        responder = router.responder(agent_id)
        try:
            text = desk.tick(busy=transmitting or responder.busy)
        except Exception as exc:   # a desk failure must never stop the radio loop
            log({'event': 'error', 'where': 'atc tick', 'error': f'{type(exc).__name__}: {exc}'})
            print(f'[atc] tick failed: {exc}')
            continue
        if text:
            responder.call(text)
        if hasattr(desk, 'reports'):   # a wingman's callouts and quiet time, phrased by its model
            try:
                events = desk.reports(busy=transmitting or responder.busy,
                                      quiet_s=time.monotonic() - responder.last_activity)
            except Exception as exc:
                log({'event': 'error', 'where': 'reports', 'error': f'{type(exc).__name__}: {exc}'})
                continue
            for event in events:
                responder.event(event)


def mission_events(watch, router: Router) -> None:
    """Mission flags that turned on, and conditions that became true, go to the agents listening to them
    (shared/triggers.py: FlagWatch, ConditionWatch)."""
    try:
        fired = watch.tick()
    except Exception as exc:   # never stops the radio loop
        log({'event': 'error', 'where': type(watch).__name__, 'error': f'{type(exc).__name__}: {exc}'})
        return
    for agent_id, event in fired:
        if 'when' in event:
            log({'event': 'condition', 'agent': agent_id, 'when': event['when'], 'cause': event.get('cause', '')})
        else:
            log({'event': 'flag_on', 'agent': agent_id, 'flag': event['flag'], 'internal': event['internal']})
        router.responder(agent_id).event(event)


class WindowWatch:
    """A window of ours counts as closed once it was seen connected and then no page is connected for WINDOW_GONE_S."""

    def __init__(self):
        self.seen, self.gone_since = False, None

    def closed(self, pages: int) -> bool:
        self.seen = self.seen or pages > 0
        if not self.seen or pages > 0:
            self.gone_since = None
            return False
        self.gone_since = self.gone_since or time.monotonic()
        return time.monotonic() - self.gone_since >= WINDOW_GONE_S


def open_srs_apps(speaker) -> None:
    from instructor.srs import open_apps, show_client
    if speaker.srs is None:
        return
    for app, status in open_apps(speaker.srs.exe).items():
        print(f'SRS {app}: {status}')
    if not show_client(speaker.srs.exe):
        print('SRS client window did not appear: open it from its tray icon')


def log_radar_change(tower: TowerView, state: CockpitState, seen):
    """Diagnostic: print and log the tower radar's status whenever it changes (contact, no contact, no radar...)."""
    radar = tower.radar()
    tower_name = tower.tower['name'] if tower.tower else None
    network = next((v for k, v in (state.radars or {}).items() if tower_name and k == tower_name), None)
    now = (tower_name, radar['status'], radar.get('reason'))
    if now != seen:
        ranges = (network or {}).get('range_km', {})
        print(f'radar {tower_name or "(no tower yet)"}: {radar["status"]}'
              f'{" (" + radar["reason"] + ")" if radar.get("reason") else ""}  radars {ranges or "-"}  '
              f'networks {sorted(state.radars or {})}')
        log({'event': 'radar', 'tower': tower_name, 'radar': radar, 'radars_km': ranges,
             'networks': sorted(state.radars or {})})
    return now


CALIBRATION_LOG = ROOT / 'logs' / 'radar_calibration.jsonl'


def log_radar_calibration(state: CockpitState, seen: dict) -> None:
    """Calibration data for the radar model (dcs/instructor_radar.lua): every time a radar's own AI starts or stops
    detecting an enemy aircraft, or our model does, write both with the distance, the aircraft's RCS and the distance
    our model allows; fitting these tells the target size DCS's detection distances are given for."""
    for airfield, network in (state.radars or {}).items():
        for entry in (network or {}).get('calibration') or []:
            key = (entry.get('radar'), entry.get('unit'))
            now = (entry.get('ai'), entry.get('model'))
            if seen.get(key) != now:
                if key in seen:   # the first sighting only sets the baseline
                    CALIBRATION_LOG.parent.mkdir(parents=True, exist_ok=True)
                    with CALIBRATION_LOG.open('a', encoding='utf-8') as f:
                        f.write(json.dumps({'time': time.time(), 'airfield': airfield, **entry}) + '\n')
                seen[key] = now


def log_radio_change(state: CockpitState, shown):
    selected = selected_radio(state.radio)
    knobs = {arg: round(state.args[arg], 3) for arg in RADIO_ARGS.get(state.aircraft, ()) if arg in state.args}
    seen = (describe(state.radio) if state.radio else None, selected and selected.get('freq'), knobs)
    if seen != shown:
        print(f'radio: {seen[0] or "no radio data"}  {knobs}')
        log({'event': 'radio', 'radio': selected, 'source': (state.radio or {}).get('source', 'srs'), 'knobs': knobs})
    return seen
