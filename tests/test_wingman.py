"""Offline tests for the scripted wingman agent (wingman/, 2026-11-04): its own AI aircraft group of the mission
(late activation), the state it is in from DCS's events, which orders fit that state, its orders as script tasks
(client/dcs_tasks.py wing_*), its number on the flight's frequency, and its condition events.

Run: python -m unittest discover tests
"""
import json
import math
import re
import tempfile
import time
import unittest
from pathlib import Path

from client.dcs_tasks import ACTIONS
from shared import triggers
from shared.agents import AgentStore
from shared.cockpit import CockpitState
from shared.dispatch import Dispatcher, addressed, all_stations_words
from instructor import voice_loop
from instructor.brain import Brain
from instructor.voice_loop import QUIET_PROMPT, REPORT_PROMPT, Responder
from wingman import desk as wingman_desk
from wingman.desk import (NOT_STARTED, PARKED_ASLEEP, WingmanDesk, clock, clock_span, formation_offset,
                          wingman_callsign)
from wingman.memory import PILOT_KEPT, Memory, brain_appendix
from wingman.playbook import PAGE_KEPT_S, Playbook
from wingman.tools import WingmanToolBox

LAT, LON = 42.0, 41.0
M_PER_DEG = 111_320.0
SETTINGS = {'language': 'en', 'srs': {'frequencies_mhz': [251.0], 'modulations': ['AM']}, 'voice': {}, 'speech': {}}


def at(north_m: float, east_m: float) -> tuple[float, float]:
    return LAT + north_m / M_PER_DEG, LON + east_m / (M_PER_DEG * math.cos(math.radians(LAT)))


class FakeTasks:
    """TaskChannel: records the orders; answers through the state like the hook."""

    def __init__(self, state):
        self.state, self.sent, self.watched = state, [], []

    def send(self, action, *args):
        assert ACTIONS[action] == len(args), (action, args)
        self.sent.append((action, *args))
        request_id = f't{len(self.sent)}'
        self.state.apply({'type': 'task', 'id': request_id, 'ok': True, 'detail': f'{action} done'})
        return request_id

    def watch(self, groups, owner=''):
        self.watched.append((list(groups), owner))


def groups(**air):
    """The hook's groups message with Manolo airborne 1 nm east of the leader, plus a squad and a zone."""
    lat, lon = at(0, 1852)
    entry = {'exists': True, 'alive': 1, 'size': 1, 'lat': lat, 'lon': lon, 'alt_m': 3000.0, 'speed_ms': 200.0}
    details = {'active': True, 'in_air': True, 'type': 'F-5E-3', 'life': 10.0, 'life0': 10.0, 'fuel': 0.8,
               'vx': 200.0, 'vz': 0.0, 'heading_deg': 0.0, 'ground_m': 0.0, 'started': 'cold', 'last_event': 'takeoff',
               'events': {'birth': 300.0, 'engine_startup': 250.0, 'takeoff': 60.0},
               'weapons': [{'name': 'Mk-82', 'count': 2, 'kind': 'bomb'}, {'name': 'M39', 'count': 280, 'kind': 'gun'}],
               'radio': {'line_of_sight': True, 'distance_m': 1852.0}}
    enemies = air.pop('enemies', None)
    exists = air.pop('exists', True)
    details.update(air)
    entry['air'] = details
    if enemies:
        entry['enemies'] = enemies
    entry['exists'] = exists
    squad_lat, squad_lon = at(10000, 0)
    zone_lat, zone_lon = at(20000, 0)
    return {'type': 'groups', 'groups': {'Manolo': entry, 'Tango': {'exists': True, 'alive': 4, 'size': 4,
                                                                     'lat': squad_lat, 'lon': squad_lon}},
            'zones': [{'name': 'Z1', 'lat': zone_lat, 'lon': zone_lon, 'radius_m': 300}]}


class Scenario:
    def __init__(self, chatter='rare', **air):
        self.state = CockpitState(aircraft='F-5E-3', callsign='Colt 1-1')
        self.state.flight = {'lat': LAT, 'lon': LON, 'alt_msl_m': 2900.0, 'alt_agl_m': 2500.0, 'heading_deg': 0.0}
        self.state.weather = {'Batumi': {'lat': LAT - 0.5, 'lon': LON, 'side': 2},
                              'Kobuleti': {'lat': LAT + 0.2, 'lon': LON, 'side': 2},
                              'Gudauta': {'lat': LAT + 0.1, 'lon': LON, 'side': 1}}
        self.state.traffic = [{'player': True, 'side': 2}]
        self.state.apply(groups(**air))
        self.tasks = FakeTasks(self.state)
        self.desk = WingmanDesk(self.state, {'id': 'manolo', 'name': 'Manolo', 'role': 'wingman',
                                             'wingman': {'number': 2, 'group': 'Manolo', 'chatter': chatter}},
                                self.tasks)
        self.folder = tempfile.TemporaryDirectory()
        self.memory = Memory(Path(self.folder.name))

    def call(self, tool, **arguments):
        return json.loads(WingmanToolBox(self.desk, self.memory).call(f'wingman.{tool}', json.dumps(arguments)))

    def update(self, **air):
        """A new groups message from the hook, then the desk's tick."""
        self.state.apply(groups(**air))
        self.desk.tick()


class States(unittest.TestCase):
    """The state the model reads, from DCS's events and the aircraft's motion (user's request 2026-11-04)."""

    def phase(self, **air):
        return Scenario(**air).desk.phase()

    def test_from_late_activation_to_airborne(self):
        self.assertEqual(self.phase(active=False, in_air=False, started=None, events=[], last_event=None),
                         NOT_STARTED)
        ground = {'in_air': False, 'vx': 0.0, 'events': {'birth': 5.0}, 'last_event': 'birth'}
        self.assertIn('starting the engines', self.phase(**ground))
        asleep = {'in_air': False, 'vx': 0.0, 'uncontrolled': True, 'started': None, 'events': [], 'last_event': None}
        self.assertEqual(self.phase(**asleep), PARKED_ASLEEP)                 # uncontrolled: visible, asleep
        self.assertEqual(Scenario(**asleep).desk.allowed(PARKED_ASLEEP), ('start_up', 'tell_pilot_when'))
        self.assertIn('starting the engines', self.phase(**{**ground, 'uncontrolled': True, 'started': 'uncontrolled'}))
        self.assertIn('engines running', self.phase(**{**ground, 'events': {'birth': 60.0, 'engine_startup': 10.0}}))
        self.assertIn('engines running', self.phase(**{**ground, 'started': 'hot'}))
        self.assertEqual(self.phase(**{**ground, 'started': 'runway'}), 'on the runway, about to take off')
        self.assertEqual(self.phase(**{**ground, 'vx': 8.0}), 'taxiing to the runway')
        self.assertEqual(self.phase(**{**ground, 'vx': 60.0}), 'takeoff roll')
        self.assertEqual(self.phase(), 'airborne')

    def test_after_landing_and_lost(self):
        landed = {'in_air': False, 'vx': 0.0, 'last_event': 'land', 'events': {'takeoff': 900.0, 'land': 30.0}}
        self.assertEqual(self.phase(**landed), 'on the ground after landing, stopped')
        self.assertEqual(self.phase(**{**landed, 'vx': 8.0}), 'taxiing in after landing')
        self.assertEqual(self.phase(**{**landed, 'last_event': 'engine_shutdown'}), 'on the ground, engines off')
        self.assertTrue(self.phase(last_event='crash').startswith('lost'))
        self.assertTrue(self.phase(exists=False).startswith('lost'))

    def test_situation_says_the_state_and_the_orders_it_allows(self):
        situation = Scenario(active=False, in_air=False).desk.situation()
        self.assertEqual(situation['your_state'], NOT_STARTED)
        self.assertEqual(situation['orders_you_can_carry_out_now'], ['start_up', 'tell_pilot_when'])
        self.assertNotIn('you', situation)
        situation = Scenario().desk.situation()
        self.assertEqual(situation['you_are'], 'Manolo, Colt 1-2 (number 2 of the flight)')
        self.assertEqual(situation['your_state'], 'airborne')
        self.assertIn('strike', situation['orders_you_can_carry_out_now'])
        self.assertEqual(situation['you']['started'], 'cold, on the parking spot')
        self.assertEqual((situation['you']['altitude_ft'], situation['you']['speed_kt']), (9800, 389))
        self.assertEqual(situation['you_from_leader'], {'distance_nm': 1.0, 'clock': 3, 'height_vs_leader_ft': 330})
        self.assertEqual(situation['mission_zones'], ['Z1'])

    def test_no_group_or_no_news(self):
        scenario = Scenario()
        scenario.desk.configure({'name': 'Manolo', 'wingman': {'number': 2}})
        self.assertIn('no DCS group', scenario.desk.situation()['you'])
        scenario = Scenario()
        scenario.state.groups_at = time.monotonic() - 60
        self.assertIn('no news', scenario.desk.situation()['you'])


