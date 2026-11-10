"""Offline tests for the air controller (controller/desk.py, role "controller", e.g. Magic): its picture from its
sensors, the pilot's check-in, the task board fed by requests for air support on the net, the handoff with the unit's
frequency, need-to-know frequencies, its own calls and its radio horizon.

Run: python -m unittest discover tests
"""
import json
import time
import unittest

import dcs_data
import quiet_log  # noqa: F401  (the program's log goes to a temporary file)
from controller.desk import ControllerDesk
from controller.tools import ControllerToolBox
from instructor import geo
from shared.cockpit import CockpitState
from shared.net import Net, NetToolBox

BASE = (25.0, 55.0)
MAGIC = {'id': 'magic', 'name': 'Magic', 'role': 'controller', 'active': True, 'frequency_mhz': 270.0,
         'modulation': 'AM', 'controller': {'sensors': ['Magic AWACS'], 'name': 'Magic'}}
AXE = {'id': 'axe', 'name': 'Axe', 'role': 'vehicles', 'active': True, 'frequency_mhz': 256.0, 'modulation': 'AM'}
ARCHER = {'id': 'archer', 'name': 'Archer', 'role': 'artillery', 'active': True, 'frequency_mhz': 257.0,
          'modulation': 'AM'}
TRIDENT = {'id': 'trident', 'name': 'Trident', 'role': 'ships', 'active': True, 'frequency_mhz': 258.0,
           'modulation': 'AM', 'frequency_sharing': 'public'}


class FakeAgents:
    def __init__(self, *agents):
        self.agents = {a['id']: a for a in agents}

    def active(self):
        return [a for a in self.agents.values() if a.get('active')]

    def get(self, agent_id):
        return self.agents.get(agent_id)


def at(north_m: float, east_m: float = 0.0) -> tuple[float, float]:
    lat, lon = geo.offset_point(BASE[0], BASE[1], 0.0, north_m)
    return geo.offset_point(lat, lon, 90.0, east_m)


def contact(unit, north_m, east_m=0.0, side='enemy', group=None, alt_m=6000.0, vx=0.0, vz=0.0, player=False):
    lat, lon = at(north_m, east_m)
    found = {'unit': unit, 'lat': lat, 'lon': lon, 'alt_m': alt_m, 'vx': vx, 'vz': vz, 'side': side, 'kind': 'air',
             'track_group': group or unit}
    if player:
        found['player'] = True
    return found


def report(state, contacts=(), emitters=(), awacs_alive=1, awacs_alt_m=9000.0, pilot_north_m=0.0, pilot_alt_m=5000.0,
           pilot_in_air=True):
    lat, lon = at(-100000)   # the AWACS 100 km south of the pilot's area
    state.apply({'type': 'groups', 'zones': [], 'groups': {
        'Axe': {'exists': True, 'alive': 2, 'lat': BASE[0], 'lon': BASE[1]},
        'sensor:Magic AWACS': {'exists': True, 'alive': awacs_alive, 'size': 1, 'lat': lat, 'lon': lon,
                               'alt_m': awacs_alt_m,
                               'sensor': {'type': 'E-3A', 'in_air': True,
                                          'radar': {'range_km': 400, 'contacts': list(contacts)},
                                          'emitters': list(emitters)}}}})
    p_lat, p_lon = at(pilot_north_m)
    state.traffic = [{'player': True, 'lat': p_lat, 'lon': p_lon, 'alt_m': pilot_alt_m, 'in_air': pilot_in_air}]
    state.traffic_at = time.monotonic()


def scenario(*agents):
    state = CockpitState(aircraft='FA-18C_hornet', callsign='Colt 1-1')
    desk = ControllerDesk(state, MAGIC, agents=FakeAgents(MAGIC, AXE, ARCHER, TRIDENT, *agents))
    return state, desk


