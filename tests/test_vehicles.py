"""Offline tests for the ground vehicles agent (vehicles/): driving on or off the road in a formation, its troop
compartment from DCS's unit files or the mission, and carrying another agent's squad as Cobra does (user, 2026-11-05).
DCS is played by the squad tests' fake.

Run: python -m unittest discover tests
"""
import json
import unittest
from pathlib import Path

import dcs_data
from shared.cockpit import CockpitState
from squad.desk import SquadDesk
from squad.tools import SquadToolBox
from tests.test_squad import FakeDcs, at
from vehicles.desk import VehicleDesk
from vehicles.tools import VehicleToolBox


class FakeCargo:
    """shared/cargo.CargoCapacities as DCS's unit files give it (BTR-80 holds 700; a tank nothing)."""

    def __init__(self, types='default'):
        self.types = {'BTR-80': 700} if types == 'default' else types   # None: the scan still runs

    def capacity(self, type_name):
        return None if self.types is None else self.types.get(type_name, 0)


def place(state, types, ground_extra=None, speed=0.0, north_m=0.0, squad_north_m=1000.0):
    lat, lon = at(north_m, 0)
    s_lat, s_lon = at(squad_north_m, 0)
    state.apply({'type': 'groups', 'groups': {
        'Bison': {'exists': True, 'alive': 2, 'size': 2, 'lat': lat, 'lon': lon, 'alt_m': 20, 'speed_ms': speed,
                  'ground': {'types': types, 'infantry': 0, 'vehicles': 2, **(ground_extra or {})}},
        'Buenos': {'exists': True, 'alive': 4, 'size': 4, 'lat': s_lat, 'lon': s_lon, 'alt_m': 20, 'speed_ms': 0,
                   'ground': {'types': {'Soldier M4': 4}, 'infantry': 4, 'vehicles': 0}}},
        'zones': [{'name': 'Z2', **dict(zip(('lat', 'lon'), at(5000, 0))), 'radius_m': 300}]})


def scenario(types=None, cargo=None, ground_extra=None):
    state = CockpitState(aircraft='Mi-8MT', callsign='Colt 1-1')
    dcs = FakeDcs(state, task_answer=(True, 'driving'))
    desk = VehicleDesk(state, {'id': 'bison', 'name': 'Bison', 'role': 'vehicles',
                               'vehicles': {'groups': ['Bison'], 'name': 'Bison'}},
                       tasks=dcs, bridge=dcs, cargo=cargo if cargo is not None else FakeCargo())
    place(state, types or {'BTR-80': 2}, ground_extra)
    state.agent_names['Buenos'] = 'Victor'
    return state, dcs, desk


def names(desk):
    return [d['name'] for d in VehicleToolBox(desk).current()]


class Driving(unittest.TestCase):
    def test_drive_on_the_road_in_a_formation(self):
        state, dcs, desk = scenario(types={'T-72B': 2})
        result = json.loads(VehicleToolBox(desk).call('vehicles.move_to', json.dumps(
            {'destination': 'Z2', 'speed': 'fast', 'road': 'on', 'formation': 'column'})))
        self.assertTrue(result['ok'])
        action, group, lat, lon, speed, road, formation = dcs.sent[-1]
        self.assertEqual((action, group, speed, road, formation), ('drive', 'Bison', 'fast', 'on', 'column'))
        self.assertAlmostEqual(lat, at(5000, 0)[0], 6)
        self.assertFalse(desk.move_to('Z2', formation='square')['ok'])
        situation = desk.situation()
        self.assertEqual(situation['vehicles'], {'vehicles': '2 of 2', 'moving': False, 'types': {'T-72B': 2},
                                                 'troop_compartment': 'none'})
        self.assertNotIn('squad', situation)

    def test_its_own_tools(self):
        state, dcs, desk = scenario(types={'T-72B': 2})
        tools = names(desk)
        for squad_only in ('wait_for_pickup', 'board_helicopter', 'disembark', 'pick_up', 'drop_off'):
            self.assertNotIn(squad_only, tools)   # a tank carries nobody
        self.assertIn('move_to', tools)
        self.assertIn('road', VehicleToolBox(desk).tools()[0]['tools'][0]['parameters']['properties'])
        self.assertEqual(VehicleToolBox(desk).tools()[0]['name'], 'vehicles')
        self.assertIn('pick_up', names(scenario()[2]))   # a BTR-80 does
        self.assertIn('error', json.loads(VehicleToolBox(desk).call('vehicles.board_helicopter', '{}')))

    def test_the_role_is_wired(self):
        from shared import triggers
        from shared.agents import ADDABLE_ROLES, ROLE_MISSIONS
        self.assertIn('vehicles', ADDABLE_ROLES)
        self.assertIn('tank commander', ROLE_MISSIONS['vehicles'].read_text(encoding='utf-8'))
        self.assertIn('pick_up', triggers.role_tool_names('vehicles'))

    def test_added_from_the_gui(self):
        import tempfile
        from shared.agents import AgentStore
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents', settings={})
            bison = store.add('vehicles', 'Bison', 256.0, 'AM', {}, 'Bison 1')
            self.assertEqual((bison['role'], bison['vehicles']), ('vehicles', {'groups': ['Bison 1'], 'name': 'Bison'}))
            self.assertIn('tank commander', store.mission(bison['id']))
            store.update(bison['id'], {'group': 'Bison 2', 'name': 'Toro'})
            self.assertEqual(store.get(bison['id'])['vehicles'], {'groups': ['Bison 2'], 'name': 'Toro'})

    def test_lost_vehicles_in_the_condition_events(self):
        from shared.triggers import happened
        state, dcs, desk = scenario()
        before = desk.facts()
        self.assertEqual(before['members'], 'vehicles')
        self.assertEqual(happened({'when': 'casualties'}, before, {**before, 'alive': 1}),
                         'lost 1 vehicles (1 of 2 left)')


