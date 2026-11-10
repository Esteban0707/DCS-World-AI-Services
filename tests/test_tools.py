"""Offline tests for what the tower instructor knows (tower_view.py) and its tools: map geometry, the tower's
charts, the window view, and that nothing from the cockpit or the aircraft's own instruments gets through.

Run: python -m unittest discover tests
"""
import json
import math
import unittest
from pathlib import Path

from instructor import geo, weather
from instructor.field_position import position_on_field
from instructor.phase import PhaseTracker
from instructor.tools import ToolBox
from instructor.tower_view import TowerView
from shared.cockpit import CockpitState
from shared.cockpit_summary import CockpitSummary
from tools.build_positions import entries_for

ROOT = Path(__file__).resolve().parent.parent
HAS_MAPS = (ROOT / 'terrains' / 'Caucasus' / 'navigation.json').exists()
# On the Batumi runway (Caucasus), from the airfield's own beacons in terrains/Caucasus/navigation.json.
BATUMI = (41.6073, 41.6043)
SENAKI = (42.24069, 42.04917)
GEAR = {'id': 'GEAR', 'hint': 'Landing Gear Lever, LG UP/LG DOWN', 'arg': [83, 83], 'arg_lim': [[0, 1], [0, 1]],
        'arg_value': [1, -1], 'class': ['class_type.TUMB'] * 2}
BATTERY = {'id': 'BATT', 'hint': 'Battery Switch, BATT/OFF', 'arg': [387, 387], 'arg_lim': [[0, 1], [0, 1]],
           'arg_value': [1, -1], 'class': ['class_type.TUMB'] * 2}
COCKPIT_ONLY = ('indicated_airspeed', 'angle_of_attack', 'rpm', 'Battery', 'altitude', 'heading', 'lat', 'lon')


def state_at(lat, lon, agl_m=1.5, **extra):
    state = CockpitState()
    state.apply({'type': 'update', 'time': 1.0, 'flight': {
        'aircraft': 'F-5E-3', 'lat': lat, 'lon': lon, 'alt_agl_m': agl_m, 'ias_ms': 80.0, 'aoa_deg': 7.0,
        'heading_deg': 300.0, 'rpm_left': 88.0, **extra}})
    return state


def summary_with(values):
    summary = CockpitSummary({e['arg']: e for c in (GEAR, BATTERY) for e in entries_for(c)})
    summary.update(values)
    return summary


class Geography(unittest.TestCase):
    def test_distance_and_bearing(self):
        self.assertAlmostEqual(geo.distance_nm(0, 0, 1, 0), 60.04, places=1)   # one degree of latitude
        self.assertAlmostEqual(geo.bearing_deg(0, 0, 0, 1), 90, places=3)
        self.assertEqual(geo.cardinal(200), 'south')

    @unittest.skipUnless(HAS_MAPS, 'run tools/build_terrains.py')
    def test_locate_on_caucasus(self):   # for radar positions (next step)
        nearest = geo.locate(*BATUMI)['nearest_airfields'][0]
        self.assertEqual((nearest['name'], nearest['tacan']), ('Batumi', ['16X']))
        south = geo.locate(BATUMI[0] - 10 / 60, BATUMI[1])['nearest_airfields'][0]
        self.assertTrue(south['aircraft_seen_from_airfield'].startswith('south of the field'))

    @unittest.skipUnless(HAS_MAPS, 'run tools/build_terrains.py')
    def test_airfield_chart_by_name(self):
        chart = geo.airfield_info('kobuleti', 'Caucasus')
        self.assertEqual(chart['name'], 'Kobuleti')
        self.assertNotIn('distance_nm', chart)          # chart data only, no radar
        self.assertIn('error', geo.airfield_info('Nowhere Field', 'Caucasus'))