class Orders(unittest.TestCase):
    def test_start_up_only_before_it_is_started(self):
        scenario = Scenario(active=False, in_air=False)
        self.assertTrue(scenario.call('start_up', how='cold')['ok'])
        self.assertEqual(scenario.tasks.sent, [('wing_start', 'Manolo', 'cold')])
        self.assertFalse(scenario.call('follow', clock_position=3, distance_m=200, height_m=0)['ok'])
        airborne = Scenario()
        result = airborne.call('start_up', how='air')
        self.assertEqual((result['ok'], result['detail']), (False, 'you are already started (airborne)'))

    def test_flying_orders_wait_for_the_air(self):
        scenario = Scenario(in_air=False, vx=8.0)
        result = scenario.call('cover_me')
        self.assertFalse(result['ok'])
        self.assertIn('taxiing to the runway', result['detail'])
        self.assertIn('once airborne', result['detail'])
        self.assertTrue(scenario.call('weapons', rules='hold')['ok'])     # rules of engagement: also on the ground
        self.assertEqual(scenario.tasks.sent, [('wing_roe', 'Manolo', 'hold')])

    def test_every_tool_airborne(self):
        scenario = Scenario(enemies=[{'group': 'Convoy', 'units': 4, 'type': 'BTR-80', 'lat': LAT, 'lon': LON}])
        examples = {'start_up': None, 'follow': {'clock_position': 9, 'distance_m': 300, 'height_m': 30},
                    'cover_me': {}, 'attack': {'target': 'convoy', 'how': 'all', 'against': 'ground'},
                    'strike': {'target': 'tango', 'weapon': 'auto', 'altitude_ft': 0, 'attack_heading': -1,
                               'attack': 'auto'},
                    'fly_to': {'where': 'Z1', 'altitude_ft': 200, 'above': 'ground', 'speed_kt': 400},
                    'orbit': {'where': 'Z1', 'altitude_ft': 0}, 'return_to_base': {'airfield': ''},
                    'weapons': {'rules': 'free'}, 'tell_pilot_when': {'condition': 'fuel_low', 'request': 'bingo'},
                    'remember': {'text': 'The leader is called Chamaco', 'about': 'pilot'},
                    'forget': {'text': 'chamaco'}, 'leave_alone': {'target': 'convoy', 'how': 'attack'},
                    'playbook': {'page': 'sam-attack'},
                    'land': None, 'pick_up': None, 'drop_off': None}   # helicopters only (class Helicopter)
        self.assertEqual(set(examples), {d['name'] for d in WingmanToolBox.definitions})
        for name, arguments in examples.items():
            if arguments is not None:
                self.assertTrue(scenario.call(name, **arguments)['ok'], name)
        sent = {s[0]: s for s in scenario.tasks.sent}
        self.assertEqual(sent['wing_follow'], ('wing_follow', 'Manolo', 0.0, -300.0, 30.0))   # 9 o'clock: abreast left
        self.assertEqual(sent['wing_escort'], ('wing_escort', 'Manolo'))
        self.assertEqual(sent['wing_attack'], ('wing_attack', 'Manolo', 'Convoy', 'all'))
        self.assertAlmostEqual(sent['wing_bomb'][2], at(10000, 0)[0])                # where the squad Tango is
        self.assertEqual(sent['wing_orbit'][4], 3000.0)                               # its own altitude
        self.assertEqual(sent['wing_land'], ('wing_land', 'Manolo', 'Kobuleti'))     # nearest friendly field

    def test_strike_a_sam_point_and_fly_low(self):
        # User, 2026-11-04: without anti-radiation missiles DCS refuses to attack an SA-8; its point it takes.
        sam = {'group': 'Ground-3', 'units': 1, 'type': 'Osa 9A33 ln', 'air': False, **IN_Z1}
        scenario = Scenario(editor_task='CAS', enemies=[sam])
        self.assertTrue(scenario.call('fly_to', where='Ground-3', altitude_ft=50, above='ground', speed_kt=450)['ok'])
        action, _, lat, lon, alt_m, kind, speed = scenario.tasks.sent[-1]
        self.assertEqual((action, alt_m, kind, round(speed)), ('wing_fly', 30.0, 'RADIO', 232))   # 100 ft lowest
        self.assertAlmostEqual(lat, IN_Z1['lat'])
        result = scenario.call('strike', target='ground 3', weapon='rockets', altitude_ft=500, attack_heading=450,
                               attack='dive')
        self.assertTrue(result['ok'])
        action, _, lat, lon, weapon, alt_m, heading, attack = scenario.tasks.sent[-1]
        self.assertEqual((action, weapon, round(alt_m), heading, attack), ('wing_bomb', 'rockets', 152, 90.0, 'dive'))
        self.assertFalse(scenario.call('strike', target='Ground-3', weapon='laser', altitude_ft=0, attack_heading=-1,
                                       attack='auto')['ok'])

    def test_formation_positions(self):
        self.assertEqual(formation_offset(3, 200), (0, 200))
        self.assertEqual(formation_offset(6, 200), (-200, 0))
        self.assertEqual(formation_offset(4, 200), (-100, 173))
        result = Scenario().call('follow', clock_position=12, distance_m=200, height_m=0)
        self.assertFalse(result['ok'])

    def test_formation_kept_when_follow_does_nothing(self):
        # Live 2026-11-04: it took off ahead of the leader and DCS's Follow did nothing (8 miles away, flying on).
        scenario = Scenario()
        scenario.state.flight['tas_ms'] = 200.0

        def wingman_at(north_m, east_m):
            scenario.state.groups['Manolo']['lat'], scenario.state.groups['Manolo']['lon'] = at(north_m, east_m)
            scenario.state.groups_at = time.monotonic()
            scenario.desk.tick()
            return scenario.tasks.sent[-1] if scenario.tasks.sent else None

        self.assertTrue(scenario.call('follow', clock_position=4, distance_m=200, height_m=0)['ok'])
        self.assertEqual(wingman_at(0, 1852)[0], 'wing_follow')                # 1 nm at 3 o'clock: in formation
        action, _, lat, lon, alt_m, kind, speed = wingman_at(9000, 0)          # 5 nm ahead: flown back
        self.assertEqual((action, alt_m, kind, speed), ('wing_fly', 2900.0, 'BARO', 200.0))
        self.assertLess(lat, LAT)                                              # to a point behind the leader
        self.assertIn('rejoining', scenario.desk.situation()['current_job'])
        count = len(scenario.tasks.sent)
        wingman_at(8000, 0)
        self.assertEqual(len(scenario.tasks.sent), count)                      # renewed every 15 s, not each tick
        scenario.desk.next_steer = 0.0                                         # 15 s later
        self.assertEqual(wingman_at(-6000, 0)[-1], 250.0)                      # far behind: faster than the leader
        self.assertEqual(wingman_at(-2000, 0), ('wing_follow', 'Manolo', 100.0, 173.0, 0.0))   # close behind
        self.assertFalse(scenario.desk.steering)
        scenario.call('orbit', where='here', altitude_ft=0)                    # another order: no formation
        count = len(scenario.tasks.sent)
        wingman_at(20000, 0)
        self.assertEqual(len(scenario.tasks.sent), count)

    def test_airborne_before_the_leader_it_orbits_the_field(self):
        # User, 2026-11-04: it took off first and turned back to land; orbit near the field until the leader is up.
        scenario = Scenario()
        scenario.state.flight.update(alt_agl_m=2.0, alt_msl_m=13.0)
        scenario.desk.formation = scenario.desk.rejoin_offset()
        scenario.state.groups['Manolo']['lat'], scenario.state.groups['Manolo']['lon'] = at(9000, 0)
        scenario.desk.tick()
        scenario.desk.tick()
        self.assertEqual(scenario.tasks.sent, [('wing_orbit', 'Manolo', LAT, LON, 613.0, 150.0)])   # once
        self.assertIn('orbiting over the field', scenario.desk.situation()['current_job'])
        scenario.state.flight.update(alt_agl_m=200.0, alt_msl_m=213.0, tas_ms=120.0)   # the leader is airborne
        scenario.desk.tick()
        self.assertEqual(scenario.tasks.sent[1], ('wing_follow', 'Manolo', 100.0, 173.0, 0.0))
        self.assertEqual(scenario.tasks.sent[-1][0], 'wing_fly')               # 5 nm away: flown to the leader

    def test_airborne_call_says_it_waits_for_the_leader(self):
        scenario = Scenario(in_air=False, vx=8.0, events={'engine_startup': 100.0}, last_event='engine_startup')
        scenario.desk.tick()
        scenario.state.flight['alt_agl_m'] = 2.0
        scenario.update()                                                      # airborne, the leader on the ground
        self.assertIn('you took off before the leader: you orbit over the field', scenario.desk.reports()[0]['report'])

    def test_an_emitter_only_on_the_rwr_has_a_direction_not_a_place(self):
        # User, 2026-11-04: an Osa called at 21.3 miles with its exact place, that nobody could see.
        osa = {'group': 'Osa-1', 'type': 'Osa 9A33 ln', 'air': False, 'rwr': True, 'bearing_deg': 270}
        scenario = Scenario(enemies=[osa])
        scenario.desk.last_phase = 'airborne'   # not the first look: calls are made
        scenario.desk.tick()
        [report] = scenario.desk.reports()
        self.assertIn('radar warning receiver picks up: Osa 9A33 ln (Osa-1), 9 o\'clock (direction only',
                      report['report'])
        situation = scenario.desk.situation()
        self.assertEqual(situation['enemies_detected'], 'none')
        self.assertEqual(situation['radar_warning_receiver']['emitters'],
                         [{'group': 'Osa-1', 'type': 'Osa 9A33 ln', 'clock': 9}])
        for tool, arguments in (('attack', {'target': 'Osa-1', 'how': 'all', 'against': 'ground'}),
                                ('strike', {'target': 'Osa-1', 'weapon': 'auto', 'altitude_ft': 0,
                                            'attack_heading': -1, 'attack': 'auto'}),
                                ('fly_to', {'where': 'Osa-1', 'altitude_ft': 100, 'above': 'ground', 'speed_kt': 0})):
            result = scenario.call(tool, **arguments)
            self.assertFalse(result['ok'], tool)
            self.assertIn('only on your radar warning receiver', result['detail'])
        self.assertEqual(scenario.tasks.sent, [])
        scenario.update(enemies=[osa])
        self.assertEqual(scenario.desk.reports(), [])                          # called once
        seen = {'group': 'Osa-1', 'type': 'Osa 9A33 ln', 'units': 1, 'air': False, **IN_Z1}
        scenario.update(enemies=[seen])                                        # now its eyes see it
        self.assertNotIn('radar_warning_receiver', scenario.desk.situation())
        self.assertTrue(scenario.call('strike', target='Osa-1', weapon='auto', altitude_ft=0, attack_heading=-1,
                                      attack='auto')['ok'])

    def test_unknown_targets(self):
        scenario = Scenario()
        self.assertIn('no zone or group called Ghost',
                      scenario.call('attack', target='Ghost', how='all', against='ground')['detail'])
        self.assertIn('known: Z1, Tango', scenario.call('strike', target='Z9', weapon='auto', altitude_ft=0,
                                                       attack_heading=-1, attack='auto')['detail'])
        self.assertEqual(scenario.tasks.sent, [])
        self.assertTrue(scenario.call('strike', target='Tango squad', weapon='auto', altitude_ft=0, attack_heading=-1,
                                      attack='auto')['ok'])   # live: the model said "Tango squad"

    def test_lost_refuses_everything(self):
        scenario = Scenario(last_event='dead')
        self.assertFalse(scenario.call('cover_me')['ok'])
        self.assertEqual(scenario.tasks.sent, [])

    def test_tick_watches_its_group(self):
        scenario = Scenario()
        scenario.desk.tick()
        self.assertEqual(scenario.tasks.watched, [(['Manolo'], 'manolo')])