class SeeingFriends(unittest.TestCase):
    """Live 2026-11-05: Bison (an AAV7) could not see Tango beside it, nor Tango's green smoke."""

    def test_friendly_units_in_sight_and_a_move_to_them(self):
        state, dcs, desk = scenario()
        friends = desk.situation()['friendly_units_in_sight']
        self.assertEqual(friends, [{'name': 'Victor', 'kind': 'infantry', 'distance_m': 1000.0, 'direction': 'north',
                                    'moving': False}])
        self.assertTrue(desk.move_to('Victor\'s position', road='off')['ok'])
        self.assertAlmostEqual(dcs.sent[-1][2], at(1000, 0)[0], 6)
        place(state, {'BTR-80': 2}, squad_north_m=9000)   # too far to see
        self.assertNotIn('friendly_units_in_sight', desk.situation())
        self.assertFalse(desk.move_to('Victor')['ok'])

    def test_a_ship_in_sight_is_a_ship(self):
        """Flown 2026-11-08: Mother saw Trident as "vehicles"."""
        state, dcs, desk = scenario()
        state.groups['Buenos']['ship'] = {'heading': 0}
        kinds = [f['kind'] for f in desk.situation()['friendly_units_in_sight']]
        self.assertEqual(kinds, ['ship'])

    def test_smoke_is_shared_and_a_place(self):
        state, dcs, bison = scenario()
        victor = SquadDesk(state, {'id': 'victor', 'name': 'Victor', 'squad': {'groups': ['Buenos'],
                                                                                'name': 'Victor'}},
                           tasks=dcs, bridge=dcs)
        self.assertTrue(victor.pop_smoke('green')['ok'])
        seen = bison.situation()['smoke_in_sight']
        self.assertEqual(seen, [{'color': 'green', 'popped_by': 'Victor', 'distance_m': 1000.0, 'direction': 'north',
                                 'seconds_ago': 0}])
        self.assertTrue(bison.move_to('the green smoke')['ok'])
        self.assertAlmostEqual(dcs.sent[-1][2], at(1000, 0)[0], 6)
        self.assertIn('no such smoke', bison.move_to('red smoke')['detail'])
        # DCS's smoke lasts about five minutes.
        from shared import smoke
        state.smokes[0]['at'] -= smoke.LASTS_S + 1
        self.assertNotIn('smoke_in_sight', bison.situation())

    def test_a_wingman_sees_the_smoke_from_the_air(self):
        from shared import smoke
        state = CockpitState()
        smoke.add(state, 'green', *at(1000, 0), 'Tango')
        smoke.add(state, 'red', *at(30000, 0), 'Victor')
        seen = smoke.in_sight(state, *at(0, 0), smoke.SEEN_FROM_AIR_M)
        self.assertEqual([s['color'] for s in seen], ['green'])
        self.assertEqual(smoke.find(seen, "Tango's smoke")['color'], 'green')
        self.assertIsNone(smoke.find(seen, 'Tango'))   # not about smoke


