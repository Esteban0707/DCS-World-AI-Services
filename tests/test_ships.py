"""Offline tests for the ships agent (ships/, user 2026-11-07): sailing at a speed in knots, the navigation aids as the
Mission Editor's naval commands (TACAN frequency as its calcTACANFrequencyMHz), a carrier turning into the wind, the
artillery's fire missions, and calls for fire reaching a ship. DCS is played by the squad tests' fake.

Run: python -m unittest discover tests
"""
import json
import unittest

import quiet_log  # noqa: F401  (the program's log goes to a temporary file)
from shared import coords
from shared.cockpit import CockpitState
from shared.net import Net, NetToolBox
from ships.desk import ShipDesk, tacan_mhz
from ships.tools import ShipToolBox
from tests.test_artillery import AXE, metres
from tests.test_net import FakeAgents
from tests.test_squad import FakeDcs, at
from vehicles.desk import VehicleDesk

MOTHER = {'id': 'mother', 'name': 'Mother', 'role': 'ships', 'active': True,
          'ships': {'groups': ['Mother'], 'name': 'Mother'}}


def sea(state, carrier=True, heading=90.0, speed_ms=0.0, wind_to=180.0, wind_ms=10 / 1.943844):
    lat, lon = at(0, 0)
    x_lat, x_lon = at(20000, 3000)
    e_lat, e_lon = at(30000, 3000)
    state.apply({'type': 'groups', 'groups': {
        'Mother': {'exists': True, 'alive': 1, 'size': 1, 'lat': lat, 'lon': lon, 'alt_m': 0, 'speed_ms': speed_ms,
                   'ground': {'types': {'CVN_71' if carrier else 'USS_Arleigh_Burke_IIa': 1}, 'infantry': 0,
                              'vehicles': 1, 'ammo': {'RIM-7': 24} if carrier else {'BGM-109B': 8}},
                   'ship': {'heading_deg': heading, 'carrier': carrier, 'link4': carrier, 'acls': carrier,
                            'wind_to_deg': wind_to, 'wind_ms': wind_ms}},
        'Axe': {'exists': True, 'alive': 2, 'size': 2, 'lat': x_lat, 'lon': x_lon, 'alt_m': 20, 'speed_ms': 0,
                'ground': {'types': {'T-72B': 2}, 'infantry': 0, 'vehicles': 2},
                'enemies': [{'group': 'M1', 'units': 3, 'type': 'T-55', 'lat': e_lat, 'lon': e_lon}]}},
        'zones': [{'name': 'Box', **dict(zip(('lat', 'lon'), at(-20000, 0))), 'radius_m': 1000}]})


def scenario(**kw):
    state = CockpitState(aircraft='FA-18C_hornet', callsign='Enfield 1-1')
    dcs = FakeDcs(state, task_answer=(True, 'sailing, 20 knots'))
    desk = ShipDesk(state, MOTHER, tasks=dcs, bridge=dcs)
    sea(state, **kw)
    return state, dcs, desk


def names(desk):
    return [d['name'] for d in ShipToolBox(desk).current()]


