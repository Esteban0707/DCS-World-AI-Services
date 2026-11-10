"""A controller agent (role "controller", e.g. "Magic"): the air controller who links the air tasks with the pilots, as
an AWACS or a ground control centre (CRC / GCI) does (design agreed with the user 2026-11-08, BLUEPRINT "Controller
Magic").

A function, not an aircraft: its eyes are any radar groups of the mission (agent.json "controller": {sensors: [group
names], name}): an AWACS or E-2, ground EWRs, airfield radars, ships. Its air picture is what they see together, with
the airfield radars' model (dcs/instructor_radar.lua own_contacts: DCS's sensor distances, RCS, terrain, horizon), and
its receivers hear enemy radars that are switched on (ESM, emitters): an AWACS's radar does not see a SAM on the
ground, its emissions it does. A sensor lost shrinks the picture; nothing else is known.
It is not the router of the whole chain of command: the other agents reach it on the internal net (request_air_support
from a unit's own eyes, shared/net.py) and it keeps the tasks on a board here, in code; the pilot checks in with his
loadout, it gives him a task that fits and hands him over to the unit on that unit's frequency. The pilot cannot
command it or its AWACS (the AWACS flies the editor's route); frequencies are given on a need-to-know basis (an agent's
"frequency_sharing": "public", else only an agent of the pilot's task).
"""
import math
import re
import time

from instructor import geo
from shared import coords
from shared.cockpit import SENSOR_PREFIX

GROUPS_STALE_S = 5.0
TRAFFIC_STALE_S = 5.0
WATCH_EVERY_S = 5.0
KT_PER_MS, FT_PER_M = 1.943844, 3.280840
HORIZON_KM_PER_SQRT_M = 4.12
RADIO_MAX_KM = 450.0          # an AWACS's UHF reaches about as far as its horizon; this caps a ground station
GROUND_ANTENNA_M = 20.0       # a ground sensor's radio mast
THREAT_NM = 40.0              # a hostile track this close to a checked-in pilot gets a threat call
THREAT_RECALL_S = 300.0       # the same track is not called again before this
# Flown 2026-11-08: two hostile tracks at 63 and 100 nm and an SA-10 radar at 100 nm were never called (the calls
# were for 40 / 60 nm only). A controller calls every new hostile group it picks up and every new radar it hears, at
# any range, once ("new group..."), and the threat call again when one closes within THREAT_NM.
LOST_CONFIRM_S = 8.0          # a sensor is "lost" only after this long with the mission running (leaving the
                              # mission also makes every group "not exist")
EMITTER_MEMORY_S = 900.0      # a radar that went silent is remembered (last heard) this long
CHECK_IN_KEPT_S = 4 * 3600.0
TASK_KINDS = ('CAS', 'CAP', 'strike', 'recon', 'transport', 'escort', 'other')
TASK_STATES = ('open', 'assigned', 'en_route', 'on_station', 'done', 'cancelled', 'refused')
LIVE_STATES = ('open', 'assigned', 'en_route', 'on_station')
SHARING = ('public', 'task')  # agent.json "frequency_sharing" of any agent; default "task"
# User, 2026-11-08 (flown): after a task the pilot was left with nothing; a controller sends him to a station to hold
# for the next task, and routes him clear of the threats it knows. Stations are the mission maker's: the zones named
# in agent.json "controller": {"stations": [...]}, else the mission's zones whose name says so (CAP North, Hold
# Alpha, Orbit 2, IP West...). With none, a holding point is made 20 nm from the pilot back towards its sensor.
STATION_WORDS = re.compile(r'\b(cap|station|stn|hold|holding|orbit|marshal|push|cp|ip)\b', re.I)
STATION_REACHED_NM = 5.0
FALLBACK_HOLD_NM = 20.0
THREAT_RING_NM = 25.0         # a SAM's reach is not known from its radar: keep this far from one heard
STATIONS_SHOWN = 6


def aspect(track_heading: float, bearing_to_pilot: float) -> str:
    """How a track flies relative to the pilot: hot (towards him), cold (away), flanking or beaming."""
    off = abs((track_heading - bearing_to_pilot + 180) % 360 - 180)
    return 'hot' if off <= 30 else 'flanking' if off <= 60 else 'beaming' if off <= 120 else 'cold'


def leg_miss_nm(start: tuple[float, float], end: tuple[float, float], point: tuple[float, float]) -> float:
    """How close a straight leg passes to a point (flat earth around the start: a few hundred miles is enough)."""
    scale = math.cos(math.radians(start[0]))

    def xy(p):
        return (p[1] - start[1]) * 60.0 * scale, (p[0] - start[0]) * 60.0

    (bx, by), (px, py) = xy(end), xy(point)
    length = bx * bx + by * by
    t = 0.0 if length == 0 else max(0.0, min(1.0, (px * bx + py * by) / length))
    return math.hypot(px - t * bx, py - t * by)