class TroopCompartment(unittest.TestCase):
    def test_from_dcs_files_or_live(self):
        self.assertEqual(scenario()[2].capacity(), 1400)              # two BTR-80s, DCS's unit files
        self.assertEqual(scenario(types={'M-113': 1}, ground_extra={'descent_capacity': 1000})[2].capacity(), 1000)
        state, dcs, desk = scenario(cargo=FakeCargo(types=None))
        self.assertIsNone(desk.capacity())                             # the scan still runs
        self.assertEqual(desk.situation()['vehicles']['troop_compartment'], 'not known yet')

    def test_parse_dcs_unit_files(self):
        from shared.cargo import parse
        btr = 'GT.Name = "BTR-80"\nGT.InternalCargo = {\n\tnominalCapacity = 700,\n\tmaximalCapacity = 700,\n}\n'
        self.assertEqual(parse(btr), ('BTR-80', 700))
        points = "GT.Name = 'Truck'\nGT.InternalCargo = {nominalCapacity = 3000, unit_point = 12}"
        self.assertEqual(parse(points), ('Truck', 1200))
        self.assertIsNone(parse('GT.Name = "T-72B"\nGT.category = "Armor"'))

    def test_real_dcs_install(self):
        from shared.cargo import scan
        install = dcs_data.DCS
        if not install.is_dir():
            self.skipTest('no DCS install')
        found = scan(install)
        self.assertEqual(found.get('BTR-80'), 700)
        self.assertEqual(found.get('MaxxPro_MRAP'), 800)


