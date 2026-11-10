"""Offline tests for the ATC agent (atc/): stations on the airfields' own frequencies, clearances decided in code,
the tower's own calls, silence out of range and the wrong-station reply. Scenarios at Senaki-Kolkhi (its real chart
from terrains/Caucasus) with one runway 09/27 laid out east-west on the field reference point.

Run: python -m unittest discover tests
"""
import math
import tempfile
import time
import unittest
from pathlib import Path

from atc import conditions
from atc.desk import Desk, radio_range_nm
from atc.phrases import Speech
from shared.agents import AgentStore, default_atc, default_instructor
from shared.cockpit import CockpitState
from dcs_data import CAUCASUS, needs

LAT, LON = 42.24069, 42.04917          # Senaki-Kolkhi
M_PER_DEG = 111_320.0
KT = 1 / 1.943844                      # m/s per knot
RUNWAY = {'name': '09', 'course': -math.pi / 2, 'length_m': 2400, 'width_m': 45, 'lat': LAT, 'lon': LON}
WEST_WIND = {'wind_north': 0.0, 'wind_east': 5.0}   # air moving east: wind from 270, runway 27 in use
THRESHOLD_27_EAST_M = 1200             # runway 27 starts at the east end


def at(north: float, east: float) -> tuple[float, float]:
    return LAT + north / M_PER_DEG, LON + east / (M_PER_DEG * math.cos(math.radians(LAT)))


def aircraft(unit: str, north: float, east: float, alt_m: float = 13.0, vn: float = 0.0, ve: float = 0.0,
             vu: float = 0.0, in_air: bool = False, player: bool = False, type_name: str = 'F-5E-3') -> dict:
    lat, lon = at(north, east)
    entry = {'unit': unit, 'type': type_name, 'callsign': unit, 'side': 2, 'lat': lat, 'lon': lon, 'alt_m': alt_m,
             'vx': vn, 'vz': ve, 'vy': vu, 'in_air': in_air}
    if player:
        entry['player'] = True
    return entry


def radio(mhz: float, modulation: int = 0) -> dict:
    return {'selected': 1, 'radios': [{'name': 'intercom', 'freq': 0, 'modulation': 2},
                                      {'name': 'AN/ARC-164', 'freq': mhz * 1e6, 'modulation': modulation}]}


class Scenario:
    def __init__(self, language: str = 'en', config: dict | None = None, mission_weather: dict | None = None):
        self.state = CockpitState(aircraft='F-5E-3', callsign='Colt 1-1', time=100.0)
        kutaisi_lat, kutaisi_lon = 42.17915, 42.49567
        self.state.weather = {
            'Senaki-Kolkhi': {'lat': LAT, 'lon': LON, 'elevation_m': 13.0, **WEST_WIND, 'temperature_k': 288.15,
                              'pressure_pa': 101200.0, 'side': 2, 'frequencies_hz': [261e6, 132e6],
                              'runways': [RUNWAY]},
            'Kutaisi': {'lat': kutaisi_lat, 'lon': kutaisi_lon, 'elevation_m': 45.0, **WEST_WIND, 'side': 2,
                        'pressure_pa': 101000.0, 'runways': []},   # no frequencies from the mission: its chart's
            'Gudauta': {'lat': 43.12, 'lon': 40.58, 'elevation_m': 20.0, 'side': 1, 'frequencies_hz': [259e6],
                        'runways': []},                             # the other coalition
        }
        self.state.weather_at = time.monotonic()
        self.state.mission_weather = mission_weather
        self.state.radio = radio(261.0)
        self.state.flight = {'heading_deg': 270.0, 'mag_heading_deg': 264.0}   # variation 6 east
        self.player = aircraft('Aerial-1-1', 300, 0, player=True)
        self.others: list[dict] = []
        self.desk = Desk(self.state, language, config)
        self.update()

    def update(self, seconds: float = 0.0) -> None:
        self.state.time += seconds
        self.state.traffic = [self.player, *self.others]
        self.state.traffic_at = time.monotonic()

    def place(self, north: float, east: float, **kw) -> None:
        self.player = aircraft('Aerial-1-1', north, east, player=True, **kw)
        self.update()


