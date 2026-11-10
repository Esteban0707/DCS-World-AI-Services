"""Offline tests for the squad agent (squad/): what it knows (realism: the helicopter only in sight), its orders to
the mission and to DCS's radio menu, and the answers it waits for. DCS is played by fakes that answer like the hook
and the patched radio menu do.

Run: python -m unittest discover tests
"""
import json
import math
import time
import unittest

from shared.cockpit import CockpitState
from squad.desk import SquadDesk
from squad.tools import SquadToolBox

LAT, LON = 42.24, 42.05
M_PER_DEG = 111_320.0


def at(north_m: float, east_m: float) -> tuple[float, float]:
    return LAT + north_m / M_PER_DEG, LON + east_m / (M_PER_DEG * math.cos(math.radians(LAT)))


class FakeDcs:
    """Answers orders at once, like the hook (task) and the radio-menu bridge (native) would."""

    def __init__(self, state: CockpitState, task_answer=(True, 'moving'), native_answer=('sent', 'embarking Buenos')):
        self.state, self.task_answer, self.native_answer = state, task_answer, native_answer
        self.sent = []
        self.aboard = ''   # what troops_on_board answers: the names DCS has aboard, ', ' separated

    def send(self, action, *args):   # TaskChannel.send / NativeBridge.send
        request_id = f'r{len(self.sent)}'
        self.sent.append((action, *args))
        if action == 'troops_on_board':
            self.state.apply({'type': 'native', 'id': request_id, 'status': 'sent', 'detail': self.aboard})
        elif action in ('embark_troops', 'disembark_troops'):
            status, detail = self.native_answer
            self.state.apply({'type': 'native', 'id': request_id, 'status': status, 'detail': detail})
        else:
            ok, detail = self.task_answer
            self.state.apply({'type': 'task', 'id': request_id, 'ok': ok, 'detail': detail})
        return request_id

    def watch(self, groups, owner=''):
        self.sent.append(('watch', *groups))


class Scenario:
    def __init__(self, heli_north_m=300.0, heli_in_air=False, **answers):
        self.state = CockpitState(aircraft='UH-1H', callsign='Colt 1-1')
        self.dcs = FakeDcs(self.state, **answers)
        self.desk = SquadDesk(self.state, {'name': 'Buenos', 'squad': {'groups': ['Buenos'], 'name': 'Los Buenos'}},
                              tasks=self.dcs, bridge=self.dcs)
        self.squad(exists=True)
        self.helicopter(heli_north_m, heli_in_air)

    def squad(self, **entry):
        lat, lon = at(0, 0)
        self.state.apply({'type': 'groups', 'groups': {'Buenos': {'alive': 4, 'size': 4, 'lat': lat, 'lon': lon,
                                                                  'alt_m': 20, 'speed_ms': 0, **entry}},
                          'zones': [{'name': 'LZ North', **dict(zip(('lat', 'lon'), at(5000, 0))), 'radius_m': 150},
                                    {'name': 'Base', **dict(zip(('lat', 'lon'), at(-20000, 0))), 'radius_m': 300}]})

    def helicopter(self, north_m: float, in_air: bool):
        lat, lon = at(north_m, 0)
        self.state.apply({'type': 'traffic', 'aircraft': [{'unit': 'Aerial-1-1', 'type': 'UH-1H', 'player': True,
                                                           'lat': lat, 'lon': lon, 'alt_m': 320 if in_air else 20,
                                                           'vx': 0, 'vz': 0, 'in_air': in_air}]})


