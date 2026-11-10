"""A ships agent (role "ships", user 2026-11-07: "mobile artillery that sails to the coordinates we give it, fires its
weapons at a point, at a speed we control"; an aircraft carrier, the Supercarrier too, is a ship group like any other).

Built on the artillery desk (artillery/desk.py): the same fire missions, queue, calls for fire from observers on the
net, "shot" / "rounds complete" from its ammunition count, corrections. Its own: it sails (Lua sail: straight to a
point, refused when land is in the way), at a speed in knots, stops, attacks an enemy group it detects (AttackGroup).
Navigation aids, as the Mission Editor's naval commands (me_action_db.lua), given at any time even if the mission
gave none: TACAN on any ship (DCS only lights one where the ship has one), and on a carrier ICLS, Link 4, ACLS (DCS's
attributes "Aircraft Carriers", "Link4", "ACLS", from the mission: dcs/instructor_tasks.lua ship_details). A carrier
turns into the wind for flying (DCS has no such task; the wind is DCS's own where the ship is, atmosphere.getWind): a
course and speed for the wind over the deck asked, down the angled deck for a recovery. The Supercarrier's own deck
crew, LSO and Marshal are DCS's: this agent only drives the ship.
agent.json "ships": {groups: [DCS group names, the first one is the agent's], name}.
"""
import math
import re
import time

from artillery.desk import ArtilleryDesk
from instructor import geo
from shared import coords

KT_PER_MS = 1.943844
SPEED_KT = (1.0, 32.0)
DEFAULT_SPEED_KT = 15.0
STEER_NM, STEER_NM_MAX = 30.0, 200.0   # a course with no point: how far it goes by default, at most
ANGLED_DECK_DEG = 9.0          # a Nimitz-class angled deck points about 9 degrees to port of the bow
WIND_OVER_DECK_KT = 25.0       # the usual wind over the deck for flying
INTO_WIND_MIN = (10.0, 180.0)  # how long it holds the course into the wind
ICLS_CHANNELS = (1, 20)
TACAN_CHANNELS = (1, 126)
LINK4_MHZ = (331.0, 399.0)     # the Mission Editor's range for Link 4
# A helicopter on a small ship's deck: steady and slow, the wind on the port bow (clear of the superstructure's eddies).
HELO_WIND_OFF_BOW_DEG = 30.0
HELO_DECK_SPEED_KT = 10.0
HELO_DECK_SPEED_KT_RANGE = (3.0, 20.0)


def tacan_mhz(channel: int, mode: str) -> float:
    """A ship's TACAN frequency as the Mission Editor works it out (me_action_db.lua calcTACANFrequencyMHz, not
    air-to-air)."""
    if mode == 'X':
        return 962 + channel - 1 if channel < 64 else 1151 + channel - 64
    return 1088 + channel - 1 if channel < 64 else 1025 + channel - 64


