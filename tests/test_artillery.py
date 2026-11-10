"""Offline tests for the artillery agent (artillery/, user 2026-11-07: "Archer", a BM-21): where it fires (an
observer's call for fire on the internal net, a zone or map mark, coordinates, corrections), DCS's FireAtPoint with
its spread, and "shot" / "rounds complete" from its ammunition count. Also the coordinates (shared/coords.py).
DCS is played by the squad tests' fake.

Run: python -m unittest discover tests
"""
import json
import time
import unittest

import quiet_log  # noqa: F401  (the program's log goes to a temporary file)
from artillery import desk as artillery_desk
from artillery.desk import ArtilleryDesk
from artillery.tools import ArtilleryToolBox
from instructor import geo
from shared import coords
from shared.cockpit import CockpitState
from shared.net import Net, NetToolBox
from tests.test_net import FakeAgents, ScriptedBrain, responder
from tests.test_squad import FakeDcs, at
from vehicles.desk import VehicleDesk

ARCHER = {'id': 'archer', 'name': 'Archer', 'role': 'artillery', 'active': True,
          'artillery': {'groups': ['Archer'], 'name': 'Archer'}}
AXE = {'id': 'axe', 'name': 'Axe', 'role': 'vehicles', 'active': True, 'vehicles': {'groups': ['Axe'], 'name': 'Axe'}}
ENEMY_AT = (12000, 3000)   # north, east of the battery, metres


def groups(state, ammo=40):
    a_lat, a_lon = at(0, 0)
    x_lat, x_lon = at(10000, 3000)        # Axe, 2 km south of the enemy
    e_lat, e_lon = at(*ENEMY_AT)
    state.apply({'type': 'groups', 'groups': {
        'Archer': {'exists': True, 'alive': 1, 'size': 1, 'lat': a_lat, 'lon': a_lon, 'alt_m': 20, 'speed_ms': 0,
                   'ground': {'types': {'Grad-URAL': 1}, 'infantry': 0, 'vehicles': 1,
                              'ammo': {'9M22U': ammo}}},
        'Axe': {'exists': True, 'alive': 2, 'size': 2, 'lat': x_lat, 'lon': x_lon, 'alt_m': 20, 'speed_ms': 0,
                'ground': {'types': {'T-72B': 2}, 'infantry': 0, 'vehicles': 2},
                'enemies': [{'group': 'M1', 'units': 3, 'type': 'T-55', 'lat': e_lat, 'lon': e_lon}]}},
        'zones': [{'name': 'Z2', **dict(zip(('lat', 'lon'), at(8000, 0))), 'radius_m': 300}]})


def scenario():
    state = CockpitState(aircraft='Mi-8MT', callsign='Colt 1-1')
    dcs = FakeDcs(state, task_answer=(True, 'fire mission given: 20 rounds, spread 150 m'))
    archer = ArtilleryDesk(state, ARCHER, tasks=dcs, bridge=dcs)
    axe = VehicleDesk(state, AXE, tasks=dcs, bridge=dcs)
    groups(state)
    delivered = []
    net = Net(FakeAgents(ARCHER, AXE), deliver=lambda to, event: delivered.append((to, event)))
    archer.net = net
    return state, dcs, archer, axe, net, delivered


def metres(a, b):
    return geo.distance_nm(a[0], a[1], b[0], b[1]) * 1852


class Coordinates(unittest.TestCase):
    def test_formats_a_pilot_reads_out(self):
        expected = (42.255, 41.786667)
        for text in ('N42 15.300 E041 47.200', 'N 42 15 18 E 41 47 12', '42.255, 41.786667', '42 15.3 N 41 47.2 E',
                     "N42°15.300' E41°47.200'"):
            found = coords.parse(text)
            self.assertLess(metres(found, expected), 2, text)
        self.assertIsNone(coords.parse('over there'))

    def test_mgrs_both_ways(self):
        for lat, lon in [(41.6103, 41.5997), (42.2550, 41.7867), (33.5, 36.3), (36.2, -115.03), (-33.86, 151.21)]:
            grid = coords.to_mgrs(lat, lon)
            self.assertLess(metres(coords.parse(grid), (lat, lon)), 2, grid)
            self.assertLess(metres(coords.parse(grid.replace(' ', '')), (lat, lon)), 2, grid)
        self.assertEqual(coords.to_mgrs(41.6103, 41.5997)[:6], '37T GG')
        self.assertLess(metres(coords.parse('18S UJ 23487 06483'), (38.88948, -77.03528)), 20)   # a known grid
        self.assertLess(metres(coords.parse('37T GG 166 097'), coords.parse('37T GG 16600 09700')), 1)   # 100 m digits