class Knowledge(unittest.TestCase):
    def test_helicopter_in_sight(self):
        situation = Scenario(heli_north_m=300).desk.situation()
        self.assertEqual(situation['you_are'], 'Los Buenos')
        self.assertEqual(situation['squad'], {'soldiers': '4 of 4', 'moving': False})
        self.assertEqual(situation['helicopter'], {'in_sight': True, 'distance_m': 300, 'direction': 'north',
                                                   'state': 'landed', 'moving': False})
        self.assertEqual(situation['mission_zones'], [{'name': 'LZ North', 'distance_km': 5.0},
                                                      {'name': 'Base', 'distance_km': 20.0}])

    def test_helicopter_far_away_is_only_a_voice(self):
        situation = Scenario(heli_north_m=30000, heli_in_air=True).desk.situation()
        self.assertEqual(situation['helicopter'], {'in_sight': False, 'note': 'you only hear the pilot on the radio'})

    def test_other_agents_aircraft_in_sight(self):
        # Live 2026-11-05: Tango waited for pickup beside Cobra (an AI Mi-8 landed there) and said it saw no helicopter.
        scenario = Scenario(heli_north_m=30000, heli_in_air=True)
        cobra_lat, cobra_lon = at(0, 400)
        far_lat, far_lon = at(20000, 0)
        squad = scenario.state.groups['Buenos']
        scenario.state.apply({'type': 'groups', 'groups': {
            'Buenos': squad,
            'Cobra': {'exists': True, 'alive': 1, 'lat': cobra_lat, 'lon': cobra_lon, 'alt_m': 20,
                      'air': {'active': True, 'in_air': False, 'type': 'Mi-8MT', 'kind': 'helicopter'}},
            'Viper': {'exists': True, 'alive': 1, 'lat': far_lat, 'lon': far_lon, 'alt_m': 3000,
                      'air': {'active': True, 'in_air': True, 'type': 'F-5E-3', 'kind': 'airplane'}}},
            'zones': scenario.state.zones})
        situation = scenario.desk.situation()
        self.assertEqual(situation['other_aircraft_in_sight'], [
            {'name': 'Cobra', 'type': 'Mi-8MT', 'kind': 'helicopter', 'distance_m': 400, 'direction': 'east',
             'state': 'landed'}])
        self.assertTrue(json.loads(SquadToolBox(scenario.desk).call('squad.move_to', json.dumps(
            {'destination': 'cobra'})))['ok'])
        self.assertEqual(scenario.dcs.sent[-1][2:4], (cobra_lat, cobra_lon))
        self.assertIn('aircraft in sight: Cobra', scenario.desk.place('Viper'))   # too far to see

    def test_no_news_and_no_group(self):
        scenario = Scenario()
        scenario.state.groups_at = time.monotonic() - 60
        self.assertEqual(scenario.desk.situation()['squad'], 'no news from the mission yet')
        lonely = SquadDesk(CockpitState(), {'name': 'X'})
        self.assertIn('no DCS group configured', lonely.situation()['squad'])