class Carrying(unittest.TestCase):
    def test_pick_up_a_squad_and_drop_it(self):
        state, dcs, desk = scenario()
        result = desk.pick_up('Victor')                       # by its agent's name
        self.assertTrue(result['ok'])
        self.assertIn('Victor told to wait for you there', result['detail'])
        self.assertEqual(dcs.sent[-2][:2], ('carry_pickup', 'Bison'))
        self.assertEqual(dcs.sent[-2][4:], ('Buenos', 'off'))
        self.assertEqual(dcs.sent[-1][0:2], ('wait_pickup', 'Buenos'))
        self.assertEqual(state.transport['Buenos']['ride'], 'vehicle')
        self.assertEqual(desk.drop_off('Z2'), {'ok': False, 'detail': 'you carry no troops'})
        # Driving there, then stopped beside the squad: it boards; driving off with them.
        place(state, {'BTR-80': 2}, speed=8.0, north_m=500)
        desk.tick()
        place(state, {'BTR-80': 2}, speed=0.0, north_m=990)
        desk.tick()
        self.assertIn('stopped at the pickup point', desk.reports()[0]['report'])
        self.assertIn('boarding or aboard', desk.situation()['vehicles']['troops']['Victor'])
        place(state, {'BTR-80': 2}, speed=6.0, north_m=1100)
        desk.tick()
        self.assertEqual(state.transport['Buenos']['stage'], 'aboard')
        self.assertIn('drove off with Victor aboard', desk.reports()[0]['report'])
        self.assertTrue(desk.drop_off('Z2', road='on')['ok'])
        self.assertEqual(dcs.sent[-1][0], 'carry_dropoff')
        self.assertEqual(dcs.sent[-1][-2:], ('Buenos', 'on'))
        place(state, {'BTR-80': 2}, speed=0.0, north_m=5000)
        desk.tick()
        place(state, {'BTR-80': 2}, speed=5.0, north_m=5300)
        desk.tick()
        self.assertNotIn('Buenos', state.transport)
        # Without DCS's count nobody knows: never told as done (live 2026-11-05, Tango stayed aboard).
        self.assertIn('could not tell whether Victor got out', desk.reports()[0]['report'])
        self.assertEqual(state.unconfirmed_drops, {'Buenos': 'Bison'})

    def counted_aboard(self):
        state, dcs, bison = scenario()
        victor = SquadDesk(state, {'id': 'victor', 'name': 'Victor', 'squad': {'groups': ['Buenos'],
                                                                                'name': 'Victor'}},
                           tasks=dcs, bridge=dcs)
        bison.pick_up('Victor')
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 4}, speed=0.0, north_m=1000)
        bison.tick()
        victor.tick()
        bison.reports(), victor.reports()
        self.assertTrue(bison.drop_off('Z2')['ok'])
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 4}, speed=0.0, north_m=5000)
        bison.tick()
        victor.tick()
        return state, dcs, bison, victor

    def test_out_only_when_dcs_counts_the_compartment_empty(self):
        state, dcs, bison, victor = self.counted_aboard()
        self.assertEqual(state.transport['Buenos']['stage'], 'drop')   # stopped there, still counted aboard
        self.assertEqual(bison.reports(), [])
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 0}, speed=0.0, north_m=5000)
        bison.tick()
        self.assertNotIn('Buenos', state.transport)
        self.assertIn('your compartment is empty', bison.reports()[0]['report'])
        victor.tick()
        report = victor.reports()[0]['report']
        self.assertIn('on the ground at the drop point', report)
        self.assertIn('do not repeat it', report)   # Bison called the drop: no echo (user, 2026-11-05)

    def test_driving_off_with_them_still_aboard(self):
        state, dcs, bison, victor = self.counted_aboard()
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 4}, speed=5.0, north_m=5300)
        bison.tick()
        self.assertEqual(state.transport['Buenos']['stage'], 'aboard')
        self.assertIn('with Victor still aboard', bison.reports()[0]['report'])
        victor.tick()
        self.assertEqual(victor.reports(), [])   # Bison says it; the squad does not echo it
        self.assertTrue(bison.drop_off('Z2')['ok'])   # try again

    def test_the_live_drop_of_2026_11_05(self):
        """As the black box recorded it: counted aboard only after driving off; at the point the BTR shuffled at
        1.7 m/s while the troops got out, and the count went to 0 twenty seconds later."""
        state, dcs, bison = scenario()
        bison.pick_up('Victor')
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 0}, speed=0.0, north_m=1000)
        bison.tick()
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 0}, speed=1.7, north_m=1010)
        bison.tick()
        self.assertEqual(state.transport['Buenos']['stage'], 'aboard')   # the stop-and-go guess
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 2}, speed=0.0, north_m=1010)
        bison.tick()
        bison.reports()
        self.assertTrue(bison.drop_off('Z2')['ok'])
        for speed, north, count in ((10.0, 4000, 2), (0.8, 5000, 2), (1.7, 5010, 2), (2.8, 4995, 2), (0.8, 5000, 0)):
            place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': count}, speed=speed, north_m=north)
            bison.tick()
        self.assertNotIn('Buenos', state.transport)
        self.assertEqual([r['report'] for r in bison.reports()], ['Victor got out at the drop point: your compartment '
                                                                  'is empty'])

    def test_stopped_long_with_them_still_aboard(self):
        from vehicles.desk import DISEMBARK_WAIT_S
        state, dcs, bison, victor = self.counted_aboard()
        state.transport['Buenos']['stopped_at'] -= DISEMBARK_WAIT_S + 1
        bison.tick()
        self.assertIn('Victor is still aboard', bison.reports()[0]['report'])
        bison.tick()
        self.assertEqual(bison.reports(), [])   # said once

    def test_drop_off_straight_from_the_pickup_point(self):
        # DCS's Embarking is the last point of the route: the vehicles may wait there with the troops aboard.
        state, dcs, desk = scenario()
        desk.pick_up('Victor')
        place(state, {'BTR-80': 2}, speed=0.0, north_m=1000)
        desk.tick()
        self.assertTrue(desk.drop_off('Z2')['ok'])
        self.assertEqual(state.transport['Buenos']['stage'], 'drop')

    def test_dcs_counts_who_is_aboard(self):
        state, dcs, desk = scenario()
        desk.pick_up('Victor')
        place(state, {'BTR-80': 2}, ground_extra={'descent_on_board': 4}, speed=0.0, north_m=1000)
        desk.tick()
        self.assertEqual(state.transport['Buenos']['stage'], 'aboard')
        self.assertIn('Victor is aboard', desk.reports()[0]['report'])

    def test_the_carried_squad_hears_of_its_vehicle(self):
        state, dcs, bison = scenario()
        victor = SquadDesk(state, {'id': 'victor', 'name': 'Victor', 'squad': {'groups': ['Buenos'],
                                                                                'name': 'Victor'}},
                           tasks=dcs, bridge=dcs)
        victor.tick()
        bison.pick_up('Victor')
        self.assertIn('Bison\'s vehicle is coming to pick you up', victor.situation()['squad']['pickup'])
        state.transport['Buenos']['stage'] = 'aboard'
        victor.tick()
        self.assertEqual(victor.reports(), [])   # Bison calls the boarding (live 2026-11-05: both said it)
        self.assertIn('aboard Bison\'s vehicle', victor.situation()['squad'])

    def test_the_squad_asks_its_ride(self):
        # User, 2026-11-05: "Tango asks Bison, Bison does it on its side".
        state, dcs, bison = scenario()
        victor = SquadDesk(state, {'id': 'victor', 'name': 'Victor', 'squad': {'groups': ['Buenos'],
                                                                                'name': 'Victor'}},
                           tasks=dcs, bridge=dcs)
        self.assertIn('no vehicles called Tiger (vehicles: Bison)', victor.board_vehicle('Tiger')['detail'])
        self.assertTrue(json.loads(SquadToolBox(victor).call('squad.board_vehicle', '{"vehicle": "bison"}'))['ok'])
        self.assertEqual(dcs.sent[-2][:2], ('carry_pickup', 'Bison'))
        self.assertAlmostEqual(dcs.sent[-2][2], at(1000, 0)[0], 6)   # where the squad is
        self.assertIn('Victor asked you on the radio to pick them up', bison.reports()[0]['report'])
        state.transport['Buenos']['stage'] = 'aboard'
        self.assertIn('disembark (asks it', victor.situation()['squad'])
        place(state, {'BTR-80': 2}, speed=0.0, north_m=3000)
        self.assertTrue(victor.disembark()['ok'])
        self.assertEqual(dcs.sent[-1][0], 'carry_dropoff')
        self.assertAlmostEqual(dcs.sent[-1][2], at(3000, 0)[0], 6)   # where the vehicles are
        self.assertEqual(state.transport['Buenos']['stage'], 'drop')
        self.assertIn('to let them out where you are', bison.reports()[0]['report'])

    def test_drop_them_200_m_from_the_helicopter(self):
        # Live 2026-11-05: "Bison, déjalo a 200 metros del helicóptero" -> "Bison cannot set that point".
        state, dcs, bison = scenario()
        state.transport['Buenos'] = {'carrier': 'Bison', 'ride': 'vehicle', 'stage': 'aboard', 'lat': 0, 'lon': 0}
        lat, lon = at(2000, 0)
        state.flight = {'lat': lat, 'lon': lon, 'alt_agl_m': 0.0, 'heading_deg': 90.0}   # landed, nose east
        result = json.loads(VehicleToolBox(bison).call('vehicles.drop_off', json.dumps(
            {'where': 'helicopter', 'offset_m': 200, 'toward': '3'})))    # its right: south
        self.assertTrue(result['ok'])
        self.assertAlmostEqual(dcs.sent[-1][2], at(1800, 0)[0], 6)
        self.assertAlmostEqual(dcs.sent[-1][3], at(1800, 0)[1], 6)
        self.assertTrue(bison.move_to('helicopter', offset_m=200, toward='west')['ok'])
        self.assertAlmostEqual(dcs.sent[-1][3], at(2000, -200)[1], 5)   # 6 cm: two metres-per-degree figures
        self.assertIn('compass direction', bison.move_to('helicopter', offset_m=200, toward='left')['detail'])
        state.flight.pop('heading_deg')
        self.assertIn('heading is unknown', bison.move_to('helicopter', offset_m=200, toward='3')['detail'])

    def test_a_renamed_vehicle_agent_is_found_by_its_new_group(self):
        state, dcs, bison = scenario()
        bison.configure({'id': 'bison', 'name': 'Bison', 'vehicles': {'groups': ['Bison 2'], 'name': 'Bison'}})
        self.assertEqual(list(state.carriers), ['Bison 2'])

    def test_no_carrying_without_a_compartment_or_a_squad(self):
        state, dcs, desk = scenario(types={'T-72B': 2})
        self.assertEqual(desk.pick_up('Victor'), {'ok': False, 'detail': 'your vehicles have no troop compartment'})
        self.assertFalse(scenario()[2].pick_up('Nobody')['ok'])
        self.assertFalse(scenario()[2].pick_up('Bison')['ok'])   # not itself, not vehicles


if __name__ == '__main__':
    unittest.main()
