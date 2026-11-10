"""What the instructor in the control tower knows about the aircraft, and nothing more (user's rule, 2026-11-01).

- Identity: aircraft type and callsign (the callsign from the mission through dcs/instructor_hook.lua).
- The tower: the airfield nearest to where the aircraft first appeared (its departure field), from the map data.
- Through the window, only within VISUAL_RANGE_NM of the tower: on the ground or airborne, and the configuration seen
  from outside (gear, flaps, speed brake, canopy, drag chute, exterior lights: positions.json "outside_visible");
  on the ground also whether it moves and where on the field it is (instructor/field_position.py: on a runway, at a
  runway's end, elsewhere).
- Radar: position, altitude, ground speed, track and climb come only from the tower's own radars: mission radar
  units named after the airfield ("Senaki-Kolkhi", "Senaki-Kolkhi-2"...), read by dcs/instructor_radar.lua. Only
  while one of them detects the aircraft; behind terrain or out of range the tower loses contact. Other aircraft
  those radars detect are traffic.
The cockpit (instruments, switches inside) is never seen; the black box records it for the debriefing.

The tower's information is a tool the model calls when it wants it (report(), user's rule 2026-11-01: an instructor,
not an ATC); each message carries only the situation(): who, which tower, and the phase of the flight.
"""
import math
import re
import time

from instructor import geo, weather
from instructor.field_position import position_on_field
from instructor.phase import MOVING_KT, ON_GROUND_AGL_M, ROLL_KT, PhaseTracker

RADAR_STALE_S = 5.0        # radar messages come once per second from the GUI hook
WEATHER_STALE_S = 30.0     # weather messages every 10 s
TRAFFIC_RANGE_NM = 30.0
TRAFFIC_MAX = 5
KT_PER_MS, FT_PER_M = 1.943844, 3.280840
VISUAL_RANGE_NM = 5.0
WEAK_RETURN_FRACTION = 0.85   # of the distance at which the radar sees that aircraft (dcs/instructor_radar.lua)
# From the tower one sees the canopy or the gear, not the handle or lever that moves them.
CONTROL_WORDS = re.compile(r'\s+(t-handle|handle|lever|thumb switch|switch)$', re.I)


def outside_name(control_name: str) -> str:
    """'Landing Gear Lever' -> 'landing gear', 'Canopy Handle' -> 'canopy'."""
    return CONTROL_WORDS.sub('', control_name).lower()


