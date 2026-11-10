"""A ground vehicles agent (role "vehicles"): an AI group of vehicles the pilot talks to on its own radio.

User, 2026-11-05: "armour is the same as a squad, but its own role". Built on the squad's desk (squad/desk.py): the
same realism (its own group, the zones, the pilot's aircraft only in sight, enemies from its crews' own detection,
radio range with a vehicle radio), the same hold, rules of engagement, smoke, attack and "tell me when...". It drives
on or off the road in a formation instead of walking, and never boards anything.

Its hold (user's idea: "detect the hold, then give it Cobra's pickup"): a vehicle with a troop compartment (DCS's
InternalCargo, read from its unit files by shared/cargo.py, or what the mission says live: dcs/instructor_tasks.lua
ground_details) picks up and drops another agent's squad as Cobra does by air: DCS's Embarking / Disembarking at the
end of a route (Lua carry_pickup / carry_dropoff), the squad waiting with EmbarkToTransport; shared through
state.transport like the helicopter (ride = vehicle), so the squad says "Bison's vehicle".
agent.json "vehicles": {groups: [DCS group names, the first one is the unit], name: how it calls itself}.
"""
import time

from instructor import geo
from squad.desk import PICKUP_RADIUS_M, SquadDesk

ROADS = ('off', 'on')
SPEEDS = ('slow', 'normal', 'fast')   # Lua VEHICLE_SPEEDS: 18, 36, 61 km/h
SPEED_KMH_MIN, SPEED_KMH_MAX = 1.0, 100.0
FORMATIONS = ('keep', 'column', 'row', 'wedge', 'vee', 'diamond', 'echelon_right', 'echelon_left')   # Lua drive
# Carrying a squad: who is aboard is DCS's count (getDescentOnBoard) when it answers. Without it, the vehicles
# stopped within CARRY_NEAR_M of the pickup point, then drove off: the squad got in if it was there. Getting out is
# only told as done when DCS counts the compartment empty (live 2026-11-05: "Tango got out" was said from the vehicles
# stopping and driving off, and Tango was still aboard); without the count it is told as not confirmed.
CARRY_NEAR_M = 200.0
CARRY_STOPPED_MS = 0.5
CARRY_MOVING_MS = 1.5
DISEMBARK_WAIT_S = 60.0   # stopped at the drop point this long with the troops still counted aboard: say so