class ControllerDesk:
    CONFIG_KEY = 'controller'

    def __init__(self, state, agent: dict, tasks=None, agents=None, names=None):
        """names: ReportingNames (shared/reporting_names.py), DCS's NATO names for the radars heard."""
        self.state, self.tasks, self.agents, self.names = state, tasks, agents, names
        self.board: list[dict] = []          # the task board (its "ATO")
        self.task_numbers = 0
        self.checked_in: dict | None = None  # {callsign, loadout, fuel, at}
        self.track_ids: dict[tuple, int] = {}
        self.emitter_memory: dict[str, dict] = {}   # enemy group -> {type, lat, lon, tracking, at, first}
        self.called_tracks: dict[int, float] = {}   # track number -> when its threat call was made
        self.new_tracks_called: set[int] = set()    # hostile tracks already called as a new group
        self.called_emitters: set[str] = set()
        self.lost_since: dict[str, float] = {}      # sensor -> since when it is not reported alive
        self.called_tasks: set[str] = set()
        self.sensors_seen: dict[str, bool] = {}     # sensor -> alive at the last tick
        self.station: dict | None = None            # where the pilot was sent to hold {name, lat, lon, altitude_ft}
        self.pending: list[dict] = []
        self.configure(agent)

    def configure(self, agent: dict) -> None:
        config = agent.get(self.CONFIG_KEY) or {}
        self.owner = agent.get('id') or ''
        self.sensors = [g for g in config.get('sensors') or [] if isinstance(g, str) and g]
        self.name = config.get('name') or agent.get('name', 'Magic')
        self.station_names = [s for s in config.get('stations') or [] if isinstance(s, str) and s.strip()]
        self.next_watch = 0.0
        for task in config.get('tasks') or []:   # tasks the mission maker wrote for the board (agent.json)
            if isinstance(task, dict) and task.get('what') and not any(t.get('from_mission') == task.get('what')
                                                                        for t in self.board):
                self.add_task(task.get('kind', 'other'), task['what'], contact=task.get('contact', ''),
                              where=task.get('where', ''), priority=task.get('priority', 'normal'),
                              from_mission=task['what'])

    # --- what it knows -------------------------------------------------------------------------------------------

    def sensor_status(self) -> dict[str, dict]:
        """Its sensors as DCS reports them (alive, where, radar, emitters); empty when the mission is not reporting."""
        at = self.state.groups_at
        if at is None or time.monotonic() - at > GROUPS_STALE_S:
            return {}
        found = getattr(self.state, 'sensors', None) or {}
        return {name: found[name] for name in self.sensors if isinstance(found.get(name), dict)}

    def live_sensors(self) -> dict[str, dict]:
        return {n: s for n, s in self.sensor_status().items()
                if s.get('exists') and (s.get('alive') or 0) > 0 and 'lat' in s}

    def pilot(self) -> dict | None:
        """The pilot's aircraft from the mission's traffic (for the radio and the geometry, not shown as such)."""
        at = self.state.traffic_at
        if at is None or time.monotonic() - at > TRAFFIC_STALE_S:
            return None
        entry = next((a for a in self.state.traffic or [] if a.get('player')), None)
        return entry if entry and 'lat' in entry else None

    def contacts(self) -> list[dict]:
        """Every radar contact of its sensors together (the same aircraft seen by two counts once)."""
        seen = {}
        for sensor in self.live_sensors().values():
            radar = (sensor.get('sensor') or {}).get('radar')
            for c in (radar or {}).get('contacts') or []:
                if isinstance(c, dict) and all(isinstance(c.get(k), (int, float)) for k in ('lat', 'lon')):
                    seen.setdefault(c.get('unit') or id(c), c)
        return list(seen.values())

    def pilot_on_radar(self) -> dict | None:
        return next((c for c in self.contacts() if c.get('player')), None)

    def reference(self) -> tuple[tuple[float, float] | None, str]:
        """Where bearings and ranges are given from: the pilot when the radar sees him (BRAA), else its first sensor."""
        me = self.pilot_on_radar()
        if me:
            return (me['lat'], me['lon']), 'from the pilot (BRAA)'
        live = self.live_sensors()
        if live:
            first = next(iter(live.values()))
            return (first['lat'], first['lon']), 'from your own sensor (the pilot is not on your radar)'
        return None, ''

    def track_number(self, contact: dict) -> int:
        key = (contact.get('side'), contact.get('track_group') or contact.get('group') or contact.get('unit'))
        if key not in self.track_ids:
            self.track_ids[key] = len(self.track_ids) + 1
        return self.track_ids[key]

    def tracks(self) -> list[dict]:
        """The air picture as tracks: a formation is one track (count), numbered as a controller would; no names or
        types (a radar does not read them), side as IFF tells it."""
        origin, _ = self.reference()
        groups: dict[int, list[dict]] = {}
        for c in self.contacts():
            if c.get('player') or c.get('kind', 'air') != 'air':
                continue
            groups.setdefault(self.track_number(c), []).append(c)
        tracks = []
        for number, members in groups.items():
            lead = members[0]
            vx, vz = lead.get('vx') or 0.0, lead.get('vz') or 0.0
            heading = math.degrees(math.atan2(vz, vx)) % 360
            track = {'track': number, 'side': {'enemy': 'hostile'}.get(lead.get('side'), lead.get('side', 'unknown')),
                     'count': len(members), 'altitude_ft': round((lead.get('alt_m') or 0) * FT_PER_M, -3),
                     'heading_deg': round(heading), 'speed_kt': round(math.hypot(vx, vz) * KT_PER_MS, -1)}
            if origin:
                bearing = geo.bearing_deg(origin[0], origin[1], lead['lat'], lead['lon'])
                track['bearing_deg'] = round(bearing)
                track['range_nm'] = round(geo.distance_nm(origin[0], origin[1], lead['lat'], lead['lon']))
                if self.pilot_on_radar():
                    track['aspect'] = aspect(heading, (bearing + 180) % 360)
            tracks.append(track)
        return sorted(tracks, key=lambda t: t.get('range_nm', 0))

    def remember_emitters(self) -> None:
        now = time.monotonic()
        for sensor in self.live_sensors().values():
            for e in (sensor.get('sensor') or {}).get('emitters') or []:
                if isinstance(e, dict) and e.get('group') and all(isinstance(e.get(k), (int, float))
                                                                  for k in ('lat', 'lon')):
                    first = (self.emitter_memory.get(e['group']) or {}).get('first', now)
                    self.emitter_memory[e['group']] = {**e, 'at': now, 'first': first}
        for name in [n for n, e in self.emitter_memory.items() if now - e['at'] > EMITTER_MEMORY_S]:
            del self.emitter_memory[name]

    def radar_name(self, type_name) -> str:
        """The NATO name a controller would say (DCS's own table), else DCS's type name."""
        return self.names.name(type_name) if self.names and type_name else str(type_name or 'unknown')

    def emitters_view(self) -> list[dict]:
        """Enemy radars its receivers heard: the radar's type (ESM tells it), where from the pilot, on now or when last
        heard. No group names."""
        origin, _ = self.reference()
        now, view = time.monotonic(), []
        for e in self.emitter_memory.values():
            entry = {'radar_of': self.radar_name(e.get('type')), 'emitting_now': now - e['at'] <= GROUPS_STALE_S * 2,
                     'tracking_something': bool(e.get('tracking'))}
            if not entry['emitting_now']:
                entry['last_heard_min_ago'] = round((now - e['at']) / 60)
            if origin:
                entry['bearing_deg'] = round(geo.bearing_deg(origin[0], origin[1], e['lat'], e['lon']))
                entry['range_nm'] = round(geo.distance_nm(origin[0], origin[1], e['lat'], e['lon']))
            view.append(entry)
        return sorted(view, key=lambda e: e.get('range_nm', 0))

    def radio_link(self) -> dict | None:
        """Its radio to the pilot from the best placed sensor: line of sight over the earth's curve, both heights
        (an AWACS high up reaches hundreds of kilometres; a ground station much less)."""
        pilot = self.pilot()
        live = self.live_sensors()
        if not pilot or not live:
            return None
        best = None
        pilot_h = max(pilot.get('alt_m') or 0.0, 2.0) if pilot.get('in_air') else 2.0
        for sensor in live.values():
            own_h = max(sensor.get('alt_m') or 0.0, GROUND_ANTENNA_M) if (sensor.get('sensor') or {}).get('in_air') \
                else GROUND_ANTENNA_M
            horizon = min(HORIZON_KM_PER_SQRT_M * (math.sqrt(own_h) + math.sqrt(pilot_h)), RADIO_MAX_KM)
            km = geo.distance_nm(sensor['lat'], sensor['lon'], pilot['lat'], pilot['lon']) * 1.852
            if best is None or km - horizon < best['distance_km'] - best['horizon_km']:
                best = {'distance_km': round(km, 1), 'horizon_km': round(horizon), 'pilot_hears': km <= horizon}
        return best

    def can_reach_pilot(self) -> bool:
        link = self.radio_link()
        return link is None or link['pilot_hears']

    def prefilter(self, text: str) -> str | None:
        """'' = nobody hears the pilot: every sensor (its radios) is lost, or the pilot is beyond its radio horizon."""
        if self.sensors and self.sensor_status() and not self.live_sensors():
            return ''
        link = self.radio_link()
        return None if link is None or link['pilot_hears'] else ''

    # --- the task board ------------------------------------------------------------------------------------------

    def add_task(self, kind: str, what: str, contact: str = '', where: str = '', lat=None, lon=None,
                 priority: str = 'normal', requested_by: str = '', from_mission: str = '') -> dict:
        self.task_numbers += 1
        task = {'id': f'T{self.task_numbers}', 'kind': kind if kind in TASK_KINDS else 'other',
                'what': ' '.join(str(what).split())[:160], 'status': 'open', 'contact': contact,
                'where': where, 'priority': 'high' if priority == 'high' else 'normal',
                'requested_by': requested_by, 'created': time.monotonic()}
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            task['lat'], task['lon'] = float(lat), float(lon)
        if from_mission:
            task['from_mission'] = from_mission
        self.board.append(task)
        return task

    def find_task(self, task_id: str) -> dict | None:
        wanted = str(task_id or '').strip().upper()
        return next((t for t in self.board if t['id'] == wanted), None)

    def agent_named(self, who: str) -> dict | None:
        if not self.agents or not who:
            return None
        return self.agents.get(who) or next((a for a in self.agents.active()
                                              if geo.simplify(a.get('name', '')) == geo.simplify(who)), None)

    def task_view(self, task: dict) -> dict:
        view = {k: task[k] for k in ('id', 'kind', 'what', 'status', 'priority') if task.get(k)}
        contact = self.agent_named(task.get('contact'))
        if contact:
            view['contact'] = contact['name']
        if task.get('where'):
            view['where'] = task['where']
        origin, _ = self.reference()
        if origin and 'lat' in task:
            view['bearing_deg'] = round(geo.bearing_deg(origin[0], origin[1], task['lat'], task['lon']))
            view['range_nm'] = round(geo.distance_nm(origin[0], origin[1], task['lat'], task['lon']))
        if task.get('assigned_to'):
            view['assigned_to'] = task['assigned_to']
        if task.get('note'):
            view['note'] = task['note']
        return view

    def pilot_task(self) -> dict | None:
        callsign = (self.checked_in or {}).get('callsign')
        return next((t for t in self.board if t.get('assigned_to') == callsign and t['status'] in LIVE_STATES[1:]),
                    None) if callsign else None

    def receive_target(self, event: dict) -> str | None:
        """A unit's request for air support on the net (shared/net.py request_air_support): a task on the board, open,
        with the unit's own sighting as the point and the unit as the contact the pilot is handed to."""
        target = event.get('target') or {}
        if target.get('kind') != 'air_support' or not all(isinstance(target.get(k), (int, float))
                                                          for k in ('lat', 'lon')):
            return None
        task = self.add_task('CAS', target.get('what') or 'air support', contact=event.get('from', ''),
                             lat=target['lat'], lon=target['lon'], requested_by=event.get('from', ''),
                             priority='high' if target.get('urgent') else 'normal')
        return task['id']

    def target_words(self, event: dict, task_id: str) -> str:
        return (f'{event.get("from_name", "?")} asked for air support; it is task {task_id} on your board (open, '
                f'{event.get("from_name", "?")} is the contact). Answer {event.get("from_name", "?")} on the net that '
                'it is on the board and you will send aircraft when one is available, and that the pilot will call '
                'it on its own frequency. Your answer here goes on the net, not on the radio: the offer to a '
                'checked-in pilot comes as a call of your own right after.')

    # --- stations and routes -------------------------------------------------------------------------------------

    def places(self) -> list[dict]:
        """The mission's zones and the pilot's F10 marks with a centre: the points it and the pilot share."""
        return [z for z in self.state.zones or [] if z.get('name') and all(isinstance(z.get(k), (int, float))
                                                                            for k in ('lat', 'lon'))]

    def stations(self) -> list[dict]:
        places = self.places()
        if self.station_names:
            wanted = {geo.simplify(n) for n in self.station_names}
            return [z for z in places if geo.simplify(z['name']) in wanted]
        return [z for z in places if not z.get('mark') and STATION_WORDS.search(z['name'])]

    def threats(self) -> list[tuple[tuple[float, float], str]]:
        """Fixed threats it knows: the enemy radars its receivers heard (moving tracks are called, not routed)."""
        return [((e['lat'], e['lon']), f'the enemy {self.radar_name(e.get("type"))}')
                for e in self.emitter_memory.values()]

    def route(self, start: tuple[float, float], end: tuple[float, float]) -> dict:
        """The direct line, or one known point to pass by when the direct line goes through a threat ring."""
        threats = self.threats()

        def blocked(a, b):
            return [words for point, words in threats if leg_miss_nm(a, b, point) < THREAT_RING_NM]

        on_end = [words for point, words in threats
                  if geo.distance_nm(end[0], end[1], point[0], point[1]) < THREAT_RING_NM]
        direct = [w for w in blocked(start, end) if w not in on_end]
        if not direct:
            return {'clear': True, 'inside': on_end}
        best = None
        for place in self.places():
            via = (place['lat'], place['lon'])
            if [w for w in blocked(start, via) + blocked(via, end) if w not in on_end]:
                continue
            length = geo.distance_nm(*start, *via) + geo.distance_nm(*via, *end)
            if best is None or length < best[0]:
                best = (length, place)
        return {'clear': False, 'threats': direct, 'via': best[1] if best else None, 'inside': on_end}

    def route_words(self, start, end) -> str:
        route = self.route(start, end)
        words = ''
        if not route['clear']:
            words = f'; the direct line passes within {THREAT_RING_NM:.0f} nm of {" and ".join(route["threats"])}'
            via = route['via']
            if via:
                origin, _ = self.reference()
                where = ''
                if origin:
                    where = (f' (bearing {geo.bearing_deg(*origin, via["lat"], via["lon"]):03.0f}, '
                             f'{geo.distance_nm(*origin, via["lat"], via["lon"]):.0f} nm from him)')
                words += f': route him via {via["name"]}{where}, then direct'
            else:
                words += ': no known point routes clear of it, tell him to keep well clear of it'
        if route['inside']:
            words += f'; the point itself is within {THREAT_RING_NM:.0f} nm of {" and ".join(route["inside"])}: ' \
                     'warn him'
        return words

    def pilot_point(self) -> tuple[float, float] | None:
        me = self.pilot_on_radar() or self.pilot()
        return (me['lat'], me['lon']) if me else None

    def holding_point(self, start: tuple[float, float]) -> dict:
        """No station in the mission: a point back towards its nearest sensor (the friendly side), or his own."""
        sensors = sorted(self.live_sensors().values(),
                         key=lambda s: geo.distance_nm(*start, s['lat'], s['lon']))
        if sensors and geo.distance_nm(*start, sensors[0]['lat'], sensors[0]['lon']) > FALLBACK_HOLD_NM * 1.5:
            bearing = geo.bearing_deg(*start, sensors[0]['lat'], sensors[0]['lon'])
            lat, lon = geo.offset_point(*start, bearing, FALLBACK_HOLD_NM * 1852.0)
            return {'name': 'a holding point', 'lat': lat, 'lon': lon}
        return {'name': 'his present position', 'lat': start[0], 'lon': start[1]}

    def station_view(self) -> dict | None:
        if not self.station:
            return None
        view = {'name': self.station['name'], 'reached': self.station.get('reached', False)}
        if self.station.get('altitude_ft'):
            view['altitude_ft'] = self.station['altitude_ft']
        origin, _ = self.reference()
        if origin:
            view['bearing_deg'] = round(geo.bearing_deg(*origin, self.station['lat'], self.station['lon']))
            view['range_nm'] = round(geo.distance_nm(*origin, self.station['lat'], self.station['lon']))
        return view

    # --- tools ---------------------------------------------------------------------------------------------------

    def check_in(self, loadout: str, fuel: str = '', notes: str = '') -> dict:
        """The pilot checked in on its frequency: airborne, with what he carries (as he said it)."""
        pilot = self.pilot()
        if pilot is not None and not pilot.get('in_air'):
            return {'ok': False, 'detail': 'the pilot is still on the ground: he checks in once airborne'}
        if not str(loadout or '').strip():
            return {'ok': False, 'detail': 'ask the pilot what he carries (weapons) and his fuel first'}
        self.checked_in = {'callsign': self.state.callsign or 'the pilot', 'loadout': str(loadout)[:200],
                           'fuel': str(fuel or '')[:60], 'notes': str(notes or '')[:200], 'at': time.monotonic()}
        open_tasks = [t['id'] for t in self.board if t['status'] == 'open']
        radar = 'he is on your radar' if self.pilot_on_radar() else 'he is NOT on your radar now'
        # Flown 2026-11-08: Magic answered the check-in with "no tasks" and never the picture it had. A controller
        # gives the picture on check-in; what is told now is not called again as new.
        self.remember_emitters()
        hostile = [t for t in self.tracks() if t['side'] == 'hostile']
        self.new_tracks_called.update(t['track'] for t in hostile)
        self.called_emitters.update(self.emitter_memory)
        picture = f'{len(hostile)} hostile track(s)' if hostile else 'no hostile tracks ("picture clean")'
        radars = len(self.emitter_memory)
        if radars:
            picture += f', {radars} enemy radar(s) heard'
        self.station = None
        detail = (f'{self.checked_in["callsign"]} checked in ({radar}); open tasks: {", ".join(open_tasks) or "none"}.'
                  f' Give him the picture now in a few words: {picture} (picture and enemy_radars_heard in your '
                  'situation, nearest first).')
        if not open_tasks:
            detail += ' No task for him yet: send him to a station to hold with assign_station.'
        return {'ok': True, 'detail': detail}

    def assign_task(self, task_id: str) -> dict:
        """Give an open task to the pilot: what, where (bearing / range from him, grid), who to contact and on which
        frequency (the handoff)."""
        if not self.checked_in:
            return {'ok': False, 'detail': 'the pilot has not checked in with you yet (check_in with his loadout)'}
        task = self.find_task(task_id)
        if task is None:
            return {'ok': False, 'detail': f'no task {task_id} on your board'}
        if task['status'] not in ('open', 'refused'):
            return {'ok': False, 'detail': f'task {task["id"]} is {task["status"]}'}
        current = self.pilot_task()
        if current and current is not task:
            current['status'], current['assigned_to'] = 'open', None   # retasked: the old one goes back on the board
        task['status'], task['assigned_to'] = 'assigned', self.checked_in['callsign']
        view = self.task_view(task)
        detail = f'task {task["id"]} assigned: {task["kind"]}, {task["what"]}'
        if 'bearing_deg' in view:
            detail += f'; bearing {view["bearing_deg"]:03d}, {view["range_nm"]} nautical miles'
        if 'lat' in task:
            detail += f'; grid {coords.to_mgrs(task["lat"], task["lon"], 4)}'
        contact = self.agent_named(task.get('contact'))
        start = self.pilot_point()
        if start and 'lat' in task:
            detail += self.route_words(start, (task['lat'], task['lon']))
        if contact and contact.get('frequency_mhz'):
            detail += f'; contact {contact["name"]} on {contact["frequency_mhz"]:.3f} {contact.get("modulation", "AM")}'
        if current and current is not task:
            detail += f' (task {current["id"]} back on the board)'
        self.station = None
        return {'ok': True, 'detail': detail}

    def update_task(self, task_id: str, status: str, note: str = '') -> dict:
        task = self.find_task(task_id)
        if task is None:
            return {'ok': False, 'detail': f'no task {task_id} on your board'}
        if status not in TASK_STATES:
            return {'ok': False, 'detail': f'status: {", ".join(TASK_STATES)}'}
        was_his = task is self.pilot_task()
        task['status'] = status
        if status in ('open', 'refused'):
            task['assigned_to'] = None
        if note:
            task['note'] = ' '.join(str(note).split())[:160]
        detail = f'task {task["id"]} is {status}'
        if was_his and status in ('done', 'cancelled', 'refused', 'open'):
            others = [t['id'] for t in self.board if t['status'] == 'open' and t is not task]
            detail += (f'; he is free now: offer him {", ".join(others)}' if others else
                       '; he is free now and no task is open') + \
                ', else send him to a station to hold with assign_station (RTB if he is low on fuel or out of weapons)'
        return {'ok': True, 'detail': detail}

    def assign_station(self, station: str = '', altitude_ft: float = 0) -> dict:
        """Send the checked-in pilot without a task to a station to hold for the next one: the one he or you named,
        else the nearest station clear of the threats known; with the route around them."""
        if not self.checked_in:
            return {'ok': False, 'detail': 'the pilot has not checked in with you yet (check_in with his loadout)'}
        task = self.pilot_task()
        if task:
            return {'ok': False, 'detail': f'he still has task {task["id"]} ({task["status"]}): mark it done, '
                                           'cancelled or refused with update_task first'}
        start = self.pilot_point()
        if start is None:
            return {'ok': False, 'detail': 'you do not know where he is (no news from the mission)'}
        if str(station or '').strip():
            wanted = geo.simplify(station)
            place = next((z for z in self.places() if geo.simplify(z['name']) == wanted), None)
            if place is None:
                names = ', '.join(z['name'] for z in self.stations()) or 'none in the mission'
                return {'ok': False, 'detail': f'no point called {station} (stations: {names}); leave it empty for '
                                               'the nearest one'}
        else:
            threats = [p for p, _ in self.threats()]

            def rank(z):
                hot = any(geo.distance_nm(z['lat'], z['lon'], *p) < THREAT_RING_NM for p in threats)
                return hot, geo.distance_nm(*start, z['lat'], z['lon'])

            stations = sorted(self.stations(), key=rank)
            place = stations[0] if stations else self.holding_point(start)
        altitude = round(float(altitude_ft)) if isinstance(altitude_ft, (int, float)) and altitude_ft > 0 else 0
        self.station = {'name': place['name'], 'lat': place['lat'], 'lon': place['lon'], 'altitude_ft': altitude}
        view = self.station_view()
        detail = f'{self.checked_in["callsign"]} to hold at {place["name"]}'
        if place['name'] != 'his present position' and 'bearing_deg' in view:
            detail += f': bearing {view["bearing_deg"]:03d}, {view["range_nm"]} nautical miles from him'
        if place['name'] == 'a holding point':
            detail += f', grid {coords.to_mgrs(place["lat"], place["lon"], 4)} (no station in the mission)'
        detail += f', angels {altitude / 1000:g}' if altitude else \
            ', give him an altitude that fits his aircraft (angels for a jet, low for a helicopter)'
        detail += self.route_words(start, (place['lat'], place['lon']))
        detail += '; tell him to hold there and await tasking ("hold at ..., await tasking"); RTB instead if he ' \
                  'says he is low on fuel or out of weapons'
        return {'ok': True, 'detail': detail}

    def give_frequency(self, agent: str) -> dict:
        """Another agent's frequency, need to know: given when that agent shares it ("frequency_sharing": "public") or
        is part of the pilot's task."""
        found = self.agent_named(agent)
        if found is None or found.get('id') == self.owner or found.get('role') == 'atc':
            return {'ok': False, 'detail': f'you know no station called {agent} (airfield frequencies are on the '
                                           'pilot\'s charts)'}
        task = self.pilot_task()
        involved = task is not None and found['id'] in (task.get('contact'), task.get('requested_by'))
        if found.get('frequency_sharing') != 'public' and not involved:
            return {'ok': False, 'detail': f'{found["name"]}\'s frequency is not for this pilot now: he has no task '
                                           f'with {found["name"]} (need to know)'}
        return {'ok': True, 'detail': f'{found["name"]}: {found["frequency_mhz"]:.3f} {found.get("modulation", "AM")}'}

    # --- the brain's situation -----------------------------------------------------------------------------------

    def situation(self) -> dict:
        self.remember_emitters()
        situation = {'aircraft': self.state.aircraft or 'unknown', 'callsign': self.state.callsign or 'unknown',
                     'you_are': self.name}
        status = self.sensor_status()
        if not self.sensors:
            situation['your_sensors'] = 'none set in the GUI: you see nothing'
        elif not status:
            situation['your_sensors'] = 'no news from the mission (is DCS running?)'
        else:
            situation['your_sensors'] = [
                {'name': name, 'type': (s.get('sensor') or {}).get('type'),
                 'state': 'lost' if not s.get('exists') or not s.get('alive') else
                          'airborne' if (s.get('sensor') or {}).get('in_air') else 'on the ground',
                 'radar_range_km': ((s.get('sensor') or {}).get('radar') or {}).get('range_km')}
                for name, s in status.items()]
        _, origin_words = self.reference()
        me = self.pilot_on_radar()
        pilot = {'on_your_radar': bool(me)}
        if me:
            vx, vz = me.get('vx') or 0.0, me.get('vz') or 0.0
            pilot.update(altitude_ft=round((me.get('alt_m') or 0) * FT_PER_M, -2),
                         heading_deg=round(math.degrees(math.atan2(vz, vx)) % 360),
                         speed_kt=round(math.hypot(vx, vz) * KT_PER_MS))
        pilot['checked_in'] = {k: v for k, v in (self.checked_in or {}).items() if k != 'at'} or False
        task = self.pilot_task()
        if task:
            pilot['task'] = self.task_view(task)
        station = self.station_view()
        if station and not task:
            pilot['holding_at'] = station
        situation['pilot'] = pilot
        origin, _ = self.reference()
        stations = [{'name': z['name'], **({'bearing_deg': round(geo.bearing_deg(*origin, z['lat'], z['lon'])),
                                             'range_nm': round(geo.distance_nm(*origin, z['lat'], z['lon']))}
                                            if origin else {})} for z in self.stations()]
        if stations:
            situation['stations'] = sorted(stations, key=lambda s: s.get('range_nm', 0))[:STATIONS_SHOWN]
        situation['picture'] = {'bearings': origin_words, 'tracks': self.tracks() or 'none'}
        emitters = self.emitters_view()
        if emitters:
            situation['enemy_radars_heard'] = emitters
        live = [self.task_view(t) for t in self.board if t['status'] in (*LIVE_STATES, 'refused')]
        situation['task_board'] = live or 'empty'
        link = self.radio_link()
        if link:
            situation['radio_with_pilot'] = {'distance_km': link['distance_km'], 'pilot_hears': link['pilot_hears']}
        return situation

    def facts(self) -> dict | None:
        return None   # no condition events of its own yet

    # --- its own calls -------------------------------------------------------------------------------------------

    def watch_picture(self) -> None:
        """Calls of its own to a checked-in pilot: a hostile track closing in, an enemy radar on his way or near his
        task, a new task on the board, a sensor lost."""
        status = self.sensor_status()
        now = time.monotonic()
        running = self.pilot() is not None   # the pilot is still in the mission (leaving it ends every group)
        for name, s in status.items():
            alive = bool(s.get('exists') and s.get('alive'))
            if alive or not running:
                self.lost_since.pop(name, None)
                if alive:
                    self.sensors_seen[name] = True
                continue
            since = self.lost_since.setdefault(name, now)
            if self.sensors_seen.get(name) and now - since >= LOST_CONFIRM_S:
                self.sensors_seen[name] = False
                if self.checked_in:
                    self.pending.append({'report': f'your sensor {name} is lost (shot down or destroyed): your '
                                                   'picture is smaller now; tell the pilot what you can no longer '
                                                   'see'})
        if not self.checked_in or time.monotonic() - self.checked_in['at'] > CHECK_IN_KEPT_S:
            return
        words = []
        me = self.pilot_point()
        if self.station and not self.station.get('reached') and me and \
                geo.distance_nm(*me, self.station['lat'], self.station['lon']) <= STATION_REACHED_NM:
            self.station['reached'] = True   # shown in his situation; the pilot reports "on station" himself
        for track in self.tracks():
            if track['side'] != 'hostile':
                continue
            where = f'bearing {track["bearing_deg"]:03d}, {track["range_nm"]} miles' if 'bearing_deg' in track else ''
            where += f' {"from the pilot" if "aspect" in track else "from your sensor"}' if where else ''
            what = f'hostile track {track["track"]} ({track["count"]} aircraft) {where}, {track["altitude_ft"]:.0f} ft' \
                   f'{", " + track["aspect"] if "aspect" in track else ""}'
            if track.get('range_nm', 1e9) <= THREAT_NM and 'aspect' in track and \
                    now - self.called_tracks.get(track['track'], -1e9) > THREAT_RECALL_S:
                self.called_tracks[track['track']] = now
                self.new_tracks_called.add(track['track'])
                words.append(f'THREAT: {what}: a threat call to the pilot now (BRAA)')
            elif track['track'] not in self.new_tracks_called:
                self.new_tracks_called.add(track['track'])
                words.append(f'new group on your radar: {what}: call it to the pilot ("new group")')
        for group, e in self.emitter_memory.items():
            if group in self.called_emitters or now - e['at'] > GROUPS_STALE_S * 2:
                continue
            self.called_emitters.add(group)
            words.append(f'your receivers hear an enemy {self.radar_name(e.get("type"))} switched on (see '
                         'enemy_radars_heard): warn the pilot where it is, from him')
        if words:
            self.pending.append({'report': '; '.join(words)})
        if self.pilot_task() is None:
            for task in self.board:
                if task['status'] == 'open' and task['id'] not in self.called_tasks:
                    self.called_tasks.add(task['id'])
                    self.pending.append({'report': f'task {task["id"]} is open on your board ({task["kind"]}, '
                                                   f'{task["what"]}) and the checked-in pilot has none: offer it to '
                                                   'him; assign_task only once he accepts'})

    def reports(self, busy: bool = False, quiet_s: float = 0.0) -> list[dict]:
        if busy or not self.pending:
            return []
        texts = '; '.join(p['report'] for p in self.pending)
        self.pending = []
        return [{'report': texts}]

    def tick(self, busy: bool = False) -> str | None:
        self.remember_emitters()
        self.watch_picture()
        if self.tasks and self.sensors and time.monotonic() >= self.next_watch:
            self.next_watch = time.monotonic() + WATCH_EVERY_S
            try:
                self.tasks.watch([SENSOR_PREFIX + g for g in self.sensors], owner=self.owner)
            except OSError:
                pass
        return None