class Sailing(unittest.TestCase):
    def test_sail_to_coordinates_at_a_speed(self):
        state, dcs, desk = scenario()
        grid = coords.to_mgrs(*at(-10000, 0))
        result = json.loads(ShipToolBox(desk).call('ships.move_to', json.dumps({'destination': grid, 'speed_kt': 20})))
        self.assertTrue(result['ok'])
        action, group, lat, lon, speed_ms = dcs.sent[-1]
        self.assertEqual((action, group), ('sail', 'Mother'))
        self.assertLess(metres((lat, lon), at(-10000, 0)), 2)
        self.assertAlmostEqual(speed_ms, 20 / 1.943844, 3)
        self.assertIn('course 180', result['detail'])
        self.assertTrue(desk.move_to('Box', speed_kt=12)['ok'])   # a zone too

    def test_a_course_with_no_point(self):
        state, dcs, desk = scenario(carrier=False)
        dcs.task_answer = (True, 'steering, 15 knots, for 41.3 km, then it stops 1 km short of the coast ahead')
        result = json.loads(ShipToolBox(desk).call('ships.steer', json.dumps({'course_deg': 27, 'speed_kt': 15})))
        self.assertTrue(result['ok'])
        self.assertIn('course 027', result['detail'])
        action, group, course, speed_ms, max_m = dcs.sent[-1]
        self.assertEqual((action, group, course), ('steer', 'Mother', 27.0))
        self.assertAlmostEqual(speed_ms, 15 / 1.943844, 3)
        self.assertAlmostEqual(max_m, 30 * 1852)   # 30 nautical miles by default
        self.assertTrue(desk.set_speed(20)['ok'])  # goes on to where the course ends
        self.assertEqual(dcs.sent[-1][0], 'sail')
        self.assertLess(abs(metres(dcs.sent[-1][2:4], at(0, 0)) - 41300), 150)   # flat earth over 41 km
        desk.flight_deck(open=True)
        self.assertFalse(desk.steer(90)['ok'])     # not with a green deck
        self.assertIn('steer', names(desk))

    def test_speed_and_all_stop(self):
        state, dcs, desk = scenario()
        self.assertFalse(desk.set_speed(10)['ok'])   # not sailing anywhere yet
        desk.move_to('Box', speed_kt=12)
        self.assertTrue(desk.set_speed(25)['ok'])
        self.assertAlmostEqual(dcs.sent[-1][4], 25 / 1.943844, 3)
        self.assertTrue(desk.set_speed(80)['ok'])     # clamped to what a ship does
        self.assertAlmostEqual(dcs.sent[-1][4], 32 / 1.943844, 3)
        desk.hold()
        self.assertEqual(dcs.sent[-1], ('ship_hold', 'Mother'))


class Carrier(unittest.TestCase):
    def test_into_the_wind_for_a_recovery_and_a_launch(self):
        state, dcs, desk = scenario()   # wind from the north at 10 knots
        result = desk.turn_into_wind(25, minutes=30, for_recovery=True)
        self.assertTrue(result['ok'])
        self.assertIn('course 009, 15 knots', result['detail'])   # 9 degrees for the angled deck, 25 - 10 knots
        action, _, lat, lon, speed_ms = dcs.sent[-1]
        self.assertAlmostEqual(speed_ms, 15 / 1.943844, 3)
        self.assertGreater(lat, at(0, 0)[0])   # steaming north
        self.assertIn('into_the_wind', desk.situation()['ships'])
        self.assertIn('course 000', desk.turn_into_wind(25, for_recovery=False)['detail'])

    def test_wind_over_the_deck(self):
        state, dcs, desk = scenario(heading=0.0, speed_ms=15 / 1.943844)   # into a 10 knot wind at 15 knots
        view = desk.situation()['ships']
        self.assertEqual(view['wind'], 'from 0 at 10.0 knots')
        self.assertAlmostEqual(view['wind_over_deck_kt'], 25.0, 0)

    def test_navigation_aids_even_if_the_mission_set_none(self):
        state, dcs, desk = scenario()
        self.assertEqual((tacan_mhz(1, 'X'), tacan_mhz(71, 'X'), tacan_mhz(71, 'Y')), (962, 1158, 1032))
        result = desk.navigation_aid('tacan', channel=71, mode='X', callsign='rou')
        self.assertEqual(result['detail'], 'TACAN 71X ROU')
        self.assertEqual(dcs.sent[-1], ('ship_aid', 'Mother', 'tacan', 'on', 71, 'X', 'ROU', 1158))
        self.assertTrue(desk.navigation_aid('icls', channel=11)['ok'])
        self.assertTrue(desk.navigation_aid('link4')['ok'])
        self.assertEqual(dcs.sent[-1][-1], 336.0)
        self.assertTrue(desk.navigation_aid('acls')['ok'])
        self.assertEqual(desk.situation()['ships']['navigation_aids_on'],
                         {'tacan': '71X ROU', 'icls': '11', 'link4': '336 MHz', 'acls': 'on'})
        self.assertFalse(desk.navigation_aid('tacan', channel=200)['ok'])
        self.assertFalse(desk.navigation_aid('icls', channel=30)['ok'])
        desk.navigation_aid('tacan', on=False)
        self.assertEqual(dcs.sent[-1][:4], ('ship_aid', 'Mother', 'tacan', 'off'))
        self.assertNotIn('tacan', desk.aids)

    def test_it_knows_the_aids_the_mission_set(self):
        state, dcs, desk = scenario()
        state.groups['Mother']['ship']['mission_aids'] = {
            'tacan': {'channel': 73, 'mode': 'X', 'callsign': 'TDR'}, 'icls': {'channel': 13}, 'acls': {}}
        self.assertEqual(desk.situation()['ships']['navigation_aids_on'],
                         {'tacan': '73X TDR (set in the mission)', 'icls': '13 (set in the mission)',
                          'acls': 'on (set in the mission)'})
        desk.navigation_aid('icls', on=False)                       # switched off by the agent
        desk.navigation_aid('tacan', channel=74, callsign='TDR')    # set again
        self.assertEqual(desk.situation()['ships']['navigation_aids_on'],
                         {'acls': 'on (set in the mission)', 'tacan': '74X TDR'})

    def test_a_destroyer_is_not_a_carrier(self):
        state, dcs, desk = scenario(carrier=False)
        self.assertFalse(desk.turn_into_wind()['ok'])
        self.assertFalse(desk.navigation_aid('icls', channel=11)['ok'])
        self.assertTrue(desk.navigation_aid('tacan', channel=40, mode='X', callsign='DDG')['ok'])
        self.assertNotIn('turn_into_wind', names(desk))
        self.assertIn('turn_into_wind', names(scenario()[2]))
        self.assertNotIn('aircraft_carrier', desk.situation()['ships'])


