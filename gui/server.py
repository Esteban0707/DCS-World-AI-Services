"""Local web GUI served by the program itself (web/gui/): the same page on the PC, the Raspberry Pi or a tablet.

Runs inside the instructor (instructor/voice_loop.py passes its Live state, so the Status screen is live and changes
apply at once) or alone (utilities/GUI.cmd: settings and voice tests without DCS). Listens on 127.0.0.1 only
("gui.port", 47822): the page can change settings and has no sign-in yet. Closing the GUI window stops the program
(the page holds /api/events open; voice_loop.WindowWatch notices when no page is connected).

API (JSON): GET /api/text, /api/status, /api/settings, /api/devices, /api/agents/<id>, /api/events (server-sent
events); POST /api/agents (add one: role, name, frequency_mhz, modulation, group), /api/agents/<id> (name,
frequency, active, voice, speech, Triggers "events"/"actions"; the ATC's name is fixed and it has no triggers), /api/agents/<id>/mission,
/api/agents/<id>/mission/restore, /api/agents/<id>/memory (a wingman's memory: pilot, flights),
/api/agents/<id>/delete (moved to backups/agents/),
/api/agents/<id>/triggers/assist (+ /reset: the Triggers assistant, gui/trigger_assistant.py), /api/audio, /api/voice/test, /api/stop. Texts come from locales/en.json "gui" (the
GUI is English only).
"""
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from client.chrome_dictation import KEEPALIVE_S, Events, ExclusiveServer
from gui.status import Live, SrsCheck, status
from gui.trigger_assistant import TriggerAssistant
from shared import settings as settings_file
from shared import triggers
from shared.agents import ADDABLE_ROLES, FIXED_ROLES, GROUP_ROLES, MISSION_FIELDS, MODULATIONS, AgentStore
from shared.locale import load_locale
from wingman.memory import Memory

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web' / 'gui'
DEFAULT_PORT = 47822
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.css': ('app.css', 'text/css; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
VOICE_RANGES = {'speed': (0.7, 1.5), 'steps': (2, 16), 'silence_s': (0.0, 1.0), 'volume': (0.0, 1.5)}
OUTPUTS = ('srs', 'speakers')
TEST_TEXT_MAX = 500
MISSION_MAX = 20000
NAME_MAX = 32
ALIASES_MAX = 8
# Languages Chrome's dictation recognizes (BCP 47), offered in each agent's STT tab.
STT_LANGUAGES = ('en-US', 'en-GB', 'es-US', 'es-ES', 'es-MX', 'de-DE', 'fr-FR', 'it-IT', 'pt-BR', 'pt-PT', 'ru-RU',
                 'uk-UA', 'pl-PL', 'nl-NL', 'cs-CZ', 'sv-SE', 'tr-TR', 'ja-JP', 'ko-KR', 'zh-CN')


def voice_changes(body: dict) -> dict:
    """The voice settings in a request, checked; ValueError names the first bad one."""
    from instructor.voice import VOICES
    changes = {}
    if 'name' in body:
        if body['name'] not in VOICES:
            raise ValueError('name')
        changes['name'] = body['name']
    for key, (low, high) in VOICE_RANGES.items():
        if key in body:
            value = float(body[key])
            if not low <= value <= high:
                raise ValueError(key)
            changes[key] = int(value) if key == 'steps' else round(value, 3)
    if 'output' in body:
        if body['output'] not in OUTPUTS:
            raise ValueError('output')
        changes['output'] = body['output']
    if 'language' in body:
        if body['language'] not in languages():
            raise ValueError('language')
        changes['language'] = body['language']
    return changes