class Sensors(unittest.TestCase):
    def test_sensors_are_kept_apart_from_the_units(self):
        state, desk = scenario()
        report(state)
        self.assertIn('Magic AWACS', state.sensors)
        self.assertNotIn('sensor:Magic AWACS', state.groups)
        self.assertIn('Axe', state.groups)
        self.assertEqual(desk.situation()['your_sensors'][0]['state'], 'airborne')

    def test_the_picture_in_braa_from_the_pilot_formations_as_one_track(self):
        state, desk = scenario()
        report(state, [contact('me', 0, player=True, side='friend'),
                       contact('bandit 1', 30 * 1852, group='Red CAP', vx=-200.0),   # 30 nm north, flying south
                       contact('bandit 2', 30 * 1852 + 500, group='Red CAP', vx=-200.0),
                       contact('friend', -10 * 1852, side='friend')])
        picture = desk.situation()['picture']
        self.assertIn('BRAA', picture['bearings'])
        hostile = [t for t in picture['tracks'] if t['side'] == 'hostile']
        self.assertEqual(len(hostile), 1)
        self.assertEqual((hostile[0]['count'], hostile[0]['bearing_deg'], hostile[0]['range_nm'], hostile[0]['aspect']),
                         (2, 0, 30, 'hot'))
        self.assertNotIn('Red CAP', json.dumps(picture))   # a radar does not read names
        self.assertEqual(len(picture['tracks']), 2)        # the pilot is not a track of his own

    def test_pilot_not_on_radar_bearings_from_the_sensor(self):
        state, desk = scenario()
        report(state, [contact('bandit', 30 * 1852)])
        situation = desk.situation()
        self.assertFalse(situation['pilot']['on_your_radar'])
        self.assertIn('not on your radar', situation['picture']['bearings'])
        self.assertNotIn('aspect', situation['picture']['tracks'][0])

    def test_a_sam_is_known_only_while_its_radar_is_heard(self):
        state, desk = scenario()
        lat, lon = at(20 * 1852, 5000)
        sam = {'type': 'Kub 1S91 str', 'group': 'SA-6 site', 'lat': lat, 'lon': lon, 'tracking': False}
        report(state, [contact('me', 0, player=True, side='friend')], [sam])
        heard = desk.situation()['enemy_radars_heard']
        self.assertEqual((heard[0]['radar_of'], heard[0]['emitting_now'], heard[0]['range_nm']),
                         ('Kub 1S91 str', True, 20))
        self.assertNotIn('SA-6 site', json.dumps(heard))
        report(state, [contact('me', 0, player=True, side='friend')], [])   # it went silent: remembered
        self.assertIn('enemy_radars_heard', desk.situation())

    def test_a_lost_sensor_is_called_and_the_pilot_unheard_beyond_the_horizon(self):
        state, desk = scenario()
        report(state)
        desk.check_in('2 GBU-12')
        desk.tick()
        self.assertIsNone(desk.prefilter('Magic, Colt'))
        report(state, awacs_alive=0)
        desk.tick()
        self.assertEqual(desk.reports(), [])   # not at once: leaving the mission looks the same
        desk.lost_since['Magic AWACS'] -= 10
        desk.tick()
        self.assertIn('is lost', desk.reports()[0]['report'])
        self.assertEqual(desk.prefilter('Magic, Colt'), '')   # nobody left to hear him

    def test_radio_horizon_from_the_awacs(self):
        state, desk = scenario()
        report(state, pilot_alt_m=5000.0)
        self.assertTrue(desk.radio_link()['pilot_hears'])          # 100 km, both high
        report(state, pilot_alt_m=30.0, pilot_north_m=500000.0)   # 600 km away, low
        self.assertFalse(desk.radio_link()['pilot_hears'])
        self.assertEqual(desk.prefilter('Magic'), '')