class Radar(unittest.TestCase):
    def test_every_contact_friend_or_foe(self):
        state, dcs, desk = scenario(carrier=False)
        north = dict(zip(('lat', 'lon'), at(74080, 0)))   # 40 nautical miles north
        east = dict(zip(('lat', 'lon'), at(0, 18520)))     # 10 nautical miles east
        state.groups['Mother']['ship']['radar'] = {'range_km': 160, 'contacts': [
            {'unit': 'Colt-1', 'type': 'UH-1H', 'player': True, **east, 'alt_m': 300, 'vx': 0, 'vz': -50,
             'side': 'friend', 'kind': 'air', 'dist_km': 18.5},
            {'unit': 'Bandit', 'type': 'MiG-29A', **north, 'alt_m': 6096, 'vx': -250, 'vz': 0,
             'side': 'enemy', 'kind': 'air', 'dist_km': 74.1},
            {'unit': 'Red Navy-1', 'type': 'MOSCOW', **north, 'alt_m': 0, 'vx': 0, 'vz': 5, 'side': 'enemy',
             'kind': 'surface', 'group': 'Red Navy', 'dist_km': 74.1}]}
        radar = desk.situation()['ships']['radar']
        self.assertEqual(radar['range_km'], 160)
        pilot, bandit, ship = radar['contacts']
        self.assertEqual((pilot['side'], pilot['who'], pilot['bearing_deg'], pilot['range_nm']),
                         ('friend', 'the pilot you talk to', 90, 10.0))
        self.assertEqual(pilot['heading_deg'], 270)
        self.assertEqual((bandit['side'], bandit['bearing_deg'], bandit['range_nm'], bandit['altitude_ft']),
                         ('enemy', 0, 40.0, 20000))
        self.assertEqual((bandit['heading_deg'], bandit['speed_kt']), (180, 486))
        self.assertEqual((ship['kind'], ship['group']), ('surface', 'Red Navy'))
        self.assertNotIn('altitude_ft', ship)

    def test_a_ship_radio_reaches_over_the_horizon_of_its_mast(self):
        from squad.desk import RADIOS
        state, dcs, desk = scenario(carrier=False)
        state.flight = {'lat': at(0, 0)[0], 'lon': at(0, 0)[1], 'alt_msl_m': 3000.0, 'alt_agl_m': 3000.0}
        state.groups['Mother']['radio'] = {'line_of_sight': True, 'distance_m': 240000, 'kind': 'ship'}
        self.assertTrue(desk.radio_link()['pilot_hears'])      # 240 km to an aircraft at 3000 m
        state.groups['Mother']['radio']['distance_m'] = 260000
        self.assertFalse(desk.radio_link()['pilot_hears'])     # beyond the horizon (about 246 km)
        self.assertEqual(RADIOS['ship']['antenna_m'], 25.0)