def agent_changes(body: dict) -> dict:
    """An agent's settings in a request, checked (the channel is checked against the others by AgentStore)."""
    changes = {}
    if 'name' in body:
        name = str(body['name']).strip()
        if not 0 < len(name) <= NAME_MAX:
            raise ValueError('name')
        changes['name'] = name
    if 'active' in body:
        changes['active'] = bool(body['active'])
    if 'frequency_mhz' in body:
        changes['frequency_mhz'] = round(float(body['frequency_mhz']), 3)
    if 'modulation' in body:
        if body['modulation'] not in MODULATIONS:
            raise ValueError('modulation')
        changes['modulation'] = body['modulation']
    if 'group' in body:   # a squad's group in the mission; AgentStore checks it and the role
        changes['group'] = str(body['group'])
    if 'number' in body:   # a wingman's number in the pilot's flight; AgentStore checks it and the role
        changes['number'] = body['number']
    if 'chatter' in body:  # how often a wingman talks on its own in calm moments; AgentStore checks it
        changes['chatter'] = str(body['chatter'])
    if 'aliases' in body:   # other names the pilot may call it by on a shared frequency (shared/dispatch.py)
        aliases = body['aliases'] if isinstance(body['aliases'], list) else str(body['aliases'] or '').split(',')
        aliases = list(dict.fromkeys(str(a).strip() for a in aliases if str(a).strip()))
        if len(aliases) > ALIASES_MAX or any(len(a) > NAME_MAX for a in aliases):
            raise ValueError('aliases')
        changes['aliases'] = aliases
    if 'voice' in body:
        changes['voice'] = voice_changes(body['voice'] or {})
    if 'speech' in body:
        language = (body['speech'] or {}).get('language')
        if language not in STT_LANGUAGES:
            raise ValueError('speech.language')
        changes['speech'] = {'language': language}
    return changes


def audio_changes(body: dict) -> dict:
    changes = {}
    if 'output_device' in body:
        changes.setdefault('audio', {})['output_device'] = str(body['output_device'])
    if 'mic_device' in body:
        changes.setdefault('mic', {})['device'] = str(body['mic_device'])
    return changes


def languages() -> list[str]:
    try:
        from supertonic.config import AVAILABLE_LANGUAGES
    except ImportError:
        return []
    return [code for code in AVAILABLE_LANGUAGES if code != 'na']


