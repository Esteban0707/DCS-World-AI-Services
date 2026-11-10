"""A squad agent (role "squad"): an AI ground group the pilot talks to on its own radio, e.g. troops to carry.

User's design (2026-11-02): the model decides, our code acts. The squad's tools (squad/tools.py) are a fixed list;
each one checks what it can and sends one order: moves, waiting for pickup, holding, rules of engagement and smoke go
into the mission through the hook (client/dcs_tasks.py); boarding and leaving the player's helicopter go through
DCS's own radio menu (client/dcs_bridge.py, the "Descent" commands), as a pilot's menu would do it.

What the squad knows (realism rule): its own group (from the mission), the mission's named zones (its briefing), and
the pilot's helicopter only while it is in sight (VISUAL_RANGE_NM); farther away it only hears the radio. Enemies
come from its soldiers' own AI detection in DCS (what they see or hear, enemies only), and losses since its last
message are counted here (2026-11-03: Victor lost 3 of 4 soldiers and never said so).
agent.json "squad": {groups: [DCS group names, the first one is the squad], name: how it calls itself}.

Ground vehicles are their own role ("vehicles", vehicles/desk.py), built on this desk.
"""
import math
import time

from instructor import geo
from instructor.phase import ON_GROUND_AGL_M
from shared import coords, smoke
from shared.triggers import PilotRequests

GROUPS_STALE_S = 5.0
TRAFFIC_STALE_S = 5.0
VISUAL_RANGE_NM = 3.0
WATCH_EVERY_S = 5.0       # the hook forgets the watched groups when DCS restarts: say them again
ON_BOARD_EVERY_S = 3.0    # how often DCS's radio menu is asked who is aboard the pilot's helicopter
REPLY_WAIT_S = 3.0        # how long a tool waits for DCS's answer
PICKUP_RADIUS_M = 200
ZONES_SHOWN = 6
ZONE_RADIUS_M = 300       # "zone reached" for a zone the mission gives no radius
# DCS's detection flickers (live 2026-11-03: Victor under fire saw M1, then "no enemies in sight" seconds later and
# refused "attack" twice): enemies are remembered with their last known place for a while.
ENEMY_MEMORY_S = 180.0    # shown as out of sight (and attackable) this long after last seen
ENEMY_HOLD_S = 30.0       # for the condition events, enemies still count as seen this long (no re-fire on flicker)
LOST_CONFIRM_S = 8.0      # "wiped out" only after this long with the mission still running (live 2026-11-03: both
                          # squads "wiped out" at once when the pilot left the mission)
# Its own calls when hit (user, 2026-11-05: "if it loses a soldier or takes damage, it reports it; if Tango is killed it
# stops answering"): a loss at once; wounds / damage when the living units' life drops by HIT_STEP of what it was new.
HIT_STEP = 0.1
# Radio range (user's design, 2026-11-03: realistic, terrain included, our own model rather than SRS's, which knows
# nothing of the ground unit's radio): DCS says whether the terrain blocks the path between the group's antenna and
# the aircraft (dcs/instructor_tasks.lua "radio": line of sight, distance, manpack or vehicle). Both ways need line of
# sight within the radio horizon of both antennas; the aircraft's radio reaches that far, the ground radio only up to
# its power limit. So the squad may hear the pilot (and obey) while the pilot cannot hear its answer.
RADIOS = {'manpack': {'antenna_m': 2.0, 'max_km': 30.0}, 'vehicle': {'antenna_m': 3.0, 'max_km': 60.0},
          # A ship's masthead antenna (ships/desk.py): its UHF reaches as far as the radio horizon, about 410 km to an
          # aircraft at 9000 m (4.12 * (sqrt 25 + sqrt 9000) km); the power is not what limits it.
          'ship': {'antenna_m': 25.0, 'max_km': 420.0}}
# Close by, a radio does not need a clear line: the signal bends and bounces round small ground (live 2026-11-05: the
# pilot landed beside Tango and was not heard, a bump in the terrain cut the line between antennas a metre or two up).
RADIO_NEAR_KM = 2.0
HORIZON_KM_PER_SQRT_M = 4.12   # radio horizon: 4.12 * (sqrt(h1 m) + sqrt(h2 m)) km
FT_PER_M, KT_PER_MS = 3.280840, 1.943844