@unittest.skipUnless(HAS_MAPS, 'run tools/build_terrains.py')
class Tower(unittest.TestCase):
    def test_each_message_carries_only_the_situation(self):
        summary = summary_with({'83': 0, '387': 1})
        state = state_at(*BATUMI)
        state.callsign = 'Enfield 1-1'
        situation = TowerView(state, lambda: summary).situation()
        self.assertEqual(situation, {'aircraft': 'F-5E-3', 'callsign': 'Enfield 1-1',
                                     'tower': {'airfield': 'Batumi', 'map': 'Caucasus'}, 'phase': 'on the ground'})

    def test_on_the_field_the_window_shows_outside_configuration_only(self):
        summary = summary_with({'83': 0, '387': 1})       # gear lever down, battery on (inside the cockpit)
        report = TowerView(state_at(*BATUMI), lambda: summary).report()
        self.assertEqual(report['tower'], 'Batumi')
        self.assertEqual(report['window'], {'in_sight': True, 'on_ground': True,
                                            'configuration': {'landing gear': 'LG DOWN'}})
        self.assertEqual(report['radar'], {'status': 'no radar data from the mission'})
        text = json.dumps(report)
        for word in COCKPIT_ONLY:
            self.assertNotIn(word, text)

    def test_out_of_sight_nothing_is_seen(self):
        view = TowerView(state_at(*BATUMI))
        view.situation()                                  # departure field chosen on the runway
        view.state.apply({'type': 'update', 'time': 60.0, 'flight': {
            'aircraft': 'F-5E-3', 'lat': BATUMI[0] - 10 / 60, 'lon': BATUMI[1], 'alt_agl_m': 900.0}})
        self.assertEqual(view.situation()['tower']['airfield'], 'Batumi')   # still the departure field
        self.assertEqual(view.report()['window'], {'in_sight': False})

    def radar_view(self, airfields, start=SENAKI):
        view = TowerView(state_at(*start))
        view.state.apply({'type': 'radar', 'airfields': airfields})
        return view

    def test_radar_named_after_the_tower_gives_the_track(self):
        # 10 nm north of Senaki at 3000 ft, flying east at 300 kt, climbing 1000 fpm.
        own = {'unit': 'Aerial-1-1', 'type': 'F-5E-3', 'player': True, 'lat': SENAKI[0] + 10 / 60, 'lon': SENAKI[1],
               'alt_m': 914.4, 'vx': 0.0, 'vz': 154.3, 'vy': 5.08}
        other = {'unit': 'Su-25', 'type': 'Su-25T', 'callsign': 'Uzi11', 'lat': SENAKI[0], 'lon': SENAKI[1] + 0.2,
                 'alt_m': 1500.0, 'vx': 100.0, 'vz': 0.0}
        far = {**other, 'unit': 'far', 'lat': SENAKI[0] + 2}
        radar = self.radar_view({'Senaki-Kolkhi': {'radars': ['Senaki-Kolkhi-1'],
                                                   'contacts': [own, other, far]}}).report()['radar']
        self.assertEqual(radar['status'], 'contact')
        self.assertEqual(radar['position'], '10.0 nm north of the field, bearing 0')
        self.assertEqual((radar['altitude_ft'], radar['ground_speed_kt'], radar['track_deg'],
                          radar['vertical_speed_fpm']), (3000, 300, 90, 1000))
        self.assertEqual([(t['callsign'], t['altitude_ft']) for t in radar['traffic']], [('Uzi11', 4920)])

    def test_radar_states(self):
        senaki = {'radars': ['Senaki-Kolkhi-1'], 'contacts': []}
        self.assertEqual(self.radar_view({'Senaki-Kolkhi': senaki}).radar(), {'status': 'no radar contact'})
        self.assertEqual(self.radar_view({'Kutaisi': senaki}).radar(), {'status': 'the tower has no radar'})
        self.assertEqual(self.radar_view({'Senaki-Kolkhi': {'radars': [], 'contacts': []}}).radar(),
                         {'status': 'radar out of service'})
        self.assertEqual(self.radar_view([]).radar(), {'status': 'the tower has no radar'})   # empty Lua table
        view = self.radar_view({'Senaki-Kolkhi': senaki})
        view.state.radar_at -= 10
        self.assertEqual(view.radar(), {'status': 'no radar data from the mission'})       # the hook stopped

    def test_toolbox_is_the_charts_of_the_tower_map(self):
        box = ToolBox(TowerView(state_at(*BATUMI)))
        self.assertEqual([t['type'] for t in box.tools()], ['namespace'])
        self.assertEqual([d['name'] for d in box.tools()[0]['tools']],
                         ['get_tower_view', 'get_weather', 'get_airfield', 'search_library', 'read_section',
                          'library_contents'])
        self.assertEqual(json.loads(box.call('tower.get_weather', '{}')), {'error': 'no weather data from the mission'})
        self.assertEqual(json.loads(box.call('tower.get_airfield', '{"name": "Kobuleti"}'))['map'], 'Caucasus')
        self.assertEqual(json.loads(box.call('tower.get_tower_view', '{}'))['window']['on_ground'], True)
        self.assertIn('error', json.loads(box.call('get_flight_data', '{}')))
        self.assertIn('error', json.loads(box.call('get_airfield', '{"wrong": 1}')))