class Gui:
    def __init__(self, live: Live | None = None, port: int = DEFAULT_PORT, settings_path: Path = settings_file.PATH,
                 agents: AgentStore | None = None):
        self.live = live or Live()
        self.settings_path = settings_path
        if self.live.agents is None:
            self.live.agents = agents or AgentStore(settings=settings_file.load(settings_path))
        self.srs = SrsCheck()
        self.assistant = TriggerAssistant(self.settings, self.live.agents)
        self.voice_lock = threading.Lock()
        self.events = Events()   # one subscriber per open GUI page
        self.server = ExclusiveServer(('127.0.0.1', port), self._handler())   # OSError: port in use; 0 = any free
        self.port = self.server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}/'

    @property
    def agents(self) -> AgentStore:
        return self.live.agents

    def origins(self) -> tuple[str, ...]:
        """Where a POST may come from: this GUI's own page (no Origin header = not a browser)."""
        return '', f'http://127.0.0.1:{self.port}', f'http://localhost:{self.port}'

    def start(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def settings(self) -> dict:
        return settings_file.load(self.settings_path)

    # --- API ----------------------------------------------------------------------------------------------------

    def get(self, path: str):
        if path == '/api/text':
            return load_locale('en')['gui']
        if path == '/api/status':
            settings = self.settings()
            return status(self.live, settings, self.srs.get(settings), self.agents)
        if path == '/api/settings':
            settings = self.settings()
            return {key: settings.get(key, {}) for key in ('audio', 'mic', 'speech', 'srs', 'brain', 'ptt')} | {
                'running': self.live.running, 'languages': languages(), 'stt_languages': list(STT_LANGUAGES),
                'mission_fields': list(MISSION_FIELDS), 'addable_roles': list(ADDABLE_ROLES)}
        if path == '/api/devices':
            return self.devices()
        if path == '/api/dcs':
            return self.dcs_state()
        if path.startswith('/api/agents/'):
            agent_id = path.removeprefix('/api/agents/')
            agent = self.agents.get(agent_id)
            if agent is None:
                return None
            # Triggers tab: the condition events and the squads they may watch ("about").
            squads = [{'id': a['id'], 'name': a['name']} for a in self.agents.all()
                      if a.get('role') in GROUP_ROLES]   # the units the condition events can watch
            extra = {}
            if agent.get('role') == 'wingman':   # its memory of the pilot (wingman/memory.py), Mission tab
                extra['memory'] = Memory(self.agents.path(agent_id)).load()
            return {**agent, 'fixed': agent.get('role') in FIXED_ROLES, 'mission': self.agents.mission(agent_id),
                    'mission_is_original': self.agents.mission(agent_id) == self.agents.original_mission(agent_id),
                    'conditions': list(triggers.CONDITIONS), 'squads': squads, **extra}
        return None

    def post(self, path: str, body: dict):
        if path == '/api/agents':
            return self.add_agent(body)
        if path.startswith('/api/agents/'):
            return self.post_agent(path.removeprefix('/api/agents/'), body)
        if path == '/api/audio':
            settings = settings_file.update(audio_changes(body), self.settings_path)
            self.apply_audio(settings, microphone=('mic_device' in body))
            return {'audio': settings.get('audio', {}), 'mic': settings.get('mic', {})}
        if path == '/api/voice/test':
            return self.test_voice(body)
        if path == '/api/stop':
            self.live.stop.set()
            return {'stopping': self.live.running}
        if path == '/api/srs/open':
            return self.open_srs()
        if path in ('/api/dcs/update', '/api/dcs/remove'):
            return self.dcs_patch(path.rsplit('/', 1)[1])
        return None

    def open_srs(self) -> dict:
        """The Status screen's "Open SRS": starts the server/client if closed (or crashed) and brings the client's
        window out of the tray (minimising it with SRS's "Minimise to tray" hides it there)."""
        from instructor.srs import find_external_audio, open_apps, show_client
        exe = find_external_audio(self.settings().get('srs', {}).get('path', ''))
        if not exe:
            raise ValueError('srs')
        apps = open_apps(exe)
        threading.Thread(target=show_client, args=(exe,), daemon=True).start()   # waits up to 20 s for the window
        self.srs.at = 0.0   # the SRS tile is checked again on the next refresh
        return {'apps': apps}

    def dcs_state(self, results=None) -> dict:
        """The DCS screen: the install, its version, our patches in it, and the export line and hook in Saved
        Games (those are only checked here; tools/install_export.py and tools/install_hook.py install them)."""
        from client import dcs_patches
        from tools import preflight
        settings = self.settings()
        install = dcs_patches.install_folder(settings)
        statuses = results if results is not None else dcs_patches.status_all(install)
        problems = {key for key, _ in preflight.installed(settings)}
        return {'install': str(install), 'version': dcs_patches.dcs_version(install),
                'patches': [{'name': s.name, 'state': s.state, 'detail': s.detail} for s in statuses],
                'export': 'export_missing' not in problems, 'hook': 'hook_missing' not in problems}

    def dcs_patch(self, action: str) -> dict:
        """The user's Update / Remove button: writes into the DCS install (user's approval, 2026-11-02)."""
        from client import dcs_patches
        install = dcs_patches.install_folder(self.settings())
        results = dcs_patches.update_all(install) if action == 'update' else dcs_patches.remove_all(install)
        return self.dcs_state(results) | {'done': action}

    def add_agent(self, body: dict) -> dict:
        """"+ Add agent": role, name, frequency (and a squad's mission group). A running program gets it ready at
        once (its frequency answers from the next press)."""
        changes = agent_changes({key: body[key] for key in ('name', 'frequency_mhz', 'modulation') if key in body})
        if 'name' not in changes:
            raise ValueError('name')
        if 'frequency_mhz' not in changes or 'modulation' not in changes:
            raise ValueError('frequency_mhz')
        agent = self.agents.add(str(body.get('role') or ''), changes['name'], changes['frequency_mhz'],
                                changes['modulation'], self.settings(), str(body.get('group') or ''),
                                body.get('number', 2))
        if self.live.router is not None:
            self.live.router.responder(agent['id'])
        return self.get(f'/api/agents/{agent["id"]}') | {'saved': True}

    def post_agent(self, rest: str, body: dict):
        agent_id, _, action = rest.partition('/')
        if self.agents.get(agent_id) is None:
            return None
        if action == 'triggers/assist':      # the Triggers assistant: fills the rows, never saves
            return self.assistant.ask(agent_id, body.get('message'), body.get('draft'))
        if action == 'triggers/assist/reset':
            self.assistant.reset(agent_id)
            return {'reset': True}
        if action == '':
            changes = agent_changes(body)
            if 'events' in body or 'actions' in body:   # the Triggers tab: both lists, checked as a whole
                stored = self.agents.get(agent_id)
                new = {'role': stored.get('role'), 'events': body.get('events', stored.get('events') or []),
                       'actions': body.get('actions', stored.get('actions') or [])}
                triggers.check(new, triggers.role_tool_names(stored.get('role')))
                changes |= {'events': triggers.events(new), 'actions': triggers.actions(new)}
            agent = self.agents.update(agent_id, changes)
        elif action == 'mission':
            text = str(body.get('text') or '')
            if len(text) > MISSION_MAX:
                raise ValueError('mission')
            self.agents.save_mission(agent_id, text)
            agent = self.agents.get(agent_id)
        elif action == 'mission/restore':
            self.agents.restore_mission(agent_id)
            agent = self.agents.get(agent_id)
        elif action == 'memory':   # a wingman's memory, edited by hand: one line each
            agent = self.agents.get(agent_id)
            if agent.get('role') != 'wingman':
                raise ValueError('memory')
            lines = {k: body.get(k) if isinstance(body.get(k), list) else str(body.get(k) or '').splitlines()
                     for k in ('pilot', 'flights')}
            Memory(self.agents.path(agent_id)).save(lines['pilot'], lines['flights'])
        elif action == 'delete':
            removed = self.agents.remove(agent_id)
            if self.live.router is not None:
                self.live.router.remove(agent_id)   # off the air at once
            return {'deleted': agent_id, 'kept_in': str(removed.relative_to(ROOT)) if removed.is_relative_to(ROOT)
                    else str(removed)}
        else:
            return None
        if self.live.router is not None:
            self.live.router.refresh()   # the running brains take the new mission and channel
        return self.get(f'/api/agents/{agent_id}') | {'saved': True, 'id': agent['id']}

    def apply_audio(self, settings: dict, microphone: bool = False) -> None:
        """Device changes take effect in the running instructor at once."""
        if self.live.voice is not None:
            self.live.voice.configure(settings)
        if microphone and self.live.dictation is not None and hasattr(self.live.dictation, 'set_microphone'):
            self.live.dictation.set_microphone(settings.get('mic', {}).get('device', ''))

    def voice(self):
        """The running instructor's voice, or one of the GUI's own when it runs alone (loads Supertonic once)."""
        with self.voice_lock:
            if self.live.voice is None:
                from instructor.voice import Voice
                self.live.voice = Voice(self.settings())
            return self.live.voice

    def test_voice(self, body: dict) -> dict:
        line = str(body.get('text') or '').strip()[:TEST_TEXT_MAX]
        if not line:
            raise ValueError('text')
        via = body.get('via')
        if via not in OUTPUTS:
            raise ValueError('via')
        agent = self.agents.get(str(body.get('agent') or ''))
        params = voice_changes(body.get('params') or {})
        params.pop('output', None)
        try:
            return self.voice().test(line, via, params, agent)
        except Exception as exc:   # SRS missing, ExternalAudio error, audio device gone...
            return {'error': str(exc)}

    def devices(self) -> dict:
        from shared.audio_devices import list_devices
        settings = self.settings()
        try:
            inputs, outputs = list_devices('input'), list_devices('output')
        except Exception as exc:   # no audio system (e.g. a server without sound)
            inputs, outputs = [], []
            print(f'[gui] audio devices: {exc}')
        return {'inputs': inputs, 'outputs': outputs, 'srs': self.srs.get(settings)}

    # --- HTTP ---------------------------------------------------------------------------------------------------

    def _handler(self):
        gui = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, data) -> None:
                self._send(code, json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json')

            def _events(self) -> None:
                """Held open by each GUI page: tells the program the window is still open; carries 'close'."""
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                q = gui.events.subscribe()
                try:
                    while True:
                        try:
                            item = q.get(timeout=KEEPALIVE_S)
                            self.wfile.write(f'data: {json.dumps(item)}\n\n'.encode('utf-8'))
                        except queue.Empty:
                            self.wfile.write(b': keepalive\n\n')
                        self.wfile.flush()
                except OSError:
                    pass
                finally:
                    gui.events.unsubscribe(q)

            def do_GET(self):
                path = self.path.split('?')[0]
                if path in STATIC:
                    name, content_type = STATIC[path]
                    self._send(200, (WEB / name).read_bytes(), content_type)
                    return
                if path == '/api/events':
                    self._events()
                    return
                try:
                    data = gui.get(path)
                except ValueError:
                    data = None
                except Exception as exc:
                    self._json(500, {'error': str(exc)})
                    return
                if data is None:
                    self.send_error(404)
                else:
                    self._json(200, data)

            def do_POST(self):
                if self.headers.get('Origin', '') not in gui.origins():
                    self.send_error(403)
                    return
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                    data = gui.post(self.path, body if isinstance(body, dict) else {})
                except (ValueError, TypeError) as exc:
                    self._json(400, {'error': 'invalid', 'field': str(exc)})
                    return
                if data is None:
                    self.send_error(404)
                else:
                    self._json(200, data)

            def log_message(self, *args):
                pass

        return Handler