class Combat(unittest.TestCase):
    """Live 2026-11-03: Victor lost 3 of 4 soldiers without saying so, and could not attack "group M1"."""

    def enemies(self, scenario, alive=4):
        lat, lon = at(0, 400)
        scenario.squad(exists=True, alive=alive, enemies=[{'group': 'M1', 'units': 3, 'type': 'Soldier AK', 'lat': lat,
                                              'lon': lon}])

    def test_enemies_in_sight_and_none(self):
        scenario = Scenario()
        self.assertEqual(scenario.desk.situation()['enemies_in_sight'], 'none')
        self.enemies(scenario)
        self.assertEqual(scenario.desk.situation()['enemies_in_sight'],
                         [{'group': 'M1', 'units': 3, 'type': 'Soldier AK', 'distance_m': 400, 'direction': 'east'}])

    def test_losses_are_told_once(self):
        scenario = Scenario()
        scenario.desk.situation()
        self.enemies(scenario, alive=1)
        self.assertEqual(scenario.desk.situation()['squad'],
                         {'soldiers': '1 of 4', 'moving': False, 'killed_since_your_last_message': 3})
        self.assertNotIn('killed_since_your_last_message', scenario.desk.situation()['squad'])

    def test_facts_for_condition_events(self):
        scenario = Scenario(heli_north_m=300)
        self.assertEqual(scenario.desk.facts(), {'lost': False, 'alive': 4, 'size': 4, 'enemies': False,
                                                 'helicopter': True, 'zones': set()})
        lat, lon = at(5000, 0)
        scenario.squad(exists=True, lat=lat, lon=lon)
        self.assertEqual(scenario.desk.facts()['zones'], {'lznorth'})     # inside LZ North's 150 m
        scenario.desk.on_board = True
        self.assertIsNone(scenario.desk.facts())

    def test_wiped_out_only_when_it_lasts_with_the_mission_running(self):
        """Live 2026-11-03: leaving the mission made both squads "wiped out" at once."""
        import squad.desk as module
        scenario = Scenario()
        scenario.squad(exists=False)
        self.assertIsNone(scenario.desk.facts())                          # not yet: it must last
        scenario.desk.lost_since -= module.LOST_CONFIRM_S
        scenario.helicopter(300, False)                                   # the mission still runs
        self.assertEqual(scenario.desk.facts(), {'lost': True})
        scenario.state.traffic_at -= 60                                   # the mission ended: no traffic
        self.assertIsNone(scenario.desk.facts())

    def test_enemies_remembered_out_of_sight(self):
        """Live 2026-11-03: under fire, Victor saw M1, then "no enemies in sight" and refused "attack" twice."""
        scenario = Scenario(task_answer=(True, 'attacking M1'))
        self.enemies(scenario)
        scenario.desk.situation()
        scenario.squad(exists=True)                                       # detection flickers off
        situation = scenario.desk.situation()
        self.assertEqual(situation['enemies_in_sight'], 'none')
        self.assertEqual(situation['enemies_out_of_sight'][0]['group'], 'M1')
        self.assertEqual(situation['enemies_out_of_sight'][0]['last_known_direction'], 'east')
        self.assertTrue(scenario.desk.facts()['enemies'])                 # no new "enemies seen" on the flicker
        self.assertEqual(scenario.desk.attack('M1'), {'ok': True, 'detail': 'attacking M1'})

    def test_attack_only_an_enemy_seen(self):
        scenario = Scenario(task_answer=(True, 'attacking M1'))
        self.assertIn('seen: none', scenario.desk.attack('M1')['detail'])
        self.enemies(scenario)
        self.assertEqual(scenario.desk.attack('m 1'), {'ok': True, 'detail': 'attacking M1'})
        self.assertEqual(scenario.dcs.sent[-1], ('attack', 'Buenos', 'M1'))

    def test_tell_pilot_when(self):
        scenario = Scenario(heli_north_m=30000, heli_in_air=True)
        result = scenario.desk.tell_pilot_when('helicopter_seen', 'tell him when we see the helicopter')
        self.assertTrue(result['ok'])
        self.assertEqual(scenario.desk.situation()['pilot_asked_you_to_tell_when'],
                         ['tell him when we see the helicopter'])
        near = Scenario(heli_north_m=300)
        self.assertIn('already true', near.desk.tell_pilot_when('helicopter_seen', 'x')['detail'])
        self.assertFalse(near.desk.tell_pilot_when('squad_lost', 'x')['ok'])