class Weather(unittest.TestCase):
    # Senaki-Kolkhi as the hook reports it: runway "09" true heading 90 (course -pi/2), elevation 13 m.
    FIELD = {'wind_north': 0.0, 'wind_east': 5.0, 'temperature_k': 288.15, 'pressure_pa': 101200.0,
             'elevation_m': 13.0, 'runways': [{'name': '09', 'course': -math.pi / 2, 'length_m': 2400}]}

    def test_dcs_conventions(self):
        direction, speed = weather.wind_from(0.0, 5.0)       # air moving east: wind from the west
        self.assertEqual((round(direction), round(speed, 1)), (270, 9.7))
        self.assertEqual(weather.runway_ends([{'name': '09', 'course': -math.pi / 2}]), [('09', 90.0), ('27', 270.0)])
        self.assertEqual([e[0] for e in weather.runway_ends([{'name': '13L', 'course': 0.0}])], ['13L', '31R'])
        self.assertAlmostEqual(weather.qnh_pa(101325, 0), 101325)

    def test_tower_report(self):
        report = weather.report(self.FIELD, variation=6.0)
        self.assertEqual(report['wind'], 'from 264 magnetic at 10 kt')
        self.assertEqual(report['runway_in_use'], {'runway': '27', 'heading_magnetic': 264, 'headwind_kt': 10,
                                                   'crosswind_kt': 0})
        self.assertEqual(report['qnh'], '1014 hPa, 29.93 inHg')
        self.assertEqual((report['temperature_c'], report['runways']), (15, ['09', '27']))
        self.assertEqual(weather.report(self.FIELD, None)['wind'], 'from 270 true at 10 kt')
        self.assertEqual(weather.report({**self.FIELD, 'wind_east': 0.5}, None)['wind'], 'calm')

    def test_runway_kept_until_the_tailwind_is_too_strong(self):
        ends = [('09', 90.0), ('27', 270.0)]
        self.assertEqual(weather.runway_in_use(ends, 90.0, 4.0, current='27')[0], '27')    # 4 kt tailwind: kept
        self.assertEqual(weather.runway_in_use(ends, 90.0, 8.0, current='27')[0], '09')    # 8 kt: changed

    @unittest.skipUnless(HAS_MAPS, 'run tools/build_terrains.py')
    def test_the_towers_own_field(self):
        state = state_at(*SENAKI, heading_deg=100.0, mag_heading_deg=94.0)
        state.apply({'type': 'weather', 'airfields': {'Senaki-Kolkhi': self.FIELD, 'Kutaisi': {}}})
        report = TowerView(state).weather()
        self.assertEqual((report['airfield'], report['wind']), ('Senaki-Kolkhi', 'from 264 magnetic at 10 kt'))


