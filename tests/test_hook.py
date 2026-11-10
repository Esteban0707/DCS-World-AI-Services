"""Offline tests for the GUI hook (callsign from the mission, airfield radars from the mission scripting environment),
run by DCS's own Lua interpreter with a fake hook API, and for both reaching the cockpit state.

Run: python -m unittest discover tests
"""
import json
import socket
import subprocess
import unittest
from pathlib import Path

from client.dcs_tasks import TaskChannel
from instructor.tower_view import TowerView
from shared.cockpit import CockpitState
from tools.install_hook import hook_text

from dcs_data import LUAE, RCS_TABLE, needs

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(LUAE.exists(), "needs DCS's own Lua interpreter (bin/luae.exe)")
class Hook(unittest.TestCase):
    def run_hook(self, case: str, radar: bool = False, bridge: str = 'none',
                 tasks: bool | str = False) -> tuple[list[dict], str]:
        requests = tasks if isinstance(tasks, str) else ('tasks' if tasks else 'none')
        result = subprocess.run([str(LUAE), str(ROOT / 'tests' / 'lua' / 'hook_harness.lua'), ROOT.as_posix() + '/',
                                 case, 'radar' if radar else 'none', bridge, requests],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = [json.loads(line[5:]) for line in result.stdout.splitlines() if line.startswith('SENT ')]
        return sent, result.stdout

    def of(self, messages, kind):
        return [m for m in messages if m['type'] == kind]

    def test_western_callsign_is_spoken_form(self):
        callsigns = self.of(self.run_hook('western')[0], 'callsign')
        self.assertEqual(callsigns[0], {'type': 'callsign', 'callsign': 'Enfield 1-1', 'unit_name': 'Aerial-1-1'})
        self.assertEqual(len(callsigns), 2)   # checked once per second, three frames of 0.6 s

    def test_eastern_numeric_callsign(self):
        self.assertEqual(self.of(self.run_hook('eastern')[0], 'callsign')[0]['callsign'], '101')

    def test_unit_property_when_not_in_mission_table(self):
        self.assertEqual(self.of(self.run_hook('property')[0], 'callsign')[0]['callsign'], 'Ford21')

    def test_nothing_without_a_player_unit(self):
        self.assertEqual(self.of(self.run_hook('none')[0], 'callsign'), [])

    def test_radar_unavailable_without_the_scripting_environment(self):
        radar = self.of(self.run_hook('western')[0], 'radar')
        self.assertIn('not reachable', radar[0]['unavailable'])

    @needs(RCS_TABLE)
    def test_airfield_radars_by_name(self):
        messages, stdout = self.run_hook('western', radar=True)
        self.assertIn('radar script loaded in the "server" environment', stdout)
        airfields = self.of(messages, 'radar')[-1]['airfields']
        self.assertEqual(sorted(airfields), ['Kutaisi', 'Senaki-Kolkhi'])
        senaki = airfields['Senaki-Kolkhi']
        self.assertEqual(senaki['radars'], ['Senaki-Kolkhi-1'])            # the tank has no radar
        self.assertEqual(senaki['range_km'], {'Senaki-Kolkhi-1': 100})      # from the radar's own sensor data
        # Friendly player seen (the AI target list missed it live); hidden by terrain, out of range and taxiing: not.
        # By size (radar equation, range ~ RCS^1/4): the 0.5 m2 drone at 90 km is not seen (56 km), the 80 m2
        # transport at 140 km is (200 km); the enemy MiG-29 at 60 km is.
        self.assertEqual(sorted(c['unit'] for c in senaki['contacts']), ['Aerial-1-1', 'Bandit', 'Transport'])
        contact = next(c for c in senaki['contacts'] if c['player'])
        self.assertEqual((contact['type'], contact['player'], contact['alt_m']), ('F-5E-3', True, 914.4))
        # The F-5E's RCS (3 m2) came from the table built from DCS's files: 100 km * (3/5)^(1/4) = 88 km.
        self.assertEqual((contact['rcs_m2'], contact['rcs_source'], contact['range_km']), (3, 'table', 88.0))
        transport = next(c for c in senaki['contacts'] if c['unit'] == 'Transport')
        self.assertEqual((transport['rcs_source'], transport['range_km']), ('dcs', 200.0))
        # Calibration: what the radar's own AI detects of enemies next to what this model says.
        self.assertEqual(senaki['calibration'], [{'radar': 'Senaki-Kolkhi-1', 'unit': 'Bandit', 'type': 'MiG-29A',
                                                  'rcs_m2': 5, 'dist_km': 60.2, 'model_km': 100.0, 'model': True,
                                                  'ai': True}])
        kutaisi = airfields['Kutaisi']                                     # matched by group name
        self.assertEqual((kutaisi['radars'], kutaisi['range_km']), (['Ground-2-1'], {'Ground-2-1': 100}))
        # Default range, 300 km north: only the transport at 160 km is close enough for its size (200 km).
        self.assertEqual([c['unit'] for c in kutaisi['contacts']], ['Transport'])

    def test_airfield_weather(self):
        messages, stdout = self.run_hook('western', radar=True)
        self.assertIn('airfield weather read', stdout)
        weather = self.of(messages, 'weather')
        self.assertEqual(len(weather), 1)                                   # every 10 s, three frames of 0.6 s
        airfields = weather[0]['airfields']
        self.assertEqual(sorted(airfields), ['Kutaisi', 'Senaki-Kolkhi'])   # FARP-1 is not an airdrome
        senaki = airfields['Senaki-Kolkhi']
        self.assertEqual((senaki['wind_north'], senaki['wind_east'], senaki['pressure_pa'], senaki['elevation_m']),
                         (0, 5, 101200, 13))
        self.assertEqual(senaki['runways'][0]['name'], '09')
        self.assertEqual((senaki['runways'][0]['lat'], senaki['runways'][0]['lon']), (42.1, 42.0))   # its centre
        # Both ends too (the fake coord.LOtoLL gives one point for all): the true heading comes from them.
        self.assertEqual({k: senaki['runways'][0][k] for k in ('start_lat', 'start_lon', 'end_lat', 'end_lon')},
                         {'start_lat': 42.1, 'start_lon': 42.0, 'end_lat': 42.1, 'end_lon': 42.0})
        # For the ATC: the airfield's coalition and its radios, and the mission's visibility and clouds.
        self.assertEqual((senaki['side'], senaki['frequencies_hz']), (2, [261000000, 132000000]))
        self.assertNotIn('frequencies_hz', airfields['Kutaisi'])            # no communicator
        mission = weather[0]['mission']
        self.assertEqual((mission['visibility_m'], mission['fog'], mission['fog_visibility_m'], mission['cloud_preset'],
                          mission['cloud_base_m']), (80000, True, 3000, 'Preset7', 1200))

    def test_mission_traffic(self):
        messages, stdout = self.run_hook('western', radar=True)
        self.assertIn('mission traffic read', stdout)
        traffic = self.of(messages, 'traffic')
        self.assertEqual(len(traffic), 2)                                   # once per second
        aircraft = {a['unit']: a for a in traffic[-1]['aircraft']}
        # Everything, seen or not by a radar, on the ground too, the player flagged.
        self.assertEqual(sorted(aircraft), ['Aerial-1-1', 'Bandit', 'Drone', 'Far', 'Hidden', 'Taxiing', 'Transport'])
        self.assertTrue(aircraft['Aerial-1-1']['player'])
        self.assertNotIn('player', aircraft['Taxiing'])
        self.assertEqual((aircraft['Taxiing']['in_air'], aircraft['Taxiing']['type'], aircraft['Taxiing']['side']),
                         (False, 'F-5E-3', 2))
        self.assertEqual((aircraft['Bandit']['vx'], aircraft['Bandit']['side']), (100, 1))

    def test_hook_drives_the_native_command_bridge(self):
        # Live 2026-11-02: DCS's GUI update callback never ran the bridge, so the hook ticks it (frames 0.6 s apart).
        for where in ('bridge_global', 'bridge_panel'):
            stdout = self.run_hook('western', bridge=where)[1]
            self.assertEqual(stdout.count('BRIDGE TICK'), 3, where)
            self.assertIn('driving the native command bridge', stdout)
        self.assertNotIn('BRIDGE TICK', self.run_hook('western')[1])   # not patched: nothing to drive

    def test_mission_tasks_only_from_the_list(self):
        messages, stdout = self.run_hook('western', radar=True, tasks=True)
        self.assertIn('mission tasks listening on 127.0.0.1:47824', stdout)
        replies = {m['id']: (m['ok'], m['detail']) for m in self.of(messages, 'task')}
        self.assertEqual(replies['t1'], (True, 'watching'))
        self.assertEqual(replies['t2'], (True, 'moving'))
        self.assertIn('TASK Mission to 42250,42040 at 7', stdout)               # off road to lat/lon, fast
        self.assertEqual(replies['t3'], (False, 'action not allowed: dostring'))
        self.assertEqual(replies['t4'], (False, 'bad argument 2 for move'))      # "north" is not a number
        self.assertEqual(replies['t5'], (False, 'request too old'))
        self.assertEqual(replies['t6'], (False, 'no group called Nobody'))
        groups = self.of(messages, 'groups')
        self.assertTrue(groups)
        self.assertEqual(groups[-1]['groups']['Buenos'],
                         {'exists': True, 'alive': 2, 'size': 4, 'lat': 42.1, 'lon': 42.0, 'alt_m': 20, 'speed_ms': 1,
                          # the harness's player unit is 20 km north: the squad's radio path to it
                          'radio': {'distance_m': 19901.005, 'kind': 'manpack', 'line_of_sight': True},
                          # what the group is made of (the harness's soldiers do not answer getDescentCapacity)
                          # and the living units' life, now and new (hits without a loss)
                          'ground': {'types': {'Soldier M4': 2}, 'infantry': 2, 'vehicles': 0, 'life': 2,
                                     'life0': 2}})
        self.assertEqual(groups[-1]['groups']['Ghost'], {'exists': False})
        self.assertEqual(groups[-1]['zones'][0]['name'], 'LZ North')

    def test_vehicles_drive_and_carry_a_squad(self):
        # User, 2026-11-05: armour like a squad, on or off the road, in a formation; a BTR carries infantry.
        messages, stdout = self.run_hook('western', radar=True, tasks='ground')
        replies = {m['id']: (m['ok'], m['detail']) for m in self.of(messages, 'task')}
        self.assertEqual(replies['g2'], (True, 'driving on the road in wedge'))
        self.assertIn('GROUND TASK Bison Mission On Road to 42250,42040 at 17', stdout)
        self.assertIn('GROUND OPTION Bison 5=3', stdout)        # DCS's radio menu: OPTION_FORMATION, Wedge 3
        self.assertEqual(replies['g3'], (True, 'driving off road'))
        self.assertIn('GROUND TASK Bison Mission Off Road to 42250,42040 at 5', stdout)
        self.assertEqual(replies['g4'], (False, 'unknown formation square'))
        self.assertEqual(replies['g5'], (True, 'driving there to pick up Buenos'))
        self.assertIn('GROUND TASK Bison Mission Off Road to 42100,42000 at 10 then Embarking groups 7', stdout)
        self.assertEqual(replies['g6'], (True, 'driving there to drop Buenos'))
        self.assertIn('GROUND TASK Bison Mission On Road to 42200,42000 at 10 then Disembarking groups 7', stdout)
        self.assertEqual(replies['g7'], (False, 'infantry carries no troops'))
        bison = self.of(messages, 'groups')[-1]['groups']['Bison']
        self.assertEqual(bison['ground'], {'types': {'BTR-80': 2}, 'infantry': 0, 'vehicles': 2, 'life': 2,
                                           'life0': 2, 'descent_capacity': 1400, 'descent_on_board': 0})
        self.assertEqual(bison['radio']['kind'], 'vehicle')

    def test_mission_flags_read_and_only_listed_ones_set(self):
        messages, stdout = self.run_hook('western', radar=True, tasks='flags')
        replies = {m['id']: (m['ok'], m['detail']) for m in self.of(messages, 'task')}
        self.assertEqual(replies['f1'], (True, 'flags listed'))
        self.assertEqual(replies['f2'], (True, 'flag 20 = 1'))
        self.assertIn('FLAG 20 = 1', stdout)
        self.assertEqual(replies['f3'], (False, 'flag not listed: 10'))     # watched, but no action may set it
        self.assertNotIn('FLAG 10', stdout)
        self.assertEqual(replies['f4'], (False, 'bad flag name'))           # the earlier lists stay
        flags = self.of(messages, 'flags')
        self.assertEqual(flags[-1]['flags'], {'10': 1, 'convoy_left': 0})   # only the watched ones

    def test_scripted_wingman_start_orders_and_state(self):
        messages, stdout = self.run_hook('western', radar=True, tasks='wing')
        replies = {m['id']: (m['ok'], m['detail']) for m in self.of(messages, 'task')}
        self.assertEqual(replies['w2'], (False, 'Manolo has not been started yet'))   # late activation: no orders yet
        self.assertEqual(replies['w3'], (False, 'unknown start sideways'))
        self.assertEqual(replies['w4'], (True, 'Manolo started (cold)'))
        # Re-spawned from its own Mission Editor entry: cold start on its parking spot, then it joins the leader.
        self.assertIn('ADD GROUP Manolo country 2 category 0 start TakeOffParking / From Parking Area late nil unit at '
                      '10,20 then Follow leader 9 points 2', stdout)   # + a second point: DCS's AI takes off to fly to it
        self.assertEqual(replies['w5'], (False, 'Manolo is already started'))
        self.assertIn('WING TASK Manolo Follow leader 9 pos -300,30,-150', stdout)   # 300 m behind, left, 30 m up
        # Strike a point: rockets, a dive from 150 m, heading 270 (DCS's weapon flags; direction in radians).
        self.assertIn('WING PUSH Manolo Bombing weapon 30720 alt 150 dir 4.71 Dive', stdout)
        self.assertEqual(replies['w7'], (True, 'striking the point with rockets from 150 m, heading 270'))
        self.assertEqual(replies['w8'][0], False)                                    # placed in the air: no cold start
        # In the air beside the leader (20 km north, heading north): 1 km behind, 300 m to its right.
        self.assertIn('ADD GROUP Lobo country 2 category 0 start Turning Point / Turning Point late nil unit at 19000,300 '
                      'then Follow leader 9 points 1', stdout)
        self.assertIn('WING OPTION Manolo 0=4', stdout)                               # weapons hold
        air = self.of(messages, 'groups')[-1]['groups']['Manolo']['air']
        self.assertEqual((air['active'], air['started'], air['last_event']), (True, 'cold', 'takeoff'))
        self.assertIn('WING TASK Manolo Follow leader 9 pos -200,0,200', stdout)   # joins the leader on takeoff
        # UNCONTROLLED: woken up with DCS's own Start command where it stands, not re-spawned.
        self.assertIn('WING COMMAND Paco Start', stdout)
        # Only its departure point in the editor (live 2026-11-04: engines on, never taxied): a second point, and its
        # own CAP task kept with Follow added.
        self.assertIn('WING TASK Paco Mission points 2 first tasks EngageTargets+Follow', stdout)
        self.assertNotIn('ADD GROUP Paco', stdout)
        self.assertEqual(replies['w12'], (True, 'Paco started on its parking spot (uncontrolled)'))
        self.assertEqual(replies['w13'], (False, 'Paco is already started'))
        self.assertEqual(air['weapons'], [{'count': 2, 'kind': 'bomb', 'name': 'Mk-82', 'against': 'ground'}])
        self.assertEqual(air['editor_task'], 'CAS')   # the group's main task in the Mission Editor
        # Combat log for the callouts: bomb away, a kill, a hit taken (numbered).
        self.assertEqual([{k: v for k, v in e.items() if k != 's_ago'} for e in air['combat']], [
            {'n': 1, 'kind': 'weapon_released', 'weapon': 'Mk-82'},
            {'n': 2, 'kind': 'kill', 'target': 'T-72B', 'air': False},
            {'n': 3, 'kind': 'hit_taken', 'by': 'ZU-23'}])
        self.assertIn('hit', air['events'])
        # Its own eyes: the T-72s beside it are seen; behind it or beyond a vehicle's 6 km: not; the SAM site 9 km
        # ahead (10 km for a SAM) is hidden by the mission's fog (3 km visibility, 200 m thick).
        manolo = self.of(messages, 'groups')[-1]['groups']['Manolo']
        seen = {e['group']: (e['type'], e['units'], e['air'], round(e['distance_m']))
                for e in manolo['enemies'] if not e.get('rwr')}
        self.assertEqual(seen, {'Red Armor': ('T-72B', 2, False, 3000), 'Static T-72B': ('T-72B', 1, False, 2062)})
        # An SA-8 only on its RWR: its direction (north-east), no position, after the ones it sees.
        self.assertEqual(manolo['enemies'][-1], {'group': 'Osa-1', 'type': 'Osa 9A33 ln', 'air': False, 'rwr': True,
                                                 'bearing_deg': 45})
        self.assertTrue(next(e for e in manolo['enemies'] if e['group'] == 'Static T-72B')['static'])
        # Started by the mission itself (hot start, live 2026-11-04): adopted, so its events reach the agent.
        halcon = self.of(messages, 'groups')[-1]['groups']['Halcon']['air']
        self.assertEqual(halcon['started'], 'mission')
        self.assertIn('ENV DCS-Instructor: wingman Halcon adopted (started by the mission)', stdout)
        self.assertEqual(stdout.count('wingman Halcon adopted'), 1)
        # A static is attacked by bombing its exact point.
        self.assertEqual(replies['w16'], (True, 'bombing the T-72B Static T-72B'))
        # Fly to a point low level (60 m above the ground), then orbit there.
        self.assertEqual(replies['w17'], (True, 'flying there at 60 m above the ground'))
        # A SAM group is bombed on its point (DCS attacks it as a group only with anti-radiation missiles); armour is
        # attacked as a group.
        self.assertEqual(replies['w18'], (True, 'bombing the point of the Kub 1S91 str of Red SAM (a SAM: DCS attacks '
                                                'it as a group only with anti-radiation missiles)'))
        self.assertEqual(replies['w19'], (True, 'attacking Red Armor'))
        self.assertIn('WING TASK Manolo Mission points 2', stdout)
        self.assertNotIn('eyes_error', manolo)
        # Attack one unit of a group (then the pilot clears the next), and everything in a zone while orbiting it.
        self.assertEqual(replies['w14'], (True, 'attacking one Soldier M4 of Buenos'))
        self.assertIn('WING PUSH Manolo AttackUnit', stdout)
        self.assertEqual(replies['w15'], (True, 'engaging ground targets in the zone'))
        self.assertIn('WING TASK Manolo ComboTask', stdout)
        # A helicopter (user, 2026-11-05: the Mi-8) while the leader flies an airplane: no air start beside them, a
        # departure route without Follow, no formation; its kind and the leader's in its status.
        self.assertEqual(replies['w20'], (False, 'an air start puts you beside the leader in formation: this '
                                                 'helicopter cannot fly with the leader\'s airplane; start on the ground'))
        self.assertEqual(replies['w21'], (True, 'Hip started on its parking spot (uncontrolled)'))
        self.assertIn('WING TASK Hip Mission points 2 first tasks \n', stdout)
        self.assertEqual(replies['w22'], (False, 'this helicopter cannot fly formation on the leader\'s airplane'))
        hip = self.of(messages, 'groups')[-1]['groups']['Hip']['air']
        self.assertEqual((hip['kind'], hip['leader_kind']), ('helicopter', 'airplane'))
        self.assertNotIn('gear_down', hip)
        # A helicopter lands at a point (DCS's Land task: 5 min, combat landing); an airplane cannot.
        self.assertEqual(replies['w23'], (True, 'landing there for 5 min'))
        self.assertIn('WING TASK Hip Land at 42100,42000 duration true 300 combat true', stdout)
        self.assertEqual(replies['w24'], (False, 'only a helicopter lands at a point: an airplane needs an airfield'))
        # A helicopter attacks the ground only by strafing (user, 2026-11-05: its guns fired, its rockets did not).
        self.assertEqual(replies['w25'], (True, 'strafing the point with rockets from 50 m'))
        self.assertIn('WING PUSH Hip Strafing weapon 30720 alt 50 dir nil nil', stdout)   # no dive for a strafe
        self.assertEqual(replies['w26'], (True, 'strafing the T-72B of Red Armor'))
        self.assertEqual(replies['w27'], (True, 'strafing the T-72B Static T-72B'))
        self.assertNotIn('WING PUSH Hip Bombing', stdout)
        self.assertNotIn('WING PUSH Hip AttackGroup', stdout)
        # A helicopter carries a squad with DCS's own transport tasks (the squad's group id 7); an airplane cannot.
        self.assertEqual(replies['w28'], (True, 'going to land there and pick up Buenos'))
        self.assertIn('WING TASK Hip Embarking at 42100,42000 groups 7', stdout)
        self.assertEqual(replies['w29'], (True, 'going to land there and drop Buenos'))
        self.assertIn('WING TASK Hip Disembarking at 42200,42000 groups 7', stdout)
        self.assertEqual(replies['w30'], (False, 'only a helicopter carries troops here'))
        # The pilot's F10 map marks come as zones by their first line, only those of the pilot's side.
        marks = [z for z in self.of(messages, 'groups')[-1]['zones'] if z.get('mark')]
        self.assertEqual([m['name'] for m in marks], ['LZ1'])

    def test_no_flags_reported_until_listed(self):
        self.assertEqual(self.of(self.run_hook('western', radar=True, tasks=True)[0], 'flags'), [])

    def test_no_traffic_without_the_scripting_environment(self):
        self.assertEqual(self.of(self.run_hook('western')[0], 'traffic'), [])

    def test_no_weather_without_the_scripting_environment(self):
        self.assertEqual(self.of(self.run_hook('western')[0], 'weather'), [])


class State(unittest.TestCase):
    def test_callsign_reaches_the_situation(self):
        state = CockpitState()
        self.assertEqual(state.apply({'type': 'callsign', 'callsign': 'Enfield 1-1'}), 'callsign')
        self.assertEqual(TowerView(state).situation()['callsign'], 'Enfield 1-1')
        state.apply({'type': 'left_aircraft', 'time': 5})
        self.assertNotIn('callsign', TowerView(state).situation())

    def test_flags_reach_the_state(self):
        state = CockpitState()
        self.assertEqual(state.apply({'type': 'flags', 'flags': {'10': 1, 'convoy_left': 0}}), 'flags')
        self.assertEqual(state.flags, {'10': 1, 'convoy_left': 0})
        state.apply({'type': 'flags', 'flags': []})   # an empty Lua table arrives as a list
        self.assertEqual(state.flags, {})

    def test_task_channel_flag_lines(self):
        hook = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        hook.bind(('127.0.0.1', 0))
        hook.settimeout(2)
        channel = TaskChannel(port=hook.getsockname()[1], log=lambda _: None)
        channel.flags(['10', 'convoy_left'], [])
        self.assertEqual(hook.recv(512).decode().split('\t')[2:], ['flags', '10|convoy_left', ''])
        channel.set_flag('20')
        self.assertEqual(hook.recv(512).decode().split('\t')[2:], ['set_flag', '20', '1'])
        self.assertIsNone(channel.flags([], []))                     # nothing to send
        for bad in ('10;os.exit()', 'a b', ''):
            with self.assertRaises(ValueError):
                channel.set_flag(bad)
        hook.close()
        channel.sock.close()

    def test_hook_file_loads_the_project_script(self):
        text = hook_text()
        self.assertIn('dcs/instructor_hook.lua', text)
        self.assertIn('pcall(', text)


if __name__ == '__main__':
    unittest.main()