class RadioRange(unittest.TestCase):
    """2026-11-03, user's wish: squads only reach the pilot within radio range, terrain included (DCS land.isVisible)."""

    def link(self, distance_m, sight=True, heli_in_air=True, kind='manpack'):
        scenario = Scenario(heli_north_m=distance_m, heli_in_air=heli_in_air)
        scenario.squad(exists=True, radio={'line_of_sight': sight, 'distance_m': distance_m, 'kind': kind})
        return scenario

    def test_in_range_flying(self):
        scenario = self.link(20000)                        # helicopter 300 m above: horizon ~77 km
        self.assertEqual(scenario.desk.radio_link(), {'hears_pilot': True, 'pilot_hears': True, 'distance_km': 20.0,
                                                      'line_of_sight': True, 'radio': 'manpack'})
        self.assertIsNone(scenario.desk.prefilter('Tango, report'))
        self.assertTrue(scenario.desk.can_reach_pilot())
        self.assertEqual(scenario.desk.situation()['radio_with_pilot'],
                         {'distance_km': 20.0, 'line_of_sight': True, 'pilot_hears': True})

    def test_the_aircraft_radio_reaches_farther(self):
        """The squad hears the pilot at 40 km (and obeys) but its manpack does not reach back; a vehicle's does."""
        far = self.link(40000)
        self.assertIsNone(far.desk.prefilter('Tango, go to Z2'))
        self.assertFalse(far.desk.can_reach_pilot())
        self.assertTrue(self.link(40000, kind='vehicle').desk.can_reach_pilot())

    def test_out_of_range(self):
        self.assertEqual(self.link(5000, sight=False).desk.prefilter('x'), '')   # behind a mountain
        self.assertEqual(self.link(12000, heli_in_air=False).desk.prefilter('x'), '')   # both on the ground: ~10 km
        self.assertFalse(self.link(5000, sight=False).desk.can_reach_pilot())

    def test_close_by_the_terrain_does_not_cut_it(self):
        # Live 2026-11-05: landed beside Tango, a bump in the ground cut the line and Tango never answered.
        near = self.link(30, sight=False, heli_in_air=False)
        self.assertIsNone(near.desk.prefilter('Tango, do you copy?'))
        self.assertTrue(near.desk.can_reach_pilot())
        self.assertEqual(self.link(2500, sight=False, heli_in_air=False).desk.prefilter('x'), '')

    def test_unknown_or_aboard_is_reachable(self):
        self.assertTrue(Scenario().desk.can_reach_pilot())                       # no "radio" from the mission
        scenario = self.link(35000)
        scenario.desk.on_board = True
        self.assertTrue(scenario.desk.can_reach_pilot())


class Hits(unittest.TestCase):
    """User, 2026-11-05: "if it loses a soldier or takes damage it reports it; if Tango is killed it stops
    answering"."""

    def test_a_loss_is_called_at_once(self):
        scenario = Scenario()
        scenario.desk.tick()
        scenario.squad(exists=True, alive=3)
        scenario.desk.tick()
        self.assertIn('you just lost 1 soldiers (3 of 4 left)', scenario.desk.reports()[0]['report'])
        scenario.desk.tick()
        self.assertEqual(scenario.desk.reports(), [])   # said once

    def test_wounds_without_a_loss(self):
        scenario = Scenario()
        scenario.squad(exists=True, ground={'life': 4, 'life0': 4})
        scenario.desk.tick()
        scenario.squad(exists=True, ground={'life': 3.8, 'life0': 4})   # 5 %: not yet
        scenario.desk.tick()
        self.assertEqual(scenario.desk.reports(), [])
        scenario.squad(exists=True, ground={'life': 3.5, 'life0': 4})   # 12.5 % since the start
        scenario.desk.tick()
        self.assertIn('some of your soldiers are wounded', scenario.desk.reports()[0]['report'])
        scenario.squad(exists=True, alive=3, ground={'life': 2.5, 'life0': 3})   # one died: a loss, not wounds
        scenario.desk.tick()
        self.assertIn('you just lost 1', scenario.desk.reports()[0]['report'])

    def test_killed_it_stops_answering_and_calling(self):
        scenario = Scenario()
        scenario.desk.tick()
        scenario.squad(exists=True, alive=0)
        scenario.desk.tick()
        self.assertEqual(scenario.desk.reports(), [])
        self.assertEqual(scenario.desk.prefilter('Tango, do you copy?'), '')
        self.assertTrue(scenario.desk.silent({'report': 'anything'}))
        scenario.squad(exists=False)
        self.assertEqual(scenario.desk.prefilter('Tango?'), '')

    def test_carried_it_is_not_dead(self):
        scenario = Scenario()
        scenario.squad(exists=False)
        scenario.state.transport['Buenos'] = {'carrier': 'Cobra', 'ride': 'helicopter', 'stage': 'aboard'}
        self.assertFalse(scenario.desk.dead())
        self.assertIsNone(scenario.desk.prefilter('Tango?'))