class Board(unittest.TestCase):
    def request(self, desk, urgent=False):
        lat, lon = at(15 * 1852)
        return desk.receive_target({'net': 'message', 'from': 'axe', 'from_name': 'Axe',
                                    'target': {'kind': 'air_support', 'lat': lat, 'lon': lon,
                                               'what': 'four tanks advancing', 'urgent': urgent}})

    def test_check_in_only_airborne_and_with_a_loadout(self):
        state, desk = scenario()
        report(state, pilot_in_air=False)
        self.assertFalse(desk.check_in('2 GBU-12')['ok'])
        report(state)
        self.assertFalse(desk.check_in('')['ok'])
        self.assertTrue(desk.check_in('2 GBU-12, gun', '6000 lb')['ok'])
        self.assertEqual(desk.situation()['pilot']['checked_in']['loadout'], '2 GBU-12, gun')

    def test_request_assign_and_handoff_with_the_units_frequency(self):
        state, desk = scenario()
        report(state, [contact('me', 0, player=True, side='friend')])
        task_id = self.request(desk)
        self.assertEqual(task_id, 'T1')
        self.assertIn('task T1', desk.target_words({'from_name': 'Axe'}, task_id))
        self.assertFalse(desk.assign_task('T1')['ok'])   # not checked in yet
        desk.check_in('2 GBU-12')
        desk.tick()
        self.assertIn('T1 is open', desk.reports()[0]['report'])   # offered by a call of its own
        result = desk.assign_task('t1')
        self.assertTrue(result['ok'])
        self.assertIn('bearing 000, 15 nautical miles', result['detail'])
        self.assertIn('contact Axe on 256.000 AM', result['detail'])
        self.assertIn('grid ', result['detail'])
        self.assertEqual(desk.situation()['pilot']['task']['id'], 'T1')
        self.assertTrue(desk.update_task('T1', 'done', '2 tanks destroyed')['ok'])
        self.assertEqual(desk.situation()['task_board'], 'empty')

    def test_a_refused_task_goes_back_on_the_board(self):
        state, desk = scenario()
        report(state)
        self.request(desk)
        desk.check_in('AIM-120 only')
        desk.assign_task('T1')
        desk.update_task('T1', 'refused', 'no air-to-ground weapons')
        self.assertIsNone(desk.pilot_task())
        self.assertEqual(desk.situation()['task_board'][0]['status'], 'refused')
        self.assertFalse(desk.update_task('T9', 'done')['ok'])
        self.assertFalse(desk.update_task('T1', 'finished')['ok'])

    def test_frequencies_need_to_know(self):
        state, desk = scenario()
        report(state)
        desk.check_in('2 GBU-12')
        self.assertFalse(desk.give_frequency('Archer')['ok'])     # nothing to do with him
        self.assertTrue(desk.give_frequency('Trident')['ok'])     # its sharing is public
        self.assertFalse(desk.give_frequency('Nobody')['ok'])
        self.request(desk)
        desk.assign_task('T1')
        self.assertIn('256.000', desk.give_frequency('axe')['detail'])   # part of his task

    def test_mission_tasks_from_agent_json(self):
        state = CockpitState(callsign='Colt 1-1')
        agent = {**MAGIC, 'controller': {**MAGIC['controller'],
                                         'tasks': [{'kind': 'CAP', 'what': 'CAP over the carrier', 'priority': 'high'}]}}
        desk = ControllerDesk(state, agent, agents=FakeAgents(agent))
        desk.configure(agent)   # a GUI change does not add it twice
        self.assertEqual([(t['id'], t['kind'], t['priority']) for t in desk.board], [('T1', 'CAP', 'high')])