class Knowledge(unittest.TestCase):
    def test_enemies_by_clock_from_the_leader(self):
        lat, lon = at(18520, 1852)   # 10 nm north of the wingman
        situation = Scenario(enemies=[{'group': 'Bandits', 'units': 2, 'type': 'MiG-29A', 'lat': lat, 'lon': lon,
                                       'alt_m': 6000.0}]).desk.situation()
        self.assertEqual(situation['enemies_detected'], [{'group': 'Bandits', 'type': 'MiG-29A', 'units': 2,
                                                          'distance_nm': 10.0, 'clock': 12, 'altitude_ft': 19700}])

    def test_friends_in_sight(self):
        lat, lon = at(0, 1852 + 3704)   # 2 nm east of the wingman
        situation = Scenario(friends_in_sight=[{'group': 'Tango', 'units': 4, 'type': 'Soldier M4', 'lat': lat,
                                                'lon': lon}]).desk.situation()
        self.assertEqual(situation['friends_in_sight'], [{'group': 'Tango', 'type': 'Soldier M4', 'units': 4,
                                                          'distance_nm': 2.0, 'clock': 3}])
        self.assertNotIn('friends_in_sight', Scenario().desk.situation())

    def test_facts(self):
        self.assertEqual(Scenario(life=7.0, fuel=0.2, weapons=[{'name': 'M39', 'count': 100, 'kind': 'gun'}])
                         .desk.facts(), {'lost': False, 'alive': 1, 'size': 1, 'damage': 0.3, 'enemies': False,
                                         'fuel': 0.2, 'fuel_low': True, 'out_of_weapons': True, 'airborne': True})
        self.assertEqual(Scenario(exists=False).desk.facts(), {'lost': True})
        self.assertIsNone(Scenario(active=False).desk.facts())   # not started: no baseline yet

    def test_radio_behind_terrain(self):
        scenario = Scenario(radio={'line_of_sight': False, 'distance_m': 20000.0})
        self.assertFalse(scenario.desk.can_reach_pilot())
        self.assertEqual(scenario.desk.prefilter('Two, rejoin'), '')

    def test_callsign_and_clock(self):
        self.assertEqual(wingman_callsign('Enfield 1-1', 3), 'Enfield 1-3')
        self.assertEqual(wingman_callsign(None, 4), 'number 4')
        self.assertEqual((clock(90, 0), clock(0, 90), clock(180, 0)), (3, 9, 6))


WINGMEN = [{'id': f'w{n}', 'name': name, 'role': 'wingman', 'wingman': {'number': n}}
           for n, name in ((2, 'Viper'), (3, 'Cobra'), (4, 'Snake'))]


class Calls(unittest.TestCase):
    """The flight's frequency (user's design): call one by its number or name, or the whole flight."""

    def setUp(self):
        self.everyone = all_stations_words()

    def to(self, text):
        chosen, how = addressed(text, WINGMEN, self.everyone)
        return [a['wingman']['number'] for a in chosen], how

    def test_by_number_or_name(self):
        self.assertEqual(self.to('Two, cover me'), ([2], 'name'))
        # The number in another language, from that language's own locale file (no Spanish in the tests).
        two = json.loads((Path(__file__).resolve().parent.parent / 'locales' / 'es.json')
                         .read_text(encoding='utf-8'))['radio']['numbers']['2'][0]
        self.assertEqual(self.to(f'{two} cover me'), ([2], 'name'))
        self.assertEqual(self.to('Colt 1-3, rejoin'), ([3], 'name'))        # heard as "colt 1 3"
        self.assertEqual(self.to('Snake, engage bandits'), ([4], 'name'))

    def test_the_whole_flight(self):
        self.assertEqual(self.to('Colt flight, two bandits north'), ([2, 3, 4], 'all'))   # "flight" comes first
        self.assertEqual(self.to('Colt flight, check in'), ([2, 3, 4], 'all'))
        self.assertEqual(Dispatcher(self.everyone).route('Colt flight, fence in', WINGMEN)[1], 'all')


class FakeAgents:
    def __init__(self, *agents):
        self.agents = {a['id']: a for a in agents}

    def active(self):
        return list(self.agents.values())

    def get(self, agent_id):
        return self.agents.get(agent_id)


class Events(unittest.TestCase):
    def test_airborne_bingo_hit_and_winchester(self):
        scenario = Scenario(in_air=False, vx=8.0)
        agent = {'id': 'w2', 'name': 'Manolo', 'role': 'wingman', 'active': True,
                 'wingman': {'number': 2, 'group': 'Manolo'},
                 'events': [{'when': 'airborne', 'instructions': 'Say you are airborne.'},
                            {'when': 'fuel_low', 'instructions': 'Call bingo.'},
                            {'when': 'damaged', 'instructions': 'Say you are hit.'},
                            {'when': 'out_of_weapons', 'instructions': 'Call winchester.'}]}
        watch = triggers.ConditionWatch(FakeAgents(agent), {'w2': scenario.desk}.get)
        self.assertEqual(watch.tick(), [])                                   # baseline: taxiing
        scenario.state.apply(groups())
        (_, event), = watch.tick()
        self.assertEqual(event['cause'], 'your aircraft took off and is airborne')
        scenario.state.apply(groups(fuel=0.2, life=8.0, weapons=[]))
        (_, event), = watch.tick()
        self.assertEqual(event['when'], 'fuel_low+damaged+out_of_weapons')
        self.assertEqual(event['cause'], 'your aircraft is low on fuel (20 % left); your aircraft was hit (damage '
                                         'now 20 %); your aircraft has no weapons left')

    def test_tell_me_when(self):
        scenario = Scenario(in_air=False, vx=8.0)
        self.assertTrue(scenario.call('tell_pilot_when', condition='airborne', request='call airborne')['ok'])
        self.assertIn('already true', Scenario(fuel=0.1).desk.tell_pilot_when('fuel_low', 'x')['detail'])