class TowerView:
    def __init__(self, state, summary=lambda: None):
        """state: the live shared.cockpit.CockpitState; summary(): the current CockpitSummary (black box) or None."""
        self.state, self.summary = state, summary
        self.tower: dict | None = None
        self.tower_map: str | None = None
        self.session_aircraft: str | None = None
        self.phases = PhaseTracker()
        self.runway: str | None = None               # runway in use, kept until the wind demands a change
        self.known_variation: float | None = None

    def on_flight(self) -> None:
        """Call after every cockpit message: keeps the phase tracker fed."""
        self.choose_tower()
        if self.state.flight:
            self.phases.update(self.state.flight, self.state.time)

    def position(self) -> tuple[float | None, float | None]:
        flight = self.state.flight or {}
        lat, lon = flight.get('lat'), flight.get('lon')
        return (lat, lon) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else (None, None)

    def choose_tower(self) -> None:
        """The departure field: picked once per aircraft session, from the first known position."""
        if self.state.aircraft != self.session_aircraft:
            self.session_aircraft, self.tower, self.tower_map = self.state.aircraft, None, None
            self.phases.reset()
            self.runway = None
        lat, lon = self.position()
        if self.tower or lat is None:
            return
        terrain = geo.find_map(lat, lon)
        placed = [a for a in (terrain or {}).get('airfields', []) if 'lat' in a]
        if placed:
            self.tower = min(placed, key=lambda a: geo.distance_nm(lat, lon, a['lat'], a['lon']))
            self.tower_map = terrain['map']

    def visual(self) -> dict:
        lat, lon = self.position()
        if not self.tower or lat is None:
            return {'in_sight': False}
        if geo.distance_nm(lat, lon, self.tower['lat'], self.tower['lon']) > VISUAL_RANGE_NM:
            return {'in_sight': False}
        seen = {'in_sight': True}
        agl = (self.state.flight or {}).get('alt_agl_m')
        if isinstance(agl, (int, float)):
            seen['on_ground'] = agl < ON_GROUND_AGL_M
            if seen['on_ground']:
                speed = self.phases.ground_speed_kt
                if speed is not None:
                    seen['moving'] = 'stopped' if speed < MOVING_KT else 'taxiing' if speed < ROLL_KT else 'rolling fast'
                where = position_on_field(lat, lon, self.field_runways())
                if where:
                    seen['where_on_the_field'] = where
        summary = self.summary()
        if summary:
            seen['configuration'] = {outside_name(summary.positions[arg]['name']): label
                                     for arg, label in sorted(summary.labels.items())
                                     if summary.positions[arg].get('outside_visible')}
        return seen

    def field_runways(self) -> list[dict]:
        """The tower's own runways from the mission (dcs/instructor_weather.lua); the geometry does not go stale."""
        if not self.tower:
            return []
        wanted = geo.simplify(self.tower['name'])
        field = next((v for k, v in (self.state.weather or {}).items() if geo.simplify(k) == wanted), None)
        return (field or {}).get('runways') or []

    def radar(self) -> dict:
        self.choose_tower()
        state = self.state
        if state.radar_at is None or time.monotonic() - state.radar_at > RADAR_STALE_S:
            return {'status': 'no radar data from the mission'}
        if state.radar_unavailable:
            return {'status': 'no radar data from the mission', 'reason': state.radar_unavailable}
        if not self.tower:
            return {'status': 'no radar contact'}
        wanted = geo.simplify(self.tower['name'])
        network = next((v for k, v in (state.radars or {}).items() if geo.simplify(k) == wanted), None)
        if network is None:
            return {'status': 'the tower has no radar'}
        if not network.get('radars'):
            return {'status': 'radar out of service'}
        contacts = network.get('contacts') or []
        own = next((c for c in contacts if c.get('player')), None)
        radar = {'status': 'contact', **self.track(own)} if own else {'status': 'no radar contact'}
        traffic = [self.track(c, brief=True) for c in contacts if not c.get('player')]
        traffic = sorted((t for t in traffic if t['range_nm'] <= TRAFFIC_RANGE_NM), key=lambda t: t['range_nm'])
        if traffic:
            radar['traffic'] = traffic[:TRAFFIC_MAX]
        return radar

    def track(self, contact: dict, brief: bool = False) -> dict:
        """A radar contact as the tower reads it: range and bearing from the field, altitude, ground speed, track."""
        field = self.tower
        rng = geo.distance_nm(field['lat'], field['lon'], contact['lat'], contact['lon'])
        bearing = geo.bearing_deg(field['lat'], field['lon'], contact['lat'], contact['lon'])
        data = {'range_nm': round(rng, 1),
                'position': ('over the field' if rng < 0.5 else
                             f'{round(rng, 1)} nm {geo.cardinal(bearing)} of the field, bearing {round(bearing)}'),
                'altitude_ft': round(contact.get('alt_m', 0) * FT_PER_M / 10) * 10}
        vx, vz = contact.get('vx', 0.0), contact.get('vz', 0.0)
        data['ground_speed_kt'] = round(math.hypot(vx, vz) * KT_PER_MS)
        if data['ground_speed_kt'] >= 5:
            data['track_deg'] = round(math.degrees(math.atan2(vz, vx)) % 360)
        # Near the edge of what the radar sees for this aircraft's size, a real return fades in and out.
        dist, reach = contact.get('dist_km'), contact.get('range_km')
        if isinstance(dist, (int, float)) and isinstance(reach, (int, float)) and reach > 0 and \
                dist >= WEAK_RETURN_FRACTION * reach:
            data['return'] = 'weak, near the limit of the radar for an aircraft this size: it may fade'
        if brief:
            return {'type': contact.get('type'), 'callsign': contact.get('callsign'), **data}
        data['vertical_speed_fpm'] = round(contact.get('vy', 0.0) * FT_PER_M * 60 / 50) * 50
        return data

    def situation(self) -> dict:
        """Sent with every pilot message: identity, the tower, and the phase of the flight."""
        self.choose_tower()
        situation = {'aircraft': self.state.aircraft or 'unknown (not in an aircraft)'}
        if self.state.callsign:
            situation['callsign'] = self.state.callsign
        if self.tower:
            situation['tower'] = {'airfield': self.tower['name'], 'map': self.tower_map}
        situation['phase'] = self.phases.phase(self.state.flight or {}, self.tower)
        return situation

    def report(self) -> dict:
        """The get_tower_view tool: what the tower sees now through the window and on its radar."""
        self.choose_tower()
        report = {'tower': self.tower['name'] if self.tower else 'unknown (no position yet)'}
        report['window'] = self.visual()
        report['radar'] = self.radar()
        return report

    def weather(self) -> dict:
        """The get_weather tool: the tower's own airfield weather and runway in use."""
        self.choose_tower()
        state = self.state
        if state.weather_at is None or time.monotonic() - state.weather_at > WEATHER_STALE_S:
            return {'error': 'no weather data from the mission'}
        if not self.tower:
            return {'error': 'no tower yet (no aircraft position)'}
        wanted = geo.simplify(self.tower['name'])
        field = next((v for k, v in (state.weather or {}).items() if geo.simplify(k) == wanted), None)
        if field is None:
            return {'error': f'no weather for {self.tower["name"]}'}
        report = weather.report(field, self.variation(), self.runway)
        self.runway = (report.get('runway_in_use') or {}).get('runway', self.runway)
        return {'airfield': self.tower['name'], **report}

    def variation(self) -> float | None:
        """Local magnetic variation (east positive) from the aircraft's true and magnetic heading, kept once known."""
        flight = self.state.flight or {}
        true_heading, magnetic_heading = flight.get('heading_deg'), flight.get('mag_heading_deg')
        if isinstance(true_heading, (int, float)) and isinstance(magnetic_heading, (int, float)):
            self.known_variation = (true_heading - magnetic_heading + 180) % 360 - 180
        return self.known_variation

    def tower_map_name(self) -> str | None:
        self.choose_tower()
        return self.tower_map