class Calls(unittest.TestCase):
    def test_the_picture_is_given_at_check_in(self):
        """Flown 2026-11-08: two hostile tracks and an SA-10 radar on the scope, the check-in said only "no tasks"."""
        state, desk = scenario()
        report(state, [contact('me', 0, player=True, side='friend'), contact('bandit', 80 * 1852, vx=-200.0)],
               [{'type': 'S-300PS 40B6MD sr', 'group': 'SA-10', 'lat': at(100 * 1852)[0], 'lon': BASE[1]}])
        detail = desk.check_in('2 AIM-120')['detail']
        self.assertIn('Give him the picture now', detail)
        self.assertIn('1 hostile track(s), 1 enemy radar(s) heard', detail)
        desk.tick()
        self.assertEqual(desk.reports(), [])   # what was told at check-in is not called again as new

    def test_new_groups_at_any_range_threats_when_close_radars_once(self):
        state, desk = scenario()
        me = contact('me', 0, player=True, side='friend')
        report(state, [me])
        desk.tick()
        self.assertEqual(desk.reports(), [])   # nobody checked in: nobody to call
        desk.check_in('2 AIM-120')
        sam = {'type': 'Kub 1S91 str', 'group': 'SA-6 site', 'lat': at(90 * 1852)[0], 'lon': BASE[1]}
        report(state, [me, contact('bandit', 90 * 1852, vx=-200.0, group='Red CAP')], [sam])
        desk.tick()
        calls = desk.reports()[0]['report']
        self.assertIn('new group on your radar: hostile track', calls)   # 90 nm away: called anyway
        self.assertIn('Kub 1S91 str', calls)
        desk.tick()
        self.assertEqual(desk.reports(), [])   # not again
        report(state, [me, contact('bandit', 30 * 1852, vx=-200.0, group='Red CAP')], [sam])
        desk.tick()
        self.assertIn('THREAT: hostile track', desk.reports()[0]['report'])   # closing in: the threat call
        desk.tick()
        self.assertEqual(desk.reports(), [])

    def test_nato_names_from_dcs_table(self):
        from shared import reporting_names
        names = reporting_names.parse('    ["S-300PS 40B6MD sr"] =             _("pAi:SA-10 Clam Shell radar"),\n'
                                      '    ["Kub 1S91 str"] = _("pAi:SA-6 Straight Flush radar"),')
        self.assertEqual(names['S-300PS 40B6MD sr'], 'SA-10 Clam Shell radar')

        class Names:
            def name(self, type_name):
                return names.get(type_name, type_name)

        state = CockpitState(callsign='Colt 1-1')
        desk = ControllerDesk(state, MAGIC, agents=FakeAgents(MAGIC), names=Names())
        report(state, [], [{'type': 'Kub 1S91 str', 'group': 'SA-6 site', 'lat': BASE[0], 'lon': BASE[1]}])
        self.assertEqual(desk.situation()['enemy_radars_heard'][0]['radar_of'], 'SA-6 Straight Flush radar')

    def test_reporting_names_from_the_real_install(self):
        from pathlib import Path
        from shared.reporting_names import ReportingNames, TABLE
        if not (dcs_data.DCS / TABLE).exists():
            self.skipTest('needs a DCS install')
        self.assertEqual(ReportingNames(dcs_data.DCS).name('S-300PS 40B6MD sr'), 'SA-10 Clam Shell radar')
        self.assertEqual(ReportingNames(dcs_data.DCS).name('No such type'), 'No such type')

    def test_the_toolbox_has_no_tool_for_its_sensors(self):
        state, desk = scenario()
        box = ControllerToolBox(desk)
        names = [d['name'] for d in box.current()]
        self.assertEqual(names, ['check_in', 'assign_task', 'update_task', 'assign_station', 'give_frequency'])
        report(state)
        answer = json.loads(box.call('controller.check_in', json.dumps({'loadout': '2 GBU-12'})))
        self.assertTrue(answer['ok'])


def zone(name, north_m, east_m=0.0, mark=False):
    lat, lon = at(north_m, east_m)
    found = {'name': name, 'lat': lat, 'lon': lon, 'radius_m': 3000}
    if mark:
        found['mark'] = True
    return found


