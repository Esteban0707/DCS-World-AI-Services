"""A wingman agent (role "wingman"): a SEPARATE AI aircraft group placed in the Mission Editor with late activation
(e.g. "Manolo"), started and ordered by the pilot on the flight's frequency.

User's design (2026-11-04), after DCS's F1 wingman orders did nothing (2026-11-03, A-4E-C): the pilot starts it by
radio ("Manolo, start up, we are leaving") cold or hot on its parking spot, on the runway, or in the air beside the
leader, and it then gets script tasks through the hook (dcs/instructor_tasks.lua wing_*, client/dcs_tasks.py), like the
squads: works with any aircraft, mods too, and no DCS wingman voices. The model always knows what state the aircraft
is in (from DCS's own events: started, engines, taxi, takeoff, airborne, landed, engines off, lost) and the code
refuses an order that does not fit that state, saying why, so the model answers in character.

What the wingman knows (realism rule): itself (damage, fuel, weapons left, its group's Mission Editor task), where it
is from its leader (distance, clock position, height), what it detects (DCS's own AI detection plus its own eyes:
dcs/instructor_tasks.lua eyes_of, range by kind, blind sector behind, terrain, fog; friends seen too), and whether the terrain blocks the radio path to the leader. agent.json "wingman": {group: its DCS group
name, number: 2-4 (how the pilot may call it: "Two, ..."), chatter: off / rare / normal (quiet-time remarks)}.

Companion (user's design, 2026-11-04, BLUEPRINT "Wingman companion"): reports() gives the voice loop what just
happened (airborne, weapon released, kill, hit taken, fuel low, winchester, new contacts, attack finished, landed)
for the model to call out in character, and "quiet time" after a calm silence. Attacks are jobs: all the targets
(pass after pass) or one at a time with the leader's clearance in between; when one ends (targets gone, winchester,
bingo, damaged) the wingman rejoins the leader by itself.
"""
import math
import re
import time

from instructor import geo
from shared import coords, smoke
from shared.triggers import PilotRequests

GROUPS_STALE_S = 5.0          # the hook reports the watched groups once a second
WATCH_EVERY_S = 5.0           # the hook forgets the watched groups when DCS restarts: say them again
REPLY_WAIT_S = 3.0
ENEMY_MEMORY_S = 120.0        # detection flickers: enemies seen lately are remembered (as squad/desk.py)
ENEMY_HOLD_S = 30.0
RWR_HOLD_S = 30.0             # an emitter on the RWR (direction only) is kept this long after its last signal
RWR_NOTE = ('emitters your radar warning receiver picks up: only their direction, not how far nor where they are; '
            'you cannot attack or fly to one until you see it (closer: eyes or radar)')
FUEL_LOW = 0.25               # "bingo" for the condition events: a quarter of internal fuel
NUMBERS = (2, 3, 4)
FT_PER_M, NM_PER_M, KT_PER_MS = 3.280840, 1 / 1852, 1.943844
HORIZON_NM_PER_SQRT_FT = 1.23  # radio horizon 1.23 * (sqrt(h1 ft) + sqrt(h2 ft)) nm, as the ATC's (atc/desk.py)
TAXI_MS, ROLL_MS = 1.0, 20.0  # on the ground: slower is stopped, faster is the takeoff (or landing) roll
STARTS = ('cold', 'hot', 'runway', 'air')
FORMATION_CLOCKS = range(3, 10)   # a wingman flies beside or behind the leader: 3 to 9 o'clock
FORMATION_DISTANCE_M = (50, 2000)
FORMATION_HEIGHT_M = (-500, 500)
ORBIT_SPEED_MS = 150.0
LOST_EVENTS = ('crash', 'dead', 'ejection', 'pilot_dead')
# The Mission Editor's main task of the group limits what DCS's AI does (user, 2026-11-04: CAP does not attack the
# ground like CAS). Task names as the Mission Editor writes them.
AIR_TASKS = ('CAP', 'Escort', 'Fighter Sweep', 'Intercept')
GROUND_TASKS = ('CAS', 'Ground Attack', 'Pinpoint Strike', 'Runway Attack', 'SEAD', 'Antiship Strike', 'AFAC')
NO_ATTACK_TASKS = ('Transport', 'Reconnaissance')   # DCS gives them no attack tasks at all
# The model hears the editor task as its tasking from base (user, 2026-11-05: never "Mission Editor" on the radio, it
# breaks the role-play; "not cleared for ground attack from base, next mission we ask for CAS").
# Attack jobs.
HOWS = ('all', 'one')
AGAINST = ('ground', 'air')
ZONE_RADIUS_M = 3000.0        # around a zone or point without a radius of its own
ZONE_MIN_RADIUS_M = 1500.0
JOB_MIN_S = 120.0             # a zone attack lasts at least this long before "no more targets" can end it
JOB_CLEAR_S = 90.0            # ...then ends when nothing has been detected in the zone for this long
JOB_NOTHING_S = 240.0         # one at a time: no target found in the zone for this long
JOB_DAMAGE = 0.5
JOB_IDLE_S = 180.0            # an attack that releases nothing for this long (once near the target) is over
JOB_NEAR_M = 10000.0
LEAVE = ('ignore', 'later', 'attack')   # leave_alone: never, after the others (then ask), again allowed
TARGET_TRY_S = 120.0        # a static target is bombed again (or the next one taken) after this long
STATIC_GONE_M = 3000.0        # a static this close and in front that its eyes no longer report: destroyed
STATIC_GONE_DEG = 90.0
REJOIN = (4, 200.0, 0.0)      # clock, distance m, height m: where it goes back to after an attack
# Keeping formation (live 2026-11-04: it took off ahead of the leader, DCS's Follow task did nothing and it flew
# straight on, 8 miles away; it had worked from behind). Out of position, it is flown to a point behind the leader,
# renewed as the leader moves, and given Follow again once close behind.
REJOIN_FAR_NM = 2.5           # this far from the leader: out of formation
REJOIN_AHEAD_NM = 0.8         # ...or this far and in front of the leader (10 to 2 o'clock)
REJOIN_NEAR_NM = 1.5          # close enough behind: Follow again
REJOIN_STEER_S = 15.0
REJOIN_BEHIND_M = 1500.0
REJOIN_EXTRA_MS, REJOIN_MAX_MS = 50.0, 260.0   # faster than the leader to catch up from behind
LEADER_AIRBORNE_AGL_M = 60.0
# DCS's AI going down to land by itself (live 2026-11-05: every order lost, it went home to land): the gear down in the
# air with no landing ordered. The program gives its order again at once and every UNCOMMANDED_RETRY_S; if it still
# does not obey the agent is asked (silently) to fix it with its tools. The leader hears nothing unless it lands.
UNCOMMANDED_AFTER_TAKEOFF_S = 60.0   # the gear goes up after takeoff: not before this long
UNCOMMANDED_SEEN_S = 3.0
UNCOMMANDED_RETRY_S = 20.0
NOT_CLOSING_STEERS = 4               # rejoining, and the leader further away at each of this many steers: ignored
MAIN_TASKS = ('wing_follow', 'wing_escort', 'wing_fly', 'wing_orbit', 'wing_engage_zone', 'wing_land', 'wing_land_at',
              'wing_pickup', 'wing_dropoff')
POINT_LANDINGS = ('wing_land_at', 'wing_pickup', 'wing_dropoff')   # a helicopter on the ground away from a field
LANDINGS = ('wing_land',) + POINT_LANDINGS
# Carrying a squad (user, 2026-11-05: DCS's own transport tasks; the squad waits with EmbarkToTransport). DCS tells
# nobody who is aboard an AI helicopter: the troops count as aboard when it lifts off from the pickup point with them
# there (or gone from the mission), and as out when it lifts off from the drop point.
TRANSPORT_NEAR_M = 400.0
PICKUP_RADIUS_M = 200.0       # the squad's EmbarkToTransport zone, as squad/desk.py's
TRANSPORT_LIFTED_AGL_M = 15.0  # in the air this high above the ground: lifted off, with or without DCS's event
HOLD_ABOVE_FIELD_M = 600.0    # airborne before the leader: it orbits this high over the leader's field
# Reports and quiet time.
REPORT_GATHER_S = 1.5         # weapon releases close together are one call ("pickle, two Mk-82")
CONTACT_GATHER_S = 5.0        # new ground contacts: one call once none has been added for this long...
CONTACT_GATHER_MAX_S = 15.0   # ...or this long after the first
WINCHESTER_CONFIRM_S = 5.0    # no weapons but the gun for this long before "winchester" is called
CHATTER_S = {'off': None, 'rare': 900.0, 'normal': 300.0}   # quiet time after this long without a word
CHATTER_DEFAULT = 'rare'
CALM_AGL_M = 300.0            # quiet time only with the leader well above the ground (not taking off or landing)
# What the pilot asked to be told (tool tell_pilot_when): the same report is then not made twice.
REQUEST_OF_REPORT = {'airborne': 'airborne', 'fuel_low': 'fuel_low', 'out_of_weapons': 'out_of_weapons',
                     'contact': 'enemies_seen', 'hit': 'damaged'}

# Phases, as the model reads them, and the orders each one allows.
NOT_STARTED = 'not started yet: waiting in the mission (late activation)'
# UNCONTROLLED in the Mission Editor: visible on its parking spot, asleep until DCS's "Start" command (2026-11-04).
PARKED_ASLEEP = 'not started yet: parked on your spot, engines off, waiting for the order to start (uncontrolled)'
AIRBORNE = 'airborne'
FLYING_ORDERS = ('follow', 'cover_me', 'attack', 'leave_alone', 'strike', 'fly_to', 'orbit', 'return_to_base',
                 'weapons', 'tell_pilot_when')
HELICOPTER_ORDERS = ('land', 'pick_up', 'drop_off')
WEAPONS = ('auto', 'bombs', 'rockets', 'guns')   # strike: DCS's weapon flags (dcs/instructor_tasks.lua)
ATTACKS = ('auto', 'dive', 'level')
ABOVE = ('ground', 'sea')                        # fly_to: altitude above the ground (RADIO) or the sea (BARO)
LOW_LEVEL_MIN_M = 30.0
GROUND_ORDERS = ('weapons', 'tell_pilot_when')
# A helicopter landed at a point (land tool) lifts off for any flying order.
LANDED_AWAY = ('on the ground after landing, stopped', 'taxiing in after landing')
LAND_HOW = ('normal', 'combat')

# Numbers by kind of aircraft (user, 2026-11-05: the same wingman for helicopters, the Mi-8 first; the kind comes from
# DCS, dcs/instructor_tasks.lua air.kind): the airplane's are the constants above; a helicopter flies slower, lower
# and closer, has no takeoff roll, and its uncommanded landing is seen low and slow instead of by the gear (fixed on
# many helicopters).
KINDS = ('airplane', 'helicopter')
PROFILES = {
    'airplane': {'orbit_speed_ms': ORBIT_SPEED_MS, 'formation_distance_m': FORMATION_DISTANCE_M, 'rejoin': REJOIN,
                 'rejoin_far_nm': REJOIN_FAR_NM, 'rejoin_ahead_nm': REJOIN_AHEAD_NM, 'rejoin_near_nm': REJOIN_NEAR_NM,
                 'rejoin_behind_m': REJOIN_BEHIND_M, 'rejoin_extra_ms': REJOIN_EXTRA_MS, 'rejoin_max_ms': REJOIN_MAX_MS,
                 'hold_above_field_m': HOLD_ABOVE_FIELD_M, 'low_level_min_m': LOW_LEVEL_MIN_M, 'roll_ms': ROLL_MS,
                 'airborne_agl_m': LEADER_AIRBORNE_AGL_M},
    'helicopter': {'orbit_speed_ms': 40.0, 'formation_distance_m': (30, 1000), 'rejoin': (4, 100.0, 0.0),
                   'rejoin_far_nm': 1.0, 'rejoin_ahead_nm': 0.4, 'rejoin_near_nm': 0.5,
                   'rejoin_behind_m': 300.0, 'rejoin_extra_ms': 15.0, 'rejoin_max_ms': 80.0,
                   # low_level_min_m: live 2026-11-05, Cobra sent at 100 ft above the ground flew into the trees (DCS's
                   # AI follows the terrain, not the trees): 150 ft at the lowest.
                   'hold_above_field_m': 100.0, 'low_level_min_m': 45.0, 'roll_ms': None,
                   'airborne_agl_m': 10.0},
}
SETTLING_AGL_M, SETTLING_MS = 15.0, 8.0   # a helicopter this low and slow in the air is settling down to land


