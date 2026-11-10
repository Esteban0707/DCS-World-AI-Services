"""An artillery agent (role "artillery", user 2026-11-07: "Archer", first a BM-21 Grad): an AI battery of the mission
that fires at points it is given; it never sees its targets (indirect fire), the one who sees them gives the point.

Built on the vehicles desk (vehicles/desk.py: it drives, holds, sees what its crews see, calls its losses). Where it
fires (fire_mission's target), as a real call for fire gives it:
- a target an observer agent sent on the internal net with call_for_fire (shared/net.py): the point comes from the
  observer's own sighting (an enemy it sees, a smoke, a zone, its own position and a distance), named "<observer>-N";
- a known point: a mission zone or one of the pilot's F10 map marks (they come as zones), as pre-planned targets;
- coordinates the pilot reads out: MGRS or latitude / longitude (shared/coords.py);
- "last": the last aim point, for corrections: offset_m toward a compass direction, or add / drop / left / right seen
  from the observer (along the line from the observer to the target, as a forward observer corrects).
DCS's FireAtPoint spreads the rounds over spread_m around the point (Lua T.fire). Rounds fired are seen from its ammo
count (Lua ground_details "ammo"): "shot" when it drops, "rounds complete" when it stops, "not firing" when nothing
leaves the tubes (out of range, no ammunition...). Those reports go to whoever asked: the observer on the net, the pilot
on the radio. agent.json "artillery": {groups: [DCS group names, the first one is the battery], name}.
"""
import time

from instructor import geo
from shared import coords
from vehicles.desk import VehicleDesk

SPREAD_M = (0.0, 2000.0)
DEFAULT_SPREAD_M = 100.0
ROUNDS_MAX = 200
TARGETS_KEPT = 12
TARGET_TTL_S = 3600.0
# DCS's battery takes its time to lay (live 2026-11-07: a BM-21 fired well over a minute after the order; at 60 s it
# had been called "not firing" and the mission dropped, so its shot was never called). Silent until it fires.
FIRE_WAIT_S = 600.0       # no round left the tubes this long after the order: it is not firing
SALVO_QUIET_S = 12.0      # no round for this long after the first: the mission's rounds are complete
QUEUE_MAX = 10
SAME_POINT_M = 10.0      # an order again on the point being laid on is not sent (it would restart the laying);
                          # a correction moves it farther than this
OBSERVER_SHIFTS = {'add': 0, 'drop': 180, 'right': 90, 'left': -90}


