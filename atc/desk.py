"""The ATC agent's desk: which airfield the pilot is calling, what its tower knows, its decisions and its own calls.

User's design (2026-11-02): the ATC is an agent (role "atc") with the same brain as the instructor; only the
information it reasons from changes. It answers on each airfield's real frequency ("frequency_mode": "airfield"):
the station is the airfield whose ATC radio the pilot's selected radio is tuned to, within radio range. It sees all
mission traffic (dcs/instructor_traffic.lua) and the airfield's weather (dcs/instructor_weather.lua).

Clearances are mixed: code decides what carries safety (taxi, takeoff, landing, go-around, radar vectors: atc/ground.py,
atc/approach.py), the model voices it word for word and answers everything else. The tower also calls on its own
(atc/tower.py situations, vectors, landing clearance on final, go-around): tick(), fixed phrases in the radio language.
Ported from DCS-ATC's controller (copied and rewritten with the user's permission; nothing imported). Not ported: the
keystrokes into DCS's comms menu (to be replaced by a native command bridge, VAICOM style, later).

The desk is shared by the tool calls (the brain's thread) and tick() (the voice loop): one lock.
"""
import math
import threading
import time
from dataclasses import dataclass

from atc import approach, conditions, ground, tower
from atc.geometry import FT_PER_M, NM, Field, RunwayEnd, Track
from atc.phrases import speech as speech_for
from instructor import geo, weather
from instructor.field_position import position_on_field
from instructor.phase import ON_GROUND_AGL_M
from shared.radio import selected_radio

TRAFFIC_STALE_S = 5.0          # traffic comes once per second from the hook
TUNED_TOLERANCE_HZ = 10_000    # DCS's own comms menu counts a station as tuned within 10 kHz
TOWER_ANTENNA_M = 15           # about 50 ft above the field
MIN_ANTENNA_M = 2              # an aircraft on the ground
TRAFFIC_RANGE_NM = 30
TRAFFIC_MAX = 8
STOPPED_KT = 1
PARKED_AFTER_LANDING_S = 120   # stopped this long after a landing ends the arrival: the next taxi is a departure
PA_PER_INHG, PA_PER_HPA = 3386.389, 100.0
MODULATION_NAMES = {0: 'AM', 1: 'FM'}
ENGINE_RUNNING_RPM = 45        # % RPM: an engine started (F-5E idles at about 50 %)
NIGHT_STARTS_H, NIGHT_ENDS_H = 19, 6   # mission time of day: DCS's runway lights need an Inbound call at night
AI_TAXI_KT = 3                 # an AI aircraft on the ground faster than this is moving (taxiing or rolling)
AI_ARRIVAL_NM, AI_ARRIVAL_AGL_FT = 3.0, 1500   # seen this low and close over the field: an arrival


def radio_range_nm(aircraft_agl_m: float, antenna_m: float = TOWER_ANTENNA_M) -> float:
    """VHF/UHF line-of-sight range: radio horizon 1.23 * (sqrt(h1 ft) + sqrt(h2 ft)) nm. Terrain is not modelled."""
    def horizon(height_m):
        return 1.23 * math.sqrt(max(height_m, MIN_ANTENNA_M) * FT_PER_M)
    return horizon(antenna_m) + horizon(aircraft_agl_m)


def track_of(entry: dict, convergence: float = 0.0) -> Track:
    """A traffic entry of dcs/instructor_traffic.lua (DCS axes: x grid north, z grid east, y up), velocity turned to
    true north with the field's convergence (instructor/weather.py)."""
    vn, ve = weather.to_true(entry.get('vx') or 0.0, entry.get('vz') or 0.0, convergence)
    return Track(lat=entry['lat'], lon=entry['lon'], alt_m=entry.get('alt_m') or 0.0, vn=vn,
                 ve=ve, vu=entry.get('vy') or 0.0, in_air=bool(entry.get('in_air')),
                 type_name=str(entry.get('type') or ''), callsign=str(entry.get('callsign') or ''),
                 unit=str(entry.get('unit') or ''), side=entry.get('side'), helicopter=bool(entry.get('helicopter')))


@dataclass
class Station:
    name: str                  # DCS airfield name, "Senaki-Kolkhi"
    callsign: str              # how its tower answers, "Kolkhi"
    field: Field
    frequencies_hz: list[float]
    side: int | None
    map: str | None