class FireMissions(unittest.TestCase):
    def test_fire_at_a_zone_with_a_spread(self):
        state, dcs, archer, _, _, _ = scenario()
        result = json.loads(ArtilleryToolBox(archer).call('artillery.fire_mission', json.dumps(
            {'target': 'Z2', 'rounds': 20, 'spread_m': 150})))
        self.assertTrue(result['ok'])
        action, group, lat, lon, spread, rounds = dcs.sent[-1]
        self.assertEqual((action, group, spread, rounds), ('fire', 'Archer', 150.0, 20))
        self.assertLess(metres((lat, lon), at(8000, 0)), 1)
        self.assertRegex(result['detail'], r'grid \d{1,2}[C-X] [A-Z]{2} \d{5} \d{5}')   # read back to the pilot
        self.assertIn('8.0 km north', result['detail'])

    def test_fire_at_coordinates_the_pilot_gives(self):
        state, dcs, archer, _, _, _ = scenario()
        grid = coords.to_mgrs(*at(6000, 6000))
        self.assertTrue(archer.fire_mission(grid, rounds=10)['ok'])
        self.assertLess(metres(dcs.sent[-1][2:4], at(6000, 6000)), 2)

    def test_what_it_cannot_aim_at(self):
        state, dcs, archer, _, _, _ = scenario()
        result = archer.fire_mission('the tanks over there')
        self.assertFalse(result['ok'])
        self.assertIn('coordinates', result['detail'])
        self.assertFalse(archer.fire_mission('last')['ok'])   # nothing to correct yet
        self.assertFalse(archer.fire_mission('Z2', rounds=500)['ok'])
        groups(state, ammo=0)
        self.assertEqual(archer.fire_mission('Z2')['detail'], 'no rounds left')
        self.assertEqual([s for s in dcs.sent if s[0] == 'fire'], [])

    def test_its_tools_and_role(self):
        from shared import triggers
        from shared.agents import ADDABLE_ROLES, GROUND_ROLES, ROLE_MISSIONS
        names = [d['name'] for d in ArtilleryToolBox(scenario()[2]).current()]
        self.assertIn('fire_mission', names)
        self.assertIn('move_to', names)
        self.assertNotIn('pick_up', names)
        self.assertIn('artillery', ADDABLE_ROLES)
        self.assertIn('artillery', GROUND_ROLES)
        self.assertIn('fire direction centre', ROLE_MISSIONS['artillery'].read_text(encoding='utf-8'))
        self.assertIn('fire_mission', triggers.role_tool_names('artillery'))

    def test_not_a_ride_for_squads(self):
        state, _, archer, axe, _, _ = scenario()
        self.assertNotIn('Archer', state.carriers)
        self.assertIn('Axe', state.carriers)


