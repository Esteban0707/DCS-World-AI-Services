"""The Status screen's data: one light per part of the program, the last radio exchanges, the services.

Texts are not built here: each tile carries a state key and raw values (names, numbers); the page words them from
locales/en.json "gui". A value or detail is either plain data or {"key": ..., "args": {...}} for a locale text.
"""
import collections
import threading
import time

from shared.agents import airfield_mode, channel_label
from shared.radio import describe, selected_radio

DCS_STALE_S = 3.0
SRS_CHECK_S = 5.0
EXCHANGES = 20


def text(key: str, **args) -> dict:
    return {'key': key, 'args': args}


class Live:
    """What a running instructor shares with the GUI (instructor/voice_loop.py fills it). Empty = the GUI runs alone."""

    def __init__(self):
        self.state = None            # shared.cockpit.CockpitState
        self.tower = None            # instructor.tower_view.TowerView
        self.agents = None           # shared.agents.AgentStore
        self.router = None           # instructor.voice_loop.Router: one brain per agent
        self.dictation = None
        self.voice = None            # instructor.voice.Voice
        self.last_message_at = None  # time.monotonic() of the last message from DCS
        self.exchanges = collections.deque(maxlen=EXCHANGES)
        self.stop = threading.Event()

    @property
    def running(self) -> bool:
        return self.state is not None

    def said(self, who: str, line: str, seconds: float | None = None, agent: str | None = None) -> None:
        """who: 'pilot' or 'agent' (agent = the agent's name)."""
        self.exchanges.append({'time': time.strftime('%H:%M:%S'), 'who': who, 'agent': agent, 'text': line,
                               'seconds': seconds})


class SrsCheck:
    """SRS install, apps running and the client's audio devices; tasklist is slow, so checked every few seconds."""

    def __init__(self):
        self.at, self.value = 0.0, None

    def get(self, settings: dict) -> dict:
        if self.value is None or time.monotonic() - self.at > SRS_CHECK_S:
            self.value, self.at = self._check(settings), time.monotonic()
        return self.value

    @staticmethod
    def _check(settings: dict) -> dict:
        from instructor.srs import SRS_APPS, client_audio, find_external_audio, is_running
        exe = find_external_audio(settings.get('srs', {}).get('path', ''))
        if not exe:
            return {'installed': False}
        install = exe.parent.parent
        return {'installed': True, 'path': str(install),
                **{app: is_running(install / SRS_APPS[app]) for app in SRS_APPS},
                'client_audio': client_audio(exe)}


def frequency_label(agents) -> str:
    """The active agents' own channels, "251.000 AM, 252.000 AM" (an ATC on the airfields' frequencies has none)."""
    return ', '.join(channel_label(a) for a in (agents.active() if agents else []) if not airfield_mode(a))


def tiles(live: Live, settings: dict, srs: dict, agents) -> list[dict]:
    return [dcs_tile(live), callsign_tile(live), srs_tile(srs), radio_tile(live, agents),
            dictation_tile(live, settings), brain_tile(live, settings), radar_tile(live), weather_tile(live)]


def tile(tile_id: str, light: str, state: str, value=None, detail=None) -> dict:
    """light: ok (green), wait (amber), off (grey), error (red)."""
    return {'id': tile_id, 'light': light, 'state': state, 'value': value, 'detail': detail}


def dcs_tile(live: Live) -> dict:
    if not live.running:
        return tile('dcs', 'off', 'not_running', text('instructor_not_running'))
    if live.last_message_at is None or time.monotonic() - live.last_message_at > DCS_STALE_S:
        return tile('dcs', 'wait', 'waiting', text('no_dcs_data'))
    aircraft = live.state.aircraft
    return tile('dcs', 'ok', 'connected', aircraft or text('not_in_aircraft'))


def callsign_tile(live: Live) -> dict:
    callsign = live.state.callsign if live.running else None
    if callsign:
        return tile('callsign', 'ok', 'from_mission', callsign)
    return tile('callsign', 'off' if not live.running else 'wait', 'unknown', '-')


def srs_tile(srs: dict) -> dict:
    if not srs.get('installed'):
        return tile('srs', 'error', 'missing', text('srs_not_found'))
    hears_on = (srs.get('client_audio') or {}).get('output') or {}
    detail = text('srs_plays_on', device=hears_on.get('name') or text('windows_default'))
    if srs.get('server') and srs.get('client'):
        return tile('srs', 'ok', 'running', text('srs_server_and_client'), detail)
    if srs.get('client'):
        return tile('srs', 'wait', 'partial', text('srs_client_only'), detail)
    if srs.get('server'):
        return tile('srs', 'wait', 'partial', text('srs_server_only'), detail)
    return tile('srs', 'off', 'stopped', text('srs_closed'), detail)