@needs(CAUCASUS)
class Stations(unittest.TestCase):
    def test_airfield_frequencies_pick_the_station(self):
        desk = Scenario().desk
        self.assertEqual(desk.station_on(261.0e6).callsign, 'Kolkhi')   # callsign from the map's chart
        self.assertEqual(desk.station_on(132.005e6).name, 'Senaki-Kolkhi')
        self.assertIsNone(desk.station_on(251.0e6))
        self.assertEqual(desk.station_on(263.0e6).name, 'Kutaisi')       # from its chart (UHF 263.0)
        self.assertIsNone(desk.station_on(259.0e6))                      # Gudauta is red: not for a blue pilot

    def test_station_name_override(self):
        desk = Scenario(config={'station_names': {'Senaki-Kolkhi': 'Senaki'}}).desk
        self.assertEqual(desk.station_on(261.0e6).callsign, 'Senaki')

    def test_radio_range(self):
        self.assertAlmostEqual(radio_range_nm(0), 1.23 * (math.sqrt(15 * 3.28084) + math.sqrt(2 * 3.28084)), 3)
        self.assertGreater(radio_range_nm(3000), 80)

    def test_gate_sends_airfield_frequencies_to_the_atc(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AgentStore(Path(folder) / 'agents', settings={'srs': {'frequencies_mhz': [251.0]}})
            desk = Scenario().desk
            store.station_finder = lambda hz: desk.station_on(hz) is not None
            self.assertEqual(store.on_frequency(261.0e6, 0)['id'], 'atc')
            self.assertEqual(store.on_frequency(40.6e6, 1), None)             # Senaki gave no VHF-low radio
            self.assertEqual(store.on_frequency(251.0e6, 0)['id'], 'instructor')
            self.assertIsNone(store.on_frequency(300.0e6, 0))
            # One airfield agent at a time; it may stay active next to fixed-frequency agents.
            store.create('atc2', {**default_atc({}), 'name': 'ATC 2', 'active': False})
            store.reload()
            with self.assertRaises(ValueError):
                store.update('atc2', {'active': True})
            store.update('instructor', default_instructor({}))


@needs(CAUCASUS)
class Board(unittest.TestCase):
    def test_board_has_the_spoken_values(self):
        board = Scenario().desk.situation()
        self.assertEqual(board['tower']['airfield'], 'Kolkhi')
        self.assertEqual(board['radio'], '261.000 AM')
        self.assertEqual(board['runway_in_use']['runway'], '27')
        self.assertEqual(board['runway_in_use']['say'], 'runway two seven')
        self.assertEqual(board['wind']['say'], 'wind two six four, one zero knots')   # 270 true - 6 east
        self.assertEqual(board['qnh']['say'], 'QNH two niner point niner three')
        self.assertEqual(board['pilot']['state'], 'on the ground')
        self.assertIn('elsewhere on the field', board['pilot']['where_on_the_field'])

    def test_no_station_on_the_frequency(self):
        scenario = Scenario()
        scenario.state.radio = radio(251.0)
        self.assertEqual(scenario.desk.situation()['tower']['airfield'], 'no airfield on this frequency')

    def test_hectopascals(self):
        board = Scenario(language='en', config={'pressure': 'hpa'}).desk.situation()
        self.assertEqual(board['qnh']['say'], 'QNH one zero one four')

    def test_instrument_conditions(self):
        fog = {'visibility_m': 80000, 'fog': True, 'fog_visibility_m': 3000}
        self.assertTrue(conditions.conditions(fog, 13.0, {})['instrument_conditions'])
        clouds = {'visibility_m': 80000, 'cloud_preset': 'Preset7', 'cloud_base_m': 400}
        sky = conditions.conditions(clouds, 13.0, {'Preset7': [[2520.0, 0.524], [6720.0, 0.363]]})
        self.assertEqual(sky['ceiling'], '1300 ft above the field')
        self.assertTrue(sky['instrument_conditions'])
        self.assertFalse(conditions.conditions({'visibility_m': 80000}, 13.0, {})['instrument_conditions'])


@needs(CAUCASUS)
class Clearances(unittest.TestCase):
    def test_taxi_to_the_holding_point(self):
        decision = Scenario().desk.request_taxi()
        self.assertEqual(decision['status'], 'cleared')
        self.assertEqual(decision['transmission'],
                         'Colt one one, Kolkhi, taxi to holding point runway two seven, QNH two niner point niner '
                         'three.')

    def test_takeoff_from_the_holding_point(self):
        scenario = Scenario(language='en')
        scenario.place(73, THRESHOLD_27_EAST_M - 10)
        decision = scenario.desk.request_takeoff()
        self.assertEqual(decision['status'], 'cleared')
        self.assertEqual(decision['say'], 'wind two six four, one zero knots, runway two seven, cleared for takeoff')
        self.assertTrue(scenario.desk.ledger.takeoff_cleared)

    def test_takeoff_waits_for_an_arrival(self):
        scenario = Scenario(language='en')
        scenario.others = [aircraft('Uzi11', 0, THRESHOLD_27_EAST_M + 3 * 1852, alt_m=300, ve=-70, in_air=True,
                                    type_name='C-130')]
        scenario.place(73, THRESHOLD_27_EAST_M - 10)
        decision = scenario.desk.request_takeoff()
        self.assertEqual(decision['status'], 'hold')
        self.assertEqual(decision['say'], 'hold position, traffic C-130 on final, three miles')

    def test_takeoff_not_from_the_apron(self):
        scenario = Scenario(language='en')
        decision = scenario.desk.request_takeoff()
        self.assertEqual((decision['status'], decision['say']), ('hold', 'taxi to holding point runway two seven'))
        self.assertIn('past the threshold of runway 27', decision['reason'])   # the model can guide the pilot

    def test_takeoff_from_an_intersection_near_the_threshold(self):
        # Live 2026-11-03: held short facing runway 27, past its threshold, beside the runway; the board said
        # "holding point area" and every takeoff request got "taxi to holding point".
        scenario = Scenario(language='en')
        scenario.place(190, THRESHOLD_27_EAST_M - 350)
        board = scenario.desk.situation()['pilot']['where_on_the_field']
        self.assertIn('holding point area', board)
        self.assertEqual(scenario.desk.request_takeoff()['status'], 'cleared')
        scenario.place(190, THRESHOLD_27_EAST_M - 900)        # mid-field: not the departure end
        self.assertEqual(scenario.desk.request_takeoff()['status'], 'hold')

    def test_landing_on_final(self):
        scenario = Scenario(language='en')
        scenario.place(0, THRESHOLD_27_EAST_M + 4 * 1852, alt_m=400, ve=-80, in_air=True)
        decision = scenario.desk.request_landing()
        self.assertEqual(decision['status'], 'cleared')
        self.assertTrue(decision['say'].endswith('runway two seven, cleared to land'))

    def test_landing_with_the_runway_occupied(self):
        scenario = Scenario(language='en')
        scenario.others = [aircraft('Uzi11', 0, 200, type_name='Su-25T')]
        scenario.place(0, THRESHOLD_27_EAST_M + 4 * 1852, alt_m=400, ve=-80, in_air=True)
        decision = scenario.desk.request_landing()
        self.assertEqual(decision['say'], 'continue approach, traffic Su-25T on the runway')

    def test_landing_far_out_gives_the_approach_data(self):
        scenario = Scenario(language='en')
        scenario.place(15 * 1852, 0, alt_m=1500, vn=-120, in_air=True)
        decision = scenario.desk.request_landing()
        self.assertEqual(decision['status'], 'continue')
        self.assertEqual(decision['say'], 'runway two seven, wind two six four, one zero knots, QFE two niner point '
                                          'eight eight, report final')

    def test_vectors_from_the_north(self):
        scenario = Scenario(language='en')
        scenario.place(15 * 1852, 0, alt_m=1500, vn=-120, in_air=True)
        decision = scenario.desk.request_vectors()
        self.assertEqual((decision['status'], decision['phase']), ('vectoring', 'positioning'))
        # 15 nm north of a westward runway: first out to a base 10 nm east, 3 nm north of the centreline.
        self.assertEqual(decision['transmission'], 'Colt one one, Kolkhi, vectors for approach runway two seven. Turn left '
                                                   'heading one three zero, descend to four thousand feet, QFE two '
                                                   'niner point eight eight.')
        self.assertTrue(scenario.desk.arrival.vectoring)

    def test_instrument_weather_landing_request_gets_vectors(self):
        scenario = Scenario(language='en', mission_weather={'visibility_m': 2000})
        scenario.place(15 * 1852, 0, alt_m=1500, vn=-120, in_air=True)
        self.assertEqual(scenario.desk.request_landing()['status'], 'vectoring')

    def test_no_station_no_decision(self):
        scenario = Scenario()
        scenario.state.radio = radio(251.0)
        self.assertIn('error', scenario.desk.request_taxi())


@needs(CAUCASUS)
class OwnCalls(unittest.TestCase):
    def test_runway_incursion(self):
        scenario = Scenario(language='en')
        scenario.place(0, 0)
        self.assertEqual(scenario.desk.tick(),
                         'Colt one one, Kolkhi, you are not cleared to enter runway two seven, hold position.')
        self.assertIsNone(scenario.desk.tick())    # called once

    def test_unannounced_taxi(self):
        scenario = Scenario(language='en')
        calls = []
        for step in range(8):
            scenario.place(300, -50 * step, vn=0, ve=-10 * KT * 1.5)
            scenario.update(2.0)
            calls.append(scenario.desk.tick())
        self.assertIn('Colt one one, Kolkhi, I see you taxiing without a clearance, hold position and say intentions.',
                      calls)

    def test_no_call_while_busy(self):
        scenario = Scenario(language='en')
        scenario.place(0, 0)
        self.assertIsNone(scenario.desk.tick(busy=True))

    def test_departed_after_the_cleared_takeoff(self):
        scenario = Scenario(language='en')
        scenario.place(73, THRESHOLD_27_EAST_M - 10)
        scenario.desk.request_takeoff()
        scenario.place(0, -3000, alt_m=500, ve=-120, vu=10, in_air=True)
        self.assertIsNone(scenario.desk.tick())
        scenario.update(21)
        self.assertEqual(scenario.desk.tick(), 'Colt one one, Kolkhi, airborne, frequency change approved, good flight.')

    def test_landing_clearance_on_final_after_a_request(self):
        scenario = Scenario(language='en')
        scenario.place(0, THRESHOLD_27_EAST_M + 15 * 1852, alt_m=1200, ve=-100, in_air=True)
        self.assertEqual(scenario.desk.request_landing()['status'], 'continue')
        self.assertIsNone(scenario.desk.tick())            # 15 nm: not on final yet
        scenario.place(0, THRESHOLD_27_EAST_M + 5 * 1852, alt_m=500, ve=-100, in_air=True)
        self.assertEqual(scenario.desk.tick(),
                         'Colt one one, Kolkhi, wind two six four, one zero knots, runway two seven, cleared to land.')

    def test_go_around_when_the_runway_is_blocked(self):
        scenario = Scenario(language='en')
        scenario.place(0, THRESHOLD_27_EAST_M + 5 * 1852, alt_m=500, ve=-100, in_air=True)
        scenario.desk.request_landing()
        scenario.desk.tick()                               # cleared to land
        scenario.others = [aircraft('Uzi11', 0, 0, type_name='Su-25T')]
        scenario.place(0, THRESHOLD_27_EAST_M + 1.5 * 1852, alt_m=150, ve=-80, in_air=True)
        self.assertEqual(scenario.desk.tick(), 'Colt one one, Kolkhi, go around, traffic Su-25T on the runway.')

    def test_no_calls_off_the_tower_frequency(self):
        scenario = Scenario(language='en')
        scenario.state.radio = radio(251.0)
        scenario.place(0, 0)
        self.assertIsNone(scenario.desk.tick())


@needs(CAUCASUS)
class BeforeTheBrain(unittest.TestCase):
    def test_out_of_radio_range_nobody_answers(self):
        scenario = Scenario()
        scenario.place(100 * 1852, 0)
        self.assertEqual(scenario.desk.prefilter('Kolkhi, Colt 1-1, radio check'), '')

    def test_no_airfield_on_the_frequency(self):
        scenario = Scenario()
        scenario.state.radio = radio(251.0)
        self.assertEqual(scenario.desk.prefilter('radio check'), '')

    def test_wrong_station(self):
        scenario = Scenario(language='en')
        self.assertEqual(scenario.desk.prefilter('Kutaisi tower, Colt 1-1, request taxi'),
                         'Colt one one, Kolkhi, you are on Kolkhi frequency, not Kutaisi. Check frequency.')
        self.assertIsNone(scenario.desk.prefilter('Kolkhi, Colt 1-1, request taxi'))

    def test_channel_is_the_stations_own_frequency(self):
        # Live 2026-11-02: a Mi-8's R-863 reads 260.001 MHz; SRS hears the pilot's radio as 260.000.
        scenario = Scenario()
        scenario.state.radio = radio(261.001)
        channel = scenario.desk.channel({'id': 'atc', 'name': 'ATC', 'frequency_mhz': 0.0, 'modulation': 'AM'})
        self.assertEqual(channel['frequency_mhz'], 261.0)

    def test_channel_is_the_tuned_frequency(self):
        scenario = Scenario()
        channel = scenario.desk.channel({'id': 'atc', 'name': 'ATC', 'frequency_mhz': 0.0, 'modulation': 'AM'})
        self.assertEqual((channel['frequency_mhz'], channel['modulation'], channel['name']), (261.0, 'AM', 'Kolkhi'))


class NativeCommands(unittest.TestCase):
    """DCS's own ATC through the radio-menu bridge (client/dcs_bridge.py): released once per departure, Inbound at
    night for the runway lights."""

    def scenario(self, **kw) -> tuple[Scenario, list]:
        scenario, sent = Scenario(language='en', **kw), []
        scenario.desk.native = lambda command, airfield: sent.append((command, airfield))
        return scenario, sent

    def test_taxi_clearance_releases_dcs_once(self):
        scenario, sent = self.scenario()
        scenario.desk.request_taxi()
        scenario.desk.request_taxi()
        self.assertEqual(sent, [('abort_takeoff', 'Senaki-Kolkhi')])

    def test_engine_start_on_the_ramp_releases_dcs(self):
        scenario, sent = self.scenario()
        scenario.desk.tick()
        self.assertEqual(sent, [])                              # engines off
        scenario.state.flight = {**scenario.state.flight, 'rpm_left': 50.0}
        scenario.desk.tick()
        scenario.desk.tick()
        self.assertEqual(sent, [('abort_takeoff', 'Senaki-Kolkhi')])

    def test_waits_while_an_ai_aircraft_departs(self):
        """User's idea, 2026-11-04: an AI departure goes before DCS's arrivals anyway, so the abort waits for it."""
        scenario, sent = self.scenario()
        scenario.state.flight = {**scenario.state.flight, 'rpm_left': 50.0}
        scenario.others = [aircraft('Uzi21', -100, 300, ve=5.0, type_name='Su-25T')]   # taxiing at the field
        scenario.update()
        scenario.desk.tick()
        self.assertEqual(sent, [])
        scenario.others = [aircraft('Uzi21', 0, -3000, alt_m=400, ve=-90, in_air=True, type_name='Su-25T')]
        scenario.update()
        scenario.desk.tick()                                     # it is airborne: now the player's release
        self.assertEqual(sent, [('abort_takeoff', 'Senaki-Kolkhi')])

    def test_waits_for_a_scripted_wingman_starting_up(self):
        scenario, sent = self.scenario()
        scenario.state.flight = {**scenario.state.flight, 'rpm_left': 50.0}
        lat, lon = at(-150, 200)
        wingman = {'exists': True, 'alive': 1, 'lat': lat, 'lon': lon,
                   'air': {'active': True, 'in_air': False, 'started': 'uncontrolled', 'events': {'birth': 30.0}}}
        scenario.state.apply({'type': 'groups', 'groups': {'Viper': wingman}, 'zones': []})
        scenario.desk.tick()
        self.assertEqual(sent, [])                              # engines starting, not moving yet: still waits
        wingman['air']['events'] = {'takeoff': 2.0}
        scenario.state.apply({'type': 'groups', 'groups': {'Viper': wingman}, 'zones': []})
        scenario.desk.tick()
        self.assertEqual(sent, [('abort_takeoff', 'Senaki-Kolkhi')])

    def test_ai_taxiing_in_after_landing_is_no_departure(self):
        scenario, sent = self.scenario()
        scenario.state.flight = {**scenario.state.flight, 'rpm_left': 50.0}
        scenario.others = [aircraft('Uzi31', 0, 1500, alt_m=60, ve=-70, in_air=True, type_name='C-130')]
        scenario.update()
        scenario.desk.tick()                                     # seen on short final
        sent.clear()
        scenario.desk.dcs_departure_released = False
        scenario.others = [aircraft('Uzi31', 0, 200, ve=-6.0, type_name='C-130')]   # landed, taxiing in
        scenario.update()
        scenario.desk.tick()
        self.assertEqual(sent, [('abort_takeoff', 'Senaki-Kolkhi')])

    def test_next_departure_releases_again(self):
        scenario, sent = self.scenario()
        scenario.desk.request_taxi()
        scenario.place(0, -3000, alt_m=500, ve=-120, in_air=True)
        scenario.desk.tick()
        scenario.place(300, 0)                                  # landed, then parked for over two minutes
        scenario.desk.tick()
        self.assertEqual(scenario.desk.request_taxi()['kind'], 'after_landing')   # to parking: DCS not touched
        self.assertEqual(len(sent), 1)
        scenario.update(130)
        scenario.desk.tick()
        self.assertEqual(scenario.desk.request_taxi()['kind'], 'departure')
        self.assertEqual(len(sent), 2)

    def test_inbound_at_night_only(self):
        scenario, sent = self.scenario(mission_weather={'time_of_day_s': 22 * 3600})
        scenario.place(15 * 1852, 0, alt_m=1500, vn=-120, in_air=True)
        scenario.desk.request_landing()
        scenario.desk.request_landing()
        self.assertEqual(sent, [('inbound', 'Senaki-Kolkhi')])
        day, sent_by_day = self.scenario(mission_weather={'time_of_day_s': 12 * 3600})
        day.place(15 * 1852, 0, alt_m=1500, vn=-120, in_air=True)
        day.desk.request_landing()
        self.assertEqual(sent_by_day, [])

    def test_without_the_bridge_nothing_breaks(self):
        scenario = Scenario(language='en')
        self.assertEqual(scenario.desk.request_taxi()['status'], 'cleared')


@needs(CAUCASUS)
class GridNorth(unittest.TestCase):
    """Live 2026-11-02 at Senaki: DCS's runway course is relative to its map's grid north, about 7 degrees from true
    north there. Taken as true, the 27 end of the runway moved 120 m sideways, over the parking: the tower called a
    runway incursion on a parked aircraft and held its taxi request. The hook now sends both ends in lat/lon."""

    TRUE_HEADING = 281.7     # runway 27 in lat/lon (the takeoff roll's track)
    GRID_HEADING = 274.7     # what DCS's course says

    def senaki(self) -> Scenario:
        scenario = Scenario(language='en')
        h, half = math.radians(self.TRUE_HEADING), 1106
        start = at(-half * math.cos(h), -half * math.sin(h))     # threshold of 27 (takeoffs start there)
        end = at(half * math.cos(h), half * math.sin(h))
        runway = {'name': '27', 'course': -math.radians(self.GRID_HEADING), 'length_m': 2212, 'width_m': 60,
                  'lat': LAT, 'lon': LON, 'start_lat': start[0], 'start_lon': start[1], 'end_lat': end[0],
                  'end_lon': end[1]}
        scenario.state.weather['Senaki-Kolkhi']['runways'] = [runway]
        scenario.state.weather = dict(scenario.state.weather)   # a new message: the desk rebuilds its stations
        return scenario

    def point(self, along: float, across: float, heading: float) -> tuple[float, float]:
        """North/east metres for a point `along` the runway from its centre and `across` to its right."""
        h = math.radians(heading)
        return along * math.cos(h) - across * math.sin(h), along * math.sin(h) + across * math.cos(h)

    def test_true_heading_from_the_ends(self):
        field = self.senaki().desk.station_on(261e6).field
        self.assertAlmostEqual(field.runways[0].heading_true, self.TRUE_HEADING, 1)
        self.assertAlmostEqual(field.convergence, self.TRUE_HEADING - self.GRID_HEADING, 1)

    def test_parked_beside_the_runway_end_is_not_on_it(self):
        scenario = self.senaki()
        # 330 m from the 27 threshold, 135 m north of the real centreline (to the right looking west): within the
        # runway when the grid course was taken as true (the old bug; checked to fail with it).
        scenario.place(*self.point(-775, 135, self.TRUE_HEADING))
        self.assertIsNone(scenario.desk.tick())
        self.assertEqual(scenario.desk.request_taxi()['status'], 'cleared')

    def test_on_the_real_centreline_near_the_threshold(self):
        scenario = self.senaki()
        scenario.place(*self.point(-1050, 0, self.TRUE_HEADING))
        self.assertEqual(scenario.desk.tick(),
                         'Colt one one, Kolkhi, you are not cleared to enter runway two seven, hold position.')

    def test_velocities_turn_to_true(self):
        scenario = self.senaki()
        # Rolling along the runway: DCS says grid 274.7, the track is true 281.7.
        grid = math.radians(self.GRID_HEADING)
        scenario.place(0, 0, vn=60 * math.cos(grid), ve=60 * math.sin(grid), in_air=True, alt_m=200)
        self.assertAlmostEqual(scenario.desk.player().track_true, self.TRUE_HEADING, 1)


class Phrases(unittest.TestCase):
    def test_spoken_numbers(self):
        en = Speech('en')
        self.assertEqual(en.runway('27L'), 'runway two seven left')
        self.assertEqual(en.height(2500), 'two thousand five hundred')
        self.assertEqual(en.pressure('qfe', '29.87'), 'QFE two niner point eight seven')
        self.assertEqual(en.call('Colt 1-1', 'Kolkhi', 'vectors. turn left'), 'Colt one one, Kolkhi, vectors. Turn left.')
        self.assertEqual(en.callsign('101'), 'one zero one')

    def test_every_language_has_every_atc_phrase(self):
        en, es = Speech('en'), Speech('es')
        from shared.locale import load_locale
        own = load_locale('es')['atc']['phrases']
        self.assertEqual(set(own), set(en.phrases))
        for key, text in en.phrases.items():
            self.assertEqual(sorted(_fields(text)), sorted(_fields(es.phrases[key])), key)


def _fields(text: str) -> list[str]:
    import re
    return re.findall(r'\{(\w+)\}', text)


if __name__ == '__main__':
    unittest.main()