def wingman_number(agent: dict) -> int:
    number = (agent.get('wingman') or {}).get('number')
    return number if number in NUMBERS else 2


def wingman_callsign(callsign: str | None, number: int) -> str:
    """The pilot's callsign with this wingman's number: "Colt 1-1" -> "Colt 1-2"."""
    if not callsign:
        return f'number {number}'
    return re.sub(r'(\d+)\s*-\s*\d+\s*$', lambda m: f'{m.group(1)}-{number}', callsign) \
        if re.search(r'\d+\s*-\s*\d+\s*$', callsign) else f'{callsign} {number}'


def clock(bearing_deg: float, heading_deg: float) -> int:
    """A bearing as a clock position from a heading: 0 deg off the nose is 12 o'clock."""
    hour = round(((bearing_deg - heading_deg) % 360) / 30) % 12
    return hour or 12


def clock_span(clocks: list[int]) -> str:
    """Clock positions as the shortest span: [10, 11, 10] -> "10 to 11 o'clock", [12, 1, 11] -> "11 to 1 o'clock"."""
    hours = sorted(set(clocks))
    if len(hours) == 1:
        return f'{hours[0]} o\'clock'
    # The largest gap between neighbours (round the dial) is where the span does not lie.
    gaps = [((hours[(i + 1) % len(hours)] - h) % 12, i) for i, h in enumerate(hours)]
    _, widest = max(gaps)
    return f'{hours[(widest + 1) % len(hours)]} to {hours[widest]} o\'clock'


def formation_offset(clock_position: int, distance_m: float) -> tuple[float, float]:
    """DCS's Follow pos (x forward, z right) for a clock position and distance from the leader."""
    angle = math.radians(clock_position * 30)
    return round(distance_m * math.cos(angle)), round(distance_m * math.sin(angle))