def radio_tile(live: Live, agents) -> dict:
    """Which agent the pilot's selected radio is tuned to."""
    if not live.running or not live.state.radio:
        return tile('radio', 'off', 'no_data', text('agent_frequencies', frequency=frequency_label(agents) or '-'))
    selected = selected_radio(live.state.radio) or {}
    detail = describe(live.state.radio)
    agent = None
    if agents and live.state.radio.get('selected') != 0:
        agent = agents.on_frequency(selected.get('freq', 0), selected.get('modulation'))
    if agent:
        return tile('radio', 'ok', 'on_frequency', text('tuned_to', agent=agent['name']), detail)
    return tile('radio', 'wait', 'off_frequency', selected.get('name') or detail, detail)


def dictation_tile(live: Live, settings: dict) -> dict:
    provider = settings.get('speech', {}).get('provider', 'none')
    microphone = settings.get('mic', {}).get('device') or text('windows_default')
    if provider == 'none':
        return tile('dictation', 'off', 'off', text('provider_' + provider), microphone)
    if not live.running or live.dictation is None:
        return tile('dictation', 'off', 'not_running', text('provider_' + provider), microphone)
    pages = len(live.dictation.events.clients) if hasattr(live.dictation, 'events') else 1
    if pages == 0:
        return tile('dictation', 'wait', 'window_closed', text('provider_' + provider), microphone)
    return tile('dictation', 'ok', 'listening', text('provider_' + provider), microphone)


def brain_tile(live: Live, settings: dict) -> dict:
    brain = settings.get('brain', {})
    provider, model = brain.get('provider', 'none'), brain.get('model', '')
    value = f'{text_provider(provider)} {model}'.strip()
    if provider == 'none':
        return tile('brain', 'off', 'off', text('brain_repeats'))
    seconds = next((e['seconds'] for e in reversed(live.exchanges) if e.get('seconds') is not None), None)
    detail = text('last_answer', seconds=seconds) if seconds is not None else None
    if not live.running:
        return tile('brain', 'off', 'not_running', value)
    return tile('brain', 'ok', 'ready', value, detail)


def text_provider(provider: str) -> str:
    return {'chatgpt': 'ChatGPT'}.get(provider, provider)


RADAR_LIGHTS = {'contact': ('ok', 'contact'), 'no radar contact': ('wait', 'no_contact'),
                'the tower has no radar': ('off', 'no_radar'), 'radar out of service': ('error', 'out_of_service'),
                'no radar data from the mission': ('off', 'no_data')}


def radar_tile(live: Live) -> dict:
    if not live.running or live.tower is None:
        return tile('radar', 'off', 'not_running', '-')
    radar = live.tower.radar()
    light, state = RADAR_LIGHTS.get(radar['status'], ('off', 'no_data'))
    tower = live.tower.tower['name'] if live.tower.tower else text('no_tower_yet')
    detail = text('range_altitude', range=radar['range_nm'], altitude=radar['altitude_ft']) \
        if radar['status'] == 'contact' else None
    return tile('radar', light, state, tower, detail)


def weather_tile(live: Live) -> dict:
    if not live.running or live.tower is None:
        return tile('weather', 'off', 'not_running', '-')
    report = live.tower.weather()
    if 'error' in report:
        return tile('weather', 'off', 'no_data', '-')
    runway = (report.get('runway_in_use') or {}).get('runway', '-')
    detail = text('qnh_runway', qnh=report.get('qnh', '-'), runway=runway)
    return tile('weather', 'ok', 'current', report.get('wind', '-'), detail)


COMING_SOON = ('atc', 'commander')   # roles shown in Services while no agent of theirs exists
ROLE_ORDER = ('instructor', 'atc', 'controller', 'squad', 'vehicles', 'artillery', 'ships', 'commander')


def services(settings: dict, agents) -> list[dict]:
    """The agents from their folders, then the roles still to come."""
    brain = settings.get('brain', {})
    connection = f'{text_provider(brain.get("provider", "none"))} {brain.get("model", "")}'.strip()
    listed = [{'id': a['id'], 'name': a['name'], 'role': a.get('role'), 'active': bool(a.get('active')),
               'frequency': None if airfield_mode(a) else channel_label(a),   # the page names the airfield mode
               'frequency_mode': a.get('frequency_mode', 'fixed'),
               'brain': connection, 'voice': (a.get('voice') or {}).get('name', '')}
              for a in (agents.all() if agents else [])]
    listed.sort(key=lambda s: (ROLE_ORDER.index(s['role']) if s['role'] in ROLE_ORDER else len(ROLE_ORDER), s['id']))
    roles = {a['role'] for a in listed}
    return listed + [{'id': role, 'soon': True} for role in COMING_SOON if role not in roles]


def status(live: Live, settings: dict, srs: dict, agents=None) -> dict:
    agents = agents or live.agents
    return {'running': live.running, 'frequency': frequency_label(agents),
            'tiles': tiles(live, settings, srs, agents), 'exchanges': list(live.exchanges),
            'services': services(settings, agents)}