class CallForFire(unittest.TestCase):
    def test_an_observer_calls_for_fire_on_what_it_sees(self):
        state, dcs, archer, axe, net, delivered = scenario()
        box = NetToolBox(None, net, 'axe', axe)
        self.assertIn('call_for_fire', [d['name'] for d in box.tools()[-1]['tools']])
        result = json.loads(box.call('net.call_for_fire', json.dumps(
            {'battery': 'Archer', 'target': 'M1', 'description': 'three tanks in the open'})))
        self.assertTrue(result['ok'])
        to, event = delivered[-1]
        self.assertEqual((to, event['net']), ('archer', 'message'))
        self.assertLess(metres((event['target']['lat'], event['target']['lon']), at(*ENEMY_AT)), 1)

        # The battery gets it: the point is kept as Axe-1 and fired at.
        brain = ScriptedBrain('Fire mission received, Axe-1, firing.')
        battery, _ = responder(brain, 'archer', 'Archer', net)
        battery.desk = archer
        battery.net_call(event)
        self.assertIn('target Axe-1', brain.asked[0])
        self.assertIn('Axe-1', [t['id'] for t in archer.situation()['targets_from_observers']])
        self.assertTrue(archer.fire_mission('Axe-1', rounds=20)['ok'])
        self.assertLess(metres(dcs.sent[-1][2:4], at(*ENEMY_AT)), 1)

    def test_no_battery_no_call_for_fire_and_only_what_it_sees(self):
        state, dcs, archer, axe, net, delivered = scenario()
        lonely = NetToolBox(None, Net(FakeAgents(AXE)), 'axe', axe)
        self.assertNotIn('call_for_fire', [d['name'] for d in lonely.tools()[-1]['tools']])
        self.assertNotIn('call_for_fire', [d['name'] for d in NetToolBox(None, net, 'axe').tools()[-1]['tools']])
        result = NetToolBox(None, net, 'axe', axe).call_for_fire('Archer', 'M7', 'tanks')
        self.assertFalse(result['ok'])
        self.assertIn('M1', result['detail'])   # it says what it does see
        self.assertEqual(delivered, [])

    def test_corrections_as_the_observer_sees_them(self):
        state, dcs, archer, axe, net, delivered = scenario()
        NetToolBox(None, net, 'axe', axe).call_for_fire('Archer', 'M1', 'tanks')
        archer.receive_target(delivered[-1][1])
        archer.fire_mission('Axe-1')
        first = dcs.sent[-1][2:4]
        archer.fire_mission('last', offset_m=200, toward='add')   # Axe is due south of M1: add = 200 m north
        self.assertLess(metres(dcs.sent[-1][2:4], at(ENEMY_AT[0] + 200, ENEMY_AT[1])), 2)
        archer.fire_mission('last', offset_m=100, toward='left')  # left of the line Axe -> M1 (north) = west
        self.assertLess(metres(dcs.sent[-1][2:4], at(ENEMY_AT[0] + 200, ENEMY_AT[1] - 100)), 2)
        archer.fire_mission('last', offset_m=50, toward='south')
        self.assertLess(metres(dcs.sent[-1][2:4], at(ENEMY_AT[0] + 150, ENEMY_AT[1] - 100)), 2)
        self.assertGreater(metres(first, dcs.sent[-1][2:4]), 100)


    def test_the_pilots_corrections_are_seen_from_the_aircraft(self):
        state, dcs, archer, _, _, _ = scenario()
        lat, lon = at(8000, -4000)   # the helicopter, due west of Z2
        state.flight = {'lat': lat, 'lon': lon, 'alt_agl_m': 300.0, 'heading_deg': 90.0}
        archer.fire_mission('Z2')
        archer.fire_mission('last', offset_m=300, toward='add')   # farther from the pilot: 300 m east
        self.assertLess(metres(dcs.sent[-1][2:4], at(8000, 300)), 2)