class FieldPosition(unittest.TestCase):
    """Runway 09/27, 2400 x 45 m, centre at SENAKI, as the hook reports it (course = minus the true heading of 09)."""
    RUNWAY = {'name': '09', 'course': -math.pi / 2, 'length_m': 2400, 'width_m': 45,
              'lat': SENAKI[0], 'lon': SENAKI[1]}

    def at(self, east_m, north_m=0.0):
        lon = SENAKI[1] + east_m / (111_320 * math.cos(math.radians(SENAKI[0])))
        return position_on_field(SENAKI[0] + north_m / 111_320, lon, [self.RUNWAY])

    def test_where_on_the_field(self):
        self.assertEqual(self.at(-1150), 'on runway 09 near its threshold')        # west end: takeoffs on 09
        self.assertEqual(self.at(1150), 'on runway 27 near its threshold')         # east end: takeoffs on 27
        self.assertEqual(self.at(0), 'on runway 09/27, between its ends')
        self.assertEqual(self.at(1100, north_m=90),
                         'near the threshold of runway 27, off the runway (holding point area)')
        self.assertEqual(self.at(0, north_m=300), 'elsewhere on the field (apron or taxiway)')
        self.assertIsNone(position_on_field(SENAKI[0], SENAKI[1], [{'name': '09', 'course': 0.0}]))

    @unittest.skipUnless(HAS_MAPS, 'run tools/build_terrains.py')
    def test_the_window_shows_it(self):
        state = state_at(*SENAKI)
        state.apply({'type': 'weather', 'airfields': {'Senaki-Kolkhi': {'runways': [self.RUNWAY]}}})
        view = TowerView(state)
        view.on_flight()
        self.assertEqual(view.report()['window']['where_on_the_field'], 'on runway 09/27, between its ends')


class Phase(unittest.TestCase):
    """The phase as the tower judges it, from position changes (not airspeed: wind moves a parked aircraft's IAS)."""
    TOWER = {'name': 'Batumi', 'lat': BATUMI[0], 'lon': BATUMI[1]}

    def run_at(self, tracker, knots, agl_m=1.5, seconds=3, start=0.0, vvi_ms=0.0, lat0=BATUMI[0]):
        """Moves north at `knots` for `seconds` of model time; returns the last flight dict."""
        for step in range(seconds * 5 + 1):
            t = start + step * 0.2
            flight = {'lat': lat0 + knots * t / 3600 / 60, 'lon': BATUMI[1], 'alt_agl_m': agl_m, 'vvi_ms': vvi_ms,
                      'ias_ms': 9.0}
            tracker.update(flight, t)
        return flight

    def test_a_whole_flight(self):
        tracker = PhaseTracker()
        self.assertEqual(tracker.phase({'alt_agl_m': 1.5}, self.TOWER), 'on the ground')   # no speed yet
        self.assertEqual(tracker.phase(self.run_at(tracker, 0), self.TOWER), 'parked')     # IAS 17 kt from wind
        self.assertEqual(tracker.phase(self.run_at(tracker, 15, start=10), self.TOWER), 'taxiing')
        holding = self.run_at(tracker, 0, start=15, lat0=BATUMI[0] + 0.001)
        self.assertEqual(tracker.phase(holding, self.TOWER), 'stopped after taxiing')      # not "parked"
        self.assertEqual(tracker.phase(self.run_at(tracker, 120, start=20), self.TOWER), 'takeoff roll')
        self.assertEqual(tracker.phase(self.run_at(tracker, 250, agl_m=900, start=30), self.TOWER), 'airborne')
        approach = self.run_at(tracker, 160, agl_m=450, start=40, vvi_ms=-4.0, lat0=BATUMI[0] - 8 / 60)
        self.assertEqual(tracker.phase(approach, self.TOWER), 'approach')
        self.assertEqual(tracker.phase(self.run_at(tracker, 130, start=50), self.TOWER), 'landing roll')
        self.assertEqual(tracker.phase(self.run_at(tracker, 12, start=60), self.TOWER), 'taxiing in after landing')
        self.assertEqual(tracker.phase(self.run_at(tracker, 0, start=70), self.TOWER), 'parked after landing')

    def test_a_new_mission_starts_over(self):
        tracker = PhaseTracker()
        self.run_at(tracker, 250, agl_m=900, start=100)
        self.assertEqual(tracker.phase(self.run_at(tracker, 0, start=0), self.TOWER), 'parked')   # model time back


if __name__ == '__main__':
    unittest.main()