class ArtilleryDesk(VehicleDesk):
    CONFIG_KEY = 'artillery'
    UNIT_KEY = 'battery'

    def __init__(self, state, agent: dict, tasks=None, bridge=None, cargo=None):
        super().__init__(state, agent, tasks, bridge, cargo)
        self.targets: dict[str, dict] = {}   # id -> {id, lat, lon, what, by, by_id, observer, at}
        self.counts: dict[str, int] = {}     # observer id -> targets it sent
        self.mission: dict | None = None     # the fire mission going on
        self.last_aim: dict | None = None    # {lat, lon, observer}: "last" for corrections
        self.queue: list[dict] = []          # fire missions for after the current one: {aim, how, rounds, spread_m}
        self.net, self.agent_id = None, agent.get('id') or ''   # the internal net, set by the voice loop

    def can_carry(self) -> bool:
        return False

    def configure(self, agent: dict) -> None:
        """Not a ride for squads (vehicles/desk.py lists itself in state.carriers)."""
        super().configure(agent)
        carriers = getattr(self.state, 'carriers', None)
        if carriers is not None and carriers.get(self.group) is self:
            del carriers[self.group]

    # --- targets ---------------------------------------------------------------------------------------------------

    def receive_target(self, event: dict) -> str | None:
        """A target attached to a net message (call_for_fire): kept under "<observer>-N"; its id, or None."""
        target = event.get('target')
        if not isinstance(target, dict) or not all(isinstance(target.get(k), (int, float)) for k in ('lat', 'lon')):
            return None
        by_id, by = event.get('from', ''), event.get('from_name') or event.get('from', '?')
        self.counts[by_id] = self.counts.get(by_id, 0) + 1
        target_id = f'{by}-{self.counts[by_id]}'
        self.targets[target_id] = {'id': target_id, 'lat': float(target['lat']), 'lon': float(target['lon']),
                                   'what': str(target.get('what') or '')[:120], 'by': by, 'by_id': by_id,
                                   'observer': target.get('observer'), 'at': time.monotonic()}
        now = time.monotonic()
        live = sorted((t for t in self.targets.values() if now - t['at'] <= TARGET_TTL_S), key=lambda t: t['at'])
        self.targets = {t['id']: t for t in live[-TARGETS_KEPT:]}
        if self.mission:   # busy: queued at once with the current mission's rounds and spread
            target = self.targets[target_id]
            self.enqueue({**target}, f'{by}\'s target {target_id}', self.mission['rounds'], self.mission['spread_m'])
        return target_id

    def find_target(self, target: str) -> dict | None:
        wanted = geo.simplify(target)
        return next((t for t in self.targets.values() if geo.simplify(t['id']) == wanted), None)

    def aim_point(self, target: str, offset_m: float = 0, toward: str = '') -> tuple[dict, str] | str:
        """({lat, lon, observer, by, by_id, what}, how) for fire_mission, or an error text."""
        wanted = geo.simplify(target)
        if wanted in ('last', 'lasttarget', 'same', 'repeat'):
            if not self.last_aim:
                return 'no earlier fire mission to correct from: give a target'
            aim, how = dict(self.last_aim), 'the last aim point'
        elif self.find_target(target):
            found = self.find_target(target)
            aim, how = {**found}, f'{found["by"]}\'s target {found["id"]}'
        elif coords.parse(target):
            lat, lon = coords.parse(target)
            aim, how = {'lat': lat, 'lon': lon}, f'the coordinates {target}'
        else:
            status = self.status() or {}
            self.remember_enemies(status)
            enemy = next((e for n, e in self.enemy_memory.items() if geo.simplify(n) == wanted), None)
            where = (enemy['lat'], enemy['lon']) if enemy else self.place(target)
            if isinstance(where, str):
                known = ', '.join(self.targets) or 'none'
                return f'{where}; targets from observers: {known}; or coordinates (MGRS or lat/lon)'
            aim, how = {'lat': where[0], 'lon': where[1]}, target
        aim.setdefault('by', 'the pilot')
        if 'observer' not in aim or not aim.get('observer'):
            heli = self.helicopter()   # the pilot gave it: corrections are seen from the pilot's aircraft
            aim['observer'] = (heli['lat'], heli['lon']) if heli else None
        if offset_m:
            word = (toward or '').strip().lower()
            if word in OBSERVER_SHIFTS:
                observer = aim.get('observer')
                if not observer:
                    return 'the observer\'s position is unknown: give the correction as a compass direction'
                bearing = geo.bearing_deg(observer[0], observer[1], aim['lat'], aim['lon']) + OBSERVER_SHIFTS[word]
                aim['lat'], aim['lon'] = geo.offset_point(aim['lat'], aim['lon'], bearing % 360, float(offset_m))
            else:
                shifted = geo.shift((aim['lat'], aim['lon']), offset_m, toward,
                                    (self.state.flight or {}).get('heading_deg'))
                if isinstance(shifted, str):
                    return shifted + ', or add / drop / left / right as the observer sees it'
                aim['lat'], aim['lon'] = shifted
            how += f', {offset_m:.0f} m {toward}'
        return aim, how

    # --- ammunition ------------------------------------------------------------------------------------------------

    def ammo(self, status: dict | None = None) -> dict | None:
        ammo = self.ground(status if status is not None else self.status()).get('ammo')
        return {k: v for k, v in ammo.items() if isinstance(v, (int, float))} if isinstance(ammo, dict) else None

    def rounds_left(self, status: dict | None = None) -> int | None:
        ammo = self.ammo(status)
        return int(sum(ammo.values())) if ammo is not None else None

    # --- orders (tools) --------------------------------------------------------------------------------------------

    def fire_mission(self, target: str, rounds: int = 0, spread_m: float = DEFAULT_SPREAD_M, offset_m: float = 0,
                     toward: str = '', queue: bool = False) -> dict:
        found = self.aim_point(target, offset_m, toward)
        if isinstance(found, str):
            return {'ok': False, 'detail': found}
        aim, how = found
        rounds = int(rounds or 0)
        if not 0 <= rounds <= ROUNDS_MAX:
            return {'ok': False, 'detail': f'rounds: 0 (as many as needed) to {ROUNDS_MAX}'}
        spread = min(max(float(spread_m if spread_m is not None else DEFAULT_SPREAD_M), SPREAD_M[0]), SPREAD_M[1])
        if queue and self.mission:
            return self.enqueue(aim, how, rounds, spread)
        return self.start(aim, how, rounds, spread)

    def enqueue(self, aim: dict, how: str, rounds: int, spread: float) -> dict:
        """A fire mission for after the current one (user, 2026-11-07: Tango sent five targets at once; four were
        never fired). The battery works through the queue by itself."""
        if len(self.queue) >= QUEUE_MAX:
            return {'ok': False, 'detail': f'the queue is full ({QUEUE_MAX} missions)'}
        self.queue.append({'aim': aim, 'how': how, 'rounds': rounds, 'spread_m': spread})
        return {'ok': True, 'detail': f'queued as number {len(self.queue)}: fired by itself after the current '
                                      f'mission ({how}, grid {coords.to_mgrs(aim["lat"], aim["lon"])})'}

    def queued_position(self, target_id: str) -> int | None:
        return next((n for n, q in enumerate(self.queue, 1) if q['aim'].get('id') == target_id), None)

    def start(self, aim: dict, how: str, rounds: int, spread: float) -> dict:
        status = self.status() or {}
        left = self.rounds_left(status)
        if left == 0:
            return {'ok': False, 'detail': 'no rounds left'}
        laying = self.mission if self.mission and self.mission['first_shot_at'] is None else None
        if laying and geo.distance_nm(laying['lat'], laying['lon'], aim['lat'], aim['lon']) * 1852 <= SAME_POINT_M \
                and laying['rounds'] == rounds and abs(laying['spread_m'] - spread) < 1:
            waited = round(time.monotonic() - laying['ordered_at'])
            return {'ok': True, 'detail': f'already laying on that point since {waited} s ago: DCS\'s battery takes a '
                                          'minute or two to aim; nothing sent again (it would restart the laying)'}
        result = self.task('fire', self.group, float(aim['lat']), float(aim['lon']), spread, rounds)
        if not result['ok']:
            return result
        self.last_aim = {k: aim.get(k) for k in ('lat', 'lon', 'observer', 'by', 'by_id', 'what', 'id')}
        self.mission = {**self.last_aim, 'rounds': rounds, 'spread_m': spread, 'ordered_at': time.monotonic(),
                        'start_ammo': left, 'last_ammo': left, 'first_shot_at': None, 'last_shot_at': None,
                        'start_weapons': self.ammo(status)}
        restarted = ' (the laying on the earlier point was dropped and starts again)' if laying else ''
        detail = f'{result["detail"]}{restarted}; aiming at {how}, grid {coords.to_mgrs(aim["lat"], aim["lon"])}'
        if 'lat' in status:
            km = geo.distance_nm(status['lat'], status['lon'], aim['lat'], aim['lon']) * 1.852
            detail += f', {km:.1f} km {geo.cardinal(geo.bearing_deg(status["lat"], status["lon"], aim["lat"], aim["lon"]))}'
        return {'ok': True, 'detail': detail}

    def cease_fire(self) -> dict:
        """Stops everything: the mission going on and the queue."""
        result = self.task('cease_fire', self.group)
        if result['ok']:
            dropped, self.queue, self.mission = len(self.queue), [], None
            if dropped:
                result['detail'] += f'; {dropped} queued fire missions dropped'
        return result

    def next_mission(self) -> str:
        """The next queued mission, started when the last one ended; words for the report ('' = none)."""
        while self.queue:
            item = self.queue.pop(0)
            result = self.start(item['aim'], item['how'], item['rounds'], item['spread_m'])
            if result['ok']:
                more = f' ({len(self.queue)} more queued after it)' if self.queue else ''
                return f' Your battery now lays on the next queued target by itself: {item["how"]}{more}.'
            self.pending.append({'report': f'the queued fire mission on {item["how"]} could not be given: '
                                           f'{result["detail"]}'})
        return ''

    # --- what it tells -----------------------------------------------------------------------------------------------

    def tell(self, mission: dict, text: str) -> None:
        """A fire mission report to whoever asked: the observer agent on the net (as a notice), else the pilot."""
        if mission.get('by_id') and self.net:
            result = self.net.send(self.agent_id, mission['by_id'], text, notice=True)
            if result.get('ok'):
                return
        self.pending.append({'report': text})

    def watch_fire(self, status: dict | None) -> None:
        mission = self.mission
        if not mission or not status:
            return
        left, now = self.rounds_left(status), time.monotonic()
        if left is None:
            return
        if mission['last_ammo'] is None:   # the count was not known yet when the order went out
            mission['start_ammo'] = mission['last_ammo'] = left
        if left < mission['last_ammo']:
            mission['last_shot_at'] = now
            if mission['first_shot_at'] is None:
                mission['first_shot_at'] = now
                # As a fire direction centre calls it to the observer: "shot, over", then it waits for the
                # observer's correction or "end of mission" (the user asked for it, 2026-11-07).
                self.tell(mission, f'Shot: your battery just fired on {mission.get("what") or "the target"}. Call '
                                   '"shot" and say you wait for corrections or end of mission.')
        mission['last_ammo'] = left
        fired = mission['start_ammo'] - left
        if mission['first_shot_at'] is None and now - mission['ordered_at'] >= FIRE_WAIT_S:
            self.mission = None
            following = self.next_mission() if left else ''
            self.tell(mission, f'Your battery has not fired in {round(FIRE_WAIT_S / 60)} minutes since the order: no '
                               'round left the tubes (the target may be out of range, or something stops the crews). '
                               f'Say so; never say rounds are on the way.{following}')
        elif mission['first_shot_at'] is not None and now - mission['last_shot_at'] >= SALVO_QUIET_S:
            self.mission = None
            following = self.next_mission() if left else ''
            if not left and self.queue:
                following = f' No rounds left: {len(self.queue)} queued fire missions cannot be fired.'
                self.queue = []
            self.tell(mission, f'Rounds complete on {mission.get("id") or mission.get("what") or "the target"}: '
                               f'{self.fired_words(mission, status, fired)}. Your crews cannot see where they fell: ask for '
                               f'corrections or end of mission.{following}')

    def fired_words(self, mission: dict, status: dict, fired: int) -> str:
        """What was fired and what is left, by weapon (live 2026-11-07: Trident's total of every round aboard,
        "33633 left", made the model miscount its Tomahawks); the totals when the weapons are not known."""
        before, now = mission.get('start_weapons') or {}, self.ammo(status) or {}
        used = {w: before[w] - now.get(w, 0) for w in before if before[w] - now.get(w, 0) > 0}
        if not used:
            return f'{fired} fired, {self.rounds_left(status)} left'
        return '; '.join(f'{n} {w} fired, {now.get(w, 0)} {w} left' for w, n in used.items())

    def unit_view(self, status: dict) -> dict:
        view = super().unit_view(status)
        view.pop('troop_compartment', None)
        ammo = self.ammo(status)
        view['rounds_left'] = ammo if ammo is not None else 'not known yet'
        if self.mission:
            fired = self.mission['start_ammo'] - self.mission['last_ammo'] \
                if self.mission['start_ammo'] is not None else 0
            view['fire_mission'] = {'target': self.mission.get('id') or self.mission.get('what') or 'the given point',
                                    'asked_by': self.mission.get('by'), 'rounds_fired': fired,
                                    'state': 'firing' if self.mission['first_shot_at'] else 'laying the guns'}
        if self.queue:
            view['fire_queue'] = [{'number': n, 'target': q['how'], 'rounds': q['rounds'] or 'as needed',
                                   'spread_m': q['spread_m']} for n, q in enumerate(self.queue, 1)]
        return view

    def situation(self) -> dict:
        situation = super().situation()
        status, now = self.status() or {}, time.monotonic()
        targets = []
        for target in sorted(self.targets.values(), key=lambda t: t['at'], reverse=True):
            entry = {'id': target['id'], 'what': target['what'], 'sent_by': target['by'],
                     'grid': coords.to_mgrs(target['lat'], target['lon']), 'min_ago': round((now - target['at']) / 60)}
            if 'lat' in status:
                entry['distance_km'] = round(geo.distance_nm(status['lat'], status['lon'], target['lat'],
                                                             target['lon']) * 1.852, 1)
            targets.append(entry)
        if targets:
            situation['targets_from_observers'] = targets
        if self.last_aim:
            situation['last_aim_point'] = coords.to_mgrs(self.last_aim['lat'], self.last_aim['lon'])
        return situation

    def tick(self, busy: bool = False) -> str | None:
        self.watch_fire(self.status())
        return super().tick(busy)