class Rounds(unittest.TestCase):
    def test_shot_and_rounds_complete_go_to_the_observer(self):
        state, dcs, archer, axe, net, delivered = scenario()
        NetToolBox(None, net, 'axe', axe).call_for_fire('Archer', 'M1', 'tanks')
        archer.receive_target(delivered[-1][1])
        archer.fire_mission('Axe-1', rounds=20)
        groups(state, ammo=30)
        archer.tick()
        self.assertEqual((delivered[-1][0], delivered[-1][1]['net']), ('axe', 'notice'))
        self.assertIn('Shot', delivered[-1][1]['text'])
        self.assertEqual(archer.situation()['battery']['fire_mission']['rounds_fired'], 10)
        groups(state, ammo=20)
        archer.tick()
        archer.mission['last_shot_at'] -= artillery_desk.SALVO_QUIET_S
        archer.tick()
        self.assertIn('Rounds complete on Axe-1: 20 9M22U fired, 20 9M22U left', delivered[-1][1]['text'])
        self.assertIsNone(archer.mission)

    def test_a_slow_battery_still_calls_its_shot(self):
        """Live 2026-11-07: the BM-21 fired over a minute after the order; it had been called "not firing" at 60 s
        and the mission dropped, so its shot was never called."""
        state, dcs, archer, _, _, _ = scenario()
        archer.fire_mission('Z2')
        archer.mission['ordered_at'] -= 150   # still laying after two and a half minutes: no call
        archer.tick()
        self.assertEqual(archer.reports(), [])
        self.assertEqual(archer.situation()['battery']['fire_mission']['state'], 'laying the guns')
        groups(state, ammo=39)
        archer.tick()
        report = archer.reports()[0]['report']
        self.assertIn('Shot', report)
        self.assertIn('corrections or end of mission', report)

    def test_the_same_order_again_does_not_restart_the_laying(self):
        state, dcs, archer, _, _, _ = scenario()
        archer.fire_mission('Z2')
        again = archer.fire_mission('last')
        self.assertTrue(again['ok'])
        self.assertIn('already laying', again['detail'])
        self.assertEqual(len([s for s in dcs.sent if s[0] == 'fire']), 1)
        moved = archer.fire_mission('last', offset_m=300, toward='north')   # a new point: sent, laying restarts
        self.assertIn('starts again', moved['detail'])
        self.assertEqual(len([s for s in dcs.sent if s[0] == 'fire']), 2)

    def test_targets_sent_while_busy_are_fired_in_turn(self):
        """Live 2026-11-07: Tango sent five targets at once; Archer fired the first and the other four never."""
        state, dcs, archer, axe, net, delivered = scenario()
        state.groups['Axe']['enemies'].append({'group': 'Static ZIL-135', 'units': 1, 'type': 'ZIL-135',
                                               'static': True, **dict(zip(('lat', 'lon'), at(11000, 4000)))})
        observer = NetToolBox(None, net, 'axe', axe)
        for target in ('M1', 'Static ZIL-135'):
            observer.call_for_fire('Archer', target, f'{target} target')
        first, second = (event for _, event in delivered[-2:])
        self.assertEqual(archer.receive_target(first), 'Axe-1')
        archer.fire_mission('Axe-1', rounds=3, spread_m=100)
        self.assertEqual(archer.receive_target(second), 'Axe-2')   # busy: queued by itself
        self.assertEqual(archer.queued_position('Axe-2'), 1)
        self.assertEqual(archer.situation()['battery']['fire_queue'][0]['rounds'], 3)
        self.assertEqual(len([s for s in dcs.sent if s[0] == 'fire']), 1)
        groups(state, ammo=37)   # the first salvo
        state.groups['Axe']['enemies'] = []
        archer.tick()
        archer.mission['last_shot_at'] -= artillery_desk.SALVO_QUIET_S
        archer.tick()
        fires = [s for s in dcs.sent if s[0] == 'fire']
        self.assertEqual(len(fires), 2)   # the next one, by itself, with the same rounds and spread
        self.assertLess(metres(fires[-1][2:4], at(11000, 4000)), 1)
        self.assertEqual(fires[-1][4:], (100.0, 3))
        report = delivered[-1][1]['text']
        self.assertIn('Rounds complete on Axe-1', report)
        self.assertIn('next queued target', report)
        self.assertEqual(archer.mission['id'], 'Axe-2')

    def test_the_pilot_may_queue_and_cease_fire_clears_it(self):
        state, dcs, archer, _, _, _ = scenario()
        archer.fire_mission('Z2')
        queued = archer.fire_mission(coords.to_mgrs(*at(6000, 6000)), rounds=10, queue=True)
        self.assertIn('queued as number 1', queued['detail'])
        self.assertEqual(len([s for s in dcs.sent if s[0] == 'fire']), 1)
        result = archer.cease_fire()
        self.assertIn('1 queued fire missions dropped', result['detail'])
        self.assertEqual((archer.queue, archer.mission), ([], None))

    def test_the_pilot_hears_when_it_does_not_fire(self):
        state, dcs, archer, _, _, delivered = scenario()
        archer.fire_mission('Z2')   # the pilot's own fire mission: reports by radio
        archer.mission['ordered_at'] = time.monotonic() - artillery_desk.FIRE_WAIT_S
        archer.tick()
        self.assertEqual(delivered, [])
        self.assertIn('has not fired', archer.reports()[0]['report'])


if __name__ == '__main__':
    unittest.main()