class WingmanDesk(PilotRequests):   # the pilot's "tell me when..." (tool tell_pilot_when)
    UNIT = 'aircraft'   # how the condition events speak of it (shared/triggers.ConditionWatch)

    def __init__(self, state, agent: dict, tasks=None):
        self.state, self.tasks = state, tasks
        self.enemy_memory: dict[str, dict] = {}
        self.rwr: dict[str, dict] = {}        # emitters only on the RWR: {type, air, bearing_deg, at}, no position
        self.rwr_called: set[str] = set()
        self.requests: list[dict] = []
        self.job: dict | None = None          # the attack being flown (attack tool)
        self.ignored: dict[str, str] = {}     # enemy group or static -> ignore / later (leave_alone tool)
        self.pending: list[dict] = []         # reports not made yet: {at, text, urgent, kind}
        self.combat_seen: int | None = None   # last combat log entry seen (dcs/instructor_tasks.lua "combat")
        self.last_phase: str | None = None
        self.flags = {'fuel_low': False, 'out_of_weapons': False}
        self.contacts: set[str] = set()       # enemy groups already called out
        self.contact_batch: dict[str, dict] = {}   # new ground contacts gathered for one call
        self.batch_first: float | None = None
        self.batch_last = 0.0
        self.weapons_seen = False             # it carried weapons (else no "winchester" for an unarmed aircraft)
        self.empty_since: float | None = None  # no weapons but the gun since (WINCHESTER_CONFIRM_S)
        self.flight_said: set[str] = set()    # flight steps already called out: engines, taxi, airborne, landed
        self.lost_said = False                # lost: its last call is made (or not), then it is silent
        self.formation: tuple | None = None   # the Follow offset it keeps (wing_follow arguments); None: other order
        self.steering = False                 # out of formation: flown back to the leader (keep_formation)
        self.holding = False                  # airborne before the leader: orbiting over the field
        self.next_steer = 0.0
        self.steer_distances: list[float] = []   # distance to the leader at each steer (keep_formation)
        self.main_task: tuple | None = None   # the last main task sent (action, args): given again if DCS drops it
        self.landing_ordered = False          # the last main task was wing_land: the gear down is expected
        self.gear_since: float | None = None
        self.trouble: dict | None = None      # {kind, text, since, next, tries, asked}: the aircraft not obeying
        self.last_quiet = time.monotonic()
        self.configure(agent)

    def configure(self, agent: dict) -> None:
        config = agent.get('wingman') or {}
        self.owner = agent.get('id') or ''   # the hook's watch list holds every agent's groups (client/dcs_tasks.py)
        self.group = config.get('group') if isinstance(config.get('group'), str) else ''
        self.number = wingman_number(agent)
        self.name = agent.get('name') or self.group or f'number {self.number}'
        if self.group and agent.get('name'):
            self.state.agent_names[self.group] = agent['name']
        self.chatter = config.get('chatter') if config.get('chatter') in CHATTER_S else CHATTER_DEFAULT
        self.next_watch = 0.0

    # --- what the wingman knows ----------------------------------------------------------------------------------

    def status(self) -> dict | None:
        """Its group's entry in the hook's groups message (with "air"); None while there is no fresh news."""
        at = self.state.groups_at
        if not self.group or at is None or time.monotonic() - at > GROUPS_STALE_S:
            return None
        return (self.state.groups or {}).get(self.group)

    @staticmethod
    def air(status: dict | None) -> dict:
        air = (status or {}).get('air')
        return air if isinstance(air, dict) else {}

    def kind(self, status: dict | None = None) -> str:
        """airplane / helicopter, from DCS (airplane until known)."""
        kind = self.air(status if status is not None else self.status()).get('kind')
        return kind if kind in KINDS else 'airplane'

    def leader_kind(self, status: dict | None = None) -> str:
        """The leader's airplane / helicopter (its own kind until known)."""
        status = status if status is not None else self.status()
        kind = self.air(status).get('leader_kind')
        return kind if kind in KINDS else self.kind(status)

    def same_kind(self, status: dict | None = None) -> bool:
        """Formation only with a leader of the same kind (user, 2026-11-05: a helicopter cannot follow an airplane;
        it can still be sent anywhere by radio)."""
        status = status if status is not None else self.status()
        return self.kind(status) == self.leader_kind(status)

    def profile(self, status: dict | None = None) -> dict:
        return PROFILES[self.kind(status)]

    @staticmethod
    def events(air: dict) -> dict:
        events = air.get('events')
        return events if isinstance(events, dict) else {}   # an empty Lua table arrives as a list

    def lost(self, status: dict | None) -> bool:
        if status is None:
            return False
        return not status.get('exists') or not status.get('alive') or self.air(status).get('last_event') in LOST_EVENTS

    def phase(self, status: dict | None = None) -> str | None:
        """Where the aircraft is in its flight, from DCS's events and its motion; None while unknown."""
        status = status if status is not None else self.status()
        if status is None:
            return None
        if self.lost(status):
            return 'lost: shot down, crashed or no longer in the mission'
        air = self.air(status)
        if not air.get('active'):
            return NOT_STARTED
        if air.get('uncontrolled') and not air.get('started'):
            return PARKED_ASLEEP
        if air.get('in_air'):
            return AIRBORNE
        speed = math.hypot(air.get('vx') or 0, air.get('vz') or 0)
        events, last = self.events(air), air.get('last_event')
        if last == 'engine_shutdown':
            return 'on the ground, engines off'
        landed = last == 'land' or ('land' in events and 'takeoff' in events and events['land'] < events['takeoff'])
        roll = self.profile(status)['roll_ms']   # a helicopter lifts off: no roll
        if roll is not None and speed >= roll:
            return 'landing roll' if landed else 'takeoff roll'
        if speed >= TAXI_MS:
            return 'taxiing in after landing' if landed else 'taxiing to the runway'
        if landed:
            return 'on the ground after landing, stopped'
        start = air.get('started')
        if start in ('cold', 'uncontrolled') and 'engine_startup' not in events:
            return 'on the parking spot, starting the engines'
        if start == 'runway':
            return 'on the runway, about to take off'
        return 'on the ground, engines running, stopped'

    def allowed(self, phase: str | None) -> tuple:
        helicopter = self.kind() == 'helicopter'
        flying = FLYING_ORDERS + (HELICOPTER_ORDERS if helicopter else ())
        if phase == AIRBORNE:
            return flying
        if helicopter and phase in LANDED_AWAY and self.main_task and self.main_task[0] in POINT_LANDINGS:
            return flying   # landed where the leader said: it lifts off for the next order
        if phase in (NOT_STARTED, PARKED_ASLEEP):
            return ('start_up', 'tell_pilot_when')
        if phase is None or phase.startswith('lost'):
            return ()
        return GROUND_ORDERS

    def leader(self) -> dict | None:
        flight = self.state.flight or {}
        if not all(isinstance(flight.get(k), (int, float)) for k in ('lat', 'lon')):
            return None
        return flight

    @staticmethod
    def damage(air: dict) -> float:
        life, life0 = air.get('life'), air.get('life0')
        if not isinstance(life, (int, float)) or not isinstance(life0, (int, float)) or life0 <= 0:
            return 0.0
        return max(0.0, min(1.0, 1 - life / life0))

    @staticmethod
    def weapons(air: dict) -> list[dict]:
        """Weapons left: name, count, kind (gun, missile, rocket, bomb) and what they are for (air, ground, both)."""
        out = []
        for w in air.get('weapons') or []:
            if isinstance(w, dict) and (w.get('count') or 0) > 0:
                against = w.get('against') or ('both' if w.get('kind') == 'gun' else 'ground')
                out.append({'name': w.get('name'), 'count': w.get('count'), 'kind': w.get('kind'),
                            'for': against})
        return out

    def out_of_weapons(self, air: dict, against: str | None = None) -> bool:
        """No weapons left but the gun (for those targets, if given)."""
        return not any(w['kind'] != 'gun' and (against is None or w['for'] in (against, 'both'))
                       for w in self.weapons(air))

    @staticmethod
    def editor_task(air: dict) -> str | None:
        task = air.get('editor_task')
        return task if isinstance(task, str) and task else None

    def task_note(self, air: dict) -> str | None:
        """Whether its tasking from base (the group's editor task) and its weapons fit each other, said plainly; None
        when fine."""
        task, weapons = self.editor_task(air), self.weapons(air)
        kinds = {w['for'] for w in weapons if w['kind'] != 'gun'}
        if task in NO_ATTACK_TASKS:
            return f'your tasking from base is {task}: you are not cleared to attack anything on this mission'
        if task in AIR_TASKS and kinds and 'air' not in kinds:
            return (f'your tasking from base is {task} (air targets only) but you were loaded with ground weapons: '
                    'the tasking and the loadout do not fit')
        if task in AIR_TASKS:
            return f'your tasking from base is {task}: you are not cleared for ground attack on this mission'
        if task in GROUND_TASKS and kinds and 'ground' not in kinds:
            return (f'your tasking from base is {task} (ground targets) but you were loaded with air-to-air missiles '
                    'only')
        return None

    def refuse_target(self, air: dict, against: str) -> str | None:
        """Why its tasking from base (the group's editor task) or its weapons do not allow this attack; None when they
        do."""
        task = self.editor_task(air)
        if task in NO_ATTACK_TASKS or (against == 'ground' and task in AIR_TASKS):
            cleared = 'to attack' if task in NO_ATTACK_TASKS else 'for ground attack'
            return (f'you are not cleared {cleared}: your tasking from base is {task}. Tell the leader in character '
                    'and suggest asking base for that tasking (CAS or Ground Attack) on the next mission')
        if not any(w['for'] in (against, 'both') for w in self.weapons(air)):
            return f'you have no weapons left for {against} targets'
        return None

    def from_leader(self, status: dict) -> dict | None:
        lead = self.leader()
        if not lead or 'lat' not in status:
            return None
        bearing = geo.bearing_deg(lead['lat'], lead['lon'], status['lat'], status['lon'])
        view = {'distance_nm': round(geo.distance_nm(lead['lat'], lead['lon'], status['lat'], status['lon']), 1)}
        heading = lead.get('heading_deg')
        if isinstance(heading, (int, float)):   # true heading in degrees (dcs/instructor_export.lua)
            view['clock'] = clock(bearing, heading)
        else:
            view['direction'] = geo.cardinal(bearing)
        if isinstance(lead.get('alt_msl_m'), (int, float)):
            view['height_vs_leader_ft'] = round(((status.get('alt_m') or 0) - lead['alt_msl_m']) * FT_PER_M, -1)
        return view

    def remember_enemies(self, status: dict | None) -> None:
        now = time.monotonic()
        current = set()
        for enemy in (status or {}).get('enemies') or []:
            if isinstance(enemy, dict) and enemy.get('group') and all(k in enemy for k in ('lat', 'lon')):
                self.enemy_memory[enemy['group']] = {**enemy, 'at': now}
                current.add(enemy['group'])
            elif isinstance(enemy, dict) and enemy.get('group') and enemy.get('rwr') and \
                    isinstance(enemy.get('bearing_deg'), (int, float)):
                self.rwr[enemy['group']] = {**enemy, 'at': now}
        for name in [n for n, e in self.rwr.items() if now - e['at'] > RWR_HOLD_S or n in current]:
            del self.rwr[name]
        stale = [n for n, e in self.enemy_memory.items() if now - e['at'] > ENEMY_MEMORY_S]
        air = self.air(status)
        heading = air.get('heading_deg')
        if status and 'lat' in status and air.get('active') and isinstance(heading, (int, float)):
            # A static right in front of it, close, that its eyes no longer report is destroyed (they report only
            # live ones).
            for name, e in self.enemy_memory.items():
                if not e.get('static') or name in current:
                    continue
                distance = geo.distance_nm(status['lat'], status['lon'], e['lat'], e['lon']) / NM_PER_M
                bearing = geo.bearing_deg(status['lat'], status['lon'], e['lat'], e['lon'])
                if distance <= STATIC_GONE_M and abs((bearing - heading + 180) % 360 - 180) <= STATIC_GONE_DEG:
                    stale.append(name)
        for name in set(stale):
            del self.enemy_memory[name]

    def enemies_view(self, status: dict) -> list[dict]:
        """Enemy groups its sensors detect now or lately: type, how many, distance, clock from the leader's
        heading (or direction), height."""
        if 'lat' not in status:
            return []
        self.remember_enemies(status)
        lead, now = self.leader() or {}, time.monotonic()
        in_sight = {e.get('group') for e in status.get('enemies') or [] if isinstance(e, dict)}
        view = []
        for name, enemy in self.enemy_memory.items():
            bearing = geo.bearing_deg(status['lat'], status['lon'], enemy['lat'], enemy['lon'])
            entry = {'group': name, 'type': enemy.get('type'), 'units': enemy.get('units'),
                     'distance_nm': round(geo.distance_nm(status['lat'], status['lon'], enemy['lat'], enemy['lon']), 1)}
            heading = lead.get('heading_deg')
            if isinstance(heading, (int, float)):
                entry['clock'] = clock(bearing, heading)
            else:
                entry['direction'] = geo.cardinal(bearing)
            if isinstance(enemy.get('alt_m'), (int, float)):
                entry['altitude_ft'] = round(enemy['alt_m'] * FT_PER_M, -2)
            if name not in in_sight:
                entry['last_seen_s_ago'] = round(now - enemy['at'])
            if enemy.get('static'):
                entry['static'] = True
            if name in self.ignored:
                entry['leave_alone'] = ('never attack it: the leader said so' if self.ignored[name] == 'ignore'
                                        else 'after the other targets, ask the leader first')
            view.append(entry)
        return sorted(view, key=lambda e: e['distance_nm'])

    def rwr_view(self) -> list[dict]:
        """Emitters only on its RWR (none it sees or saw lately): type and direction, as a clock from the leader's
        heading (it flies beside the leader), never a distance."""
        heading = (self.leader() or {}).get('heading_deg')
        view = []
        for name, emitter in self.rwr.items():
            if name in self.enemy_memory:
                continue
            entry = {'group': name, 'type': emitter.get('type')}
            if isinstance(heading, (int, float)):
                entry['clock'] = clock(emitter['bearing_deg'], heading)
            else:
                entry['direction'] = geo.cardinal(emitter['bearing_deg'])
            if name in self.ignored:
                entry['leave_alone'] = True
            view.append(entry)
        return view

    def friends_view(self, status: dict, air: dict) -> list[dict]:
        """Friendly groups its own eyes see (dcs/instructor_tasks.lua eyes_of): never called out as contacts."""
        if 'lat' not in status:
            return []
        heading = (self.leader() or {}).get('heading_deg')
        view = []
        for friend in air.get('friends_in_sight') or []:
            if not isinstance(friend, dict) or not all(k in friend for k in ('group', 'lat', 'lon')):
                continue
            bearing = geo.bearing_deg(status['lat'], status['lon'], friend['lat'], friend['lon'])
            entry = {'group': friend['group'], 'type': friend.get('type'), 'units': friend.get('units'),
                     'distance_nm': round(geo.distance_nm(status['lat'], status['lon'], friend['lat'],
                                                          friend['lon']), 1)}
            if isinstance(heading, (int, float)):
                entry['clock'] = clock(bearing, heading)
            else:
                entry['direction'] = geo.cardinal(bearing)
            view.append(entry)
        return view

    def radio_link(self, status: dict | None = None) -> dict | None:
        """{in_range, distance_nm, line_of_sight}: aircraft to aircraft, both radios strong: line of sight (terrain,
        from DCS) within the radio horizon of both heights above the ground. None when not known."""
        status = status if status is not None else self.status()
        air = self.air(status)
        radio = air.get('radio')
        if not isinstance(radio, dict) or not (status or {}).get('exists'):
            return None
        lead = self.leader() or {}
        own_agl = (status.get('alt_m') or 0) - (air.get('ground_m') or 0)
        heights_ft = [max(own_agl * FT_PER_M, 10), max((lead.get('alt_agl_m') or 3) * FT_PER_M, 10)]
        horizon = HORIZON_NM_PER_SQRT_FT * sum(math.sqrt(h) for h in heights_ft)
        distance = float(radio.get('distance_m') or 0) * NM_PER_M
        sight = radio.get('line_of_sight') is not False
        return {'in_range': sight and distance <= horizon, 'distance_nm': round(distance, 1), 'line_of_sight': sight}

    def can_reach_pilot(self) -> bool:
        link = self.radio_link()
        return link is None or link['in_range']

    def prefilter(self, text: str) -> str | None:
        """'' = it does not answer: out of radio range (terrain or horizon), or lost (shot down, crashed)."""
        return None if self.can_reach_pilot() and not self.silent() else ''

    def facts(self) -> dict | None:
        """What the condition events compare (shared/triggers.ConditionWatch); None while unknown or not started."""
        status = self.status()
        if status is None:
            return None
        if self.lost(status):
            return {'lost': True}
        air = self.air(status)
        if not air.get('active'):
            return None
        self.remember_enemies(status)
        recent = any(time.monotonic() - e['at'] <= ENEMY_HOLD_S for e in self.enemy_memory.values())
        fuel = air.get('fuel') if isinstance(air.get('fuel'), (int, float)) else None
        return {'lost': False, 'alive': 1, 'size': 1, 'damage': round(self.damage(air), 3),
                'enemies': bool(status.get('enemies')) or recent, 'fuel': fuel,
                'fuel_low': fuel is not None and fuel < FUEL_LOW, 'out_of_weapons': self.out_of_weapons(air),
                'airborne': bool(air.get('in_air'))}

    def situation(self) -> dict:
        status = self.status()
        situation = {'aircraft': self.state.aircraft or 'unknown', 'callsign': self.state.callsign or 'unknown',
                     'you_are': f'{self.name}, {wingman_callsign(self.state.callsign, self.number)} '
                                f'(number {self.number} of the flight)'}
        if getattr(self.state, 'pilot', None):
            situation['pilot_name_in_dcs'] = self.state.pilot
        if not self.group:
            situation['you'] = 'no DCS group configured for this agent (agent.json "wingman.group")'
            return situation
        phase = self.phase(status)
        if phase is None:
            situation['you'] = f'no news from the mission yet (is there an aircraft group called {self.group}?)'
            return situation
        situation['your_state'] = phase
        situation['orders_you_can_carry_out_now'] = list(self.allowed(phase))
        air = self.air(status)
        if phase == NOT_STARTED or phase.startswith('lost'):
            if phase == NOT_STARTED and self.editor_task(air):
                situation['your_tasking_from_base'] = self.editor_task(air)
            if self.requests:
                situation['pilot_asked_you_to_tell_when'] = [r['request'] for r in self.requests]
            return situation
        you = {'type': air.get('type'), 'kind': self.kind(status), 'damage_percent': round(self.damage(air) * 100)}
        if not self.same_kind(status):
            you['formation'] = self.other_kind_note() + '; they can send you anywhere by radio'
        if air.get('started'):
            you['started'] = {'cold': 'cold, on the parking spot', 'hot': 'hot, on the parking spot',
                              'runway': 'on the runway', 'air': 'in the air',
                              'uncontrolled': 'on your parking spot, where you were parked',
                              'mission': 'on schedule, as base planned your flight'
                              }.get(air['started'], air['started'])
        if air.get('in_air'):
            you['altitude_ft'] = round((status.get('alt_m') or 0) * FT_PER_M, -2)
            you['speed_kt'] = round(math.hypot(air.get('vx') or 0, air.get('vz') or 0) * KT_PER_MS)
        if isinstance(air.get('fuel'), (int, float)):
            you['fuel_percent_internal'] = round(air['fuel'] * 100)
        you['weapons'] = self.weapons(air) or 'none left'
        if self.editor_task(air):
            you['tasking_from_base'] = self.editor_task(air)
        note = self.task_note(air)
        if note:
            you['task_note'] = note
        situation['you'] = you
        situation['current_job'] = self.job_text() if self.job else \
            'waiting for the leader: orbiting over the field until the leader is airborne, then joining' \
            if self.holding else \
            'rejoining: out of formation, flying back behind the leader' if self.steering else \
            'none: following your orders'
        trouble = self.trouble_text()
        if trouble:
            situation['aircraft_not_obeying'] = trouble
        stages = {'pickup': 'to pick up', 'aboard': 'aboard', 'drop': 'aboard, to drop'}
        troops = {self.called(s): stages[self.state.transport[s]['stage']] for s in self.carried()}
        if troops:
            situation['troops'] = troops
        position = self.from_leader(status)
        if position:
            situation['you_from_leader'] = position
        situation['enemies_detected'] = self.enemies_view(status) or 'none'
        emitters = self.rwr_view()
        if emitters:
            situation['radar_warning_receiver'] = {'note': RWR_NOTE, 'emitters': emitters}
        friends = self.friends_view(status, air)
        if friends:
            situation['friends_in_sight'] = friends
        smokes = self.smoke_in_sight(status)
        if smokes:   # the agents' smoke (shared/smoke.py): a place too ("land at the green smoke")
            situation['smoke_in_sight'] = smoke.shown(smokes)
        link = self.radio_link(status)
        if link:
            situation['radio_with_leader'] = link
        if self.requests:
            situation['pilot_asked_you_to_tell_when'] = [r['request'] for r in self.requests]
        zones = [z.get('name') for z in self.state.zones or [] if z.get('name') and not z.get('mark')]
        if zones:
            situation['mission_zones'] = zones[:8]
        marks = [z.get('name') for z in self.state.zones or [] if z.get('name') and z.get('mark')]
        if marks:   # the leader's F10 map marks: places by their text, like zones
            situation['leader_map_marks'] = marks[:8]
        return situation

    # --- orders (tools) ------------------------------------------------------------------------------------------

    def refuse(self, order: str) -> dict | None:
        """Why an order does not fit the aircraft's state now, or None."""
        phase = self.phase()
        if phase is None:
            return {'ok': False, 'detail': f'no news from the mission about the group {self.group or "(none set)"}'}
        if order in self.allowed(phase):
            return None
        if phase in (NOT_STARTED, PARKED_ASLEEP):
            return {'ok': False, 'detail': 'you have not been started yet: the pilot must tell you to start up first'}
        if phase.startswith('lost'):
            return {'ok': False, 'detail': 'you are lost: ' + phase}
        if order == 'start_up':
            return {'ok': False, 'detail': f'you are already started ({phase})'}
        return {'ok': False, 'detail': f'you are {phase}: that order is only for the air; you join the leader by '
                                       'yourself once airborne'}

    def wait_reply(self, request_id: str) -> dict | None:
        deadline = time.monotonic() + REPLY_WAIT_S
        while time.monotonic() < deadline:
            reply = self.state.replies.get(request_id)
            if reply:
                return reply
            time.sleep(0.05)
        return None

    def task(self, order: str, action: str, *args) -> dict:
        refused = self.refuse(order)
        if refused:
            return refused
        if not self.tasks:
            return {'ok': False, 'detail': 'no connection to the mission'}
        reply = self.wait_reply(self.tasks.send(action, self.group, *args))
        if reply is None:
            return {'ok': False, 'detail': 'no answer from the mission (is DCS running with our hook?)'}
        if reply.get('ok'):
            self.sent_main(action, args)
        if reply.get('ok') and order in ('follow', 'cover_me', 'strike', 'fly_to', 'orbit', 'return_to_base', 'land',
                                         'pick_up', 'drop_off') and \
                self.job:
            self.job = None   # a new order ends the attack
        if reply.get('ok') and order != 'follow':
            self.formation, self.steering, self.holding = None, False, False
            if self.trouble and self.trouble['kind'] == 'rejoin':
                self.trouble = None
        return {'ok': bool(reply.get('ok')), 'detail': reply.get('detail')}

    def send(self, action: str, *args) -> None:
        """A task from tick() (the main loop): sent without waiting for DCS's answer."""
        if self.tasks:
            try:
                self.tasks.send(action, self.group, *args)
            except OSError:
                return
            self.sent_main(action, args)

    def sent_main(self, action: str, args: tuple) -> None:
        """Remembers the last main task (what it is flying now), to give it again when DCS's AI drops it."""
        if action in MAIN_TASKS:
            self.main_task, self.landing_ordered = (action, tuple(args)), action in LANDINGS

    def start_up(self, how: str) -> dict:
        if how not in STARTS:
            return {'ok': False, 'detail': f'unknown start {how} (one of {", ".join(STARTS)})'}
        if how == 'air' and not self.same_kind():
            return {'ok': False, 'detail': self.other_kind_note() + ': an air start puts you beside them, so start on '
                                                                    'the ground'}
        return self.task('start_up', 'wing_start', how)

    def other_kind_note(self) -> str:
        def an(kind: str) -> str:
            return f'an {kind}' if kind == 'airplane' else f'a {kind}'
        return f'you fly {an(self.kind())} and the leader {an(self.leader_kind())}: you cannot fly formation with them'

    def follow(self, clock_position: int = 4, distance_m: float = 200, height_m: float = 0) -> dict:
        if int(clock_position) not in FORMATION_CLOCKS:
            return {'ok': False, 'detail': 'a wingman flies beside or behind the leader: 3 to 9 o\'clock'}
        if not self.same_kind():
            return {'ok': False, 'detail': self.other_kind_note() + '; you can go where they send you (fly_to, orbit, '
                                                                    'attack, strike)'}
        limits = self.profile()['formation_distance_m']
        distance = min(max(float(distance_m), limits[0]), limits[1])
        height = min(max(float(height_m), FORMATION_HEIGHT_M[0]), FORMATION_HEIGHT_M[1])
        forward, right = formation_offset(int(clock_position), distance)
        # wing_follow takes the distance behind (DCS's pos.x = -distance), the interval (+ right) and the elevation.
        offset = (float(-forward), float(right), height)
        result = self.task('follow', 'wing_follow', *offset)
        if result['ok']:
            self.formation, self.steering = offset, False
        return result

    def cover_me(self) -> dict:
        if not self.same_kind():
            return {'ok': False, 'detail': self.other_kind_note() + ' nor escort them'}
        return self.task('cover_me', 'wing_escort')

    def enemy_names(self) -> list[str]:
        self.remember_enemies(self.status())
        return list(self.enemy_memory)

    # --- attacks (jobs) -------------------------------------------------------------------------------------------

    def attack(self, target: str, how: str = 'all', against: str = 'ground') -> dict:
        """target: an enemy group its sensors detect or detected lately (its name in the situation, matched loosely),
        or a mission zone / place (point()): everything found there. how: all = pass after pass until the targets are
        gone; one = one target, then it waits for the leader's clearance (the same order again attacks the next).
        against (zones only): ground or air targets."""
        if how not in HOWS:
            return {'ok': False, 'detail': f'how must be one of {", ".join(HOWS)}'}
        refused = self.refuse('attack')
        if refused:
            return refused
        air = self.air(self.status())
        names = self.enemy_names()
        wanted = geo.simplify(target)
        group = next((n for n in names if geo.simplify(n) == wanted), None)
        if group:
            against = 'air' if self.enemy_memory[group].get('air') else 'ground'
        elif against not in AGAINST:
            return {'ok': False, 'detail': f'against must be one of {", ".join(AGAINST)}'}
        problem = self.refuse_target(air, against)
        if problem:
            return {'ok': False, 'detail': problem}
        if group in self.ignored:
            return {'ok': False, 'detail': f'the leader told you to leave {group} alone: if they now want it attacked, '
                                           'call leave_alone with how "attack" first'}
        job = self.job
        if job and job['how'] == 'one' and job.get('waiting') and geo.simplify(job['target']) == wanted:
            return self.clear_next()   # the leader's clearance for the next target
        if group:
            result = self.task('attack', 'wing_attack', group, how)
            if result['ok']:
                self.start_job(target=group, how=how, against=against, group=group, attacking=True)
            return result
        place = self.point(target)
        if isinstance(place, str):
            return {'ok': False, 'detail': f'{place}; detected enemy groups: {", ".join(names) or "none"}'}
        zone = next((z for z in self.state.zones or [] if geo.simplify(z.get('name', '')) == wanted), {})
        radius = max(float(zone.get('radius_m') or ZONE_RADIUS_M), ZONE_MIN_RADIUS_M)
        status = self.status() or {}
        numbers = self.profile(status)
        # An airplane orbits 1000 m up at least; a helicopter where it is, 100 m above the ground at least.
        lowest = 1000.0 if self.kind(status) == 'airplane' else float(air.get('ground_m') or 0) + 100.0
        alt_m = max(float(status.get('alt_m') or 0), lowest)
        speed = max(math.hypot(air.get('vx') or 0, air.get('vz') or 0), numbers['orbit_speed_ms'])
        # Orbit over it; tick() gives it the targets seen there one after another (all) or one (one), never those
        # the leader said to leave alone: DCS's own zone attack (EngageTargetsInZone) ignores statics and would pick
        # its targets by itself (user, 2026-11-04: "ignore those six soldiers").
        result = self.task('attack', 'wing_orbit', float(place[0]), float(place[1]), alt_m, speed)
        if result['ok']:
            self.start_job(target=target, how=how, against=against, lat=place[0], lon=place[1], radius_m=radius,
                           orbit=(float(place[0]), float(place[1]), alt_m, speed))
            # Live 2026-11-04: the answer was just "orbiting" and the model told the leader DCS would not attack.
            if how == 'one':
                self.attack_one_in_zone()
                result['detail'] = (f'attack started: orbiting over {target}, attacking the first target found there, '
                                    'then waiting for the leader\'s clearance')
            else:
                result['detail'] = (f'attack started: orbiting over {target} while you attack the targets seen there '
                                    'one after another, pass after pass (orbiting is part of the attack)')
        return result

    def start_job(self, **job) -> None:
        now = time.monotonic()
        air = self.air(self.status())
        had = not self.out_of_weapons(air, job['against'])   # else it attacks with the gun only
        load = sum(w['count'] or 0 for w in self.weapons(air) if w['kind'] != 'gun')
        self.job = {'started': now, 'last_seen': now, 'kills': 0, 'waiting': False, 'attacking': False,
                    'had_weapons': had, 'load': load, **job}

    def in_zone(self, ignored: bool = False) -> list[str]:
        """Enemy groups and statics (of the job's kind) seen lately inside the job's zone, nearest its centre first;
        without those the leader said to leave alone (or only those, with ignored=True)."""
        job, now = self.job, time.monotonic()
        found = []
        for name, e in self.enemy_memory.items():
            from_centre = geo.distance_nm(job['lat'], job['lon'], e['lat'], e['lon']) / NM_PER_M
            if now - e['at'] <= ENEMY_HOLD_S and bool(e.get('air')) == (job['against'] == 'air') and \
                    from_centre <= job['radius_m'] and (name in self.ignored) == ignored:
                found.append((from_centre, name))
        return [name for _, name in sorted(found)]

    def next_target(self) -> None:
        """All the targets of a zone, one after another (a static bombed on its exact point, a group attacked whole);
        one that survives is tried again later."""
        job, now = self.job, time.monotonic()
        current = job.get('current')
        if current and current in self.enemy_memory and current not in self.ignored and \
                now - job.get('current_since', now) < TARGET_TRY_S:
            return
        targets = self.in_zone()
        if not targets:
            job['current'] = None
            return
        tried = job.setdefault('tried', [])
        target = next((t for t in targets if t not in tried), None)
        if target is None:   # every one tried: again, from the start
            tried.clear()
            target = targets[0]
        tried.append(target)
        self.send('wing_attack', target, 'one' if self.enemy_memory[target].get('static') else 'all')
        job.update(current=target, current_since=now)

    def attack_one_in_zone(self) -> bool:
        found = self.in_zone()
        if not found:
            return False
        self.send('wing_attack', found[0], 'one')
        self.job.update(attacking=True, waiting=False, group=found[0])
        return True

    def leave_alone(self, target: str, how: str = 'ignore') -> dict:
        """The leader's "ignore those": how ignore = never attack it (nor call it again as a contact), later = after
        the other targets, then ask the leader, attack = it may be attacked again."""
        if how not in LEAVE:
            return {'ok': False, 'detail': f'how must be one of {", ".join(LEAVE)}'}
        names = self.enemy_names() + [n for n in {*self.ignored, *self.rwr} if n not in self.enemy_memory]
        wanted = geo.simplify(target)
        found = [n for n in names if geo.simplify(n) == wanted] or \
                [n for n in names if wanted and wanted in geo.simplify(n)]
        if len(found) != 1:
            return {'ok': False, 'detail': f'which one? {"several match" if found else "none matches"} '
                                           f'(seen: {", ".join(names) or "none"})'}
        name = found[0]
        if how == 'attack':
            self.ignored.pop(name, None)
            return {'ok': True, 'detail': f'{name} may be attacked again'}
        self.ignored[name] = how
        job = self.job
        if job and 'lat' in job and name in (job.get('current'), job.get('group')):
            # Attacking it right now: back to orbiting the zone (setTask drops the pushed attack); the next target
            # (all) at the next tick, or the leader's clearance (one).
            self.send('wing_orbit', *job['orbit'])
            job.update(current=None, attacking=False, group=None)
        if job and job.get('group') == name and 'lat' not in job:
            self.job = None
            self.rejoin()
            return {'ok': True, 'detail': f'leaving {name} alone: attack called off, rejoining the leader'}
        return {'ok': True, 'detail': f'leaving {name} alone' + (' for now: you will ask the leader about it once '
                                                                 'the other targets are done' if how == 'later' else '')}

    def rejoin_offset(self) -> tuple[float, float, float]:
        clock_position, distance, height = self.profile()['rejoin']
        forward, right = formation_offset(clock_position, distance)
        return float(-forward), float(right), height

    def rejoin(self) -> None:
        """Back to the leader on its 4 o'clock (an attack over); keep_formation flies it there when it is far."""
        self.formation, self.steering = self.rejoin_offset(), False
        self.send('wing_follow', *self.formation)

    def keep_formation(self, status: dict, phase: str | None) -> None:
        """Following, but out of position (far, or ahead of the leader): flown to a point behind the leader at the
        leader's altitude, renewed every REJOIN_STEER_S as the leader moves, then Follow again once close behind."""
        lead = self.leader()
        if phase != AIRBORNE:   # on the ground, not started, lost (a mission restart too): nothing kept
            self.steering = self.holding = False
            if not (status.get('air') or {}).get('started'):
                self.formation = None
            return
        if not self.formation or self.job or 'lat' not in status or not lead or \
                not isinstance(lead.get('heading_deg'), (int, float)) or not self.same_kind(status):
            return
        numbers = self.profile(status)
        if (lead.get('alt_agl_m') or 0) < PROFILES[self.leader_kind(status)]['airborne_agl_m']:
            # Airborne before the leader (user's idea, 2026-11-04: Follow on a leader still on the ground made it fly
            # off and turn back to land): it orbits over the field until the leader is up.
            if not self.holding:
                self.holding, self.steering = True, False
                self.send('wing_orbit', float(lead['lat']), float(lead['lon']),
                          float((lead.get('alt_msl_m') or 0) + numbers['hold_above_field_m']), numbers['orbit_speed_ms'])
            return
        if self.holding:   # the leader is airborne: join
            self.holding = False
            self.send('wing_follow', *self.formation)
        distance = geo.distance_nm(lead['lat'], lead['lon'], status['lat'], status['lon'])
        ahead = clock(geo.bearing_deg(lead['lat'], lead['lon'], status['lat'], status['lon']),
                      lead['heading_deg']) in (10, 11, 12, 1, 2)
        if self.steering and distance <= numbers['rejoin_near_nm'] and not ahead:
            self.steering = False
            if self.trouble and self.trouble['kind'] == 'rejoin':
                self.trouble = None
            self.send('wing_follow', *self.formation)
            return
        if not self.steering:
            if distance <= numbers['rejoin_far_nm'] and not (ahead and distance > numbers['rejoin_ahead_nm']):
                return
            self.steering, self.next_steer, self.steer_distances = True, 0.0, []
        now = time.monotonic()
        if now < self.next_steer:
            return
        self.next_steer = now + REJOIN_STEER_S
        # Flown back to the leader but further away at every steer (live 2026-11-05: 1.5 -> 11.6 nm): not obeying.
        self.steer_distances = (self.steer_distances + [distance])[-(NOT_CLOSING_STEERS + 1):]
        d = self.steer_distances
        if len(d) > NOT_CLOSING_STEERS and all(b > a for a, b in zip(d, d[1:])):
            self.not_obeying('rejoin', f'you are being flown back to the leader but get further away '
                                       f'({round(d[0], 1)} -> {round(d[-1], 1)} nm): your aircraft is not answering')
        lat, lon = geo.offset_point(lead['lat'], lead['lon'], lead['heading_deg'] + 180, numbers['rejoin_behind_m'])
        lead_speed = lead.get('tas_ms') if isinstance(lead.get('tas_ms'), (int, float)) else numbers['orbit_speed_ms']
        speed = lead_speed if ahead else min(lead_speed + numbers['rejoin_extra_ms'], numbers['rejoin_max_ms'])
        alt = lead.get('alt_msl_m') if isinstance(lead.get('alt_msl_m'), (int, float)) else status.get('alt_m')
        self.send('wing_fly', float(lat), float(lon), float(alt or 1500.0), 'BARO', float(speed))

    def landing_expected(self) -> bool:
        """Told to land, or never told anything (its own Mission Editor route may end in a landing)."""
        return self.landing_ordered or not (self.main_task or self.formation)

    def going_to_land(self, status: dict, air: dict) -> bool:
        """What an onlooker sees of a landing: an airplane's gear down; a helicopter (many have a fixed gear: user,
        2026-11-05, the Mi-8) low and slow, SETTLING_AGL_M and SETTLING_MS."""
        if self.kind(status) == 'airplane':
            return air.get('gear_down') is True
        agl = (status.get('alt_m') or 0) - (air.get('ground_m') or 0)
        speed = math.hypot(air.get('vx') or 0, air.get('vz') or 0)
        return 'alt_m' in status and agl < SETTLING_AGL_M and speed < SETTLING_MS

    def watch_orders(self, status: dict, air: dict, phase: str | None) -> None:
        """DCS's AI taking the aircraft down to land by itself, with no landing ordered (DCS tells nobody what its AI
        decided: going_to_land is what an onlooker sees)."""
        now = time.monotonic()
        takeoff_s_ago = self.events(air).get('takeoff')
        gear = phase == AIRBORNE and self.going_to_land(status, air) and not self.landing_expected() and \
            (not isinstance(takeoff_s_ago, (int, float)) or takeoff_s_ago >= UNCOMMANDED_AFTER_TAKEOFF_S)
        if not gear:
            self.gear_since = None
            if phase == AIRBORNE and self.trouble and self.trouble['kind'] == 'landing':
                self.trouble = None   # the gear is up again: it obeys
            return
        self.gear_since = self.gear_since or now
        if now - self.gear_since >= UNCOMMANDED_SEEN_S:
            seen = 'gear down' if self.kind(status) == 'airplane' else 'settling low and slow'
            self.not_obeying('landing', f'DCS\'s AI is taking your aircraft down to land by itself ({seen}): nobody '
                                        'ordered a landing')

    def not_obeying(self, kind: str, text: str) -> None:
        """The aircraft does not do what it was told: its order is given again at once (a landing: every
        UNCOMMANDED_RETRY_S), and the agent is asked to fix it with its tools, silently; the leader hears nothing."""
        now = time.monotonic()
        trouble = self.trouble
        if not trouble or trouble['kind'] != kind:
            trouble = self.trouble = {'kind': kind, 'since': now, 'next': 0.0, 'tries': 0, 'asked': False}
        trouble['text'] = text
        if kind == 'rejoin':   # keep_formation already steers it every few seconds: the agent tries its own way
            trouble['tries'] = NOT_CLOSING_STEERS
        elif now >= trouble['next']:
            trouble['next'], trouble['tries'] = now + UNCOMMANDED_RETRY_S, trouble['tries'] + 1
            self.give_again()
        if trouble['tries'] >= 2 and not trouble['asked']:
            trouble['asked'] = True
            self.pending.append({'at': now, 'kind': 'fix', 'urgent': True, 'fix': True,
                                 'text': f'{text}. Your order was already given again and it still does not obey.'})

    def give_again(self) -> None:
        """The last main task again (DCS dropped it), else the formation on the leader."""
        if self.main_task:
            self.send(self.main_task[0], *self.main_task[1])
        elif self.formation:
            self.send('wing_follow', *self.formation)
        else:
            self.rejoin()

    def trouble_text(self) -> str | None:
        trouble = self.trouble
        if not trouble:
            return None
        return (f'{trouble["text"]} ({round(time.monotonic() - trouble["since"])} s): the program gives your order '
                'again; fix it with your tools if it goes on. Do not tell the leader')

    def clear_next(self) -> dict:
        job = self.job
        if 'lat' in job:   # a zone: the next target found there
            if self.attack_one_in_zone():
                return {'ok': True, 'detail': f'cleared: attacking the next target, in {job["group"]}'}
            job.update(waiting=False, attacking=False, searching_since=time.monotonic())
            return {'ok': True, 'detail': 'cleared: no target detected in the zone right now, searching'}
        result = self.task('attack', 'wing_attack', job['group'], 'one')
        if result['ok']:
            job.update(waiting=False, attacking=True)
        return result

    def job_text(self) -> str:
        job = self.job
        where = f'in zone {job["target"]}' if 'lat' in job else f'group {job["target"]}'
        done = f' ({job["kills"]} destroyed so far)' if job['kills'] else ''
        if job['how'] == 'all':
            now = f'; now attacking {job["current"]}' if job.get('current') else ''
            return f'attacking all {job["against"]} targets {where}, pass after pass{done}{now}'
        if job['waiting']:
            return f'one at a time {where}: waiting for the leader\'s clearance to attack the next{done}'
        if job['attacking']:
            return f'one at a time {where}: attacking one target{done}'
        return f'one at a time {where}: over the zone, searching for a target{done}'

    def job_tick(self, status: dict, air: dict, kills: int) -> None:
        """Ends the attack when it is over and rejoins the leader; one at a time: waits for clearance after a kill."""
        job, now = self.job, time.monotonic()
        if job is None:
            return
        if not air.get('in_air') or self.lost(status):
            self.job = None
            return
        job['kills'] += kills
        if kills and job['how'] == 'one' and not job['waiting']:
            job.update(waiting=True, attacking=False)
            self.report('waiting', f'you destroyed your target in {job["target"]}: you now wait over there for the '
                                   'leader\'s clearance for the next one (ask for it)', urgent=True)
            return
        # Weapons it still has: a release shows as a drop, combat log or not (live 2026-11-04: it kept orbiting the
        # zone for 6 minutes after its last bombs, with rockets left).
        load = sum(w['count'] or 0 for w in self.weapons(air) if w['kind'] != 'gun')
        if load < job.setdefault('load', load):
            job.update(load=load, last_release=now)
        # Still flying to it (live 2026-11-04: an Osa 19 miles away, called off after 3 minutes on the way): the
        # 3 minutes count only once it is near.
        aim = self.enemy_memory.get(job.get('group')) if 'lat' not in job else job
        if aim and 'lat' in status and \
                geo.distance_nm(status['lat'], status['lon'], aim['lat'], aim['lon']) / NM_PER_M > JOB_NEAR_M:
            job['last_release'] = now
        reason = None
        if job['had_weapons'] and self.out_of_weapons(air, job['against']):
            reason = 'out of weapons'
        elif now - job.get('last_release', job['started']) >= JOB_IDLE_S and not job['waiting']:
            reason = f'no weapon released for {round(JOB_IDLE_S / 60)} minutes: it is not getting any attack done'
        elif isinstance(air.get('fuel'), (int, float)) and air['fuel'] < FUEL_LOW:
            reason = 'fuel low'
        elif self.damage(air) >= JOB_DAMAGE:
            reason = 'badly damaged'
        elif 'lat' in job:
            if self.in_zone():
                job['last_seen'] = now
            if job['how'] == 'all':
                self.next_target()
            if job['how'] == 'all' and now - job['started'] >= JOB_MIN_S and now - job['last_seen'] >= JOB_CLEAR_S:
                reason = 'no more targets found in the zone'
            if job['how'] == 'one' and not job['waiting'] and not job['attacking']:
                if not self.attack_one_in_zone() and now - job.get('searching_since', job['started']) >= JOB_NOTHING_S:
                    reason = 'no target found in the zone'
        elif job['group'] not in self.enemy_memory:
            reason = f'the target group {job["group"]} is destroyed or out of sight'
        if reason:
            later = [n for n in (self.in_zone(ignored=True) if 'lat' in job else []) if self.ignored[n] == 'later']
            self.job = None
            self.rejoin()
            ask =(f'; the leader asked you to leave {", ".join(later)} for later: ask now whether to attack '
                   'them' if later else '')
            self.report('job_done', f'your attack on {job["target"]} is over ({reason}, {job["kills"]} destroyed): '
                                    f'you are rejoining the leader on their {REJOIN[0]} o\'clock{ask}', urgent=True)

    def smoke_in_sight(self, status: dict | None) -> list[dict]:
        if not status or 'lat' not in status:
            return []
        return smoke.in_sight(self.state, status['lat'], status['lon'], smoke.SEEN_FROM_AIR_M)

    def point(self, where: str) -> tuple[float, float] | str:
        """(lat, lon) of 'here' (the wingman), 'leader', a mission zone or one of the pilot's F10 map marks (they come
        as zones, dcs/instructor_tasks.lua), another agent's group (e.g. a squad that popped smoke), an enemy it saw,
        or an airfield; else an error text."""
        status = self.status() or {}
        self.remember_enemies(status)
        wanted = geo.simplify(where)
        if wanted == 'here':
            return (status['lat'], status['lon']) if 'lat' in status else 'your position is unknown'
        if wanted == 'leader':
            lead = self.leader()
            return (lead['lat'], lead['lon']) if lead else 'the leader\'s position is unknown'
        groups = {n: g for n, g in (self.state.groups or {}).items() if n != self.group and isinstance(g, dict)}
        fields = {n: f for n, f in (self.state.weather or {}).items() if isinstance(f, dict) and 'lat' in f}
        places = dict(fields)
        places.update({z.get('name', ''): z for z in self.state.zones or [] if 'lat' in z})
        places.update({n: g for n, g in groups.items() if 'lat' in g})
        places.update({self.called(n): g for n, g in groups.items() if 'lat' in g})   # by the agent's name too
        places.update(self.enemy_memory)   # an enemy it sees or saw lately (a SAM site, a static...): its point
        exact = [p for n, p in places.items() if geo.simplify(n) == wanted]
        # Live check 2026-11-04: "bomb where Tango is" came as "Tango squad": one known name inside the words.
        inside = [p for n, p in places.items() if geo.simplify(n) and geo.simplify(n) in wanted]
        found = exact or (inside if len(inside) == 1 else [])
        if found:
            return found[0]['lat'], found[0]['lon']
        marked = smoke.find(self.smoke_in_sight(status), where)
        if marked:
            return marked['lat'], marked['lon']
        if 'smoke' in wanted:
            return 'you see no such smoke: ask for it to be popped again'
        emitter = next((n for n in self.rwr if geo.simplify(n) == wanted or
                        (geo.simplify(n) and geo.simplify(n) in wanted)), None)
        if emitter:
            return (f'{emitter} is only on your radar warning receiver: you know its direction, not where it is. '
                    'You cannot attack it, strike it or fly to it until you see it: closer, with your eyes or radar '
                    '(the leader can lead you that way, or you can fly to a known place in that direction)')
        known = [z.get('name', '') for z in self.state.zones or []] + list(groups) + list(self.enemy_memory) + \
            list(fields)
        return f'no zone or group called {where} (known: {", ".join(known) or "none"})'

    def strike(self, target: str, weapon: str = 'auto', altitude_ft: float = 0, attack_heading: float = -1,
               attack: str = 'auto') -> dict:
        """Attack a point (an enemy it sees, a static, a zone, another agent's group): DCS's AI takes a point whatever
        is there, a SAM site too (user's idea, 2026-11-04: without anti-radiation missiles DCS refuses to attack the
        SAM group itself). weapon, attack altitude, attack heading (true degrees), dive or level."""
        if weapon not in WEAPONS or attack not in ATTACKS:
            return {'ok': False, 'detail': f'weapon one of {", ".join(WEAPONS)}, attack one of {", ".join(ATTACKS)}'}
        where = self.point(target)
        if isinstance(where, str):
            return {'ok': False, 'detail': where}
        alt_m = float(altitude_ft or 0) / FT_PER_M
        heading = float(attack_heading) % 360 if attack_heading is not None and float(attack_heading) >= 0 else -1.0
        return self.task('strike', 'wing_bomb', float(where[0]), float(where[1]), weapon, alt_m, heading, attack)

    def fly_to(self, where: str, altitude_ft: float, above: str = 'ground', speed_kt: float = 0) -> dict:
        """Fly to a place at an altitude above the ground (low level: terrain following as DCS's AI does it) or the
        sea, then orbit there; the remote control for the aircraft (user, 2026-11-04)."""
        if above not in ABOVE:
            return {'ok': False, 'detail': f'above must be one of {", ".join(ABOVE)}'}
        place = self.point(where)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        numbers = self.profile()
        alt_m = max(float(altitude_ft or 0) / FT_PER_M, numbers['low_level_min_m'] if above == 'ground' else 100.0)
        air = self.air(self.status())
        speed = float(speed_kt) / KT_PER_MS if speed_kt else max(math.hypot(air.get('vx') or 0, air.get('vz') or 0),
                                                                 numbers['orbit_speed_ms'])
        return self.task('fly_to', 'wing_fly', float(place[0]), float(place[1]), alt_m,
                         'RADIO' if above == 'ground' else 'BARO', speed)

    def orbit(self, where: str = 'here', altitude_ft: float | None = None) -> dict:
        place = self.point(where)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        status = self.status() or {}
        air = self.air(status)
        alt_m = float(altitude_ft) / FT_PER_M if altitude_ft else float(status.get('alt_m') or 1500.0)
        speed = max(math.hypot(air.get('vx') or 0, air.get('vz') or 0), self.profile(status)['orbit_speed_ms'])
        return self.task('orbit', 'wing_orbit', float(place[0]), float(place[1]), alt_m, speed)

    def target_point(self, target: str, offset_m: float = 0, toward: str = '') -> tuple[tuple, tuple | None] | str:
        """A call for fire's point from its own eyes and sensors (shared/net.py call_for_fire): an enemy it sees or
        saw lately, any place point() knows, or coordinates the leader gave. ((lat, lon), its own (lat, lon))."""
        where = coords.parse(target) or self.point(target)
        if isinstance(where, str):
            return where
        where = self.offset(where, offset_m, toward)
        if isinstance(where, str):
            return where
        status = self.status() or {}
        return where, ((status['lat'], status['lon']) if 'lat' in status else None)

    def offset(self, place: tuple[float, float], offset_m: float, toward: str) -> tuple[float, float] | str:
        """A place moved offset_m toward a compass direction ("north", "south-west") or a clock position from the
        leader's nose ("3" = to the leader's right); unchanged with no offset."""
        return geo.shift(place, offset_m, toward, (self.leader() or {}).get('heading_deg') if offset_m else None)

    def land(self, where: str = 'here', offset_m: float = 0, toward: str = '', minutes: float = 0,
             how: str = 'normal') -> dict:
        """A helicopter lands at a place (user, 2026-11-05): "land here", "land beside Tango", "land at LZ1" (an F10
        map mark), "land at my 3 o'clock, 200 m" (where = leader, offset). DCS finds a spot it can land on near the
        point. minutes 0 = it stays until the next order (any flying order lifts it off)."""
        if self.kind() != 'helicopter':
            return {'ok': False, 'detail': 'only a helicopter lands at a point: an airplane needs an airfield '
                                           '(return_to_base)'}
        if how not in LAND_HOW:
            return {'ok': False, 'detail': f'how must be one of {", ".join(LAND_HOW)}'}
        place = self.point(where)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        place = self.offset(place, offset_m, toward)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        return self.task('land', 'wing_land_at', float(place[0]), float(place[1]),
                         max(float(minutes or 0), 0.0) * 60.0, how)

    def called(self, group: str) -> str:
        """How the pilot calls an agent's group: the agent's name (the GUI's), else the group's name."""
        return self.state.agent_names.get(group) or group

    def squad_group(self, name: str) -> str | None:
        """A ground group of the agents (a squad) by its agent's name or its group's name in the mission, matched
        loosely ("Tango", "Tango squad")."""
        wanted = geo.simplify(name)
        def infantry(g: dict) -> bool:   # a vehicle agent's group is not a squad to carry (squad/desk.py kind)
            ground = g.get('ground') if isinstance(g.get('ground'), dict) else {}
            return (ground.get('infantry') or 0) >= (ground.get('vehicles') or 0)

        ground = [n for n, g in (self.state.groups or {}).items()
                  if isinstance(g, dict) and 'air' not in g and n != self.group and infantry(g)]
        names = {n: {geo.simplify(n), geo.simplify(self.called(n))} - {''} for n in ground}
        exact = [n for n in ground if wanted in names[n]]
        inside = [n for n in ground if any(s in wanted for s in names[n])]
        found = exact or (inside if len(inside) == 1 else [])
        return found[0] if found else None

    def carried(self, stage: str | None = None) -> list[str]:
        """The squads this helicopter is picking up, carrying or dropping (state.transport), or those of one stage."""
        return [s for s, t in self.state.transport.items()
                if t.get('carrier') == self.group and (stage is None or t.get('stage') == stage)]

    def pick_up(self, squad: str, where: str = '', offset_m: float = 0, toward: str = '') -> dict:
        """User, 2026-11-05: "Cobra, pick up Tango at the extraction point". DCS lands it there and the squad walks in
        (the squad should be waiting there: its wait_for_pickup). where '' = where the squad is now."""
        if self.kind() != 'helicopter':
            return {'ok': False, 'detail': 'only a helicopter picks up troops'}
        group = self.squad_group(squad)
        if not group:
            names = [self.called(n) for n, g in (self.state.groups or {}).items()
                     if isinstance(g, dict) and 'air' not in g]
            return {'ok': False, 'detail': f'no squad called {squad} (squads: {", ".join(names) or "none"})'}
        if self.carried('aboard'):
            carrying = ', '.join(self.called(s) for s in self.carried('aboard'))
            return {'ok': False, 'detail': f'you already carry {carrying}: drop them first'}
        place = self.point(where or group)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        place = self.offset(place, offset_m, toward)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        result = self.task('pick_up', 'wing_pickup', float(place[0]), float(place[1]), group)
        if result['ok']:
            self.state.transport[group] = {'carrier': self.group, 'stage': 'pickup', 'lat': place[0], 'lon': place[1]}
            # Live 2026-11-05: Victor had been told to move there, not to wait for pickup, and DCS's Embarking found
            # nobody to take. DCS keeps the helicopter on the ground until the squad waiting with EmbarkToTransport
            # is aboard: the squad is told to wait there too (the pilot's "pick up Victor" means it).
            try:
                self.tasks.send('wait_pickup', group, float(place[0]), float(place[1]), PICKUP_RADIUS_M)
            except OSError:
                pass
            result['detail'] += f'; {self.called(group)} told to wait for you there'
        return result

    def drop_off(self, where: str = 'here', offset_m: float = 0, toward: str = '') -> dict:
        """User, 2026-11-05: "Cobra, take Tango to Z1": it lands there and the troops it carries get out."""
        squads = self.carried('aboard')
        if not squads:
            return {'ok': False, 'detail': 'you carry no troops'}
        place = self.point(where)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        place = self.offset(place, offset_m, toward)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        for squad in squads:
            result = self.task('drop_off', 'wing_dropoff', float(place[0]), float(place[1]), squad)
            if not result['ok']:
                return result
            self.state.transport[squad].update(stage='drop', lat=place[0], lon=place[1])
        return result

    def watch_transport(self, status: dict, air: dict) -> None:
        """Who is aboard (DCS does not say): on the ground within TRANSPORT_NEAR_M of a pickup / drop point, then
        DCS's takeoff event (live 2026-11-05: "in the air" flickered as it touched down and Victor counted as aboard
        9 s after landing; DCS lifts it off only once the waiting squad is aboard): the squad boarded if it was there
        (or is gone from the mission), got out at a drop."""
        if 'lat' not in status:
            return
        events = self.events(air)
        on_ground = not air.get('in_air') or \
            ('land' in events and ('takeoff' not in events or events['land'] < events['takeoff']))
        # DCS gave no takeoff event when Cobra left the pickup with Victor (live 2026-11-05, second flight): well up
        # in the air counts too, which a touchdown bounce never is.
        agl = (status.get('alt_m') or 0) - (air.get('ground_m') or 0)
        lifted = bool(air.get('in_air')) and (
            ('takeoff' in events and 'land' in events and events['takeoff'] < events['land']) or
            agl >= TRANSPORT_LIFTED_AGL_M)
        for squad in self.carried():
            entry = self.state.transport[squad]
            if entry['stage'] == 'aboard':
                continue
            near = geo.distance_nm(status['lat'], status['lon'], entry['lat'], entry['lon']) / NM_PER_M <= \
                TRANSPORT_NEAR_M
            if not lifted:
                entry['landed'] = entry.get('landed') or (near and on_ground)
                continue
            if not entry.get('landed'):
                continue
            if entry['stage'] == 'drop':
                del self.state.transport[squad]
                self.report('transport', f'you lifted off from the drop point: {self.called(squad)} got out there')
                continue
            troops = (self.state.groups or {}).get(squad) or {}
            there = not troops.get('exists', True) or 'lat' not in troops or \
                geo.distance_nm(troops['lat'], troops['lon'], entry['lat'], entry['lon']) / NM_PER_M <= \
                TRANSPORT_NEAR_M
            if there:
                entry.update(stage='aboard', landed=False)
                self.report('transport', f'you lifted off with {self.called(squad)} aboard')
            else:
                del self.state.transport[squad]
                self.report('transport', f'you lifted off without {self.called(squad)}: they had not reached the '
                                         'pickup point', urgent=True)

    def airfield(self, name: str = '') -> str | None:
        """An airfield by (part of) its DCS name, else the nearest one of the pilot's side (or neutral)."""
        fields = {n: f for n, f in (self.state.weather or {}).items() if isinstance(f, dict) and 'lat' in f}
        if name:
            wanted = geo.simplify(name)
            return next((n for n in fields if wanted and wanted in geo.simplify(n)), None)
        status = self.status() or {}
        if 'lat' not in status:
            return None
        player = next((a for a in self.state.traffic or [] if a.get('player')), {})
        side = player.get('side')
        friendly = [n for n, f in fields.items() if side is None or f.get('side') in (side, 0, None)]
        return min(friendly, key=lambda n: geo.distance_nm(status['lat'], status['lon'], fields[n]['lat'],
                                                           fields[n]['lon']), default=None)

    def return_to_base(self, airfield: str = '') -> dict:
        found = self.airfield(airfield)
        if not found:
            return {'ok': False, 'detail': f'no airfield called {airfield}' if airfield else 'no airfield known yet'}
        return self.task('return_to_base', 'wing_land', found)

    def weapons_rules(self, rules: str) -> dict:
        result = self.task('weapons', 'wing_roe', rules)
        if result['ok'] and rules == 'free' and any(h == 'ignore' for h in self.ignored.values()):
            # Weapons free: DCS's AI attacks whatever it finds, those too.
            result['detail'] = (f'{result["detail"]}; careful: with weapons free DCS\'s AI may also attack what the '
                                f'leader told you to leave alone ({", ".join(self.ignored)})')
        return result

    # --- reports and quiet time --------------------------------------------------------------------------------------

    def report(self, kind: str, text: str, urgent: bool = False) -> None:
        """Something to call out; not made when the pilot already asked to be told of it (tell_pilot_when)."""
        asked = REQUEST_OF_REPORT.get(kind)
        if asked and any(r.get('when') == asked for r in self.requests):
            return
        self.pending.append({'at': time.monotonic(), 'kind': kind, 'text': text, 'urgent': urgent})

    def combat_log(self, air: dict) -> int:
        """New entries of DCS's combat log for this aircraft become reports; returns the kills among them."""
        log = [e for e in air.get('combat') or [] if isinstance(e, dict) and isinstance(e.get('n'), int)]
        newest = max((e['n'] for e in log), default=0)
        if self.combat_seen is None or newest < self.combat_seen:   # first sight, or the mission restarted
            self.combat_seen = newest
            return 0
        kills, released = 0, {}
        for entry in sorted(log, key=lambda e: e['n']):
            if entry['n'] <= self.combat_seen:
                continue
            kind = entry.get('kind')
            if kind == 'weapon_released':
                name = entry.get('weapon') or 'a weapon'
                released[name] = released.get(name, 0) + 1
            elif kind == 'kill':
                kills += 1
                what = entry.get('target') or 'a target'
                if (self.enemy_memory.get(entry.get('name')) or {}).get('static'):
                    del self.enemy_memory[entry['name']]   # a destroyed static: not a target any more
                if self.job and self.job.get('current') == entry.get('name'):
                    self.job['current'] = None
                self.report('kill', f'you {"shot down" if entry.get("air") else "destroyed"} a {what}', urgent=True)
            elif kind == 'hit_taken':
                by = f' by a {entry["by"]}' if entry.get('by') else ''
                self.report('hit', f'you were hit{by}: your damage is now {round(self.damage(air) * 100)} percent',
                            urgent=True)
        self.combat_seen = newest
        if released:
            names = ', '.join(f'{count} {name}' for name, count in released.items())
            self.report('weapon', f'you released: {names}')
        return kills

    def watch_flight(self, status: dict, air: dict, phase: str) -> None:
        """Takeoff, landing, fuel low, winchester and new contacts, as reports."""
        # Each flight step once, from DCS's events, whenever they come: live 2026-11-04 the takeoff event came a few
        # seconds after the aircraft was already "in the air" and the land event after the landing roll had begun,
        # so watching for the moment the phase changed missed both.
        first, self.last_phase = self.last_phase is None, phase   # first look (program started late): no calls
        events, said = self.events(air), self.flight_said

        def step(name: str, text: str) -> None:
            said.add(name)
            if not first:
                self.report(name, text)

        if not air.get('started'):
            said.clear()
            return self.watch_fuel_weapons_contacts(status, air)
        if 'engine_startup' in events and 'engines' not in said and not air.get('in_air'):
            if air.get('started') in ('cold', 'uncontrolled', 'mission'):
                step('engines', 'your engines are running (say only that: you are not taxiing yet)')
            said.add('engines')
        if phase == 'taxiing to the runway' and 'taxi' not in said:
            step('taxi', 'you are taxiing to the runway')
        took_off = 'takeoff' in events and ('land' not in events or events['takeoff'] < events['land'])
        # A helicopter leaving a point landing (a pickup) got no takeoff event from DCS (live 2026-11-05): well up in
        # the air after a landing call counts as a takeoff.
        agl = (status.get('alt_m') or 0) - (air.get('ground_m') or 0)
        high = bool(air.get('in_air')) and 'alt_m' in status and agl >= TRANSPORT_LIFTED_AGL_M
        took_off = took_off or (high and 'landed' in said)
        if took_off and air.get('in_air') and 'airborne' not in said:
            said.update({'engines', 'taxi'})
            said.discard('landed')
            self.trouble = None
            order = self.main_task[0] if self.main_task else None
            if order in POINT_LANDINGS:
                # A helicopter's time on the ground where the leader had it land is up: back to the leader, or holding
                # there for orders (with no task DCS would take it home).
                self.main_task, self.landing_ordered = None, False
                done = {'wing_pickup': 'you lifted off from the pickup point',
                        'wing_dropoff': 'you lifted off from the drop point'}.get(
                    order, 'your time on the ground is up and you lifted off again')
                if not first and self.same_kind(status):
                    self.rejoin()
                    step('airborne', f'{done}: you are joining the leader')
                elif not first:
                    self.hold_here(status, air)
                    step('airborne', f'{done}: you hold there for the leader\'s orders')
            elif order and order not in LANDINGS:   # landed at a point, lifted off for the leader's next order
                step('airborne', 'you lifted off for the leader\'s order (say only that you are on your way)')
            elif not self.same_kind(status):
                self.main_task, self.landing_ordered = None, False   # a new flight
                # No formation with a leader of the other kind (dcs/instructor_tasks.lua gives no Follow): it waits
                # over its field for orders; its departure route would end in DCS taking it back to land.
                if not first:
                    self.hold_here(status, air)
                step('airborne', f'you took off and are airborne. {self.other_kind_note()}: you orbit over the '
                                 'field and wait for the leader\'s orders')
            else:
                self.main_task, self.landing_ordered = None, False   # a new flight
                if not first:   # the mission script joins it to the leader on takeoff (dcs/instructor_tasks.lua)
                    self.formation = self.formation or self.rejoin_offset()
                leader_agl = (self.leader() or {}).get('alt_agl_m') or 0
                if leader_agl < PROFILES[self.leader_kind(status)]['airborne_agl_m']:
                    step('airborne', 'you took off before the leader: you orbit over the field until the leader is '
                                     'airborne, then you join')
                else:
                    step('airborne', 'you took off and are airborne: you are joining the leader')
        landed = 'land' in events and ('takeoff' not in events or events['land'] < events['takeoff']) and not high
        if landed and 'airborne' in said and 'landed' not in said:
            said.discard('airborne')
            if self.main_task and self.main_task[0] == 'wing_land_at':
                seconds = self.main_task[1][2]   # wing_land_at: lat, lon, seconds, how
                wait = f'for {round(seconds / 60)} minutes' if seconds else 'until the leader gives you the next order'
                step('landed', f'you are on the ground where the leader told you to land; you wait there {wait} '
                               '(any flying order lifts you off)')
            elif self.main_task and self.main_task[0] == 'wing_pickup':
                squad = self.called(self.main_task[1][2])   # wing_pickup: lat, lon, squad
                step('landed', f'you are on the ground at the pickup point: {squad} boards you now. You stay on the '
                               'ground until they are all aboard, then you lift off by yourself; an order that '
                               'flies would leave them behind, so only if the leader insists (say only that you '
                               'are on the ground waiting for them)')
            elif self.main_task and self.main_task[0] == 'wing_dropoff':
                squad = self.called(self.main_task[1][2])
                step('landed', f'you are on the ground at the drop point: {squad} gets out now')
            elif self.landing_expected():
                step('landed', 'you landed. If something of this flight is worth keeping, save one short line about '
                               'it with remember (about: flight)')
            else:
                # DCS's AI landed it by itself, whatever it was told (user, 2026-11-05): to the leader it is an
                # emergency landing, in character.
                field = self.airfield()
                self.trouble = None
                said.add('landed')
                if not first:
                    self.report('landed', f'you landed{" at " + field if field else ""} although nobody ordered it: '
                                          'DCS\'s AI took your aircraft down by itself. For the leader this was an '
                                          'emergency landing: tell them in character that you had a problem with the '
                                          'aircraft and had to land, choose a believable fault, and why you did not '
                                          'call before (for example the leader was busy on the radio with the tower). '
                                          'You stay on the ground now. If something of this flight is worth keeping, '
                                          'save one short line about it with remember (about: flight)', urgent=True)
        self.watch_fuel_weapons_contacts(status, air)

    def hold_here(self, status: dict, air: dict) -> None:
        """Circle over where it is, low (its kind's height above the ground), waiting for orders."""
        if 'lat' not in status:
            return
        numbers = self.profile(status)
        self.send('wing_orbit', float(status['lat']), float(status['lon']),
                  float(air.get('ground_m') or 0) + numbers['hold_above_field_m'], numbers['orbit_speed_ms'])

    def watch_fuel_weapons_contacts(self, status: dict, air: dict) -> None:
        fuel = air.get('fuel') if isinstance(air.get('fuel'), (int, float)) else None
        if fuel is not None and air.get('in_air'):
            if fuel < FUEL_LOW and not self.flags['fuel_low']:
                self.report('fuel_low', f'your fuel is low: {round(fuel * 100)} percent of internal fuel (bingo)',
                            urgent=True)
            self.flags['fuel_low'] = fuel < FUEL_LOW
        empty = self.out_of_weapons(air)
        # Live 2026-11-05: DCS left Cobra's rockets out of one reading (damaged) and "winchester" was called with 40
        # rockets aboard: out of weapons only once it lasts.
        now = time.monotonic()
        self.empty_since = (self.empty_since or now) if empty else None
        confirmed = empty and now - self.empty_since >= WINCHESTER_CONFIRM_S
        if air.get('in_air') and confirmed and not self.flags['out_of_weapons'] and self.weapons_seen:
            self.report('out_of_weapons', 'you are out of weapons but the gun (winchester)')
        self.flags['out_of_weapons'] = confirmed
        self.weapons_seen = self.weapons_seen or not empty
        if not air.get('in_air'):
            return
        view = {e['group']: e for e in self.enemies_view(status)}
        new = [(n, e) for n, e in view.items() if n not in self.contacts and 'last_seen_s_ago' not in e and
               n not in self.ignored]
        aircraft = [(n, e) for n, e in new if self.enemy_memory.get(n, {}).get('air')]
        if aircraft:   # a threat: at once, each one
            parts = []
            for name, enemy in aircraft[:6]:
                where = f'{enemy["clock"]} o\'clock' if 'clock' in enemy else enemy.get('direction', '')
                what = f'{enemy.get("units") or ""} {enemy.get("type") or ""}'.strip()
                parts.append(f'{what} ({name}), {where} from the leader, {enemy["distance_nm"]} miles'
                             + (f', {enemy["altitude_ft"]} ft' if 'altitude_ft' in enemy else ''))
            more = f'; and {len(aircraft) - 6} more' if len(aircraft) > 6 else ''
            self.report('contact', 'new aircraft contacts: ' + '; '.join(parts) + more, urgent=True)
        # Ground contacts (user, 2026-11-04: a target area came as three calls naming every truck): its eyes find them
        # one after another as it gets closer, so they are gathered a moment and called as one group.
        now = time.monotonic()
        ground = [(n, e) for n, e in new if not self.enemy_memory.get(n, {}).get('air')]
        if ground:
            self.contact_batch.update(ground)
            self.batch_last, self.batch_first = now, self.batch_first or now
        if self.contact_batch and (now - self.batch_last >= CONTACT_GATHER_S or
                                   now - self.batch_first >= CONTACT_GATHER_MAX_S):
            self.report('contact', self.ground_contacts_text(list(self.contact_batch.values())))
            self.contact_batch, self.batch_first = {}, None
        self.contacts = {n for n in self.contacts if n in self.enemy_memory} | set(view)
        emitters = [e for e in self.rwr_view() if e['group'] not in self.rwr_called and e['group'] not in self.contacts
                    and not e.get('leave_alone')]
        if emitters:
            parts = [f'{e["type"]} ({e["group"]}), {e["clock"]} o\'clock' if 'clock' in e else
                     f'{e["type"]} ({e["group"]}), {e["direction"]}' for e in emitters]
            self.report('rwr', 'your radar warning receiver picks up: ' + '; '.join(parts) +
                        ' (direction only: you do not know how far nor exactly where)', urgent=True)
        self.rwr_called = {n for n in self.rwr_called | {e['group'] for e in emitters} if n in self.rwr}

    @staticmethod
    def ground_contacts_text(enemies: list[dict]) -> str:
        """New ground contacts as one group: how many, of what, where (clock span from the leader) and how far; the
        model names them one by one only when the leader asks (the situation lists them)."""
        kinds: dict[str, int] = {}
        for enemy in enemies:
            kind = enemy.get('type') or 'unknown'
            kinds[kind] = kinds.get(kind, 0) + (enemy.get('units') or 1)
        total = sum(kinds.values())
        what = ', '.join(f'{count} {kind}' for kind, count in sorted(kinds.items(), key=lambda k: -k[1]))
        clocks = [e['clock'] for e in enemies if 'clock' in e]
        where = clock_span(clocks) if clocks else ', '.join(sorted({e.get('direction', '') for e in enemies}))
        near, far = min(e['distance_nm'] for e in enemies), max(e['distance_nm'] for e in enemies)
        miles = (f'{round(near)} mile{"s" if round(near) != 1 else ""}' if round(near) == round(far) else
                 f'{round(near)} to {round(far)} miles')
        return (f'new ground contacts: {total} target{"s" if total > 1 else ""} ({what}), {where} from the leader, '
                f'{miles}. Call them as one group: where and how far (e.g. "contacts, your ten to eleven, three '
                'miles"); name them one by one only if the leader asks')

    def going_down(self, status: dict) -> None:
        """Lost (user, 2026-11-04: it answered "from the beyond" after being shot down): the hits it took, then one
        last call if its pilot is alive to make it (ejecting, crashed); afterwards it is silent (silent())."""
        self.lost_said, self.job = True, None
        for squad in self.carried():   # the squads it was picking up or carrying are on their own now
            del self.state.transport[squad]
        air = self.air(status)
        self.combat_log(air)   # the hit that brought it down (it can come in the same second as the loss)
        events = self.events(air)
        if not status.get('exists') or air.get('last_event') in ('dead', 'pilot_dead') or 'pilot_dead' in events:
            self.pending = []   # killed: nobody makes a call
            return
        how = 'you are ejecting' if 'ejection' in events else 'your aircraft crashed'
        hit = ' after being hit' if 'hit' in events else ''
        self.report('lost', f'your aircraft is lost{hit}: {how}. This is your last call on the radio: very short, '
                            'like a pilot going down; after it you will not talk again', urgent=True)
        for report in self.pending:
            report['final'] = True

    def silent(self, event: dict | None = None) -> bool:
        """Lost: it answers nothing and calls nothing, except its last call (a report marked final)."""
        if not self.lost(self.status()) and not self.lost_said:
            return False
        return not (event or {}).get('final')

    def calm(self, status: dict | None) -> bool:
        """Airborne, the leader well above the ground, no threats, no attack going on, fuel fine."""
        air = self.air(status)
        if self.phase(status) != AIRBORNE or self.job or self.pending:
            return False
        now = time.monotonic()
        if any(now - e['at'] <= ENEMY_MEMORY_S for e in self.enemy_memory.values()):
            return False
        lead = self.leader() or {}
        if not isinstance(lead.get('alt_agl_m'), (int, float)) or lead['alt_agl_m'] < CALM_AGL_M:
            return False
        return not self.flags['fuel_low'] and self.damage(air) < JOB_DAMAGE and self.can_reach_pilot()

    def reports(self, busy: bool = False, quiet_s: float = 0.0) -> list[dict]:
        """What the voice loop gives the model to call out now (Responder events): the pending reports in one call
        (an urgent one at once, weapon releases gathered a moment), else quiet time after a calm silence."""
        if busy:
            return []
        now = time.monotonic()
        fix = next((p for p in self.pending if p.get('fix')), None)
        if fix:   # for the agent alone, never mixed with the calls the leader hears
            self.pending.remove(fix)
            return [{'report': fix['text'], 'fix': True}]
        if self.pending:
            newest = self.pending[-1]
            if any(p['urgent'] for p in self.pending) or newest['kind'] != 'weapon' or \
                    now - newest['at'] >= REPORT_GATHER_S:
                texts = '; '.join(p['text'] for p in self.pending)
                final = any(p.get('final') for p in self.pending)
                self.pending = []
                return [{'report': texts, 'final': True} if final else {'report': texts}]
        if self.lost_said:
            return []
        every = CHATTER_S.get(self.chatter)
        if every and quiet_s >= every and now - self.last_quiet >= every and self.calm(self.status()):
            self.last_quiet = now
            return [{'report': f'nobody has talked on the radio for {round(quiet_s / 60)} minutes and things are '
                               'calm', 'quiet': True}]
        return []

    def tick(self, busy: bool = False) -> str | None:
        """Every couple of seconds: keeps the hook reporting its group and its memory of the enemies, follows the
        attack and gathers the reports (reports() hands them out). Never a fixed call of its own."""
        status = self.status()
        self.remember_enemies(status)
        if status is not None and not self.lost(status):
            self.lost_said = False
            air = self.air(status)
            phase = self.phase(status)
            kills = self.combat_log(air)
            self.watch_flight(status, air, phase)
            self.job_tick(status, air, kills)
            self.keep_formation(status, phase)
            self.watch_orders(status, air, phase)
            self.watch_transport(status, air)
        elif status is not None and not self.lost_said:
            self.going_down(status)
        if self.tasks and self.group and time.monotonic() >= self.next_watch:
            self.next_watch = time.monotonic() + WATCH_EVERY_S
            try:
                self.tasks.watch([self.group], owner=self.owner)
            except OSError:
                pass
        return None