class VehicleDesk(SquadDesk):
    CONFIG_KEY = 'vehicles'
    KIND = 'vehicles'
    UNIT_KEY = 'vehicles'
    UNIT = 'vehicles'   # shared/triggers.py: "Bison's vehicles lost 1"

    def __init__(self, state, agent: dict, tasks=None, bridge=None, cargo=None):
        self.cargo = cargo   # shared/cargo.CargoCapacities: which vehicle types carry troops, from DCS's files
        super().__init__(state, agent, tasks, bridge)

    def configure(self, agent: dict) -> None:
        """Also listed in state.carriers by its group, where a squad finds it to ask for a ride (asked_by)."""
        carriers = getattr(self.state, 'carriers', None)
        if carriers is not None:
            for group in [g for g, desk in carriers.items() if desk is self]:
                del carriers[group]
        super().configure(agent)
        if carriers is not None and self.group:
            carriers[self.group] = self

    # --- what it knows -------------------------------------------------------------------------------------------

    @staticmethod
    def ground(status: dict | None) -> dict:
        """What the mission says the group is made of: types, infantry, vehicles, maybe descent_capacity / _on_board."""
        ground = (status or {}).get('ground')
        return ground if isinstance(ground, dict) else {}

    def aboard(self, status: dict | None = None) -> bool:
        return False   # vehicles board nothing

    def facts(self) -> dict | None:
        facts = super().facts()
        if facts is not None:
            facts['members'] = 'vehicles'   # shared/triggers.py: "lost 1 vehicles", not soldiers
        return facts

    def capacity(self, status: dict | None = None) -> int | None:
        """The size its vehicles hold for troops (DCS's units; 0: no troop compartment, None: not known yet). What
        the mission says live wins; else DCS's unit files (shared/cargo.py)."""
        ground = self.ground(status if status is not None else self.status())
        if isinstance(ground.get('descent_capacity'), (int, float)) and ground['descent_capacity'] > 0:
            return int(ground['descent_capacity'])
        if not self.cargo or not isinstance(ground.get('types'), dict):
            return 0
        sizes = [(self.cargo.capacity(t), n) for t, n in ground['types'].items()]
        if any(size is None for size, _ in sizes):
            return None
        return sum(size * (n if isinstance(n, int) else 1) for size, n in sizes)

    def can_carry(self) -> bool:
        return bool(self.capacity())

    def carried(self, stage: str | None = None) -> list[str]:
        """The squads it is picking up, carrying or dropping (state.transport)."""
        return [s for s, t in (getattr(self.state, 'transport', None) or {}).items()
                if t.get('carrier') == self.group and (stage is None or t.get('stage') == stage)]

    def unit_view(self, status: dict) -> dict:
        """How many are left, of which types, moving, its troop compartment and the troops it carries."""
        ground = self.ground(status)
        view = {'vehicles': f'{status.get("alive")} of {status.get("size")}',
                'moving': (status.get('speed_ms') or 0) >= CARRY_STOPPED_MS}
        if isinstance(ground.get('types'), dict):
            view['types'] = ground['types']
        lost = self.losses(status)
        if lost:
            view['lost_since_your_last_message'] = lost
        capacity = self.capacity(status)
        view['troop_compartment'] = 'not known yet' if capacity is None else 'yes' if capacity else 'none'
        stages = {'pickup': 'going to pick them up (they wait for you)', 'aboard': 'aboard',
                  'drop': 'aboard, going to drop them'}
        troops = {}
        for squad in self.carried():
            entry = self.state.transport[squad]
            troops[self.called(squad)] = stages.get(entry.get('stage'), '?')
            if entry.get('stage') == 'pickup' and entry.get('stopped') and self.squad_at_point(squad):
                troops[self.called(squad)] = 'with you at the pickup point: boarding or aboard (drop_off takes them)'
        if troops:
            view['troops'] = troops
        return view

    # --- orders (tools) ------------------------------------------------------------------------------------------

    def move_to(self, destination: str, speed: str = 'normal', road: str = 'off', formation: str = 'keep',
                offset_m: float = 0, toward: str = '', speed_kmh: float = 0) -> dict:
        """Drive on or off the road, in a formation (Lua drive). speed_kmh > 0 wins over slow/normal/fast."""
        where = self.place_off(destination, offset_m, toward)
        if isinstance(where, str):
            return {'ok': False, 'detail': where}
        if road not in ROADS or formation not in FORMATIONS:
            return {'ok': False, 'detail': f'road: {"/".join(ROADS)}; formation: {", ".join(FORMATIONS)}'}
        return self.drive(float(where[0]), float(where[1]), speed, road, formation, speed_kmh)

    def drive(self, lat: float, lon: float, speed: str, road: str, formation: str, speed_kmh: float = 0) -> dict:
        """Sends the drive and remembers it, so set_speed can change the speed on the way."""
        kmh = max(SPEED_KMH_MIN, min(float(speed_kmh), SPEED_KMH_MAX)) if speed_kmh and speed_kmh > 0 else 0
        result = self.task('drive', self.group, lat, lon, kmh or speed, road, formation)
        if result['ok']:
            self.last_drive = {'lat': lat, 'lon': lon, 'road': road}
            if kmh:
                result['detail'] += f' at {kmh:.0f} km/h'
        return result

    def set_speed(self, speed: str = '', speed_kmh: float = 0) -> dict:
        """Change the speed on the current move: the same destination and road, sent again with the new speed."""
        status = self.status() or {}
        if not getattr(self, 'last_drive', None) or (status.get('speed_ms') or 0) < CARRY_STOPPED_MS:
            return {'ok': False, 'detail': 'you are not driving anywhere: give a destination with move_to'}
        if not speed_kmh and speed not in SPEEDS:
            return {'ok': False, 'detail': f'speed: {"/".join(SPEEDS)} or speed_kmh'}
        last = self.last_drive
        return self.drive(last['lat'], last['lon'], speed or 'normal', last['road'], 'keep', speed_kmh)

    def squad_group(self, name: str) -> str | None:
        """Another agent's infantry group by its agent's name or its group's name (loosely)."""
        wanted = geo.simplify(name)
        infantry = [n for n, g in (self.state.groups or {}).items()
                    if isinstance(g, dict) and 'air' not in g and n not in self.groups and
                    (self.ground(g).get('infantry') or 0) >= (self.ground(g).get('vehicles') or 0)]
        names = {n: {geo.simplify(n), geo.simplify(self.called(n))} - {''} for n in infantry}
        exact = [n for n in infantry if wanted in names[n]]
        inside = [n for n in infantry if any(s in wanted for s in names[n])]
        found = exact or (inside if len(inside) == 1 else [])
        return found[0] if found else None

    def pick_up(self, squad: str, where: str = '', road: str = 'off', offset_m: float = 0, toward: str = '') -> dict:
        """Drive to a squad and take it aboard, as Cobra does: DCS's Embarking there, and the squad told to wait for
        it there (EmbarkToTransport). where '' = where the squad is now."""
        if not self.can_carry():
            return {'ok': False, 'detail': 'your vehicles have no troop compartment'}
        if road not in ROADS:
            return {'ok': False, 'detail': f'road: {"/".join(ROADS)}'}
        group = self.squad_group(squad)
        if not group:
            names = [self.called(n) for n, g in (self.state.groups or {}).items()
                     if isinstance(g, dict) and 'air' not in g and n not in self.groups]
            return {'ok': False, 'detail': f'no squad called {squad} (squads: {", ".join(names) or "none"})'}
        if self.carried('aboard'):
            return {'ok': False, 'detail': f'you already carry {", ".join(map(self.called, self.carried("aboard")))}:'
                                           ' drop them first'}
        if where:
            place = self.place_off(where, offset_m, toward)
        else:
            troops = (self.state.groups or {}).get(group) or {}
            place = (troops['lat'], troops['lon']) if 'lat' in troops else f'where {self.called(group)} is is unknown'
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        result = self.task('carry_pickup', self.group, float(place[0]), float(place[1]), group, road)
        if result['ok']:
            self.state.transport[group] = {'carrier': self.group, 'ride': 'vehicle', 'stage': 'pickup',
                                           'lat': place[0], 'lon': place[1]}
            try:
                self.tasks.send('wait_pickup', group, float(place[0]), float(place[1]), float(PICKUP_RADIUS_M))
            except OSError:
                pass
            result['detail'] += f'; {self.called(group)} told to wait for you there'
        return result

    def drop_off(self, where: str = 'here', road: str = 'off', offset_m: float = 0, toward: str = '') -> dict:
        """Drive there and let the troops out (DCS's Disembarking). A squad that the vehicles stopped for at its
        pickup point and that was there counts as aboard now (they wait at the end of their route: no driving off to
        tell)."""
        for squad in self.carried('pickup'):
            if self.state.transport[squad].get('stopped') and self.squad_at_point(squad):
                self.state.transport[squad].update(stage='aboard', stopped=False)
        squads = self.carried('aboard')
        if not squads:
            return {'ok': False, 'detail': 'you carry no troops'}
        if road not in ROADS:
            return {'ok': False, 'detail': f'road: {"/".join(ROADS)}'}
        place = self.place_off(where, offset_m, toward)
        if isinstance(place, str):
            return {'ok': False, 'detail': place}
        result = {'ok': False, 'detail': 'nothing sent'}
        for squad in squads:
            result = self.task('carry_dropoff', self.group, float(place[0]), float(place[1]), squad, road)
            if not result['ok']:
                return result
            self.state.transport[squad].update(stage='drop', lat=place[0], lon=place[1], stopped=False)
        return result

    def asked_by(self, squad: str, wants: str) -> dict:
        """A squad asks its ride on the radio (squad/desk.py board_vehicle / disembark): wants 'board' (drive to
        where it is and take it aboard) or 'out' (stop here and let it out). Done on this side, as if the pilot had
        ordered it; the vehicles' own call tells the pilot."""
        result = self.pick_up(squad) if wants == 'board' else self.drop_off('here')
        if result['ok']:
            asked = 'to pick them up' if wants == 'board' else 'to let them out where you are'
            self.pending.append({'report': f'{self.called(squad)} asked you on the radio {asked}: you are doing it '
                                           f'({result["detail"]})'})
        return result

    # --- who is aboard -------------------------------------------------------------------------------------------

    def squad_at_point(self, squad: str) -> bool:
        """The squad is at its pickup point (or gone from the mission: DCS may drop a group that is aboard)."""
        entry = self.state.transport[squad]
        troops = (self.state.groups or {}).get(squad) or {}
        return not troops.get('exists', True) or 'lat' not in troops or \
            geo.distance_nm(troops['lat'], troops['lon'], entry['lat'], entry['lon']) * 1852 <= CARRY_NEAR_M

    def watch_transport(self, status: dict | None) -> None:
        """Who is aboard: what the mission says (getDescentOnBoard, when it answers), else the vehicles stopped within
        CARRY_NEAR_M of the pickup point and then drove off: the squad got in if it was there (or is gone from the
        mission). Out at a drop only when DCS counts them out; without its count, not confirmed."""
        if not status or 'lat' not in status or not self.carried():
            return
        speed = status.get('speed_ms') or 0
        on_board = self.ground(status).get('descent_on_board')
        known = isinstance(on_board, (int, float))
        for squad in self.carried():
            entry = self.state.transport[squad]
            if known and on_board > 0 and entry['stage'] in ('aboard', 'drop'):
                entry['counted'] = True   # live 2026-11-05: DCS counted them 10 s after the vehicles drove off
            if entry['stage'] == 'aboard':
                continue
            near = geo.distance_nm(status['lat'], status['lon'], entry['lat'], entry['lon']) * 1852 <= CARRY_NEAR_M
            if known and entry['stage'] == 'pickup' and on_board > 0 and near:
                entry.update(stage='aboard', stopped=False, counted=True)   # DCS counts them: trusted for the drop
                self.pending.append({'report': f'{self.called(squad)} is aboard your vehicles'})
                continue
            if entry['stage'] == 'drop':
                self.watch_drop(squad, entry, near, speed, on_board if known and entry.get('counted') else None)
                continue
            if speed < CARRY_STOPPED_MS:
                if near and not entry.get('stopped'):
                    self.pending.append({'report': f'you stopped at the pickup point for {self.called(squad)}: they '
                                                   'board by themselves; never say they are aboard before your '
                                                   'situation shows it'})
                entry['stopped'] = entry.get('stopped') or near
                continue
            if speed < CARRY_MOVING_MS or not entry.get('stopped'):
                continue
            if self.squad_at_point(squad):
                entry.update(stage='aboard', stopped=False)
                self.pending.append({'report': f'you drove off with {self.called(squad)} aboard'})
            else:
                del self.state.transport[squad]
                self.pending.append({'report': f'you drove off without {self.called(squad)}: they had not reached '
                                               'the pickup point'})

    def watch_drop(self, squad: str, entry: dict, near: bool, speed: float, on_board: float | None) -> None:
        """Letting a squad out: on_board is DCS's count, None when it does not count for these vehicles. Out when the
        count is 0 at the point; leaving the point (beyond CARRY_NEAR_M) with them still counted aboard, they are
        aboard again (drop_off again); without the count, leaving the point is told as not confirmed."""
        who = self.called(squad)
        if on_board == 0 and near:
            del self.state.transport[squad]
            self.pending.append({'report': f'{who} got out at the drop point: your compartment is empty'})
            return
        if speed < CARRY_STOPPED_MS:
            if near:
                entry['stopped'] = True
                entry.setdefault('stopped_at', time.monotonic())
                if on_board and not entry.get('still_told') and \
                        time.monotonic() - entry['stopped_at'] >= DISEMBARK_WAIT_S:
                    entry['still_told'] = True
                    self.pending.append({'report': f'you are stopped at the drop point and {who} is still aboard: '
                                                   'they have not got out yet; never say they got out'})
            return
        # Gone from the point, not just shuffling there: live 2026-11-05 the BTR moved at 1.7 m/s at the point while
        # the troops got out (counted out 20 s after it stopped).
        if speed < CARRY_MOVING_MS or not entry.get('stopped') or near:
            return
        if on_board:
            entry.update(stage='aboard', stopped=False)
            entry.pop('stopped_at', None)
            entry.pop('still_told', None)
            self.pending.append({'report': f'you drove off from the drop point with {who} still aboard: they did not '
                                           'get out (drop_off again to try)'})
            return
        del self.state.transport[squad]
        self.state.unconfirmed_drops[squad] = self.group
        self.pending.append({'report': f'you drove off from the drop point: you could not tell whether {who} got out; '
                                       'tell the pilot you cannot confirm it'})

    def tick(self, busy: bool = False) -> str | None:
        self.watch_transport(self.status())
        return super().tick(busy)