class LandingMemory:
    """landed: the aircraft flew earlier and is on the ground now, not yet parked for PARKED_AFTER_LANDING_S."""

    def __init__(self):
        self.last_airborne, self.stopped_since, self.parked = None, None, False

    def update(self, player: Track, t: float) -> None:
        if player.in_air:
            self.last_airborne, self.stopped_since, self.parked = t, None, False
        elif self.last_airborne is not None and not self.parked:
            if player.ground_speed_kt < STOPPED_KT:
                self.stopped_since = t if self.stopped_since is None else self.stopped_since
                self.parked = t - self.stopped_since >= PARKED_AFTER_LANDING_S
            else:
                self.stopped_since = None

    @property
    def landed(self) -> bool:
        return self.last_airborne is not None and not self.parked


class Desk:
    def __init__(self, state, language: str, config: dict | None = None):
        """state: the live shared.cockpit.CockpitState; language: the radio language of the fixed phrases (the
        agent's voice language); config: agent.json "atc" {pressure: "inhg"|"hpa", runway_protect_nm,
        station_names: {DCS name: callsign}}."""
        config = config or {}
        self.state = state
        self.speech = speech_for(language)
        self.pressure_unit = config.get('pressure', 'inhg')
        self.protect_nm = float(config.get('runway_protect_nm', ground.PROTECT_NM))
        self.station_names = config.get('station_names') or {}
        self.lock = threading.RLock()
        self.runways: dict[str, str] = {}        # station name -> runway end number in use
        self.known_variation: float | None = None
        self.charts: dict[str, dict] = {}
        self._stations_key, self._stations = None, []
        self.session = (None, None)
        self.native = None   # native(command, airfield): DCS's own radio menu (client/dcs_bridge.py), when patched
        self.reset()

    def reset(self) -> None:
        """A new mission or aircraft: the tower forgets what it gave the pilot."""
        self.ledger, self.watch = tower.Ledger(), tower.Watch()
        self.arrival = approach.ArrivalState()
        self.landing = LandingMemory()
        self.airborne_since = None
        self.runway_called: set[str] = set()
        self.last_time = None
        self.dcs_departure_released = False   # DCS's own departure aborted for this departure
        self.departure_waiting_for: str | None = None   # the AI departure the abort waits for (logged once)
        self.arriving: set[str] = set()       # AI units seen low and close over a field: arrivals
        self.landed_here: set[str] = set()    # ...then on the ground: taxiing in, not departing
        self.dcs_inbound_sent = False         # DCS's own Inbound sent for this arrival (runway lights)

    # --- what the desk knows ------------------------------------------------------------------------------------

    def chart(self, name: str, data: dict) -> dict:
        """The airfield's chart (terrains/<map>/navigation.json): callsigns and frequencies."""
        if name not in self.charts:
            terrain = geo.find_map(data['lat'], data['lon']) or {}
            wanted = geo.simplify(name)
            airfield = next((a for a in terrain.get('airfields', []) if geo.simplify(a['name']) == wanted), {})
            self.charts[name] = {**airfield, 'map': terrain.get('map')}
        return self.charts[name]

    def player_entry(self) -> dict | None:
        if not self.traffic_fresh():
            return None
        return next((a for a in self.state.traffic or [] if a.get('player')), None)

    def traffic_fresh(self) -> bool:
        at = self.state.traffic_at
        return at is not None and time.monotonic() - at <= TRAFFIC_STALE_S

    def player_side(self) -> int | None:
        entry = self.player_entry()
        return entry.get('side') if entry else None

    def stations(self) -> list[Station]:
        """Every airdrome with an ATC that the pilot's coalition may talk to (own side and neutral)."""
        key = (id(self.state.weather), self.player_side())
        if key == self._stations_key:
            return self._stations
        side, result = key[1], []
        for name, data in (self.state.weather or {}).items():
            if not isinstance(data, dict) or not all(isinstance(data.get(k), (int, float)) for k in ('lat', 'lon')):
                continue
            if side is not None and data.get('side') not in (None, 0, side):
                continue
            chart = self.chart(name, data)
            frequencies = [f for f in data.get('frequencies_hz') or [] if isinstance(f, (int, float)) and f > 0]
            if not frequencies:   # the mission did not give them: the map's charts
                frequencies = [v['mhz'] * 1e6 for band, v in (chart.get('radio') or {}).items() if band != 'HF']
            callsign = self.station_names.get(name) or (chart.get('callsigns') or [name])[0]
            result.append(Station(name, callsign, Field(name, data), frequencies, data.get('side'), chart.get('map')))
        self._stations_key, self._stations = key, result
        return result

    def station_on(self, freq_hz: float) -> Station | None:
        return next((s for s in self.stations()
                     if any(abs(f - freq_hz) < TUNED_TOLERANCE_HZ for f in s.frequencies_hz)), None)

    def tuned(self) -> tuple[float | None, str, Station | None]:
        """(frequency Hz, modulation name, station) of the pilot's selected radio (not the intercom)."""
        radio = self.state.radio
        selected = selected_radio(radio)
        if not selected or (radio or {}).get('selected') == 0 or not selected.get('freq'):
            return None, 'AM', None
        freq = selected['freq']
        return freq, MODULATION_NAMES.get(selected.get('modulation'), 'AM'), self.station_on(freq)

    def player(self) -> Track | None:
        """The pilot's aircraft: from the mission traffic (with its velocity), else from the export (no velocity)."""
        entry = self.player_entry()
        if entry:
            return track_of(entry, self.convergence())
        flight = self.state.flight or {}
        lat, lon, alt, agl = (flight.get(k) for k in ('lat', 'lon', 'alt_msl_m', 'alt_agl_m'))
        if not all(isinstance(v, (int, float)) for v in (lat, lon, alt)):
            return None
        heading = flight.get('heading_deg')   # grid, like every DCS vector
        return Track(lat, lon, alt, in_air=isinstance(agl, (int, float)) and agl >= ON_GROUND_AGL_M,
                     type_name=self.state.aircraft or '',
                     track_hint=(heading + self.convergence()) % 360 if isinstance(heading, (int, float)) else None)

    def traffic(self) -> list[Track] | None:
        """Every other aircraft of the mission; None without fresh traffic data."""
        if not self.traffic_fresh():
            return None
        conv = self.convergence()
        return [track_of(a, conv) for a in self.state.traffic or [] if not a.get('player') and 'lat' in a and 'lon' in a]

    def reachable(self, station: Station, player: Track) -> tuple[bool, float, float]:
        """(within radio line of sight, distance nm, range nm)."""
        agl = max(0.0, station.field.height_ft(player) / FT_PER_M) if player.in_air else 0.0
        distance, reach = station.field.distance_nm(player), radio_range_nm(agl)
        return distance <= reach, distance, reach

    def variation(self) -> float | None:
        flight = self.state.flight or {}
        true_heading, magnetic_heading = flight.get('heading_deg'), flight.get('mag_heading_deg')
        if isinstance(true_heading, (int, float)) and isinstance(magnetic_heading, (int, float)):
            self.known_variation = (true_heading - magnetic_heading + 180) % 360 - 180
        return self.known_variation

    def convergence(self) -> float:
        """True - grid north here (the tuned station's field, else any): turns DCS's vectors to true."""
        _, _, station = self.tuned()
        stations = [station] if station else self.stations()
        return stations[0].field.convergence if stations else 0.0

    def true_variation(self, station: Station) -> float | None:
        """For true headings: the export's variation is relative to grid north."""
        variation = self.variation()
        return None if variation is None else variation + station.field.convergence

    def wind_true(self, station: Station) -> tuple[float, float] | None:
        """(direction the wind comes from, true degrees; speed kt), or None."""
        data = station.field.data
        north, east = data.get('wind_north'), data.get('wind_east')
        if not all(isinstance(v, (int, float)) for v in (north, east)):
            return None
        return weather.wind_from(*weather.to_true(north, east, station.field.convergence))

    def runway(self, station: Station) -> RunwayEnd | None:
        """Runway in use, keeping the one already announced unless the wind demands a change."""
        ends, wind = station.field.ends(), self.wind_true(station)
        if not ends or not wind:
            return None
        chosen = weather.runway_in_use([(e.number, e.heading_true) for e in ends], *wind, self.runways.get(station.name))
        self.runways[station.name] = chosen[0]
        return next(e for e in ends if e.number == chosen[0])

    def wind_spoken(self, station: Station) -> str | None:
        wind = self.wind_true(station)
        if not wind:
            return None
        direction, speed = wind
        if speed < weather.CALM_KT:
            return self.speech.wind(None, None)
        return self.speech.wind(weather.magnetic(direction, self.true_variation(station)), speed)

    def pressure_text(self, station: Station, kind: str) -> str | None:
        """QNH or QFE as the radio says it: "29.92" (inHg) or "1013" (hPa), from settings "atc.pressure"."""
        data = station.field.data
        qfe, elevation = data.get('pressure_pa'), data.get('elevation_m')
        if not isinstance(qfe, (int, float)):
            return None
        if kind == 'qnh':
            if not isinstance(elevation, (int, float)):
                return None
            value = weather.qnh_pa(qfe, elevation)
        else:
            value = qfe
        return f'{round(value / PA_PER_HPA)}' if self.pressure_unit == 'hpa' else f'{value / PA_PER_INHG:.2f}'

    def channel(self, agent: dict) -> dict:
        """The agent as the voice transmits it: on the frequency and modulation the pilot is tuned to, named after the
        station (instructor/voice.py agent_channel)."""
        freq, modulation, station = self.tuned()
        if not freq:
            return agent
        # The station's own frequency, not the dial's reading: a Mi-8's R-863 reports 260.001 MHz, SRS rounds the
        # pilot's radio to 260.000 and a transmission on 260.001 was never heard (live 2026-11-02).
        nearest = min(station.frequencies_hz, key=lambda f: abs(f - freq)) if station and station.frequencies_hz \
            else round(freq / 5000) * 5000
        return {**agent, 'frequency_mhz': round(nearest / 1000) / 1000, 'modulation': modulation,
                'name': station.callsign if station else agent.get('name', 'ATC')}

    def new_mission_check(self) -> None:
        session = (self.state.aircraft, self.state.callsign)
        t = self.state.time
        if session != self.session or (self.last_time is not None and t < self.last_time):
            self.session = session
            self.reset()
        self.last_time = t

    # --- the board: what the controller has in front of them -----------------------------------------------------

    def pilot_view(self, station: Station, player: Track) -> dict:
        """Where the pilot's aircraft is (the ATC sees all traffic: user's choice)."""
        field = station.field
        north, east = field.where(player)
        distance = math.hypot(north, east) / NM
        bearing = math.degrees(math.atan2(east, north)) % 360
        variation = self.true_variation(station)
        if not player.in_air:
            view = {'state': 'on the ground', 'ground_speed_kt': round(player.ground_speed_kt)}
            if field.at_airfield(north, east):
                where = position_on_field(player.lat, player.lon, field.data.get('runways') or [])
                if where:
                    view['where_on_the_field'] = where
            else:
                view['position'] = f'{distance:.1f} nm {geo.cardinal(bearing)} of the field (not at this airfield)'
            if self.landing.landed:
                view['landed'] = 'after a landing, taxiing in'
            return view
        view = {'state': 'airborne',
                'position': f'{distance:.1f} nm {geo.cardinal(bearing)} of the field, bearing '
                            f'{weather.magnetic(bearing, variation):03d} from the field',
                'altitude_ft': round(player.alt_m * FT_PER_M / 10) * 10,
                'height_above_field_ft': round(field.height_ft(player) / 10) * 10,
                'ground_speed_kt': round(player.ground_speed_kt),
                'vertical_speed_fpm': round(player.vu * FT_PER_M * 60 / 50) * 50}
        if player.track_true is not None:
            view['track_magnetic'] = f'{weather.magnetic(player.track_true, variation):03d}'
        end = self.runway(station)
        if end:
            final = approach.on_final(field, end, player)
            if final is not None:
                view['on_final'] = f'runway {end.number}, {final:.1f} nm from the threshold'
        return view

    def traffic_view(self, station: Station) -> list[dict] | None:
        traffic = self.traffic()
        if traffic is None:
            return None
        field, end, result = station.field, self.runway(station), []
        for a in traffic:
            north, east = field.where(a)
            distance = math.hypot(north, east) / NM
            if distance > TRAFFIC_RANGE_NM:
                continue
            bearing = math.degrees(math.atan2(east, north)) % 360
            item = {'type': a.type_name, 'callsign': a.callsign or None, 'range_nm': round(distance, 1)}
            if a.in_air:
                item.update({'position': f'{distance:.1f} nm {geo.cardinal(bearing)} of the field',
                             'altitude_ft': round(a.alt_m * FT_PER_M / 100) * 100,
                             'ground_speed_kt': round(a.ground_speed_kt)})
                if a.track_true is not None:
                    item['track_magnetic'] = f'{weather.magnetic(a.track_true, self.true_variation(station)):03d}'
                arrivals = ground.arrivals_on_final(field, end, [a], ground.TRAFFIC_INFO_NM * 2) if end else []
                if arrivals:
                    item['on_final'] = f'runway {end.number}, {arrivals[0].distance_nm:.1f} nm'
            else:
                runway = field.runway_under(north, east)
                item['state'] = (f'on the ground, on runway {runway.name}' if runway else
                                 'on the ground at the field' if field.at_airfield(north, east) else 'on the ground')
                if a.ground_speed_kt >= 3:
                    item['ground_speed_kt'] = round(a.ground_speed_kt)
            result.append(item)
        return sorted(result, key=lambda i: i['range_nm'])[:TRAFFIC_MAX]

    def situation(self) -> dict:
        """Sent with every pilot message: the controller's board."""
        with self.lock:
            self.new_mission_check()
            freq, modulation, station = self.tuned()
            situation = {'aircraft': self.state.aircraft or 'unknown (not in an aircraft)',
                         'callsign': self.state.callsign or 'unknown'}
            if freq:
                situation['radio'] = f'{freq / 1e6:.3f} {modulation}'
            if not station:
                situation['tower'] = {'airfield': 'no airfield on this frequency'}
                return situation
            situation['tower'] = {'airfield': station.callsign, 'dcs_name': station.name, 'map': station.map}
            report = weather.report(station.field.data, self.variation(), self.runways.get(station.name))
            end = self.runway(station)
            if end:
                situation['runway_in_use'] = {**report.get('runway_in_use', {}), 'say': self.speech.runway(end.number)}
            wind = self.wind_spoken(station)
            if wind:
                situation['wind'] = {'value': report.get('wind'), 'say': wind}
            for kind in ('qnh', 'qfe'):
                text = self.pressure_text(station, kind)
                if text:
                    situation[kind] = {'value': report.get(kind), 'say': self.speech.pressure(kind, text)}
            for key in ('temperature_c', 'field_elevation_ft'):
                if key in report:
                    situation[key] = report[key]
            sky = conditions.conditions(self.state.mission_weather, station.field.elevation_m)
            if sky:
                situation['visibility_and_ceiling'] = sky
            player = self.player()
            if player:
                situation['pilot'] = self.pilot_view(station, player)
            situation['given_to_the_pilot'] = {
                'taxi_cleared': self.ledger.taxi_cleared, 'takeoff_cleared': self.ledger.takeoff_cleared,
                'landing_requested': self.arrival.requested, 'vectoring': self.arrival.vectoring,
                'landing_cleared': self.arrival.cleared}
            return situation

    # --- decisions (tools) -------------------------------------------------------------------------------------

    def context(self):
        """(station, player, runway in use) for a decision, or an error dict for the model."""
        self.new_mission_check()   # before recording a decision, never after (it would forget it)
        _, _, station = self.tuned()
        if not station:
            return {'error': 'no airfield on the frequency the pilot is tuned to'}
        player = self.player()
        if not player:
            return {'error': 'the pilot is not in an aircraft'}
        return station, player, self.runway(station)

    def transmission(self, station: Station, decision: dict) -> dict:
        if decision.get('say'):
            decision['transmission'] = self.speech.call(self.state.callsign, station.callsign, decision['say'])
        return decision

    def request_taxi(self) -> dict:
        with self.lock:
            context = self.context()
            if isinstance(context, dict):
                return context
            station, player, end = context
            arrivals = ground.arrivals_on_final(station.field, end, self.traffic() or [], ground.TRAFFIC_INFO_NM) \
                if end else []
            decision = ground.taxi_clearance(station.field, player, end, self.landing.landed,
                                             self.pressure_text(station, 'qnh'), self.speech,
                                             ground.traffic_note(arrivals[0], self.speech) if arrivals else None)
            self.ledger.record('taxi', decision)
            if decision.get('status') == 'cleared' and decision.get('kind') == 'departure':
                self.release_dcs_departure(station, player)
            return self.transmission(station, decision)

    def takeoff_decision(self, station: Station, player: Track, end: RunwayEnd | None) -> dict:
        return ground.takeoff_clearance(station.field, player, end, self.traffic(), self.wind_spoken(station),
                                        self.speech, self.protect_nm)

    def request_takeoff(self) -> dict:
        with self.lock:
            context = self.context()
            if isinstance(context, dict):
                return context
            station, player, end = context
            decision = self.takeoff_decision(station, player, end)
            self.ledger.record('takeoff', decision)
            return self.transmission(station, decision)

    def request_landing(self) -> dict:
        """Arms the tower's own arrival calls: the landing clearance on final, and vectors in instrument weather."""
        with self.lock:
            context = self.context()
            if isinstance(context, dict):
                return context
            station, player, end = context
            self.arrival.requested = True
            if self.night() and not self.dcs_inbound_sent:   # DCS turns its runway lights on for an Inbound
                self.dcs_inbound_sent = self.send_native('inbound', station)
            decision = approach.landing_clearance(station.field, end, player, self.traffic(),
                                                  self.wind_spoken(station), self.pressure_text(station, 'qfe'),
                                                  self.speech)
            sky = conditions.conditions(self.state.mission_weather, station.field.elevation_m) or {}
            if sky.get('instrument_conditions') and decision['status'] == 'continue' and not self.arrival.vectoring:
                vectors = self.vectors(station, player, end)
                if vectors:
                    return vectors
            if decision['status'] == 'cleared':
                self.arrival.cleared = True
            return self.transmission(station, decision)

    def request_vectors(self) -> dict:
        with self.lock:
            context = self.context()
            if isinstance(context, dict):
                return context
            station, player, end = context
            if not player.in_air:
                return {'status': 'unable', 'reason': 'the aircraft is on the ground'}
            self.arrival.requested = True
            return self.vectors(station, player, end) or {'status': 'unable', 'reason': 'no runway in use'}

    def vectors(self, station: Station, player: Track, end: RunwayEnd | None) -> dict | None:
        v = end and approach.vector(station.field, end, player)
        call = v and self.arrival.next_call(v, time.monotonic())
        if not call:
            return None
        self.arrival.vectoring = call.phase != 'established'
        say = approach.vector_phrase(end, call, player, self.true_variation(station), True, self.pressure_text(station, 'qfe'),
                                     station.field.height_ft(player), self.speech)
        return self.transmission(station, {'status': 'vectoring', 'phase': call.phase, 'say': say})

    def traffic_report(self) -> dict:
        with self.lock:
            _, _, station = self.tuned()
            if not station:
                return {'error': 'no airfield on the frequency the pilot is tuned to'}
            traffic = self.traffic_view(station)
            if traffic is None:
                return {'error': 'no traffic data from the mission'}
            return {'airfield': station.callsign, 'traffic': traffic or 'none within 30 nm'}

    # --- DCS's own ATC (native commands through the radio-menu bridge) -----------------------------------------

    def send_native(self, command: str, station: Station) -> bool:
        if not self.native:
            return False
        try:
            self.native(command, station.name)
            return True
        except Exception as exc:   # the bridge is optional: our ATC works without it
            print(f'[atc] native {command} failed: {exc}')
            return False

    def release_dcs_departure(self, station: Station, player: Track) -> None:
        """DCS's own ATC holds AI arrivals while it thinks the player departs (DCS-ATC, live 2026-10-26): abort the
        player's departure in DCS once per departure, so DCS lets them land and our ATC handles the player. Only the
        player's departure: AI departures are DCS's own (user's decision, 2026-11-03, after trying to clear AI
        wingmen of the player's group through DCS's ATC)."""
        if self.dcs_departure_released or player.in_air:
            return
        # User's idea (2026-11-04): DCS's ATC lets an AI departure go before its own arrivals anyway (a DCS fault we
        # live with), and aborting then gains nothing: while an AI aircraft at this field is about to depart, wait
        # (tried again on every tick until the player is airborne); a player alone sends it at once, as before.
        departing = self.ai_departure(station)
        if departing:
            if departing != self.departure_waiting_for:
                self.departure_waiting_for = departing
                print(f'[atc] DCS abort_takeoff waits: AI departure at {station.name} ({departing})')
            return
        self.departure_waiting_for = None
        self.dcs_departure_released = self.send_native('abort_takeoff', station)

    def ai_departure(self, station: Station) -> str | None:
        """An AI aircraft on the ground at this field that is about to take off, or None: one taxiing (or rolling)
        that has not landed here, or a scripted wingman started on the ground and not yet airborne (its own DCS
        events: dcs/instructor_tasks.lua "air"), engines starting included."""
        field = station.field
        for name, group in (self.state.groups or {}).items():
            air = (group or {}).get('air') if isinstance(group, dict) else None
            events = (air or {}).get('events')
            events = events if isinstance(events, dict) else {}
            if (isinstance(air, dict) and air.get('active') and air.get('started') and not air.get('in_air')
                    and 'takeoff' not in events and 'lat' in group
                    and field.at_airfield(*field.frame_of(group['lat'], group['lon']))):
                return name
        for a in self.traffic() or []:
            if a.in_air or a.ground_speed_kt < AI_TAXI_KT or not field.at_airfield(*field.where(a)):
                continue
            if a.unit not in self.landed_here:
                return a.label
        return None

    def note_ai_landings(self, station: Station) -> None:
        """AI aircraft seen airborne close in over this field and then on the ground: arrivals, not departures."""
        field = station.field
        for a in self.traffic() or []:
            if a.in_air and field.distance_nm(a) <= AI_ARRIVAL_NM and field.height_ft(a) <= AI_ARRIVAL_AGL_FT:
                self.arriving.add(a.unit)
            elif not a.in_air and a.unit in self.arriving:
                self.landed_here.add(a.unit)

    def night(self) -> bool:
        seconds = (self.state.mission_weather or {}).get('time_of_day_s')
        if not isinstance(seconds, (int, float)):
            return False
        hour = (seconds / 3600) % 24
        return hour < NIGHT_ENDS_H or hour >= NIGHT_STARTS_H

    def engine_running(self) -> bool:
        flight = self.state.flight or {}
        return any(isinstance(flight.get(k), (int, float)) and flight[k] >= ENGINE_RUNNING_RPM
                   for k in ('rpm_left', 'rpm_right'))

    # --- before the brain: silence and wrong station --------------------------------------------------------------

    def prefilter(self, text: str) -> str | None:
        """'' = nobody answers (no airfield on that frequency, or out of radio range: a real radio stays silent);
        a fixed reply (the pilot called another airfield on this one's frequency); None = the brain answers."""
        with self.lock:
            _, _, station = self.tuned()
            player = self.player()
            if not station:
                return ''
            if player and not self.reachable(station, player)[0]:
                return ''
            others = [n for s in self.stations() if s is not station for n in (s.name, s.callsign)]
            other = tower.called_station(text, [station.callsign, station.name], others)
            if not other:
                return None
            called = next((s for s in self.stations() if other in (s.name, s.callsign)), None)
            return self.speech.call(self.state.callsign, station.callsign, self.speech.say(
                'wrong_station', station=station.callsign, other=called.callsign if called else other))

    # --- the tower's own calls ---------------------------------------------------------------------------------

    def tick(self, busy: bool = False) -> str | None:
        """Called every couple of seconds by the voice loop: the call the tower makes on its own now, or None.
        busy: the pilot is talking to this agent (or it is still answering): the tower waits."""
        with self.lock:
            self.new_mission_check()
            player, t = self.player(), self.state.time
            if not player:
                return None
            self.landing.update(player, t)
            freq, _, station = self.tuned()
            if not station:
                return None
            self.note_ai_landings(station)   # so an AI taxiing in after landing is not taken for a departure
            if not self.reachable(station, player)[0]:
                return None
            end = self.runway(station)
            if not end:
                return None
            call = self.airborne_calls(station, player, end, t, busy) if player.in_air else \
                self.ground_calls(station, player, end, t, busy)
            return self.speech.call(self.state.callsign, station.callsign, call) if call else None

    def airborne_calls(self, station: Station, player: Track, end: RunwayEnd, t: float, busy: bool) -> str | None:
        self.dcs_departure_released = False   # the next departure needs its own release
        if self.ledger.takeoff_cleared:   # our departure: release them once they are climbing away
            self.airborne_since = t if self.airborne_since is None else self.airborne_since
            if t - self.airborne_since < tower.DEPARTED_CALL_AFTER_S or busy:
                return None
            self.ledger = tower.Ledger()
            return self.speech.say('departed')
        self.ledger, self.watch = tower.Ledger(), tower.Watch()   # the next departure starts fresh
        if self.arrival.requested and not busy:
            return self.arrival_call(station, player, end)
        return None

    def arrival_call(self, station: Station, player: Track, end: RunwayEnd) -> str | None:
        """The next vector, the landing clearance once on final, "continue approach" when traffic holds it, and a
        go-around when the runway is blocked close in."""
        arrival, field, traffic = self.arrival, station.field, self.traffic()
        final = approach.on_final(field, end, player)
        if arrival.cleared and not arrival.go_around_called and final is not None \
                and final <= approach.GO_AROUND_WITHIN_NM:
            occupant = ground.runway_occupant(field, end, traffic or [])
            if occupant:
                arrival.go_around_called, arrival.cleared = True, False
                return f'{self.speech.say("go_around")}, {self.speech.say("traffic_on_runway", type=occupant.type_name)}'
        landing = approach.landing_clearance(field, end, player, traffic, self.wind_spoken(station),
                                             self.pressure_text(station, 'qfe'), self.speech)
        if arrival.vectoring and (arrival.cleared or (final is not None and landing['status'] == 'cleared'
                                                      and final <= approach.CLEAR_WITHIN_NM)):
            arrival.vectoring = False   # on final and clear to land: the clearance says it all
        if arrival.vectoring:
            v = approach.vector(field, end, player)
            call = v and arrival.next_call(v, time.monotonic())
            if call:
                if call.phase == 'established':
                    arrival.vectoring = False
                return approach.vector_phrase(end, call, player, self.true_variation(station), False, None,
                                              field.height_ft(player), self.speech)
        if final is None or final > approach.CLEAR_WITHIN_NM or arrival.cleared:
            return None
        if landing['status'] == 'cleared':
            arrival.cleared, arrival.held_key = True, None
            return landing['say']
        if landing['status'] == 'hold' and landing.get('traffic_key') != arrival.held_key:
            arrival.held_key = landing.get('traffic_key')
            return landing['say']
        return None

    def ground_calls(self, station: Station, player: Track, end: RunwayEnd, t: float, busy: bool) -> str | None:
        self.airborne_since = None
        if self.arrival.requested:
            self.arrival = approach.ArrivalState()   # landed (or never left): the next arrival starts fresh
            self.dcs_inbound_sent = False
        field, traffic = station.field, self.traffic() or []
        if not self.landing.landed and self.engine_running() and field.at_airfield(*field.where(player)):
            # Engines started before departure, as DCS-ATC did; tried on every tick (it may wait for an AI departure).
            self.release_dcs_departure(station, player)
        on_runway = ground.on_runway_in_use(field, player, end)
        takeoff = self.takeoff_decision(station, player, end)
        view = tower.View(t=t, on_ground=True, landed=self.landing.landed, ground_speed_kt=player.ground_speed_kt,
                          at_departure_end=ground.at_departure_end(field, player, end), on_runway=on_runway,
                          takeoff=takeoff)
        if busy:
            return None
        seen = self.watch.update(view, self.ledger)
        if seen:
            close = ground.arrivals_on_final(field, end, traffic, self.protect_nm)
            note = ground.traffic_note(close[0], self.speech) if close else None
            occupant = ground.runway_occupant(field, end, traffic)
            if occupant:   # on the runway beats on final: it is the nearer conflict
                note = self.speech.say('traffic_on_runway', type=occupant.type_name)
            if seen == 'runway_free':   # it carries the takeoff clearance (or the new reason to hold)
                self.ledger.record('takeoff', takeoff)
            return tower.phrase(seen, self.speech, self.speech.runway(end.number), takeoff, note)
        # Cleared and on the runway while an arrival closes in: depart now or vacate.
        if not (on_runway and self.ledger.takeoff_cleared):
            return None
        arrivals = [a for a in ground.arrivals_on_final(field, end, traffic, self.protect_nm)
                    if a.aircraft.unit not in self.runway_called]
        if not arrivals:
            return None
        self.runway_called.add(arrivals[0].aircraft.unit)
        return ground.runway_call(end, arrivals[0], self.speech)