class Stations(unittest.TestCase):
    """User, 2026-11-08 (flown): after the task the pilot was left with nothing; send him to a station, routed."""

    def setUp(self):
        self.state, self.desk = scenario()
        report(self.state, [contact('me', 0, player=True, side='friend')])
        nm = 1852
        self.state.zones = [zone('CAP North', 40 * nm), zone('CAP West', 20 * nm, -40 * nm),
                            zone('CAP Deep', 70 * nm), zone('Point Bravo', 30 * nm, 50 * nm),
                            zone('Target area', 15 * nm), zone('my mark', 5 * nm, mark=True)]

    def test_after_the_task_he_is_sent_to_hold(self):
        self.desk.check_in('2 GBU-12')
        lat, lon = at(15 * 1852)
        self.desk.receive_target({'from': 'axe', 'from_name': 'Axe',
                                  'target': {'kind': 'air_support', 'lat': lat, 'lon': lon, 'what': 'tanks'}})
        self.desk.assign_task('T1')
        self.assertIn('T1', self.desk.assign_station()['detail'])           # not while he has a task
        detail = self.desk.update_task('T1', 'done', '2 tanks')['detail']
        self.assertIn('assign_station', detail)
        result = self.desk.assign_station(altitude_ft=20000)
        self.assertTrue(result['ok'])
        self.assertIn('hold at CAP North: bearing 000, 40 nautical miles', result['detail'])   # the nearest station
        self.assertIn('angels 20', result['detail'])
        self.assertEqual(self.desk.situation()['pilot']['holding_at']['name'], 'CAP North')
        names = [s['name'] for s in self.desk.situation()['stations']]
        self.assertEqual(names, ['CAP North', 'CAP West', 'CAP Deep'])      # named so; not targets or marks

    def test_a_station_by_name_and_the_route_around_a_sam(self):
        self.desk.check_in('2 AIM-120')
        sam = {'type': 'Kub 1S91 str', 'group': 'SA-6', 'lat': at(30 * 1852)[0], 'lon': BASE[1]}
        zones = self.state.zones
        report(self.state, [contact('me', 0, player=True, side='friend')], [sam])
        self.state.zones = zones
        self.desk.remember_emitters()
        result = self.desk.assign_station('cap deep')
        self.assertIn('hold at CAP Deep', result['detail'])
        self.assertIn('passes within 25 nm of the enemy Kub 1S91 str', result['detail'])
        self.assertIn('route him via Point Bravo (bearing 059, 58 nm from him)', result['detail'])
        self.assertNotIn('the point itself', result['detail'])
        self.assertIn('the point itself is within', self.desk.assign_station('CAP North')['detail'])
        self.assertFalse(self.desk.assign_station('Nowhere')['ok'])
        auto = self.desk.assign_station()['detail']   # CAP North is 20 nm from the SAM: the nearest clear of it
        self.assertIn('CAP West', auto)
        self.assertNotIn('passes within', auto)

    def test_no_station_in_the_mission_a_holding_point_towards_the_sensor(self):
        self.state.zones = []
        self.desk.check_in('gun')
        detail = self.desk.assign_station()['detail']
        self.assertIn('hold at a holding point: bearing 180, 20 nautical miles', detail)   # the AWACS is south
        self.assertIn('grid ', detail)

    def test_stations_named_in_agent_json(self):
        agent = {**MAGIC, 'controller': {**MAGIC['controller'], 'stations': ['Point Bravo']}}
        self.desk.configure(agent)
        self.assertEqual([z['name'] for z in self.desk.stations()], ['Point Bravo'])

    def test_reached(self):
        self.desk.check_in('gun')
        self.desk.assign_station('my mark')
        zones = self.state.zones
        report(self.state, [contact('me', 5 * 1852, player=True, side='friend')], pilot_north_m=5 * 1852)
        self.state.zones = zones
        self.desk.tick()
        self.assertTrue(self.desk.situation()['pilot']['holding_at']['reached'])


class Eyes:
    """A unit's desk with eyes (target_point), as squad/desk.py has."""

    def target_point(self, target, offset_m=0, toward=''):
        if target != 'T-72 group':
            return 'no such enemy'
        return (25.1, 55.1), (25.0, 55.0)


class RequestOnTheNet(unittest.TestCase):
    def test_a_unit_asks_the_controller_with_its_own_sighting(self):
        delivered = []
        net = Net(FakeAgents(MAGIC, AXE, ARCHER), deliver=lambda to, event: delivered.append((to, event)))
        box = NetToolBox(None, net, 'axe', Eyes())
        names = [d['name'] for d in box.definitions()]
        self.assertIn('request_air_support', names)
        result = json.loads(box.call('net.request_air_support', json.dumps(
            {'controller': 'Magic', 'target': 'T-72 group', 'description': 'four tanks advancing', 'urgent': True})))
        self.assertTrue(result['ok'])
        to, event = delivered[0]
        self.assertEqual(to, 'magic')
        self.assertEqual((event['target']['kind'], event['target']['lat'], event['target']['urgent']),
                         ('air_support', 25.1, True))
        self.assertFalse(box.request_air_support('Archer', 'T-72 group', 'x')['ok'])   # not a controller
        self.assertFalse(box.request_air_support('Magic', 'nothing', 'x')['ok'])
        desk = ControllerDesk(CockpitState(), MAGIC, agents=FakeAgents(MAGIC, AXE))
        self.assertEqual(desk.receive_target(event), 'T1')
        self.assertEqual(desk.board[0]['priority'], 'high')

    def test_no_controller_no_tool(self):
        net = Net(FakeAgents(AXE, ARCHER))
        box = NetToolBox(None, net, 'axe', Eyes())
        self.assertNotIn('request_air_support', [d['name'] for d in box.definitions()])


if __name__ == '__main__':
    unittest.main()