class SquadDesk(PilotRequests):   # the pilot's "tell me when..." (tool tell_pilot_when)
    CONFIG_KEY = 'squad'   # agent.json key of its groups and name (a vehicle unit's: "vehicles")
    KIND = 'infantry'
    UNIT_KEY = 'squad'     # the situation's entry for its own unit

    def __init__(self, state, agent: dict, tasks=None, bridge=None):
        self.state, self.tasks, self.bridge = state, tasks, bridge
        self.configure(agent)
        # Aboard comes from DCS's radio menu (troops_on_board): while aboard, the mission still reports the group
        # where it embarked, alive and standing still (live 2026-11-02), so its status cannot tell.
        self.on_board: bool | None = None   # None: not asked yet
        self.on_board_request: str | None = None
        self.next_on_board = 0.0
        self.pending: list[dict] = []        # calls of its own for the voice loop (reports())
        self.carried_seen: tuple | None = None   # (stage, carrier) of state.transport at the last tick

    def configure(self, agent: dict) -> None:
        """Its group and name from agent.json; again after a change in the GUI (the hook is told the new group at
        once)."""
        config = agent.get(self.CONFIG_KEY) or {}
        self.owner = agent.get('id') or ''   # the hook's watch list holds every squad's groups (client/dcs_tasks.py)
        self.groups: list[str] = [g for g in config.get('groups') or [] if isinstance(g, str) and g]
        self.group = self.groups[0] if self.groups else ''
        self.name = config.get('name') or self.group or agent.get('name', 'squad')
        names = getattr(self.state, 'agent_names', None)
        if self.group and agent.get('name') and names is not None:
            names[self.group] = agent['name']
        self.next_watch = 0.0
        self.reported_alive: int | None = None   # soldiers alive at the last situation the model saw
        self.watched_alive: int | None = None    # alive / damage at the last tick, for its own calls when hit
        self.watched_damage: float | None = None
        self.enemy_memory: dict[str, dict] = {}   # enemy group -> {group, units, type, lat, lon, at}
        self.lost_since: float | None = None
        self.requests: list[dict] = []           # the pilot's "tell me when...": condition rows of this run only

    # --- what the squad knows ------------------------------------------------------------------------------------

    def status(self) -> dict | None:
        at = self.state.groups_at
        if at is None or time.monotonic() - at > GROUPS_STALE_S:
            return None
        return (self.state.groups or {}).get(self.group)

    def helicopter(self) -> dict | None:
        """The pilot's aircraft: lat, lon, alt_m, in_air, speed_kt (mission traffic when fresh, else the export)."""
        at = self.state.traffic_at
        if at is not None and time.monotonic() - at <= TRAFFIC_STALE_S:
            entry = next((a for a in self.state.traffic or [] if a.get('player')), None)
            if entry and 'lat' in entry:
                return {'lat': entry['lat'], 'lon': entry['lon'], 'alt_m': entry.get('alt_m') or 0.0,
                        'in_air': bool(entry.get('in_air')),
                        'speed_kt': math.hypot(entry.get('vx') or 0, entry.get('vz') or 0) * KT_PER_MS}
        flight = self.state.flight or {}
        if not all(isinstance(flight.get(k), (int, float)) for k in ('lat', 'lon')):
            return None
        agl = flight.get('alt_agl_m')
        return {'lat': flight['lat'], 'lon': flight['lon'], 'alt_m': flight.get('alt_msl_m') or 0.0,
                'in_air': isinstance(agl, (int, float)) and agl >= ON_GROUND_AGL_M, 'speed_kt': None}

    def called(self, group: str) -> str:
        """How the pilot calls another agent's group: the agent's name (the GUI's), else the group's name."""
        return (getattr(self.state, 'agent_names', None) or {}).get(group) or group

    def kind(self, status: dict | None = None) -> str:
        return self.KIND

    def carrier(self, stage: str | None = None) -> str | None:
        """The agent's AI helicopter or vehicle (by its agent's name) picking the squad up, carrying it or dropping it
        (wingman/desk.py or vehicles/desk.py pick_up, shared through state.transport), or None; with stage, only in
        that stage (pickup / aboard / drop)."""
        entry = (getattr(self.state, 'transport', None) or {}).get(self.group)
        if not entry or (stage and entry.get('stage') != stage):
            return None
        return self.called(entry.get('carrier') or '')

    def ride(self) -> str:
        """What carries the squad: "Cobra's helicopter", "Bison's vehicle"."""
        entry = (getattr(self.state, 'transport', None) or {}).get(self.group) or {}
        return f'{self.called(entry.get("carrier") or "")}\'s {entry.get("ride") or "helicopter"}'

    def aboard(self, status: dict | None = None) -> bool:
        """Aboard the pilot's helicopter (DCS's radio menu says) or an agent's AI helicopter (state.transport)."""
        return bool(self.on_board) or bool(self.carrier('aboard') or self.carrier('drop'))

    def on_board_reply(self, reply: dict | None) -> None:
        if reply and reply.get('status') == 'sent':
            names = [n.strip() for n in str(reply.get('detail') or '').split(',')]
            self.on_board = self.group in names

    def helicopter_view(self, status: dict | None) -> dict:
        heli = self.helicopter()
        if not heli or not status or 'lat' not in status:
            return {'in_sight': False}
        distance = geo.distance_nm(status['lat'], status['lon'], heli['lat'], heli['lon'])
        if distance > VISUAL_RANGE_NM:
            return {'in_sight': False, 'note': 'you only hear the pilot on the radio'}
        bearing = geo.bearing_deg(status['lat'], status['lon'], heli['lat'], heli['lon'])
        view = {'in_sight': True, 'distance_m': round(distance * 1852, -1), 'direction': geo.cardinal(bearing),
                'state': 'airborne' if heli['in_air'] else 'landed'}
        if heli['in_air']:
            view['height_above_you_ft'] = round((heli['alt_m'] - (status.get('alt_m') or 0)) * FT_PER_M, -1)
        if heli.get('speed_kt') is not None:
            view['moving'] = heli['speed_kt'] >= 5
        return view

    def aircraft_in_sight(self, status: dict | None) -> dict[str, dict]:
        """The other agents' aircraft (an AI wingman's helicopter: user, 2026-11-05, Tango did not see Cobra landed
        beside it) within sight, VISUAL_RANGE_NM, by the agent's name: where, landed or airborne, what type. The mission
        reports every agent's groups in the same message (dcs/instructor_tasks.lua status)."""
        if not status or 'lat' not in status:
            return {}
        seen = {}
        for name, group in (self.state.groups or {}).items():
            air = group.get('air') if isinstance(group, dict) else None
            if name in self.groups or not isinstance(air, dict) or not air.get('active') or \
                    not group.get('exists') or 'lat' not in group:
                continue
            distance = geo.distance_nm(status['lat'], status['lon'], group['lat'], group['lon'])
            if distance > VISUAL_RANGE_NM:
                continue
            bearing = geo.bearing_deg(status['lat'], status['lon'], group['lat'], group['lon'])
            view = {'type': air.get('type'), 'kind': air.get('kind') or 'airplane',
                    'distance_m': round(distance * 1852, -1), 'direction': geo.cardinal(bearing),
                    'state': 'airborne' if air.get('in_air') else 'landed',
                    'lat': group['lat'], 'lon': group['lon']}
            if air.get('in_air'):
                view['height_above_you_ft'] = round(((group.get('alt_m') or 0) - (status.get('alt_m') or 0)) *
                                                    FT_PER_M, -1)
            seen[self.called(name)] = view
        return seen

    def friends_in_sight(self, status: dict | None) -> dict[str, dict]:
        """The other agents' ground units (squads, vehicles) within sight, VISUAL_RANGE_NM, by the agent's name (user,
        2026-11-05: Bison could not see Tango beside it): where, infantry or vehicles, moving."""
        if not status or 'lat' not in status:
            return {}
        seen = {}
        for name, group in (self.state.groups or {}).items():
            if name in self.groups or not isinstance(group, dict) or 'air' in group or not group.get('exists') or \
                    'lat' not in group:
                continue
            distance = geo.distance_nm(status['lat'], status['lon'], group['lat'], group['lon'])
            if distance > VISUAL_RANGE_NM:
                continue
            ground = group.get('ground') if isinstance(group.get('ground'), dict) else {}
            if isinstance(group.get('ship'), dict):   # a ship agent (Trident seen by Mother was called "vehicles")
                kind = 'ship'
            else:
                kind = 'vehicles' if (ground.get('vehicles') or 0) > (ground.get('infantry') or 0) else 'infantry'
            seen[self.called(name)] = {'kind': kind, 'distance_m': round(distance * 1852, -1),
                                       'direction': geo.cardinal(geo.bearing_deg(status['lat'], status['lon'],
                                                                                 group['lat'], group['lon'])),
                                       'moving': (group.get('speed_ms') or 0) >= 0.5,
                                       'lat': group['lat'], 'lon': group['lon']}
        return seen

    def smoke_in_sight(self, status: dict | None) -> list[dict]:
        if not status or 'lat' not in status:
            return []
        return smoke.in_sight(self.state, status['lat'], status['lon'], smoke.SEEN_FROM_GROUND_M)

    def zones_view(self, status: dict | None) -> list[dict]:
        zones = [z for z in self.state.zones or [] if all(k in z for k in ('name', 'lat', 'lon'))]
        if status and 'lat' in status:   # distances from the squad, nearest first
            zones = sorted(({**z, 'distance_km': round(geo.distance_nm(status['lat'], status['lon'], z['lat'],
                                                                         z['lon']) * 1.852, 1)} for z in zones),
                           key=lambda z: z['distance_km'])
        return [{k: z[k] for k in ('name', 'distance_km') if k in z} for z in zones[:ZONES_SHOWN]]

    def enemies_view(self, status: dict | None) -> list[dict]:
        """The enemy groups the squad's soldiers detect (dcs/instructor_tasks.lua), nearest first."""
        found = (status or {}).get('enemies')
        if not isinstance(found, list) or 'lat' not in (status or {}):
            return []
        enemies = []
        for enemy in found:
            if not (isinstance(enemy, dict) and enemy.get('group') and all(k in enemy for k in ('lat', 'lon'))):
                continue
            distance = geo.distance_nm(status['lat'], status['lon'], enemy['lat'], enemy['lon'])
            bearing = geo.bearing_deg(status['lat'], status['lon'], enemy['lat'], enemy['lon'])
            enemies.append({'group': enemy['group'], 'units': enemy.get('units'), 'type': enemy.get('type'),
                            'distance_m': round(distance * 1852, -1), 'direction': geo.cardinal(bearing)})
        return sorted(enemies, key=lambda e: e['distance_m'])

    def remember_enemies(self, status: dict | None) -> None:
        now = time.monotonic()
        for enemy in (status or {}).get('enemies') or []:
            if isinstance(enemy, dict) and enemy.get('group') and all(k in enemy for k in ('lat', 'lon')):
                self.enemy_memory[enemy['group']] = {**enemy, 'at': now}
        for name in [n for n, e in self.enemy_memory.items() if now - e['at'] > ENEMY_MEMORY_S]:
            del self.enemy_memory[name]

    def enemies_out_of_sight(self, status: dict | None) -> list[dict]:
        """Enemy groups seen lately, not in sight now: where they were last seen, and how long ago."""
        if not status or 'lat' not in status:
            return []
        in_sight = {e['group'] for e in self.enemies_view(status)}
        now, gone = time.monotonic(), []
        for name, enemy in self.enemy_memory.items():
            if name in in_sight:
                continue
            distance = geo.distance_nm(status['lat'], status['lon'], enemy['lat'], enemy['lon'])
            bearing = geo.bearing_deg(status['lat'], status['lon'], enemy['lat'], enemy['lon'])
            gone.append({'group': name, 'units_when_seen': enemy.get('units'), 'type': enemy.get('type'),
                         'last_seen_s_ago': round(now - enemy['at']),
                         'last_known_distance_m': round(distance * 1852, -1),
                         'last_known_direction': geo.cardinal(bearing)})
        return sorted(gone, key=lambda e: e['last_seen_s_ago'])

    def enemy_names(self) -> list[str]:
        """Enemy groups the squad may attack: in sight, or seen lately (ENEMY_MEMORY_S)."""
        status = self.status()
        self.remember_enemies(status)
        names = [e['group'] for e in self.enemies_view(status)]
        return names + [n for n in self.enemy_memory if n not in names]

    def radio_link(self, status: dict | None = None) -> dict | None:
        """{hears_pilot, pilot_hears, distance_km, line_of_sight, radio} between the group and the pilot's aircraft;
        None when not known (no news, no aircraft) or aboard (then they talk face to face)."""
        status = status if status is not None else self.status()
        radio = (status or {}).get('radio')
        if not self.group or not isinstance(radio, dict) or self.aboard(status) or not status.get('exists'):
            return None
        kind = radio.get('kind') if radio.get('kind') in RADIOS else 'manpack'
        heli = self.helicopter()
        height = max(0.0, (heli or {}).get('alt_m', 0.0) - (status.get('alt_m') or 0.0)) if heli and heli['in_air'] \
            else 1.0
        horizon = HORIZON_KM_PER_SQRT_M * (math.sqrt(RADIOS[kind]['antenna_m']) + math.sqrt(max(height, 1.0)))
        distance = float(radio.get('distance_m') or 0.0) / 1000
        sight = radio.get('line_of_sight') is not False
        hears = distance <= RADIO_NEAR_KM or (sight and distance <= horizon)
        return {'hears_pilot': hears, 'pilot_hears': hears and distance <= RADIOS[kind]['max_km'],
                'distance_km': round(distance, 1), 'line_of_sight': sight, 'radio': kind}

    def can_reach_pilot(self) -> bool:
        """The group's own radio reaches the pilot (its answers and calls are heard)."""
        link = self.radio_link()
        return link is None or link['pilot_hears']

    def dead(self, status: dict | None = None) -> bool:
        """Killed: the mission reports the group gone or nobody alive, and it is not riding anything (DCS may report
        a carried group gone)."""
        status = status if status is not None else self.status()
        if not status or self.aboard(status) or self.carrier():
            return False
        return not status.get('exists') or status.get('alive') == 0

    def silent(self, event: dict | None = None) -> bool:
        """Killed: it calls nothing (instructor/voice_loop.py event_call)."""
        return self.dead()

    @staticmethod
    def damage(status: dict | None) -> float | None:
        """How hurt its living units are, 0..1, from their life (dcs/instructor_tasks.lua ground_details)."""
        ground = (status or {}).get('ground')
        life, life0 = (ground.get('life'), ground.get('life0')) if isinstance(ground, dict) else (None, None)
        if not isinstance(life, (int, float)) or not isinstance(life0, (int, float)) or life0 <= 0:
            return None
        return max(0.0, min(1.0, 1 - life / life0))

    def watch_hits(self, status: dict | None) -> None:
        """Its own calls: units lost, or hurt without a loss (DCS reports the living units' life)."""
        if not status or not status.get('exists') or self.aboard(status) or self.carrier():
            return
        alive, damage = status.get('alive'), self.damage(status)
        if not isinstance(alive, int):
            return
        members = getattr(self, 'UNIT', 'soldiers')
        lost = self.watched_alive is not None and alive < self.watched_alive
        hit = damage is not None and self.watched_damage is not None and damage - self.watched_damage >= HIT_STEP
        if lost and alive > 0:
            self.pending.append({'report': f'you just lost {self.watched_alive - alive} {members} '
                                           f'({alive} of {status.get("size")} left): call it now, then what you do'})
        elif hit:
            hurt = 'some of your vehicles are damaged' if members == 'vehicles' else 'some of your soldiers are wounded'
            self.pending.append({'report': f'you are taking hits: {hurt}, nobody lost yet: call it now'})
        # The damage counted from: the last call or loss (a loss takes its life out of the sum), never higher than now.
        if lost or hit or self.watched_damage is None or damage is None or damage < self.watched_damage:
            self.watched_damage = damage
        self.watched_alive = alive

    def prefilter(self, text: str) -> str | None:
        """'' = the pilot's radio does not reach the group (terrain, horizon), or it was killed: it does not hear (a
        real radio stays silent)."""
        if self.dead():
            return ''
        link = self.radio_link()
        return None if link is None or link['hears_pilot'] else ''

    def losses(self, status: dict) -> int:
        """Soldiers lost since the model last saw the squad (counted once)."""
        alive = status.get('alive')
        if not isinstance(alive, int):
            return 0
        lost = max(0, self.reported_alive - alive) if self.reported_alive is not None else 0
        self.reported_alive = alive
        return lost

    def facts(self) -> dict | None:
        """What the condition events compare (shared/triggers.ConditionWatch); None while unknown or aboard (aboard,
        the mission still reports the group where it embarked)."""
        status = self.status()
        if not self.group or status is None or self.aboard(status) or self.carrier():
            self.lost_since = None   # carried by an AI helicopter, DCS may report the group gone: not lost
            return None
        if not status.get('exists') or not status.get('alive'):
            # Only once it lasts, with the mission running (the pilot's aircraft still in the mission's traffic):
            # leaving the mission also makes every group "not exist".
            now = time.monotonic()
            self.lost_since = self.lost_since or now
            running = self.state.traffic_at is not None and now - self.state.traffic_at <= TRAFFIC_STALE_S and \
                any(a.get('player') for a in self.state.traffic or [])
            return {'lost': True} if running and now - self.lost_since >= LOST_CONFIRM_S else None
        self.lost_since = None
        self.remember_enemies(status)
        recent = any(time.monotonic() - e['at'] <= ENEMY_HOLD_S for e in self.enemy_memory.values())
        zones = set()
        if 'lat' in status:
            for zone in self.state.zones or []:
                if all(k in zone for k in ('name', 'lat', 'lon')):
                    distance_m = geo.distance_nm(status['lat'], status['lon'], zone['lat'], zone['lon']) * 1852
                    if distance_m <= (zone.get('radius_m') or ZONE_RADIUS_M):
                        zones.add(geo.simplify(zone['name']))
        return {'lost': False, 'alive': status.get('alive'), 'size': status.get('size'),
                'enemies': bool(self.enemies_view(status)) or recent,
                'helicopter': bool(self.helicopter_view(status).get('in_sight')), 'zones': zones}

    def unit_view(self, status: dict) -> dict:
        """Its own unit in the situation: the soldiers left, moving, killed since last time, a pickup coming."""
        squad = {'soldiers': f'{status.get("alive")} of {status.get("size")}',
                 'moving': (status.get('speed_ms') or 0) >= 0.5}
        lost = self.losses(status)
        if lost:
            squad['killed_since_your_last_message'] = lost
        if self.carrier('pickup'):
            squad['pickup'] = (f'{self.ride()} is coming to pick you up: wait for it where you were told '
                               '(wait_for_pickup); you board by yourselves when it stops there')
        return squad

    def situation(self) -> dict:
        status = self.status()
        situation = {'aircraft': self.state.aircraft or 'unknown', 'callsign': self.state.callsign or 'unknown',
                     'you_are': self.name}
        key = self.UNIT_KEY
        if not self.group:
            situation[key] = f'no DCS group configured for this agent (agent.json "{self.CONFIG_KEY}.groups")'
        elif status is None:
            situation[key] = 'no news from the mission yet'
        elif self.aboard(status) and self.carrier() and self.ride_desk():
            situation[key] = (f'aboard {self.ride()}: it drops you where the pilot sends it; to get out where it is '
                              'now, disembark (asks it on the radio to stop and let you out)')
        elif self.aboard(status) and self.carrier():
            situation[key] = (f'aboard {self.ride()}: it drops you where the pilot sends it (you get out when it '
                              'stops there)')
        elif self.aboard(status):
            situation[key] = 'aboard the pilot\'s helicopter: to get out, disembark when the pilot says so'
        elif not status.get('exists'):
            situation[key] = 'the group no longer exists in the mission (killed, or removed)'
        else:
            situation[key] = self.unit_view(status)
        if status is not None and status.get('exists') and not self.aboard(status):
            situation['helicopter'] = self.helicopter_view(status)
            others = self.aircraft_in_sight(status)
            if others:   # the other agents' aircraft in sight (e.g. an AI helicopter): by name, no coordinates
                situation['other_aircraft_in_sight'] = [
                    {'name': n, **{k: v for k, v in view.items() if k not in ('lat', 'lon')}}
                    for n, view in sorted(others.items(), key=lambda item: item[1]['distance_m'])]
            friends = self.friends_in_sight(status)
            if friends:   # the other agents' ground units in sight: by name, no coordinates
                situation['friendly_units_in_sight'] = [
                    {'name': n, **{k: v for k, v in view.items() if k not in ('lat', 'lon')}}
                    for n, view in sorted(friends.items(), key=lambda item: item[1]['distance_m'])]
            smokes = self.smoke_in_sight(status)
            if smokes:
                situation['smoke_in_sight'] = smoke.shown(smokes)
            self.remember_enemies(status)
            situation['enemies_in_sight'] = self.enemies_view(status) or 'none'
            gone = self.enemies_out_of_sight(status)
            if gone:
                situation['enemies_out_of_sight'] = gone
            link = self.radio_link(status)
            if link:
                situation['radio_with_pilot'] = {k: link[k] for k in ('distance_km', 'line_of_sight', 'pilot_hears')}
        if self.requests:
            situation['pilot_asked_you_to_tell_when'] = [r['request'] for r in self.requests]
        zones = self.zones_view(status)
        if zones:
            situation['mission_zones'] = zones
        return situation

    # --- orders (tools) ------------------------------------------------------------------------------------------

    def wait_reply(self, request_id: str) -> dict | None:
        deadline = time.monotonic() + REPLY_WAIT_S
        while time.monotonic() < deadline:
            reply = self.state.replies.get(request_id)
            if reply:
                return reply
            time.sleep(0.05)
        return None

    def task(self, action: str, *args) -> dict:
        if not self.tasks:
            return {'ok': False, 'detail': 'no connection to the mission'}
        reply = self.wait_reply(self.tasks.send(action, *args))
        if reply is None:
            return {'ok': False, 'detail': 'no answer from the mission (is DCS running with our hook?)'}
        return {'ok': bool(reply.get('ok')), 'detail': reply.get('detail')}

    def native(self, command: str) -> dict:
        if not self.bridge:
            return {'ok': False, 'detail': 'no connection to the radio menu bridge'}
        reply = self.wait_reply(self.bridge.send(command, self.group))
        if reply is None:
            return {'ok': False, 'detail': 'no answer from DCS (is its radio menu patched? DCS screen, Update)'}
        return {'ok': reply.get('status') == 'sent', 'detail': reply.get('detail')}

    def place(self, destination: str) -> tuple[float, float] | str:
        """(lat, lon) of 'here', 'helicopter' (only in sight), another aircraft in sight by its name (e.g. "Cobra") or
        a mission zone; else an error text."""
        status = self.status()
        if destination == 'here':
            if not status or 'lat' not in status:
                return 'the squad position is unknown'
            return status['lat'], status['lon']
        if destination == 'helicopter':
            heli, view = self.helicopter(), self.helicopter_view(status)
            if not heli or not view.get('in_sight'):
                return 'the helicopter is not in sight: ask the pilot where to go'
            return heli['lat'], heli['lon']
        wanted = geo.simplify(destination)
        others = {**self.aircraft_in_sight(status), **self.friends_in_sight(status)}
        exact = [v for n, v in others.items() if geo.simplify(n) == wanted]
        inside = [v for n, v in others.items() if geo.simplify(n) and geo.simplify(n) in wanted]   # "Tango's position"
        seen = exact or (inside if len(inside) == 1 else [])
        if seen:
            return seen[0]['lat'], seen[0]['lon']
        marked = smoke.find(self.smoke_in_sight(status), destination)
        if marked:
            return marked['lat'], marked['lon']
        if 'smoke' in wanted:
            return 'you see no such smoke: ask the pilot or the unit to pop it again'
        zone = next((z for z in self.state.zones or [] if geo.simplify(z.get('name', '')) == wanted), None)
        if zone is None:
            names = ', '.join(z.get('name', '') for z in self.state.zones or []) or 'none'
            in_sight = f'; units and aircraft in sight: {", ".join(others)}' if others else ''
            return f'no mission zone called {destination} (zones: {names}{in_sight})'
        return zone['lat'], zone['lon']

    def target_point(self, target: str, offset_m: float = 0, toward: str = '') -> tuple[tuple, tuple | None] | str:
        """A call for fire's point from the group's own eyes (shared/net.py call_for_fire): an enemy group it sees or
        saw lately, coordinates the pilot gave, or a place (place_off). ((lat, lon), the observer's own (lat, lon) or
        None), or an error text."""
        status = self.status() or {}
        if self.aboard(status):
            return 'you are aboard: you cannot observe for the artillery'
        self.remember_enemies(status)
        wanted = geo.simplify(target)
        enemy = next((e for n, e in self.enemy_memory.items() if geo.simplify(n) == wanted), None)
        where = (enemy['lat'], enemy['lon']) if enemy else coords.parse(target)
        if where:
            heading = (self.state.flight or {}).get('heading_deg') if offset_m else None
            where = geo.shift(where, offset_m, toward, heading)
        else:
            where = self.place_off(target, offset_m, toward)
        if isinstance(where, str):
            seen = ', '.join(self.enemy_names()) or 'none'
            return f'{where}; enemies you see or saw lately: {seen}'
        return where, ((status['lat'], status['lon']) if 'lat' in status else None)

    ABOARD = {'ok': False, 'detail': 'the squad is aboard the helicopter: disembark first'}

    def place_off(self, destination: str, offset_m: float = 0, toward: str = '') -> tuple[float, float] | str:
        """A place (place()) moved offset_m toward a compass direction or a clock position from the pilot's nose
        (user, 2026-11-05: "Bison, drop them 200 m from the helicopter" could not be done), as the wingman's land."""
        where = self.place(destination)
        if isinstance(where, str):
            return where
        heading = (self.state.flight or {}).get('heading_deg') if offset_m else None
        return geo.shift(where, offset_m, toward, heading)

    def move_to(self, destination: str, speed: str = 'normal', offset_m: float = 0, toward: str = '') -> dict:
        if self.aboard():
            return self.ABOARD
        where = self.place_off(destination, offset_m, toward)
        if isinstance(where, str):
            return {'ok': False, 'detail': where}
        return self.task('move', self.group, float(where[0]), float(where[1]), speed)

    def wait_for_pickup(self, destination: str = 'here', offset_m: float = 0, toward: str = '') -> dict:
        if self.aboard():
            return {'ok': True, 'detail': 'already aboard the helicopter'}
        place = self.place_off(destination, offset_m, toward)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        return self.task('wait_pickup', self.group, float(place[0]), float(place[1]), float(PICKUP_RADIUS_M))

    def board_helicopter(self) -> dict:
        if self.aboard():
            return {'ok': True, 'detail': 'already aboard the helicopter'}
        if self.carrier('pickup'):
            return {'ok': False, 'detail': f'{self.ride()} picks you up: you board by yourselves when it stops where '
                                           'you wait'}
        view = self.helicopter_view(self.status())
        if view.get('state') != 'landed':
            return {'ok': False, 'detail': 'the helicopter has not landed near you'}
        result = self.native('embark_troops')
        if result['ok']:
            self.on_board = True    # DCS confirms with the next troops_on_board
        return result

    def ride_desk(self):
        """The vehicles agent carrying the squad now (state.carriers), or None (an AI helicopter, or no ride)."""
        entry = (getattr(self.state, 'transport', None) or {}).get(self.group) or {}
        return (getattr(self.state, 'carriers', None) or {}).get(entry.get('carrier'))

    def board_vehicle(self, vehicle: str) -> dict:
        """Ask another agent's vehicles on the radio to come and take the squad aboard where it is (user, 2026-11-05:
        "Tango asks Bison, Bison does it on its side"): its pick_up, as if the pilot had ordered it."""
        if self.aboard():
            return {'ok': True, 'detail': f'already aboard {self.ride() if self.carrier() else "the helicopter"}'}
        wanted = geo.simplify(vehicle)
        carriers = getattr(self.state, 'carriers', None) or {}
        found = [d for g, d in carriers.items() if wanted and wanted in {geo.simplify(g), geo.simplify(self.called(g))}]
        if not found:
            names = ', '.join(self.called(g) for g in carriers) or 'none'
            return {'ok': False, 'detail': f'no vehicles called {vehicle} (vehicles: {names})'}
        return found[0].asked_by(self.group, 'board')

    def disembark(self) -> dict:
        desk = self.ride_desk()
        if desk and (self.carrier('aboard') or self.carrier('drop')):
            return desk.asked_by(self.group, 'out')   # the vehicles stop and let the squad out
        if self.carrier('aboard') or self.carrier('drop'):
            return {'ok': False, 'detail': f'you are aboard {self.ride()}: you get out when it stops where the pilot '
                                           'sends it (the pilot orders it)'}
        result = self.native('disembark_troops')
        if result['ok']:
            self.on_board = False
        return result

    def hold(self) -> dict:
        if self.aboard():
            return self.ABOARD
        return self.task('hold', self.group)

    def set_rules_of_engagement(self, rules: str) -> dict:
        return self.task('roe', self.group, rules)

    def pop_smoke(self, color: str) -> dict:
        if self.aboard():
            return self.ABOARD
        result = self.task('smoke', self.group, color)
        status = self.status()
        if result['ok'] and status and 'lat' in status:   # DCS cannot tell anyone where it is: we do (shared/smoke.py)
            smoke.add(self.state, color, status['lat'], status['lon'], self.called(self.group))
        return result

    def attack(self, enemy_group: str) -> dict:
        """Only an enemy group the squad sees or saw lately (by its name in the situation, matched loosely)."""
        if self.aboard():
            return self.ABOARD
        names = self.enemy_names()
        wanted = geo.simplify(enemy_group)
        target = next((n for n in names if geo.simplify(n) == wanted), None)
        if target is None:
            return {'ok': False, 'detail': f'no enemy group called {enemy_group} seen '
                                           f'(seen: {", ".join(names) or "none"})'}
        return self.task('attack', self.group, target)

    def watch_carrier(self) -> None:
        """Boarding and leaving an agent's AI helicopter become calls of the squad (user, 2026-11-05: Victor boarded
        Cobra and never said so)."""
        entry = (getattr(self.state, 'transport', None) or {}).get(self.group)
        now = (entry.get('stage'), f'{self.called(entry.get("carrier") or "")}\'s {entry.get("ride") or "helicopter"}') \
            if entry else None
        before, self.carried_seen = self.carried_seen, now
        if now == before or before is None and now is None:
            return
        stage, ride = now or (None, None)
        last_stage, last_ride = before or (None, None)
        # A vehicles agent calls boarding and a failed drop itself (vehicles/desk.py): the squad saying the same right
        # after it sounded like a machine (user, 2026-11-05). It only speaks once on the ground.
        by_vehicle = (ride or last_ride or '').endswith('vehicle')
        unconfirmed = stage is None and \
            (getattr(self.state, 'unconfirmed_drops', None) or {}).pop(self.group, None) is not None
        if stage == 'aboard' and last_stage == 'drop':
            if not by_vehicle:
                self.pending.append({'report': f'{ride} left the drop point with your squad still aboard: you did '
                                               'not get out'})
        elif stage == 'aboard' and last_stage != 'aboard':
            if not by_vehicle:
                self.pending.append({'report': f'your squad boarded {ride} and it lifted off with you aboard'})
        elif stage is None and last_stage in ('aboard', 'drop') and unconfirmed:
            pass   # the vehicles say they cannot confirm it; the squad has nothing sure to add
        elif stage is None and last_stage in ('aboard', 'drop'):
            crew = ' (its crew has called the drop: do not repeat it)' if by_vehicle else ''
            self.pending.append({'report': f'your squad is out of {last_ride}, on the ground at the drop point{crew}: '
                                           'say in a few words what you do now'})
        elif stage is None and last_stage == 'pickup':
            self.pending.append({'report': f'{last_ride} left without your squad'})

    def reports(self, busy: bool = False, quiet_s: float = 0.0) -> list[dict]:
        """The squad's own calls for the voice loop (instructor/voice_loop.py own_calls): boarding, getting out."""
        if self.dead():
            self.pending = []   # killed: nobody makes a call
        if busy or not self.pending:
            return []
        texts = '; '.join(p['report'] for p in self.pending)
        self.pending = []
        return [{'report': texts}]

    def tick(self, busy: bool = False) -> str | None:
        """Called every couple of seconds: keeps the hook reporting this squad's group and watches its rides in the
        agents' AI helicopters and vehicles (calls through reports())."""
        self.watch_carrier()
        self.watch_hits(self.status())
        self.remember_enemies(self.status())
        if self.tasks and self.groups and time.monotonic() >= self.next_watch:
            self.next_watch = time.monotonic() + WATCH_EVERY_S
            try:
                self.tasks.watch(self.groups, owner=self.owner)
            except OSError:
                pass
        if self.bridge and self.group and self.kind() == 'infantry' and time.monotonic() >= self.next_on_board:
            self.next_on_board = time.monotonic() + ON_BOARD_EVERY_S
            if self.on_board_request:
                self.on_board_reply(self.state.replies.get(self.on_board_request))
            try:
                self.on_board_request = self.bridge.send('troops_on_board', self.group)
            except (OSError, ValueError):
                self.on_board_request = None
        return None