class FlightDeck(unittest.TestCase):
    def test_green_deck_for_a_helicopter(self):
        state, dcs, desk = scenario(carrier=False)   # wind from the north at 10 knots
        result = json.loads(ShipToolBox(desk).call('ships.flight_deck', json.dumps({'open': True})))
        self.assertTrue(result['ok'])
        self.assertIn('green deck: steady on course 030 at 10 knots', result['detail'])   # wind 30 on the port bow
        self.assertAlmostEqual(dcs.sent[-1][4], 10 / 1.943844, 3)
        self.assertIn('green', desk.situation()['ships']['flight_deck']['state'])
        sent = len(dcs.sent)
        for refused in (desk.move_to('Box'), desk.set_speed(20), desk.turn_into_wind()):
            self.assertFalse(refused['ok'])
        self.assertIn('flight deck is green', desk.move_to('Box')['detail'])
        self.assertEqual(len(dcs.sent), sent)   # nothing moved the ship
        self.assertIn('closed', desk.flight_deck(open=False)['detail'])
        self.assertTrue(desk.move_to('Box')['ok'])
        self.assertIn('flight_deck', names(desk))

    def test_the_deck_closes_by_itself_in_the_end(self):
        state, dcs, desk = scenario(carrier=False)
        desk.flight_deck(open=True, minutes=10)
        desk.deck['until'] -= 601
        desk.tick()
        self.assertIsNone(desk.deck)
        self.assertIn('flight deck time is over', desk.reports()[0]['report'])


class Weapons(unittest.TestCase):
    def test_its_tools_and_role(self):
        from shared import triggers
        from shared.agents import ADDABLE_ROLES, ROLE_MISSIONS
        tools = names(scenario()[2])
        for wanted in ('move_to', 'set_speed', 'hold', 'fire_mission', 'cease_fire', 'attack', 'navigation_aid'):
            self.assertIn(wanted, tools)
        for not_wanted in ('pop_smoke', 'pick_up', 'drop_off', 'wait_for_pickup'):
            self.assertNotIn(not_wanted, tools)
        self.assertIn('speed_kt', ShipToolBox(scenario()[2]).tools()[0]['tools'][0]['parameters']['properties'])
        self.assertIn('ships', ADDABLE_ROLES)
        self.assertIn('naval officer', ROLE_MISSIONS['ships'].read_text(encoding='utf-8'))
        self.assertIn('navigation_aid', triggers.role_tool_names('ships'))

    def test_a_call_for_fire_reaches_a_ship(self):
        state, dcs, desk = scenario(carrier=False)
        axe = VehicleDesk(state, AXE, tasks=dcs, bridge=dcs)
        delivered = []
        net = Net(FakeAgents(MOTHER, AXE), deliver=lambda to, event: delivered.append((to, event)))
        result = NetToolBox(None, net, 'axe', axe).call_for_fire('Mother', 'M1', 'tanks in the open')
        self.assertTrue(result['ok'])
        self.assertEqual(desk.receive_target(delivered[-1][1]), 'Axe-1')
        self.assertTrue(desk.fire_mission('Axe-1', rounds=2)['ok'])
        self.assertEqual(dcs.sent[-1][0], 'fire')
        self.assertLess(metres(dcs.sent[-1][2:4], at(30000, 3000)), 1)

    def test_rounds_complete_by_weapon(self):
        """Live 2026-11-07: Trident fired one Tomahawk and was told "33633 left" (every round aboard)."""
        state, dcs, desk = scenario(carrier=False)
        state.groups['Mother']['ground']['ammo'] = {'BGM-109B': 26, '127mm HE': 600, 'SM-2': 80}
        desk.fire_mission(coords.to_mgrs(*at(100000, 0)), rounds=1)
        state.groups['Mother']['ground']['ammo'] = {'BGM-109B': 25, '127mm HE': 600, 'SM-2': 80}
        desk.tick()
        desk.mission['last_shot_at'] -= 20
        desk.tick()
        report = desk.reports()[-1]['report']
        self.assertIn('1 BGM-109B fired, 25 BGM-109B left', report)
        self.assertNotIn('705', report)

    def test_attack_only_what_it_detects(self):
        state, dcs, desk = scenario()
        self.assertFalse(desk.attack('Kuznetsov')['ok'])
        state.groups['Mother']['enemies'] = [{'group': 'Red Navy', 'units': 2, 'type': 'MOSCOW',
                                             **dict(zip(('lat', 'lon'), at(40000, 0)))}]
        self.assertTrue(desk.attack('red navy')['ok'])
        self.assertEqual(dcs.sent[-1], ('ship_attack', 'Mother', 'Red Navy'))


if __name__ == '__main__':
    unittest.main()