class Store(unittest.TestCase):
    def test_add_with_its_group_and_renumber(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = AgentStore(Path(tmp) / 'agents', SETTINGS)
            two = agents.add('wingman', 'Manolo', 316.0, 'AM', SETTINGS, group='Manolo', number=2)
            three = agents.add('wingman', 'Lobo', 316.0, 'AM', SETTINGS, group='Lobo', number=3)   # same frequency
            self.assertEqual((two['wingman'], three['wingman']), ({'number': 2, 'group': 'Manolo', 'chatter': 'rare'},
                                                                  {'number': 3, 'group': 'Lobo', 'chatter': 'rare'}))
            self.assertIn('personality', agents.mission(two['id']))   # the user's part; the rules are fixed
            self.assertEqual(agents.update(two['id'], {'chatter': 'off'})['wingman']['chatter'], 'off')
            with self.assertRaisesRegex(ValueError, 'chatter'):
                agents.update(two['id'], {'chatter': 'loud'})
            self.assertEqual(agents.update(two['id'], {'number': 4})['wingman']['number'], 4)
            self.assertEqual(agents.update(two['id'], {'group': 'Manolo 2'})['wingman']['group'], 'Manolo 2')
            for bad in (1, 5, 'x'):
                with self.assertRaisesRegex(ValueError, 'number'):
                    agents.update(two['id'], {'number': bad})
            with self.assertRaisesRegex(ValueError, 'number'):
                agents.add('wingman', 'X', 316.0, 'AM', SETTINGS, group='X', number=7)
            with self.assertRaisesRegex(ValueError, 'group'):
                agents.add('wingman', 'Y', 316.0, 'AM', SETTINGS, group='', number=3)


IN_Z1 = dict(zip(('lat', 'lon'), at(20000, 500)))   # inside zone Z1 (radius 300 m, searched over 1.5 km at least)
TANKS = {'group': 'Tanks', 'units': 3, 'type': 'T-72B', 'air': False, **IN_Z1}


class EditorTask(unittest.TestCase):
    """The group's Mission Editor task limits DCS's AI (user, 2026-11-04: CAP does not do CAS)."""

    def test_cap_with_bombs_does_not_fit_and_refuses_the_ground(self):
        scenario = Scenario(editor_task='CAP', enemies=[TANKS])
        you = scenario.desk.situation()['you']
        self.assertEqual(you['tasking_from_base'], 'CAP')
        self.assertIn('loaded with ground weapons', you['task_note'])
        result = scenario.call('attack', target='Z1', how='all', against='ground')
        self.assertFalse(result['ok'])
        self.assertIn('not cleared for ground attack: your tasking from base is CAP', result['detail'])
        self.assertEqual(scenario.tasks.sent, [])

    def test_never_the_editor_in_what_the_model_reads(self):
        # User, 2026-11-05: "Mission Editor" on the radio breaks the role-play.
        scenario = Scenario(editor_task='Transport', started='uncontrolled')
        text = json.dumps(scenario.desk.situation()) + json.dumps(
            scenario.call('attack', target='Z1', how='all', against='ground'))
        self.assertIn('not cleared to attack', text)
        self.assertNotIn('editor', text.lower())

    def test_known_before_it_is_started(self):
        situation = Scenario(active=False, in_air=False, editor_task='CAS').desk.situation()
        self.assertEqual(situation['your_tasking_from_base'], 'CAS')

    def test_no_weapons_for_air_targets(self):
        scenario = Scenario(editor_task='CAS', weapons=[{'name': 'Mk-82', 'count': 2, 'kind': 'bomb',
                                                         'against': 'ground'}])
        self.assertIn('no weapons left for air', scenario.call('attack', target='Z1', how='all',
                                                               against='air')['detail'])


class Attacks(unittest.TestCase):
    """Attack jobs: all the targets, or one at a time with the leader's clearance; then it rejoins by itself."""

    def test_all_in_a_zone_then_rejoin(self):
        scenario = Scenario(editor_task='CAS')
        result = scenario.call('attack', target='Z1', how='all', against='ground')
        self.assertTrue(result['ok'])
        action, group, lat, lon, alt, speed = scenario.tasks.sent[-1]
        self.assertEqual((action, alt, speed), ('wing_orbit', 3000.0, 200.0))   # over it; targets given one by one
        self.assertIn('attacking all ground targets in zone Z1', scenario.desk.situation()['current_job'])
        scenario.update(enemies=[TANKS])
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', 'Tanks', 'all'))   # a group: whole
        self.assertIsNotNone(scenario.desk.job)
        scenario.desk.job['started'] -= wingman_desk.JOB_MIN_S + 1            # long enough...
        scenario.desk.job['last_seen'] -= wingman_desk.JOB_CLEAR_S + 1        # ...and nothing seen there lately
        scenario.desk.enemy_memory.clear()
        scenario.update()
        self.assertIsNone(scenario.desk.job)
        self.assertEqual(scenario.tasks.sent[-1], ('wing_follow', 'Manolo', 100.0, 173.0, 0.0))   # 4 o'clock
        (event,) = scenario.desk.reports()
        self.assertIn('no more targets found in the zone', event['report'])
        self.assertIn('rejoining the leader', event['report'])

    def test_winchester_ends_the_attack(self):
        scenario = Scenario(editor_task='CAS')
        scenario.call('attack', target='Z1', how='all', against='ground')
        scenario.update(weapons=[{'name': 'M39', 'count': 280, 'kind': 'gun', 'against': 'both'}])
        self.assertIsNone(scenario.desk.job)
        self.assertIn('out of weapons', scenario.desk.reports()[0]['report'])

    def test_one_at_a_time_with_clearance(self):
        scenario = Scenario(editor_task='CAS', enemies=[TANKS], combat=[])
        scenario.desk.tick()                                                   # combat log baseline
        result = scenario.call('attack', target='Z1', how='one', against='ground')
        self.assertTrue(result['ok'])
        self.assertEqual([s[0] for s in scenario.tasks.sent], ['wing_orbit', 'wing_attack'])
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', 'Tanks', 'one'))
        scenario.update(enemies=[TANKS], combat=[{'n': 1, 'kind': 'weapon_released', 'weapon': 'Mk-82'},
                                                 {'n': 2, 'kind': 'kill', 'target': 'T-72B', 'air': False}])
        self.assertTrue(scenario.desk.job['waiting'])
        (event,) = scenario.desk.reports()
        self.assertIn('you released: 1 Mk-82', event['report'])
        self.assertIn('you destroyed a T-72B', event['report'])
        self.assertIn('clearance', event['report'])
        self.assertIn('waiting for the leader\'s clearance', scenario.desk.situation()['current_job'])
        self.assertTrue(scenario.call('attack', target='Z1', how='one', against='ground')['ok'])   # "cleared"
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', 'Tanks', 'one'))
        self.assertFalse(scenario.desk.job['waiting'])

    def test_all_in_a_zone_bombs_its_statics_one_after_another(self):
        # Live 2026-11-04: the targets of zone L-1 were static objects, which DCS's zone attack ignores.
        statics = [{'group': f'Static {n}', 'units': 1, 'type': kind, 'air': False, 'static': True,
                    **dict(zip(('lat', 'lon'), at(20000 + 100 * n, 500)))}
                   for n, kind in ((1, 'T-72B'), (2, 'BTR-80'))]
        scenario = Scenario(editor_task='CAS', enemies=statics, combat=[])
        scenario.desk.tick()
        scenario.call('attack', target='Z1', how='all', against='ground')
        scenario.update(enemies=statics, combat=[])
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', 'Static 1', 'one'))   # nearest the centre
        self.assertIn('now attacking Static 1', scenario.desk.situation()['current_job'])
        scenario.update(enemies=statics[1:], combat=[{'n': 1, 'kind': 'kill', 'target': 'T-72B', 'name': 'Static 1',
                                                      'air': False}])
        self.assertNotIn('Static 1', scenario.desk.enemy_memory)                 # destroyed: not a target any more
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', 'Static 2', 'one'))
        self.assertIn('you destroyed a T-72B', scenario.desk.reports()[0]['report'])

    def test_contacts_of_a_target_area_in_one_call(self):
        statics = [{'group': f'Static {n}', 'units': 1, 'type': 'r11_volvo', 'air': False, 'static': True,
                    'lat': LAT, 'lon': LON} for n in range(8)]
        scenario = Scenario()
        scenario.desk.tick()
        scenario.update(enemies=statics[:5])
        self.assertEqual(scenario.desk.reports(), [])                          # gathered a moment
        scenario.update(enemies=statics)                                       # more come into sight
        scenario.desk.batch_last -= 6                                          # then none for 5 s
        scenario.update(enemies=statics)
        (event,) = scenario.desk.reports()
        self.assertTrue(event['report'].startswith('new ground contacts: 8 targets (8 r11_volvo), 9 o\'clock from '
                                                   'the leader, 1 mile. Call them as one group'), event['report'])
        self.assertIn('name them one by one only if the leader asks', event['report'])
        scenario.update(enemies=statics)
        self.assertEqual(scenario.desk.reports(), [])                          # once

    def test_clock_spans(self):
        self.assertEqual(clock_span([10]), "10 o'clock")
        self.assertEqual(clock_span([11, 10, 10]), "10 to 11 o'clock")
        self.assertEqual(clock_span([12, 1, 11]), "11 to 1 o'clock")

    def test_leave_alone_those_soldiers(self):
        # User, 2026-11-04: "ignore those six soldiers" (infantry near houses inside zone L-1).
        soldiers = {'group': 'M-1', 'units': 6, 'type': 'Soldier M4 GRG', 'air': False,
                    **dict(zip(('lat', 'lon'), at(20000, 200)))}                # nearest the zone's centre
        tank = {'group': 'Static T-72B', 'units': 1, 'type': 'T-72B', 'air': False, 'static': True, **IN_Z1}
        scenario = Scenario(editor_task='CAS', enemies=[soldiers, tank], combat=[])
        scenario.desk.tick()
        result = scenario.call('leave_alone', target='m 1', how='ignore')
        self.assertEqual((result['ok'], result['detail']), (True, 'leaving M-1 alone'))
        self.assertIn('never attack it', next(e for e in scenario.desk.situation()['enemies_detected']
                                             if e['group'] == 'M-1')['leave_alone'])
        self.assertIn('leave M-1 alone', scenario.call('attack', target='M-1', how='all', against='ground')['detail'])
        scenario.call('attack', target='Z1', how='all', against='ground')
        scenario.update(enemies=[soldiers, tank], combat=[])
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', 'Static T-72B', 'one'))   # not M-1
        self.assertNotIn('M-1', scenario.desk.in_zone())

    def test_left_for_later_then_asked(self):
        soldiers = {'group': 'M-1', 'units': 6, 'type': 'Soldier M4 GRG', 'air': False, **IN_Z1}
        scenario = Scenario(editor_task='CAS', enemies=[soldiers, TANKS], combat=[])
        scenario.desk.tick()
        scenario.call('attack', target='Z1', how='all', against='ground')
        scenario.update(enemies=[soldiers, TANKS], combat=[])
        first = scenario.tasks.sent[-1][2]
        result = scenario.call('leave_alone', target=first, how='later')   # attacking it right now: stop
        self.assertIn('ask the leader about it', result['detail'])
        self.assertEqual(scenario.tasks.sent[-1][0], 'wing_orbit')
        scenario.update(enemies=[soldiers, TANKS], combat=[])
        other = 'Tanks' if first == 'M-1' else 'M-1'
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', other, 'all'))
        scenario.desk.job['started'] -= wingman_desk.JOB_MIN_S + 1
        scenario.desk.job['last_seen'] -= wingman_desk.JOB_CLEAR_S + 1
        scenario.desk.enemy_memory.pop(other)
        scenario.state.apply(groups(enemies=[{**soldiers, 'group': first}] if first == 'M-1' else [TANKS]))
        scenario.desk.job['last_seen'] -= wingman_desk.JOB_CLEAR_S + 1
        scenario.desk.tick()
        self.assertIsNone(scenario.desk.job)
        self.assertIn(f'leave {first} for later: ask now whether to attack them',
                      scenario.desk.reports()[-1]['report'])

    def test_an_attack_that_releases_nothing_ends(self):
        # Live 2026-11-04: 6 minutes orbiting the zone after its last bombs, rockets left, no combat log.
        rockets = {'name': 'Hydra 70', 'count': 12, 'kind': 'rocket', 'against': 'ground'}
        scenario = Scenario(editor_task='CAS', weapons=[{'name': 'Mk-82', 'count': 5, 'kind': 'bomb'}, rockets])
        scenario.call('attack', target='Z1', how='all', against='ground')
        scenario.update(weapons=[rockets])
        self.assertIsNotNone(scenario.desk.job)                                # the bombs went: a release
        scenario.desk.job['last_release'] -= wingman_desk.JOB_IDLE_S
        scenario.update(weapons=[rockets])
        # Z1 is 11 miles away: still flying there (live: an Osa 19 miles off, called off on the way): not counted.
        self.assertIsNotNone(scenario.desk.job)
        scenario.desk.job.update(lat=LAT, lon=LON)                              # now near it
        scenario.desk.job['last_release'] -= wingman_desk.JOB_IDLE_S
        scenario.update(weapons=[rockets])
        self.assertIsNone(scenario.desk.job)
        self.assertEqual(scenario.tasks.sent[-1][0], 'wing_follow')
        self.assertIn('no weapon released for 3 minutes', scenario.desk.reports()[-1]['report'])

    def test_a_new_order_ends_the_attack(self):
        scenario = Scenario(editor_task='CAS', enemies=[TANKS])
        scenario.call('attack', target='Tanks', how='all', against='ground')
        self.assertEqual(scenario.tasks.sent[-1], ('wing_attack', 'Manolo', 'Tanks', 'all'))
        scenario.call('follow', clock_position=3, distance_m=200, height_m=0)
        self.assertIsNone(scenario.desk.job)


class Reports(unittest.TestCase):
    """Callouts phrased by the model: what just happened to the aircraft."""

    def test_takeoff_combat_and_hits(self):
        scenario = Scenario(in_air=False, vx=8.0, combat=[{'n': 4, 'kind': 'weapon_released'}])
        scenario.desk.tick()                                                   # taxiing; old log entries ignored
        self.assertEqual(scenario.desk.reports(), [])
        scenario.update(combat=[{'n': 4, 'kind': 'weapon_released'}])         # airborne
        self.assertIn('you took off and are airborne', scenario.desk.reports()[0]['report'])
        scenario.update(life=6.0, combat=[{'n': 5, 'kind': 'hit_taken', 'by': 'ZU-23'}])
        self.assertIn('you were hit by a ZU-23: your damage is now 40 percent', scenario.desk.reports()[0]['report'])

    def test_every_flight_step_once_even_when_dcs_tells_late(self):
        # Live 2026-11-04: DCS's takeoff event came after the aircraft was already in the air, its land event after
        # the landing roll had begun: no call was ever made.
        parked = {'in_air': False, 'vx': 0.0, 'uncontrolled': True, 'started': None, 'events': [], 'last_event': None}
        scenario = Scenario(**parked)
        scenario.desk.tick()
        steps = [({'started': 'uncontrolled', 'events': {'birth': 30.0}, 'last_event': 'birth'}, None),
                 ({'started': 'uncontrolled', 'events': {'birth': 40.0, 'engine_startup': 5.0}}, 'engines are running'),
                 ({'started': 'uncontrolled', 'vx': 8.0, 'events': {'engine_startup': 60.0}}, 'taxiing to the runway'),
                 ({'started': 'uncontrolled', 'vx': 60.0, 'events': {'engine_startup': 90.0}}, None),   # takeoff roll
                 ({'started': 'uncontrolled', 'in_air': True, 'vx': 90.0, 'events': {'engine_startup': 95.0}}, None),
                 ({'started': 'uncontrolled', 'in_air': True, 'vx': 100.0,
                   'events': {'engine_startup': 98.0, 'takeoff': 1.0}}, 'took off and are airborne'),
                 ({'started': 'uncontrolled', 'in_air': True, 'events': {'takeoff': 600.0}}, None),
                 ({'started': 'uncontrolled', 'vx': 60.0, 'events': {'takeoff': 900.0}}, None),   # rolling, no event yet
                 ({'started': 'uncontrolled', 'vx': 50.0, 'events': {'takeoff': 905.0, 'land': 2.0},
                   'last_event': 'land'}, 'you landed')]
        for changes, expected in steps:
            scenario.update(**{**parked, 'uncontrolled': False, **changes})
            reports = scenario.desk.reports()
            if expected is None:
                self.assertEqual(reports, [], changes)
            else:
                self.assertIn(expected, reports[0]['report'], changes)

    def test_no_calls_for_what_happened_before_the_program_started(self):
        scenario = Scenario()          # already airborne when the program starts
        scenario.desk.tick()
        self.assertEqual(scenario.desk.reports(), [])
        scenario.update(in_air=False, vx=40.0, last_event='land', events={'takeoff': 900.0, 'land': 5.0})
        self.assertIn('you landed', scenario.desk.reports()[0]['report'])

    def test_weapon_releases_are_gathered(self):
        scenario = Scenario(combat=[])
        scenario.desk.tick()
        scenario.update(combat=[{'n': 1, 'kind': 'weapon_released', 'weapon': 'Mk-82'},
                                {'n': 2, 'kind': 'weapon_released', 'weapon': 'Mk-82'}])
        self.assertEqual(scenario.desk.reports(), [])                          # a moment for the others
        scenario.desk.pending[-1]['at'] -= wingman_desk.REPORT_GATHER_S
        self.assertIn('you released: 2 Mk-82', scenario.desk.reports()[0]['report'])
        self.assertEqual(scenario.desk.reports(busy=True), [])

    def test_fuel_winchester_and_contacts_once(self):
        scenario = Scenario()
        scenario.desk.tick()
        scenario.update(fuel=0.2, weapons=[], enemies=[{**TANKS, 'lat': LAT, 'lon': LON}])
        report = scenario.desk.reports()[0]['report']
        self.assertIn('fuel is low: 20 percent', report)
        self.assertNotIn('out of weapons', report)                             # one reading is not enough
        scenario.desk.empty_since -= wingman_desk.WINCHESTER_CONFIRM_S
        scenario.update(fuel=0.2, weapons=[], enemies=[{**TANKS, 'lat': LAT, 'lon': LON}])
        self.assertIn('out of weapons', scenario.desk.reports()[0]['report'])
        scenario.desk.batch_last -= 6
        scenario.update(fuel=0.2, weapons=[], enemies=[{**TANKS, 'lat': LAT, 'lon': LON}])
        self.assertIn('new ground contacts: 3 targets (3 T-72B)', scenario.desk.reports()[0]['report'])
        scenario.update(fuel=0.2, weapons=[], enemies=[{**TANKS, 'lat': LAT, 'lon': LON}])
        self.assertEqual(scenario.desk.reports(), [])                          # said once

    def test_weapons_missing_from_one_reading_is_not_winchester(self):
        # Live 2026-11-05: Cobra's 40 rockets missing from one DCS reading -> "winchester", then back.
        scenario = Scenario()
        scenario.desk.tick()
        scenario.update(weapons=[{'name': 'PKT', 'count': 750, 'kind': 'gun'}])
        scenario.update()                                                      # the rockets are back
        scenario.desk.empty_since = None if scenario.desk.empty_since is None else scenario.desk.empty_since - 60
        scenario.update()
        self.assertEqual(scenario.desk.reports(), [])

    def test_not_twice_when_the_pilot_asked(self):
        scenario = Scenario(in_air=False, vx=8.0)
        scenario.desk.tick()
        scenario.call('tell_pilot_when', condition='airborne', request='call airborne')
        scenario.update()
        self.assertEqual(scenario.desk.reports(), [])                          # the pilot's request makes that call

    def test_landing_asks_for_a_line_in_memory(self):
        scenario = Scenario()
        scenario.desk.tick()
        scenario.update(in_air=False, vx=40.0, last_event='land', events={'takeoff': 900.0, 'land': 5.0})
        self.assertIn('remember (about: flight)', scenario.desk.reports()[0]['report'])


class NotObeying(unittest.TestCase):
    """Live 2026-11-05: DCS's AI dropped every order and took it home to land. The user's design: the program and the
    agent fix it without a word to the leader; only a landing that happens anyway is called, as an emergency."""

    def gear_down(self, scenario, seconds=5.0, **air):
        scenario.update(gear_down=True, **air)
        if scenario.desk.gear_since is not None:
            scenario.desk.gear_since -= seconds
        scenario.desk.tick()

    def test_gear_down_without_a_landing_order_gives_the_order_again(self):
        scenario = Scenario()
        scenario.desk.tick()
        self.assertTrue(scenario.call('orbit', where='Z1', altitude_ft=0)['ok'])
        orbit = scenario.tasks.sent[-1]
        self.gear_down(scenario)
        self.assertEqual(scenario.tasks.sent[-1], orbit)                       # given again at once
        self.assertIn('down to land by itself', scenario.desk.situation()['aircraft_not_obeying'])
        self.assertEqual(scenario.desk.reports(), [])                          # the leader hears nothing
        scenario.desk.trouble['next'] = 0.0                                    # still down 20 s later
        scenario.desk.tick()
        self.assertEqual(scenario.tasks.sent[-1], orbit)
        [fix] = scenario.desk.reports()
        self.assertTrue(fix['fix'])
        self.assertIn('still does not obey', fix['report'])
        scenario.update(gear_down=False)                                       # it obeys again
        self.assertNotIn('aircraft_not_obeying', scenario.desk.situation())

    def test_no_trouble_when_landing_was_ordered_or_just_after_takeoff(self):
        scenario = Scenario()
        scenario.desk.tick()
        self.assertTrue(scenario.call('return_to_base', airfield='')['ok'])
        count = len(scenario.tasks.sent)
        self.gear_down(scenario)
        self.assertEqual((len(scenario.tasks.sent), scenario.desk.trouble), (count, None))
        scenario = Scenario()
        scenario.desk.formation = scenario.desk.rejoin_offset()
        self.gear_down(scenario, events={'takeoff': 20.0})                     # the gear is still coming up
        self.assertIsNone(scenario.desk.trouble)

    def test_landed_anyway_is_an_emergency_landing(self):
        scenario = Scenario()
        scenario.desk.tick()
        self.assertTrue(scenario.call('follow', clock_position=4, distance_m=200, height_m=0)['ok'])
        scenario.update(in_air=False, vx=40.0, last_event='land', events={'takeoff': 900.0, 'land': 5.0})
        [report] = scenario.desk.reports()
        self.assertIn('emergency landing', report['report'])
        self.assertIn('Kobuleti', report['report'])

    def test_rejoin_that_gets_further_away_asks_the_agent(self):
        scenario = Scenario()
        scenario.state.flight['tas_ms'] = 200.0
        self.assertTrue(scenario.call('follow', clock_position=4, distance_m=200, height_m=0)['ok'])
        for north_m in (-6000, -7000, -8000, -9000, -10000):
            scenario.state.groups['Manolo']['lat'], scenario.state.groups['Manolo']['lon'] = at(north_m, 0)
            scenario.state.groups_at = time.monotonic()
            scenario.desk.next_steer = 0.0
            scenario.desk.tick()
        [fix] = scenario.desk.reports()
        self.assertIn('get further away', fix['report'])
        self.assertEqual(scenario.tasks.sent[-1][0], 'wing_fly')

    def test_a_fix_is_never_transmitted(self):
        for answer in ('SILENT', 'Two, fixing it.'):
            spoken = []
            responder = Responder(SilentBrain(answer), {}, 'en', CockpitState(), speak=spoken.append,
                                  show=lambda _: None, name='Viper', role='wingman')
            responder.event_call({'report': 'not obeying', 'fix': True})
            self.assertEqual(spoken, [])
            self.assertIn('Fix it now with your tools', responder.brain.asked[0])


class Helicopter(unittest.TestCase):
    """User, 2026-11-05: the same wingman for helicopters (the Mi-8 first); the kind comes from DCS (air.kind)."""

    def test_no_takeoff_roll_and_helicopter_numbers(self):
        rolling = Scenario(kind='helicopter', in_air=False, vx=25.0, events={'engine_startup': 60.0},
                           last_event='engine_startup')
        self.assertEqual(rolling.desk.phase(), 'taxiing to the runway')
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        self.assertEqual(scenario.desk.situation()['you']['kind'], 'helicopter')
        self.assertTrue(scenario.call('orbit', where='here', altitude_ft=0)['ok'])
        self.assertEqual(scenario.tasks.sent[-1][-1], 200.0)                   # its own speed (above 40 m/s)
        self.assertTrue(scenario.call('fly_to', where='Z1', altitude_ft=10, above='ground', speed_kt=0)['ok'])
        self.assertEqual(scenario.tasks.sent[-1][4], 45.0)   # live 2026-11-05: at 100 ft it flew into the trees
        self.assertEqual(scenario.desk.rejoin_offset(), (50.0, 87.0, 0.0))     # 4 o'clock, 100 m

    def test_no_formation_with_the_other_kind(self):
        scenario = Scenario(kind='helicopter', leader_kind='airplane')
        result = scenario.call('follow', clock_position=4, distance_m=100, height_m=0)
        self.assertFalse(result['ok'])
        self.assertIn('you fly a helicopter and the leader an airplane', result['detail'])
        self.assertFalse(scenario.call('cover_me')['ok'])
        self.assertIn('cannot fly formation', scenario.desk.situation()['you']['formation'])
        self.assertTrue(scenario.call('orbit', where='Z1', altitude_ft=300)['ok'])   # sent anywhere by radio
        self.assertTrue(scenario.call('attack', target='Z1', how='all', against='ground')['ok'])
        asleep = Scenario(kind='helicopter', leader_kind='airplane', active=False, in_air=False)
        self.assertFalse(asleep.call('start_up', how='air')['ok'])
        self.assertTrue(asleep.call('start_up', how='cold')['ok'])

    def test_airborne_with_an_airplane_leader_it_holds_for_orders(self):
        scenario = Scenario(kind='helicopter', leader_kind='airplane', in_air=False, vx=0.0,
                            events={'engine_startup': 100.0}, last_event='engine_startup')
        scenario.desk.tick()
        scenario.update(kind='helicopter', leader_kind='airplane')
        self.assertEqual(scenario.tasks.sent[-1][0], 'wing_orbit')
        self.assertEqual(scenario.tasks.sent[-1][4], 100.0)                     # 100 m above its field
        self.assertIn('wait for the leader\'s orders', scenario.desk.reports()[0]['report'])
        self.assertIsNone(scenario.desk.formation)

    def test_settling_low_and_slow_is_an_uncommanded_landing(self):
        # User, 2026-11-05: no gear trick for helicopters (fixed on the Mi-8): low and slow.
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        scenario.desk.tick()
        self.assertTrue(scenario.call('orbit', where='Z1', altitude_ft=300)['ok'])
        scenario.update(kind='helicopter', leader_kind='helicopter', gear_down=True)   # a fixed gear: not a sign
        self.assertIsNone(scenario.desk.trouble)
        low = {'kind': 'helicopter', 'leader_kind': 'helicopter', 'vx': 3.0, 'ground_m': 2990.0}
        scenario.update(**low)
        scenario.desk.gear_since -= 5
        scenario.update(**low)
        self.assertIn('settling low and slow', scenario.desk.situation()['aircraft_not_obeying'])

    def test_land_at_places_and_offsets(self):
        # User, 2026-11-05: "land here", beside the leader, at a zone, at an F10 map mark, at my 3 o'clock 200 m.
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        scenario.state.zones.append({'name': 'LZ1', 'lat': LAT + 0.05, 'lon': LON, 'radius_m': 0, 'mark': True})
        self.assertEqual(scenario.desk.situation()['leader_map_marks'], ['LZ1'])
        self.assertNotIn('LZ1', scenario.desk.situation()['mission_zones'])
        self.assertTrue(scenario.call('land', where='LZ1', offset_m=0, toward='', minutes=0, how='normal')['ok'])
        self.assertEqual(scenario.tasks.sent[-1], ('wing_land_at', 'Manolo', LAT + 0.05, LON, 0.0, 'normal'))
        self.assertTrue(scenario.call('land', where='leader', offset_m=200, toward='3', minutes=5, how='combat')['ok'])
        _, _, lat, lon, seconds, how = scenario.tasks.sent[-1]
        self.assertEqual((round(lat, 5), round(lon, 5), seconds, how), (*map(lambda v: round(v, 5), at(0, 200)),
                                                                         300.0, 'combat'))   # 200 m east (heading 0)
        self.assertTrue(scenario.call('land', where='Kobuleti', offset_m=1000, toward='south', minutes=0,
                                      how='normal')['ok'])                    # an airfield, moved 1 km south
        self.assertAlmostEqual(scenario.tasks.sent[-1][2], LAT + 0.2 - 1000 / M_PER_DEG, places=5)
        self.assertIn('compass direction', scenario.call('land', where='here', offset_m=100, toward='up', minutes=0,
                                                         how='normal')['detail'])
        self.assertTrue(scenario.desk.landing_expected())                       # no "uncommanded landing" now
        airplane = Scenario()
        self.assertIn('needs an airfield', airplane.call('land', where='here', offset_m=0, toward='', minutes=0,
                                                         how='normal')['detail'])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn('land', {d['name'] for d in WingmanToolBox(airplane.desk, Memory(Path(tmp))).tools()[0]
                                      ['tools']})

    def test_on_the_ground_at_the_point_then_lifting_off(self):
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        scenario.desk.tick()
        scenario.desk.flight_said.add('airborne')
        self.assertTrue(scenario.call('land', where='here', offset_m=0, toward='', minutes=0, how='normal')['ok'])
        ground = {'kind': 'helicopter', 'leader_kind': 'helicopter', 'in_air': False, 'vx': 0.0,
                  'last_event': 'land', 'events': {'takeoff': 600.0, 'land': 2.0}}
        scenario.update(**ground)
        self.assertIn('on the ground where the leader told you to land', scenario.desk.reports()[0]['report'])
        self.assertIn('fly_to', scenario.desk.situation()['orders_you_can_carry_out_now'])   # lifts off for orders
        self.assertTrue(scenario.call('fly_to', where='Z1', altitude_ft=100, above='ground', speed_kt=0)['ok'])
        scenario.update(kind='helicopter', leader_kind='helicopter', last_event='takeoff',
                        events={'land': 60.0, 'takeoff': 1.0})
        self.assertIn('lifted off for the leader\'s order', scenario.desk.reports()[0]['report'])
        self.assertEqual(scenario.desk.main_task[0], 'wing_fly')               # its order kept

    def test_pick_up_carry_and_drop_a_squad(self):
        # User, 2026-11-05: "Cobra, pick up Tango", "take Tango to Z1" with DCS's own transport tasks.
        from squad.desk import SquadDesk
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        tango = SquadDesk(scenario.state, {'name': 'Tango', 'squad': {'groups': ['Tango']}})
        scenario.desk.tick()
        scenario.desk.flight_said.add('airborne')
        result = scenario.call('pick_up', squad='tango squad', where='', offset_m=0, toward='')
        self.assertIn('Tango told to wait for you there', result['detail'])
        self.assertEqual(scenario.tasks.sent[-2], ('wing_pickup', 'Manolo', *at(10000, 0), 'Tango'))
        # Live 2026-11-05: Victor had only been told to move there; DCS boards only a squad waiting for transport.
        self.assertEqual(scenario.tasks.sent[-1], ('wait_pickup', 'Tango', *at(10000, 0), 200.0))
        self.assertEqual(scenario.desk.situation()['troops'], {'Tango': 'to pick up'})
        self.assertIn('is coming to pick you up', tango.situation()['squad']['pickup'])
        self.assertFalse(scenario.call('drop_off', where='Z1', offset_m=0, toward='')['ok'])   # nobody aboard yet

        def heli(north_m, in_air, events, agl=None):
            lat, lon = at(north_m, 0)
            agl = (0.0 if not in_air else 100.0) if agl is None else agl
            scenario.update(kind='helicopter', leader_kind='helicopter', in_air=in_air, vx=0.0 if not in_air else 40.0,
                            events=events, last_event='land' if not in_air else 'takeoff', ground_m=3000.0 - agl)
            scenario.state.groups['Manolo'].update(lat=lat, lon=lon)
            scenario.desk.tick()

        heli(10050, False, {'takeoff': 600.0, 'land': 2.0})                    # on the ground at the pickup point
        self.assertIn('Tango boards you now', scenario.desk.reports()[0]['report'])
        heli(10050, True, {'takeoff': 600.0, 'land': 9.0}, agl=2.0)   # live: "in the air" bounced on touchdown
        self.assertEqual(scenario.state.transport['Tango']['stage'], 'pickup')
        self.assertEqual(scenario.desk.reports(), [])
        heli(10050, True, {'takeoff': 600.0, 'land': 60.0})   # live: lifted off, and DCS sent no takeoff event
        report = scenario.desk.reports()[0]['report']
        self.assertIn('with Tango aboard', report)
        self.assertEqual(scenario.state.transport['Tango']['stage'], 'aboard')
        tango.tick()                                                           # live: Victor never said it boarded
        self.assertIn('boarded Manolo\'s helicopter', tango.reports()[0]['report'])
        self.assertIn('aboard Manolo\'s helicopter', tango.situation()['squad'])
        self.assertIsNone(tango.facts())                                       # never "wiped out" while carried
        self.assertFalse(json.loads(json.dumps(tango.disembark()))['ok'])
        self.assertTrue(scenario.call('drop_off', where='Z1', offset_m=0, toward='')['ok'])
        self.assertEqual(scenario.tasks.sent[-1][0], 'wing_dropoff')
        heli(20000, False, {'takeoff': 300.0, 'land': 2.0})
        self.assertIn('Tango gets out now', scenario.desk.reports()[0]['report'])
        heli(20000, True, {'land': 60.0, 'takeoff': 1.0})
        self.assertIn('Tango got out there', scenario.desk.reports()[0]['report'])
        self.assertEqual(scenario.state.transport, {})
        self.assertFalse(tango.aboard())
        tango.tick()
        self.assertIn('out of Manolo\'s helicopter, on the ground', tango.reports()[0]['report'])
        tango.tick()
        self.assertEqual(tango.reports(), [])                                  # said once

    def test_agent_names_and_group_names_both_work(self):
        # User, 2026-11-05: "if I change the names, does it still work?" The agent "Tango" whose group in the mission
        # is "Infantry-1": the pilot may say either.
        from squad.desk import SquadDesk
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        scenario.state.groups['Infantry-1'] = scenario.state.groups.pop('Tango')
        tango = SquadDesk(scenario.state, {'name': 'Tango', 'squad': {'groups': ['Infantry-1']}})
        scenario.desk.configure({'id': 'manolo', 'name': 'Cobra', 'wingman': {'number': 2, 'group': 'Manolo'}})
        self.assertTrue(scenario.call('pick_up', squad='Tango', where='', offset_m=0, toward='')['ok'])
        self.assertEqual(scenario.tasks.sent[-2][-1], 'Infantry-1')            # DCS gets the group's name
        self.assertEqual(scenario.desk.situation()['troops'], {'Tango': 'to pick up'})
        self.assertIn('Cobra\'s helicopter is coming', tango.situation()['squad']['pickup'])
        self.assertEqual(scenario.desk.squad_group('infantry 1'), 'Infantry-1')
        self.assertEqual(scenario.desk.point('Tango'), scenario.desk.point('Infantry-1'))

    def test_lifting_off_without_the_squad_says_so(self):
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        scenario.desk.tick()
        self.assertTrue(scenario.call('pick_up', squad='Tango', where='Z1', offset_m=0, toward='')['ok'])
        for in_air, events in ((False, {'takeoff': 600.0, 'land': 2.0}), (True, {'land': 60.0, 'takeoff': 1.0})):
            scenario.update(kind='helicopter', leader_kind='helicopter', in_air=in_air, events=events)
            lat, lon = at(20000, 0)                                            # at Z1; Tango is 10 km away
            scenario.state.groups['Manolo'].update(lat=lat, lon=lon)
            scenario.desk.tick()
        texts = ' '.join(r['report'] for r in scenario.desk.reports())
        self.assertIn('without Tango', texts)
        self.assertEqual(scenario.state.transport, {})

    def test_helicopter_instructions_tools_and_playbook(self):
        scenario = Scenario(kind='helicopter', leader_kind='helicopter')
        with tempfile.TemporaryDirectory() as tmp:
            box = WingmanToolBox(scenario.desk, Memory(Path(tmp)))
            text = brain_appendix(Memory(Path(tmp)), box.playbook)()
            self.assertIn('You fly a helicopter', text)
            self.assertIn('- air-defence: Air defences', text)
            self.assertNotIn('- sam-attack:', text)
            tools = {d['name']: d['description'] for d in box.tools()[0]['tools']}
            self.assertIn('nap of the earth', tools['fly_to'])
            self.assertIn('Start your helicopter', box.guide())
            airplane = WingmanToolBox(Scenario().desk, Memory(Path(tmp)))
            self.assertIn('You fly an airplane', brain_appendix(Memory(Path(tmp)), airplane.playbook)())
            self.assertIn('- sam-attack:', airplane.playbook.prompt())
        for page in Playbook(kind=lambda: 'helicopter').pages():   # every page in English
            self.assertTrue(page['title'] and page['when'] and page['text'], page['name'])
            self.assertIsNone(re.search(r'[áéíóúñ¿¡]', page['text'] + page['when']), page['name'])


class ShotDown(unittest.TestCase):
    """User, 2026-11-04: "it answers from the beyond". Hit: it calls it; lost: one last call, then silence."""

    def test_hit_ejecting_last_call_then_silence(self):
        scenario = Scenario(combat=[])
        scenario.desk.tick()
        # The hit and the loss in the same second (live: engine shutdown, ejection, crash within 4 s).
        scenario.update(life=0.0, last_event='ejection', combat=[{'n': 1, 'kind': 'hit_taken', 'by': 'Osa 9A33 ln'}],
                        events={'takeoff': 400.0, 'hit': 2.0, 'ejection': 1.0})
        (event,) = scenario.desk.reports()
        self.assertTrue(event['final'])
        self.assertIn('you were hit by a Osa 9A33 ln', event['report'])
        self.assertIn('you are ejecting. This is your last call', event['report'])
        self.assertFalse(scenario.desk.silent(event))                           # its last call goes out
        self.assertTrue(scenario.desk.silent({'report': 'anything'}))
        self.assertTrue(scenario.desk.silent({'when': 'fuel_low', 'instructions': 'x'}))
        self.assertEqual(scenario.desk.prefilter('Two, do you copy?'), '')    # nobody answers
        scenario.update(life=0.0, last_event='crash', events={'takeoff': 410.0, 'ejection': 11.0, 'crash': 9.0})
        self.assertEqual(scenario.desk.reports(quiet_s=4000), [])               # nothing more, ever

    def test_killed_makes_no_call(self):
        scenario = Scenario(combat=[])
        scenario.desk.tick()
        scenario.update(life=0.0, last_event='pilot_dead', events={'takeoff': 400.0, 'pilot_dead': 1.0})
        self.assertEqual(scenario.desk.reports(), [])
        self.assertEqual(scenario.desk.prefilter('Two?'), '')

    def test_the_responder_keeps_silent(self):
        spoken = []
        responder = Responder(SilentBrain('Two, still here!'), {}, 'en', CockpitState(), speak=spoken.append,
                              show=lambda _: None, name='Viper', role='wingman')
        responder.desk = type('Desk', (), {'silent': lambda self, event=None: not (event or {}).get('final'),
                                           'can_reach_pilot': lambda self: True})()
        responder.event_call({'report': 'new contacts'})
        self.assertEqual(spoken, [])
        responder.event_call({'report': 'you are ejecting', 'final': True})
        self.assertEqual(spoken, ['Two, still here!'])


class QuietTime(unittest.TestCase):
    def test_after_a_calm_silence(self):
        scenario = Scenario(chatter='normal')
        scenario.desk.tick()
        self.assertEqual(scenario.desk.reports(quiet_s=100), [])
        scenario.desk.last_quiet -= 400
        (event,) = scenario.desk.reports(quiet_s=400)
        self.assertTrue(event['quiet'])
        self.assertEqual(scenario.desk.reports(quiet_s=400), [])               # not again right away

    def test_never_off_low_or_with_enemies(self):
        for chatter, flight, enemies in (('off', {}, None), ('normal', {'alt_agl_m': 100.0}, None),
                                         ('normal', {}, [TANKS])):
            scenario = Scenario(chatter=chatter, enemies=enemies)
            scenario.state.flight.update(flight)
            scenario.desk.tick()
            scenario.desk.pending.clear()
            scenario.desk.last_quiet -= 4000
            self.assertEqual(scenario.desk.reports(quiet_s=4000), [], (chatter, flight))


class WingmanMemory(unittest.TestCase):
    def test_remember_forget_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = Memory(Path(tmp))
            self.assertIn('first flight together', memory.prompt())
            self.assertTrue(memory.remember('The leader is called Chamaco', 'pilot')['ok'])
            self.assertEqual(memory.remember('the leader is called chamaco', 'pilot')['detail'],
                             'you already remember that')
            self.assertTrue(memory.remember('Bombed L1 together, two tanks', 'flight')['ok'])
            prompt = memory.prompt()
            self.assertIn('- The leader is called Chamaco', prompt)
            self.assertRegex(prompt, r'- \d{4}-\d\d-\d\d: Bombed L1 together, two tanks')
            self.assertFalse(memory.forget('nothing like this')['ok'])
            self.assertTrue(memory.forget('chamaco')['ok'])
            memory.save([f'line {n}' for n in range(PILOT_KEPT)], [])
            self.assertIn('full', memory.remember('one more', 'pilot')['detail'])
            appendix = brain_appendix(memory)()
            self.assertIn('How you work as a wingman', appendix)
            self.assertIn('- line 0', appendix)


class WingmanPlaybook(unittest.TestCase):
    """User's idea, 2026-11-04: a manual of instructions by situation (wingman/playbook/*.md)."""

    def test_the_index_always_a_page_once_opened(self):
        playbook = Playbook()
        names = {p['name'] for p in playbook.pages()}
        self.assertTrue({'sam-attack', 'target-area', 'takeoff-and-join', 'return-and-landing', 'air-threat',
                         'fuel-damage-weapons'} <= names)
        for page in playbook.pages():   # every page in English, with its title and when it applies
            self.assertTrue(page['title'] and page['when'] and page['text'], page['name'])
            self.assertIsNone(re.search(r'[áéíóúñ¿¡]', page['text'] + page['when']), page['name'])
        with tempfile.TemporaryDirectory() as tmp:
            appendix = brain_appendix(Memory(Path(tmp)), playbook)
            text = appendix()
            self.assertIn('- sam-attack: Attacking a SAM site. When:', text)
            self.assertNotIn('Never fly over a SAM', text)                     # pages cost nothing until opened
            box =WingmanToolBox(Scenario().desk, Memory(Path(tmp)), playbook)
            result = json.loads(box.call('wingman.playbook', json.dumps({'page': 'sam attack'})))
            self.assertTrue(result['ok'])
            self.assertIn('Never fly over a SAM', result['page'])
            self.assertIn('Open playbook page "Attacking a SAM site"', appendix())   # kept in the instructions
            self.assertIn('pages:', json.loads(box.call('wingman.playbook', json.dumps({'page': 'x'})))['detail'])

    def test_pages_kept_a_while_and_the_last_two(self):
        playbook = Playbook()
        for name in ('sam-attack', 'target-area', 'air-threat'):
            playbook.open(name)
        self.assertEqual(list(playbook.opened), ['target-area', 'air-threat'])
        playbook.opened['target-area'] -= PAGE_KEPT_S + 1
        prompt = playbook.prompt()
        self.assertIn('Open playbook page "Enemy aircraft"', prompt)
        self.assertNotIn('Open playbook page "A target area', prompt)


class SilentBrain(Brain):
    name = 'fake'

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def ask(self, question, context, language):
        self.asked.append(question)
        return self.answer


class ReportCalls(unittest.TestCase):
    def responder(self, answer):
        spoken = []
        responder = Responder(SilentBrain(answer), {}, 'en', CockpitState(), speak=spoken.append,
                              show=lambda _: None, name='Viper', role='wingman')
        return responder, spoken

    def test_report_is_phrased_by_the_model(self):
        responder, spoken = self.responder('Two, airborne, joining.')
        responder.event_call({'report': 'you took off'})
        self.assertEqual(spoken, ['Two, airborne, joining.'])
        self.assertEqual(responder.brain.asked, [REPORT_PROMPT.format(report='you took off', language='en')])

    def test_quiet_time_may_stay_silent(self):
        responder, spoken = self.responder('SILENT')
        responder.event_call({'report': 'calm', 'quiet': True})
        self.assertEqual(spoken, [])
        self.assertEqual(responder.brain.asked, [QUIET_PROMPT.format(report='calm', language='en')])


if __name__ == '__main__':
    unittest.main()