class ShipDesk(ArtilleryDesk):
    CONFIG_KEY = 'ships'
    UNIT_KEY = 'ships'
    UNIT = 'ships'

    def __init__(self, state, agent: dict, tasks=None, bridge=None, cargo=None):
        super().__init__(state, agent, tasks, bridge, cargo)
        self.aids: dict[str, str] = {}      # what it switched on: tacan "71X ROU", icls "11", link4 "336", acls "on"
        self.last_sail: dict | None = None  # {lat, lon, speed_kt}: set_speed goes on with it
        self.into_wind: dict | None = None  # {course, speed_kt, until}
        self.aids_off: set[str] = set()     # aids it switched off (one the mission set too)
        self.deck: dict | None = None       # green deck for a helicopter: {course, speed_kt, until}

    # --- what it knows -----------------------------------------------------------------------------------------------

    @staticmethod
    def ship(status: dict | None) -> dict:
        ship = (status or {}).get('ship')
        return ship if isinstance(ship, dict) else {}

    def is_carrier(self, status: dict | None = None) -> bool:
        return bool(self.ship(status if status is not None else self.status()).get('carrier'))

    def wind(self, status: dict | None = None) -> dict | None:
        """Where the wind comes from (true degrees) and how strong (knots), where the ship is."""
        ship = self.ship(status if status is not None else self.status())
        if not isinstance(ship.get('wind_to_deg'), (int, float)) or not isinstance(ship.get('wind_ms'), (int, float)):
            return None
        return {'from_deg': round((ship['wind_to_deg'] + 180) % 360), 'kt': round(ship['wind_ms'] * KT_PER_MS, 1)}

    def facts(self) -> dict | None:
        facts = super().facts()
        if facts is not None:
            facts['members'] = 'ships'
        return facts

    def unit_view(self, status: dict) -> dict:
        view = super().unit_view(status)
        view['ships'] = view.pop('vehicles', None)
        view['speed_kt'] = round((status.get('speed_ms') or 0) * KT_PER_MS, 1)
        ship = self.ship(status)
        if isinstance(ship.get('heading_deg'), (int, float)):
            view['heading_deg'] = round(ship['heading_deg'])
        if self.is_carrier(status):
            view['aircraft_carrier'] = {'link4': ship.get('link4'), 'acls': ship.get('acls')}
            wind = self.wind(status)
            if wind:
                view['wind'] = f'from {wind["from_deg"]} at {wind["kt"]} knots'
                if isinstance(ship.get('heading_deg'), (int, float)):
                    view['wind_over_deck_kt'] = round(self.wind_over_deck(status), 1)
            if self.into_wind and time.monotonic() < self.into_wind['until']:
                view['into_the_wind'] = {'course': self.into_wind['course'], 'speed_kt': self.into_wind['speed_kt'],
                                         'minutes_left': round((self.into_wind['until'] - time.monotonic()) / 60)}
        if self.deck and time.monotonic() < self.deck['until']:
            view['flight_deck'] = {'state': 'green: a helicopter may land; do not manoeuvre',
                                   'course': self.deck['course'], 'speed_kt': self.deck['speed_kt'],
                                   'minutes_left': round((self.deck['until'] - time.monotonic()) / 60)}
        radar = self.radar_view(status)
        if radar:
            view['radar'] = radar
        aids = self.aids_on(status)
        if aids:
            view['navigation_aids_on'] = aids
        return view

    def radar_view(self, status: dict) -> dict | None:
        """Its radar picture (Lua DCSInstructorRadar.own_contacts, the airfield radars' model): every contact, friend
        or foe as IFF tells it, with bearing and range from the ship, altitude, heading and speed."""
        radar = self.ship(status).get('radar')
        if not isinstance(radar, dict) or 'lat' not in status:
            return None
        contacts = []
        for c in radar.get('contacts') or []:
            if not isinstance(c, dict) or not all(isinstance(c.get(k), (int, float)) for k in ('lat', 'lon')):
                continue
            vx, vz = c.get('vx') or 0.0, c.get('vz') or 0.0
            entry = {'side': c.get('side', 'unknown'), 'kind': c.get('kind', 'air'), 'type': c.get('type'),
                     'bearing_deg': round(geo.bearing_deg(status['lat'], status['lon'], c['lat'], c['lon'])),
                     'range_nm': round(geo.distance_nm(status['lat'], status['lon'], c['lat'], c['lon']), 1),
                     'heading_deg': round(math.degrees(math.atan2(vz, vx)) % 360),
                     'speed_kt': round(math.hypot(vx, vz) * KT_PER_MS)}
            if entry['kind'] == 'air':
                entry['altitude_ft'] = round((c.get('alt_m') or 0) * 3.28084, -2)
            if c.get('player'):
                entry['who'] = 'the pilot you talk to'
            elif c.get('group'):
                entry['group'] = c['group']
            contacts.append(entry)
        return {'range_km': radar.get('range_km'), 'contacts': contacts or 'none'}

    def aids_on(self, status: dict | None) -> dict:
        """The aids on now: what the mission maker set in the ship's route (Lua mission_aids), unless this agent switched
        it off or set it again, and what this agent switched on."""
        found = {}
        mission = self.ship(status).get('mission_aids')
        for aid, entry in (mission.items() if isinstance(mission, dict) else []):
            if aid in self.aids or aid in self.aids_off or not isinstance(entry, dict):
                continue
            if aid == 'tacan' and entry.get('channel'):
                found[aid] = f'{entry["channel"]}{entry.get("mode") or "X"} {entry.get("callsign") or ""}'.strip()
            elif aid == 'icls' and entry.get('channel'):
                found[aid] = str(entry['channel'])
            elif aid == 'link4' and entry.get('mhz'):
                found[aid] = f'{float(entry["mhz"]):.0f} MHz'
            elif aid == 'acls':
                found[aid] = 'on'
            else:
                continue
            found[aid] += ' (set in the mission)'
        return {**found, **self.aids}

    def wind_over_deck(self, status: dict) -> float:
        """The wind along the deck: the true wind plus the ship's own speed, ahead of the bow."""
        ship, wind = self.ship(status), self.wind(status)
        own = (status.get('speed_ms') or 0) * KT_PER_MS
        if not wind:
            return own
        angle = math.radians(wind['from_deg'] - ship['heading_deg'])
        return own + wind['kt'] * math.cos(angle)

    # --- orders (tools) ------------------------------------------------------------------------------------------

    def where(self, destination: str, offset_m: float = 0, toward: str = '') -> tuple[float, float] | str:
        """Coordinates, or a place (a zone, an F10 map mark, "here", a unit in sight), moved by an offset."""
        found = coords.parse(destination)
        if found:
            return geo.shift(found, offset_m, toward, (self.state.flight or {}).get('heading_deg') if offset_m else None)
        return self.place_off(destination, offset_m, toward)

    def move_to(self, destination: str, speed_kt: float = DEFAULT_SPEED_KT, offset_m: float = 0,
                toward: str = '') -> dict:
        busy = self.deck_busy()
        if busy:
            return busy
        where = self.where(destination, offset_m, toward)
        if isinstance(where, str):
            return {'ok': False, 'detail': where}
        self.into_wind = None
        return self.sail(float(where[0]), float(where[1]), speed_kt)

    def sail(self, lat: float, lon: float, speed_kt: float) -> dict:
        speed = min(max(float(speed_kt or DEFAULT_SPEED_KT), SPEED_KT[0]), SPEED_KT[1])
        result = self.task('sail', self.group, lat, lon, speed / KT_PER_MS)
        if result['ok']:
            self.last_sail = {'lat': lat, 'lon': lon, 'speed_kt': speed}
            status = self.status() or {}
            if 'lat' in status:
                nm = geo.distance_nm(status['lat'], status['lon'], lat, lon)
                course = round(geo.bearing_deg(status['lat'], status['lon'], lat, lon))
                result['detail'] += f'; course {course:03d}, {nm:.1f} nautical miles'
        return result

    def steer(self, course_deg: float, speed_kt: float = DEFAULT_SPEED_KT, nautical_miles: float = 0) -> dict:
        """A course with no point (user, 2026-11-07: "Trident, course 027 at 15 knots"): DCS's route goes along it up to
        nautical_miles (0: STEER_NM) or 1 km short of land ahead (Lua steer)."""
        busy = self.deck_busy()
        if busy:
            return busy
        course = float(course_deg) % 360
        speed = min(max(float(speed_kt or DEFAULT_SPEED_KT), SPEED_KT[0]), SPEED_KT[1])
        nm = min(max(float(nautical_miles or STEER_NM), 1.0), STEER_NM_MAX)
        result = self.task('steer', self.group, course, speed / KT_PER_MS, nm * 1852)
        if not result['ok']:
            return result
        self.into_wind = None
        status = self.status() or {}
        km = re.search(r'for ([\d.]+) km', result.get('detail') or '')
        if 'lat' in status and km:   # where the route ends: set_speed goes on with it
            lat, lon = geo.offset_point(status['lat'], status['lon'], course, float(km.group(1)) * 1000)
            self.last_sail = {'lat': lat, 'lon': lon, 'speed_kt': speed}
        result['detail'] = f'course {round(course) % 360:03d}, {result["detail"]}'
        return result

    def set_speed(self, speed_kt: float = 0, speed: str = '', speed_kmh: float = 0) -> dict:
        busy = self.deck_busy()
        if busy:
            return busy
        if not self.last_sail:
            return {'ok': False, 'detail': 'you are not sailing anywhere: give a destination with move_to'}
        if not speed_kt:
            return {'ok': False, 'detail': 'speed_kt: the new speed in knots'}
        last = self.last_sail
        return self.sail(last['lat'], last['lon'], speed_kt)

    def hold(self) -> dict:
        self.into_wind, self.last_sail = None, None
        return self.task('ship_hold', self.group)

    def attack(self, enemy_group: str) -> dict:
        """An enemy group it detects or detected lately, by DCS's AttackGroup."""
        names = self.enemy_names()
        wanted = geo.simplify(enemy_group)
        target = next((n for n in names if geo.simplify(n) == wanted), None)
        if target is None:
            return {'ok': False, 'detail': f'no enemy group called {enemy_group} detected '
                                           f'(detected: {", ".join(names) or "none"})'}
        return self.task('ship_attack', self.group, target)

    def turn_into_wind(self, wind_over_deck_kt: float = WIND_OVER_DECK_KT, minutes: float = 30,
                       for_recovery: bool = True) -> dict:
        """A carrier steams into the wind for flying: the course the wind comes from (plus the angled deck for a
        recovery) at the speed that makes the wind over the deck asked."""
        busy = self.deck_busy()
        if busy:
            return busy
        status = self.status() or {}
        if not self.is_carrier(status):
            return {'ok': False, 'detail': 'only an aircraft carrier turns into the wind for flying'}
        wind = self.wind(status)
        if not wind or 'lat' not in status:
            return {'ok': False, 'detail': 'the wind where you are is not known yet'}
        course = (wind['from_deg'] + (ANGLED_DECK_DEG if for_recovery else 0)) % 360
        speed = min(max(float(wind_over_deck_kt) - wind['kt'], SPEED_KT[0]), SPEED_KT[1])
        minutes = min(max(float(minutes or 30), INTO_WIND_MIN[0]), INTO_WIND_MIN[1])
        lat, lon = geo.offset_point(status['lat'], status['lon'], course, speed / KT_PER_MS * minutes * 60)
        result = self.sail(lat, lon, speed)
        if not result['ok']:
            return result
        self.into_wind = {'course': round(course), 'speed_kt': round(speed), 'until': time.monotonic() + minutes * 60}
        over_deck = speed + wind['kt'] * (0.988 if for_recovery else 1.0)   # cos 9 degrees down the angled deck
        note = ' (the wind alone is stronger than asked: slowest speed)' if wind['kt'] + SPEED_KT[0] > \
            wind_over_deck_kt else ''
        return {'ok': True, 'detail': f'into the wind: course {round(course):03d}, {round(speed)} knots, wind from '
                                      f'{wind["from_deg"]} at {wind["kt"]} knots, about {round(over_deck)} knots over '
                                      f'the {"angled deck" if for_recovery else "bow"}{note}, for {round(minutes)} '
                                      'minutes, clear of land along that course'}

    def flight_deck(self, open: bool = True, speed_kt: float = HELO_DECK_SPEED_KT, minutes: float = 30) -> dict:
        """A helicopter lands on the ship's deck (user, 2026-11-07: "ask the Ticonderoga to land"; DCS's own deck net
        rises by itself): open = "green deck", the ship steadies on a course with the real wind HELO_WIND_OFF_BOW_DEG
        on the port bow, slow, and does not manoeuvre until the deck is closed again (move_to, set_speed, hold and
        turn_into_wind wait). Closed = "red deck": free to manoeuvre."""
        if not open:
            if not self.deck:
                return {'ok': True, 'detail': 'the flight deck was not open'}
            self.deck = None
            return {'ok': True, 'detail': 'flight deck closed (red deck): free to manoeuvre'}
        status = self.status() or {}
        wind = self.wind(status)
        if 'lat' not in status:
            return {'ok': False, 'detail': 'your position is not known yet'}
        speed = min(max(float(speed_kt or HELO_DECK_SPEED_KT), HELO_DECK_SPEED_KT_RANGE[0]),
                    HELO_DECK_SPEED_KT_RANGE[1])
        minutes = min(max(float(minutes or 30), INTO_WIND_MIN[0]), INTO_WIND_MIN[1])
        ship = self.ship(status)
        if wind and wind['kt'] >= 3:
            course = (wind['from_deg'] + HELO_WIND_OFF_BOW_DEG) % 360   # the wind 30 degrees on the port bow
        else:
            course = ship.get('heading_deg') or 0.0                   # calm: hold the heading it has
        lat, lon = geo.offset_point(status['lat'], status['lon'], course, speed / KT_PER_MS * minutes * 60)
        self.deck = None   # the move below is the deck's own
        result = self.sail(lat, lon, speed)
        if not result['ok']:
            return result
        self.into_wind = None
        self.deck = {'course': round(course), 'speed_kt': round(speed), 'until': time.monotonic() + minutes * 60}
        relative = f'wind from {wind["from_deg"]} at {wind["kt"]} knots, {HELO_WIND_OFF_BOW_DEG:.0f} degrees on the ' \
                   'port bow' if wind and wind['kt'] >= 3 else 'calm wind'
        return {'ok': True, 'detail': f'green deck: steady on course {round(course):03d} at {round(speed)} knots, '
                                      f'{relative}; you do not manoeuvre until the deck is closed (for '
                                      f'{round(minutes)} minutes at most)'}

    def deck_busy(self) -> dict | None:
        """While the deck is green no order moves the ship (a helicopter may be landing)."""
        if self.deck and time.monotonic() < self.deck['until']:
            return {'ok': False, 'detail': 'the flight deck is green (a helicopter may be landing or on deck): close '
                                           'it first with flight_deck open false, once the pilot says it has landed '
                                           'and is secure, or has left'}
        return None

    def navigation_aid(self, aid: str, on: bool = True, channel: int = 0, mode: str = 'X', callsign: str = '',
                       mhz: float = 0) -> dict:
        """TACAN (any ship), ICLS / Link 4 / ACLS (a carrier), on or off, as the Mission Editor's naval commands."""
        aid = str(aid or '').lower().replace(' ', '')
        status = self.status() or {}
        ship = self.ship(status)
        if aid not in ('tacan', 'icls', 'link4', 'acls'):
            return {'ok': False, 'detail': 'aid: tacan, icls, link4 or acls'}
        if aid != 'tacan' and not ship.get('carrier'):
            return {'ok': False, 'detail': f'{aid.upper()} is only aboard an aircraft carrier'}
        if aid in ('link4', 'acls') and on and not ship.get(aid):
            return {'ok': False, 'detail': f'this carrier has no {aid.upper()} in DCS'}
        if not on:
            result = self.task('ship_aid', self.group, aid, 'off', 0, 'X', '-', 0)
            if result['ok']:
                self.aids.pop(aid, None)
                self.aids_off.add(aid)
            return result
        mode = str(mode or 'X').upper()
        if aid == 'tacan':
            channel, callsign = int(channel or 0), ''.join(c for c in str(callsign or '').upper() if c.isalnum())[:4]
            if not TACAN_CHANNELS[0] <= channel <= TACAN_CHANNELS[1] or mode not in ('X', 'Y'):
                return {'ok': False, 'detail': 'TACAN: channel 1 to 126, X or Y (e.g. 71X)'}
            callsign = callsign or self.name[:3].upper()
            result = self.task('ship_aid', self.group, 'tacan', 'on', channel, mode, callsign,
                               tacan_mhz(channel, mode))
            shown = f'{channel}{mode} {callsign}'
        elif aid == 'icls':
            channel = int(channel or 0)
            if not ICLS_CHANNELS[0] <= channel <= ICLS_CHANNELS[1]:
                return {'ok': False, 'detail': 'ICLS: channel 1 to 20'}
            result = self.task('ship_aid', self.group, 'icls', 'on', channel, 'X', '-', 0)
            shown = str(channel)
        elif aid == 'link4':
            mhz = float(mhz or 336.0)
            if not LINK4_MHZ[0] <= mhz <= LINK4_MHZ[1]:
                return {'ok': False, 'detail': 'Link 4: 331 to 399 MHz'}
            result = self.task('ship_aid', self.group, 'link4', 'on', 0, 'X', '-', mhz)
            shown = f'{mhz:.0f} MHz'
        else:
            result = self.task('ship_aid', self.group, 'acls', 'on', 0, 'X', '-', 0)
            shown = 'on'
        if result['ok']:
            self.aids[aid] = shown
            self.aids_off.discard(aid)
            result['detail'] = f'{aid.upper()} {shown}'
        return result

    def tick(self, busy: bool = False) -> str | None:
        if self.deck and time.monotonic() >= self.deck['until']:
            self.deck = None
            self.pending.append({'report': 'your flight deck time is over: the deck is red again and you hold that '
                                           'course to its end point; ask the pilot whether the helicopter is clear'})
        if self.into_wind and time.monotonic() >= self.into_wind['until']:
            self.into_wind = None
            self.pending.append({'report': 'your time steaming into the wind is over: you hold that course to its end '
                                           'point and stop there; ask the pilot what next'})
        return super().tick(busy)