class Orders(unittest.TestCase):
    def test_move_to_a_zone(self):
        scenario = Scenario()
        result = SquadToolBox(scenario.desk).call('squad.move_to', json.dumps({'destination': 'lz north',
                                                                               'speed': 'fast'}))
        self.assertEqual(json.loads(result), {'ok': True, 'detail': 'moving'})
        action, group, lat, lon, speed = scenario.dcs.sent[-1]
        self.assertEqual((action, group, speed), ('move', 'Buenos', 'fast'))
        self.assertAlmostEqual(lat, at(5000, 0)[0], 6)

    def test_unknown_zone_names_the_ones_there_are(self):
        result = Scenario().desk.move_to('the bridge')
        self.assertEqual(result, {'ok': False, 'detail': 'no mission zone called the bridge (zones: LZ North, Base)'})

    def test_cannot_go_to_a_helicopter_out_of_sight(self):
        scenario = Scenario(heli_north_m=30000, heli_in_air=True)
        self.assertFalse(scenario.desk.move_to('helicopter')['ok'])
        self.assertEqual(scenario.dcs.sent, [])                           # nothing reached DCS

    def test_wait_for_pickup_at_the_helicopter(self):
        scenario = Scenario(task_answer=(True, 'waiting for pickup'))
        self.assertTrue(scenario.desk.wait_for_pickup('helicopter')['ok'])
        self.assertEqual(scenario.dcs.sent[-1][0], 'wait_pickup')
        self.assertEqual(scenario.dcs.sent[-1][4], 200.0)

    def test_board_only_a_landed_helicopter_then_aboard(self):
        flying = Scenario(heli_north_m=300, heli_in_air=True)
        self.assertEqual(flying.desk.board_helicopter(), {'ok': False, 'detail': 'the helicopter has not landed near you'})
        scenario = Scenario(heli_north_m=100)
        self.assertEqual(scenario.desk.board_helicopter(), {'ok': True, 'detail': 'embarking Buenos'})
        self.assertEqual(scenario.dcs.sent[-1], ('embark_troops', 'Buenos'))
        # Live 2026-11-02: aboard, the mission still reports the group where it embarked, alive.
        self.assertIn('aboard', scenario.desk.situation()['squad'])
        self.assertEqual(scenario.desk.board_helicopter(), {'ok': True, 'detail': 'already aboard the helicopter'})
        self.assertFalse(scenario.desk.move_to('LZ North')['ok'])                    # disembark first
        self.assertEqual(scenario.desk.disembark(), {'ok': True, 'detail': 'embarking Buenos'})
        self.assertNotIn('aboard', str(scenario.desk.situation()['squad']))

    def test_aboard_comes_from_dcs_radio_menu(self):
        scenario = Scenario()
        scenario.dcs.aboard = 'Buenos, Otros'
        scenario.desk.tick()                       # asks
        scenario.desk.next_on_board = 0
        scenario.desk.tick()                       # reads the answer, asks again
        self.assertTrue(scenario.desk.aboard())
        self.assertIn('aboard', scenario.desk.situation()['squad'])
        scenario.dcs.aboard = 'Otros'
        for _ in range(2):                         # the answer is read one cycle later (every 3 s in DCS)
            scenario.desk.next_on_board = 0
            scenario.desk.tick()
        self.assertFalse(scenario.desk.aboard())

    def test_dcs_refusal_is_reported(self):
        scenario = Scenario(heli_north_m=100, native_answer=('unavailable', 'Buenos is not ready to embark on this '
                                                                            'helicopter (offered: )'))
        result = scenario.desk.board_helicopter()
        self.assertFalse(result['ok'])
        self.assertIn('not ready to embark', result['detail'])

    def test_no_answer_from_dcs(self):
        scenario = Scenario()
        scenario.desk.tasks = type('Silent', (), {'send': lambda self, *a: 'never'})()
        import squad.desk as module
        wait, module.REPLY_WAIT_S = module.REPLY_WAIT_S, 0.1
        try:
            self.assertIn('no answer from the mission', scenario.desk.hold()['detail'])
        finally:
            module.REPLY_WAIT_S = wait

    def test_every_tool_takes_the_arguments_its_definition_names(self):
        # Live 2026-11-02: wait_for_pickup's definition said "destination", the code "where": every call failed.
        scenario = Scenario(heli_north_m=100)
        box = SquadToolBox(scenario.desk)
        examples = {'move_to': {'destination': 'here', 'speed': 'slow'}, 'wait_for_pickup': {'destination': 'here'},
                    'board_helicopter': {}, 'board_vehicle': {'vehicle': 'Bison'}, 'disembark': {}, 'hold': {},
                    'set_rules_of_engagement': {'rules': 'hold'},
                    'pop_smoke': {'color': 'green'}, 'attack': {'enemy_group': 'M1'},
                    'tell_pilot_when': {'condition': 'enemies_seen', 'request': 'tell me when you see enemies'}}
        self.assertEqual(set(examples), {d['name'] for d in box.definitions})
        for definition in box.definitions:
            arguments = examples[definition['name']]
            self.assertTrue(set(arguments) <= set(definition['parameters']['properties']), definition['name'])
            result = json.loads(box.call(f'squad.{definition["name"]}', json.dumps(arguments)))
            self.assertNotIn('error', result, definition['name'])

    def test_unknown_tool(self):
        self.assertIn('unknown tool', SquadToolBox(Scenario().desk).call('squad.self_destruct', '{}'))

    def test_watch_is_repeated(self):
        scenario = Scenario()
        scenario.desk.tick()
        scenario.desk.tick()
        self.assertEqual([s for s in scenario.dcs.sent if s[0] == 'watch'], [('watch', 'Buenos')])   # every 5 s

    def test_group_renamed_in_the_gui(self):
        """The group was renamed in the Mission Editor and then in the GUI: the hook is told at once."""
        scenario = Scenario()
        scenario.desk.tick()
        scenario.desk.configure({'name': 'Tango', 'squad': {'groups': ['Tango'], 'name': 'Tango'}})
        scenario.desk.tick()
        self.assertEqual([s for s in scenario.dcs.sent if s[0] == 'watch'], [('watch', 'Buenos'), ('watch', 'Tango')])
        self.assertEqual(scenario.desk.situation()['you_are'], 'Tango')


class WatchList(unittest.TestCase):
    """The hook keeps one list of groups: every squad's groups go together."""

    def test_two_squads_together_and_one_forgotten(self):
        from client import dcs_tasks
        channel = dcs_tasks.TaskChannel(port=9)   # discard port: nothing listens
        lines = []
        channel.send = lambda action, *args: lines.append((action, *args))
        channel.watch(['Buenos'], owner='buenos')
        channel.watch(['Tango'], owner='tango')
        self.assertEqual(lines[-1], ('watch', 'Buenos|Tango'))
        groups, _ = channel.watched['buenos']
        channel.watched['buenos'] = (groups, time.monotonic() - dcs_tasks.WATCH_FORGET_S - 1)   # switched off
        channel.watch(['Tango'], owner='tango')
        self.assertEqual(lines[-1], ('watch', 'Tango'))


if __name__ == '__main__':
    unittest.main()
