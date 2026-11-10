"""Offline tests for the local GUI: settings file, device lists, agents, the Status data and the HTTP API.

Run: python -m unittest discover tests
"""
import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import quiet_log  # noqa: F401  (the program's log goes to a temporary file)
from client import mic
from gui import status as gui_status
from gui.server import Gui, agent_changes, audio_changes, voice_changes
from gui.status import Live, SrsCheck
from instructor.srs import client_audio
from instructor.voice_loop import WindowWatch
from shared import audio_devices, settings as settings_file
from shared.agents import AgentStore, fill_mission
from shared.cockpit import CockpitState

SETTINGS = {'language': 'en', 'unknown_key': {'kept': True},
            'voice': {'name': 'M1', 'speed': 1.05, 'output': 'srs', 'model_dir': 'models/supertonic3'},
            'mic': {'device': ''}, 'speech': {'provider': 'chrome'},
            'srs': {'frequencies_mhz': [251.0], 'modulations': ['AM']},
            'brain': {'provider': 'chatgpt', 'model': 'test-model'},
            'ptt': {'joystick': 'Stick', 'button': 6, 'instructor_frequency_only': True}}
SRS_RUNNING = {'installed': True, 'server': True, 'client': True,
               'client_audio': {'output': {'id': 'x', 'name': 'Headphones (Test Headset)'}, 'input': {}}}
RADIO_ON_251 = {'selected': 1, 'radios': [{'name': 'intercom', 'freq': 100.0, 'modulation': 2},
                                          {'name': 'UHF', 'freq': 251.0e6, 'modulation': 0}]}


class SettingsFile(unittest.TestCase):
    def test_update_merges_and_keeps_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text(json.dumps(SETTINGS), encoding='utf-8')
            result = settings_file.update({'voice': {'speed': 1.2}, 'audio': {'output_device': 'X'}}, path)
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(result, saved)
            self.assertEqual(saved['voice']['speed'], 1.2)
            self.assertEqual(saved['voice']['model_dir'], 'models/supertonic3')
            self.assertEqual(saved['unknown_key'], {'kept': True})
            self.assertEqual(saved['audio'], {'output_device': 'X'})
            self.assertFalse(path.with_suffix('.json.tmp').exists())


class Validation(unittest.TestCase):
    def test_voice_values_in_range(self):
        self.assertEqual(voice_changes({'name': 'F3', 'speed': 1.2, 'steps': 10.0, 'output': 'speakers'}),
                         {'name': 'F3', 'speed': 1.2, 'steps': 10, 'output': 'speakers'})
        for bad in ({'name': 'X9'}, {'speed': 3}, {'steps': 1}, {'volume': -0.1}, {'output': 'radio'},
                    {'language': 'xx'}, {'silence_s': 'abc'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                voice_changes(bad)

    def test_audio_changes(self):
        self.assertEqual(audio_changes({'output_device': 'VR', 'mic_device': 'Mic', 'other': 1}),
                         {'audio': {'output_device': 'VR'}, 'mic': {'device': 'Mic'}})


API_DIRECTSOUND = {'name': 'Windows DirectSound', 'default_input_device': 2, 'default_output_device': 5}
DEVICES = [
    {'name': 'Microphone (Wireless Gaming Hea', 'hostapi': 0, 'max_input_channels': 1, 'max_output_channels': 0},
    {'name': 'Headphones (Wireless Gaming Hea', 'hostapi': 0, 'max_input_channels': 0, 'max_output_channels': 2},
    {'name': 'Primary Sound Capture Driver', 'hostapi': 1, 'max_input_channels': 2, 'max_output_channels': 0},
    {'name': 'Microphone (Wireless Gaming Headset)', 'hostapi': 1, 'max_input_channels': 1, 'max_output_channels': 0},
    {'name': 'Headset Microphone (Oculus Virtual Audio Device)', 'hostapi': 1, 'max_input_channels': 1,
     'max_output_channels': 0},
    {'name': 'Primary Sound Driver', 'hostapi': 1, 'max_input_channels': 0, 'max_output_channels': 2},
    {'name': 'Headphones (Oculus Virtual Audio Device)', 'hostapi': 1, 'max_input_channels': 0,
     'max_output_channels': 2},
]
APIS = [{'name': 'MME', 'default_input_device': 0, 'default_output_device': 1}, API_DIRECTSOUND]


class Devices(unittest.TestCase):
    def test_full_names_without_the_default_alias(self):
        self.assertEqual(audio_devices.list_devices('input', DEVICES, APIS),
                         ['Microphone (Wireless Gaming Headset)', 'Headset Microphone (Oculus Virtual Audio Device)'])
        self.assertEqual(audio_devices.list_devices('output', DEVICES, APIS),
                         ['Headphones (Oculus Virtual Audio Device)'])

    def test_recorder_finds_a_full_name_among_cut_mme_names(self):
        inputs = [{'index': 0, 'name': 'Microphone (Wireless Gaming Hea', 'api': 'MME', 'rate': 48000},
                  {'index': 3, 'name': 'Microphone (Wireless Gaming Headset)', 'api': 'Windows WASAPI', 'rate': 48000}]
        with mock.patch('client.mic.input_devices', return_value=inputs):
            self.assertEqual(mic.find_device('Microphone (Wireless Gaming Headset)')['index'], 0)
            self.assertEqual(mic.find_device('Wireless Gaming')['index'], 0)
            with self.assertRaises(mic.MicError):
                mic.find_device('Oculus')

    def test_srs_client_devices_from_its_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            (install / 'Client').mkdir()
            (install / 'Client' / 'global.cfg').write_text(
                '[Client Settings]\nAudioInputDeviceId=\nAudioOutputDeviceId={0.0.0.00000000}.{guid-vr}\n',
                encoding='utf-8')
            endpoints = {'{0.0.0.00000000}.{guid-vr}': 'Headphones (Oculus Virtual Audio Device)'}
            with mock.patch('shared.audio_devices.windows_endpoints',
                            side_effect=lambda flow: endpoints if flow == 'Render' else {}):
                audio = client_audio(install / 'ExternalAudio' / 'DCS-SR-ExternalAudio.exe')
        self.assertEqual(audio['output']['name'], 'Headphones (Oculus Virtual Audio Device)')
        self.assertEqual(audio['input'], {'id': '', 'name': None})


class FakeVoice:
    def __init__(self):
        self.configured, self.tests = [], []

    def configure(self, settings):
        self.configured.append(settings)

    def test(self, text, via, params, agent=None):
        self.tests.append((text, via, params, agent))
        return {'synthesis_s': 0.1, 'audio_s': 1.0, 'via': via}


class FakeDictation:
    def __init__(self):
        self.microphones = []
        self.events = mock.Mock(clients=[object()])

    def set_microphone(self, name):
        self.microphones.append(name)


def running_live(agents: AgentStore) -> Live:
    live = Live()
    live.agents = agents
    live.state = CockpitState(aircraft='F-5E-3', callsign='Colt 1-1', radio=RADIO_ON_251)
    live.tower = mock.Mock(tower={'name': 'Senaki-Kolkhi'})
    live.tower.radar.return_value = {'status': 'contact', 'range_nm': 4.2, 'altitude_ft': 3000}
    live.tower.weather.return_value = {'wind': 'from 263 magnetic at 8 kt', 'qnh': '1011 hPa, 29.85 inHg',
                                       'runway_in_use': {'runway': '27'}}
    live.dictation = FakeDictation()
    live.voice = FakeVoice()
    live.router = mock.Mock()
    live.last_message_at = time.monotonic()
    return live


class WithAgents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = AgentStore(Path(self.tmp.name) / 'agents', settings=SETTINGS)

    def tearDown(self):
        self.tmp.cleanup()


class Agents(WithAgents):
    def test_first_run_makes_the_instructor_from_the_old_settings(self):
        agent = self.agents.get('instructor')
        self.assertEqual((agent['frequency_mhz'], agent['modulation'], agent['active']), (251.0, 'AM', True))
        self.assertEqual(agent['voice']['name'], 'M1')
        self.assertEqual(self.agents.mission('instructor'), self.agents.original_mission('instructor'))
        self.assertIn('{tool_guide}', self.agents.mission('instructor'))

    def test_mission_edit_and_restore(self):
        self.agents.save_mission('instructor', 'You are a calm instructor at {tower}. {unknown} stays.')
        self.assertIn('calm', self.agents.mission('instructor'))
        self.assertEqual(fill_mission(self.agents.mission('instructor'), {'tower': 'Senaki'}),
                         'You are a calm instructor at Senaki. {unknown} stays.\n')
        with self.assertRaises(ValueError):
            self.agents.save_mission('instructor', '   ')
        self.agents.restore_mission('instructor')
        self.assertEqual(self.agents.mission('instructor'), self.agents.original_mission('instructor'))

    def test_channel_checks(self):
        self.agents.create('tower', {'name': 'Tower', 'role': 'atc', 'active': True, 'frequency_mhz': 260.0,
                                     'modulation': 'AM'})
        self.agents.reload()
        self.agents.update('tower', {'frequency_mhz': 251.0})   # shared with the instructor: allowed (2026-11-03)
        self.assertEqual([a['id'] for a in self.agents.on_channel(self.agents.get('tower'))], ['instructor', 'tower'])
        self.agents.update('tower', {'frequency_mhz': 251.0, 'modulation': 'FM'})   # same MHz, other modulation
        self.assertEqual([a['id'] for a in self.agents.on_channel(self.agents.get('tower'))], ['tower'])
        self.agents.create('atc2', {'name': 'ATC 2', 'role': 'atc', 'active': True, 'frequency_mode': 'airfield',
                                    'frequency_mhz': 0.0, 'modulation': 'AM'})
        self.agents.reload()
        with self.assertRaises(ValueError) as caught:   # one agent answers for the airfields
            self.agents.update('atc2', {'name': 'ATC 2'})
        self.assertEqual(str(caught.exception), 'frequency_taken')
        with self.assertRaises(ValueError):
            self.agents.update('tower', {'frequency_mhz': 500.0})
        with self.assertRaises(ValueError):
            self.agents.path('../escape')

    def test_agent_changes_validation(self):
        self.assertEqual(agent_changes({'frequency_mhz': '252.0251', 'modulation': 'FM', 'active': 0,
                                        'speech': {'language': 'es-US'}}),
                         {'frequency_mhz': 252.025, 'modulation': 'FM', 'active': False,
                          'speech': {'language': 'es-US'}})
        for bad in ({'modulation': 'SSB'}, {'speech': {'language': 'xx'}}, {'name': ''},
                    {'voice': {'name': 'X1'}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                agent_changes(bad)


class StatusData(WithAgents):
    def test_not_running(self):
        data = gui_status.status(Live(), SETTINGS, {'installed': False}, self.agents)
        tiles = {t['id']: t for t in data['tiles']}
        self.assertFalse(data['running'])
        self.assertEqual(tiles['dcs']['state'], 'not_running')
        self.assertEqual(tiles['srs']['light'], 'error')
        self.assertEqual(data['frequency'], '251.000 AM')
        self.assertEqual([s['id'] for s in data['services']], ['instructor', 'atc', 'commander'])
        # The ATC exists (created with the instructor) and answers on the airfields' own frequencies.
        atc = data['services'][1]
        self.assertEqual((atc.get('soon'), atc['frequency_mode'], atc['frequency']), (None, 'airfield', None))
        self.assertTrue(data['services'][2]['soon'])

    def test_running(self):
        live = running_live(self.agents)
        live.said('pilot', 'radio check')
        live.said('agent', 'loud and clear', 2.5, 'Instructor')
        data = gui_status.status(live, SETTINGS, SRS_RUNNING)
        tiles = {t['id']: t for t in data['tiles']}
        self.assertEqual({k: tiles[k]['light'] for k in tiles},
                         {'dcs': 'ok', 'callsign': 'ok', 'srs': 'ok', 'radio': 'ok', 'dictation': 'ok', 'brain': 'ok',
                          'radar': 'ok', 'weather': 'ok'})
        self.assertEqual(tiles['radio']['value'], {'key': 'tuned_to', 'args': {'agent': 'Instructor'}})
        self.assertEqual(tiles['srs']['detail']['args']['device'], 'Headphones (Test Headset)')
        self.assertEqual(tiles['brain']['detail'], {'key': 'last_answer', 'args': {'seconds': 2.5}})
        self.assertEqual(tiles['weather']['detail']['args'], {'qnh': '1011 hPa, 29.85 inHg', 'runway': '27'})
        self.assertEqual([(e['who'], e['agent']) for e in data['exchanges']],
                         [('pilot', None), ('agent', 'Instructor')])

    def test_off_frequency_and_stale_dcs(self):
        live = running_live(self.agents)
        live.state.radio = {'selected': 1, 'radios': [{}, {'name': 'UHF', 'freq': 243.0e6, 'modulation': 0}]}
        live.last_message_at = time.monotonic() - 10
        tiles = {t['id']: t for t in gui_status.status(live, SETTINGS, SRS_RUNNING)['tiles']}
        self.assertEqual(tiles['radio']['state'], 'off_frequency')
        self.assertEqual(tiles['dcs']['state'], 'waiting')


class Api(WithAgents):
    def setUp(self):
        super().setUp()
        self.path = Path(self.tmp.name) / 'settings.json'
        self.path.write_text(json.dumps(SETTINGS), encoding='utf-8')
        self.live = running_live(self.agents)
        self.gui = Gui(self.live, port=0, settings_path=self.path)
        self.gui.srs.get = lambda settings: SRS_RUNNING
        self.gui.start()

    def tearDown(self):
        self.gui.close()
        super().tearDown()

    def call(self, path, body=None, origin=None):
        data = None if body is None else json.dumps(body).encode('utf-8')
        request = urllib.request.Request(self.gui.url.rstrip('/') + path, data=data,
                                         headers={'Content-Type': 'application/json',
                                                  **({'Origin': origin} if origin else {})})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()

    def test_page_and_texts(self):
        status, page = self.call('/')
        self.assertEqual(status, 200)
        self.assertIn(b'app.js', page)
        texts = json.loads(self.call('/api/text')[1])
        self.assertEqual(texts['app_name'], 'DCS_AI_Service')
        self.assertEqual(texts['tabs']['mission'], 'Mission')

    def test_status(self):
        data = json.loads(self.call('/api/status')[1])
        self.assertTrue(data['running'])
        self.assertEqual(len(data['tiles']), 8)

    def test_agent_read_and_save(self):
        agent = json.loads(self.call('/api/agents/instructor')[1])
        self.assertTrue(agent['mission_is_original'])
        origin = f'http://127.0.0.1:{self.gui.port}'
        data = json.loads(self.call('/api/agents/instructor', {'frequency_mhz': 252.5, 'voice': {'name': 'F2'}},
                                    origin)[1])
        self.assertEqual((data['frequency_mhz'], data['voice']['name'], data['voice']['speed']), (252.5, 'F2', 1.05))
        saved = json.loads((Path(self.tmp.name) / 'agents' / 'instructor' / 'agent.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['frequency_mhz'], 252.5)
        self.live.router.refresh.assert_called()   # the running brains take the change

    def test_mission_save_and_restore(self):
        data = json.loads(self.call('/api/agents/instructor/mission', {'text': 'Be brief. {tower}'})[1])
        self.assertEqual((data['mission'], data['mission_is_original']), ('Be brief. {tower}', False))
        data = json.loads(self.call('/api/agents/instructor/mission/restore', {})[1])
        self.assertTrue(data['mission_is_original'])

    def test_unknown_agent_and_bad_value(self):
        for path in ('/api/agents/nobody', '/api/agents/..%2Fsettings'):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.call(path)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call('/api/agents/instructor', {'voice': {'speed': 9}})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.agents.get('instructor')['voice']['speed'], 1.05)

    def test_add_agent(self):
        data = json.loads(self.call('/api/agents', {'role': 'squad', 'name': 'Tango 2', 'frequency_mhz': 256.0,
                                                    'modulation': 'AM', 'group': 'Tango'})[1])
        self.assertEqual((data['id'], data['name'], data['role'], data['active']), ('tango-2', 'Tango 2', 'squad', True))
        self.assertEqual(data['squad'], {'groups': ['Tango'], 'name': 'Tango 2'})
        self.assertTrue(data['mission_is_original'])                     # the role's default mission
        self.assertTrue((Path(self.tmp.name) / 'agents' / 'tango-2' / 'mission.md').exists())
        self.live.router.responder.assert_called_with('tango-2')         # ready at once in a running program
        again = json.loads(self.call('/api/agents', {'role': 'instructor', 'name': 'Tango 2', 'frequency_mhz': 257.0,
                                                     'modulation': 'FM'})[1])
        self.assertEqual(again['id'], 'tango-2-2')                       # same name, another folder

    def test_add_agent_refused(self):
        cases = [({'role': 'atc', 'name': 'Tower 2', 'frequency_mhz': 260.0, 'modulation': 'AM'}, 'role'),
                 ({'role': 'squad', 'name': 'Tango', 'frequency_mhz': 256.0, 'modulation': 'AM'}, 'group'),
                 ({'role': 'instructor', 'name': 'Mech', 'frequency_mhz': 500.0, 'modulation': 'AM'},
                  'frequency_mhz'),
                 ({'role': 'instructor', 'name': '','frequency_mhz': 252.0, 'modulation': 'AM'}, 'name'),
                 ({'role': 'instructor', 'name': 'Mech', 'modulation': 'AM'}, 'frequency_mhz')]
        for body, field in cases:
            with self.subTest(field=field), self.assertRaises(urllib.error.HTTPError) as caught:
                self.call('/api/agents', body)
            self.assertEqual(json.loads(caught.exception.read())['field'], field)
        self.assertEqual(sorted(a['id'] for a in self.agents.all()), ['atc', 'instructor'])

    def test_rename(self):
        data = json.loads(self.call('/api/agents/instructor', {'name': 'Mechanic'})[1])
        self.assertEqual((data['name'], data['fixed']), ('Mechanic', False))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call('/api/agents/atc', {'name': 'Tower'})
        self.assertEqual(json.loads(caught.exception.read())['field'], 'fixed_name')
        data = json.loads(self.call('/api/agents/atc', {'name': 'ATC', 'active': False})[1])   # same name: fine
        self.assertEqual((data['active'], data['fixed']), (False, True))
        squad = self.agents.add('squad', 'Buenos', 255.0, 'AM', SETTINGS, 'Buenos')
        data = json.loads(self.call(f'/api/agents/{squad["id"]}', {'name': 'Los Buenos'})[1])
        self.assertEqual(data['squad']['name'], 'Los Buenos')            # who the squad says it is

    def test_open_srs(self):
        """"Open SRS" starts what is closed and brings the client out of the tray (both faked here)."""
        from unittest import mock
        with mock.patch('instructor.srs.find_external_audio', return_value=None), \
                self.assertRaises(urllib.error.HTTPError) as caught:
            self.call('/api/srs/open', {})
        self.assertEqual(json.loads(caught.exception.read())['field'], 'srs')
        exe = Path('C:/SRS/ExternalAudio/DCS-SR-ExternalAudio.exe')
        with mock.patch('instructor.srs.find_external_audio', return_value=exe), \
                mock.patch('instructor.srs.open_apps', return_value={'server': 'running', 'client': 'started'}), \
                mock.patch('instructor.srs.show_client', return_value=True) as shown:
            data = json.loads(self.call('/api/srs/open', {})[1])
        self.assertEqual(data['apps'], {'server': 'running', 'client': 'started'})
        deadline = time.monotonic() + 2   # shown from a thread
        while not shown.called and time.monotonic() < deadline:
            time.sleep(0.01)
        shown.assert_called_once_with(exe)

    def test_squad_group_renamed(self):
        """The group renamed in the Mission Editor (Buenos -> Tango): the squad follows it from the Frequency tab."""
        squad = self.agents.add('squad', 'Buenos', 255.0, 'AM', SETTINGS, 'Buenos')
        data = json.loads(self.call(f'/api/agents/{squad["id"]}', {'name': 'Tango', 'group': ' Tango '})[1])
        self.assertEqual(data['squad'], {'groups': ['Tango'], 'name': 'Tango'})
        self.assertNotIn('group', data)
        data = json.loads(self.call(f'/api/agents/{squad["id"]}', {'aliases': [' Tango 1', 'Green Team', '', 'Tango 1']})[1])
        self.assertEqual(data['aliases'], ['Tango 1', 'Green Team'])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call(f'/api/agents/{squad["id"]}', {'aliases': [f'name {n}' for n in range(9)]})
        self.assertEqual(json.loads(caught.exception.read())['field'], 'aliases')
        for agent_id, body in ((squad['id'], {'group': ''}), ('instructor', {'group': 'Tango'})):
            with self.subTest(agent=agent_id), self.assertRaises(urllib.error.HTTPError) as caught:
                self.call(f'/api/agents/{agent_id}', body)
            self.assertEqual(json.loads(caught.exception.read())['field'], 'group')

    def test_delete_agent_keeps_its_folder(self):
        squad = self.agents.add('squad', 'Tango', 256.0, 'AM', SETTINGS, 'Tango')
        data = json.loads(self.call(f'/api/agents/{squad["id"]}/delete', {})[1])
        self.assertEqual(data['deleted'], 'tango')
        kept = Path(data['kept_in'])
        self.assertTrue((kept / 'agent.json').exists())
        self.assertEqual(kept.parent, Path(self.tmp.name) / 'backups' / 'agents')
        self.assertIsNone(self.agents.get('tango'))
        self.assertFalse((Path(self.tmp.name) / 'agents' / 'tango').exists())
        self.live.router.remove.assert_called_with('tango')              # off the air at once
        again = self.agents.add('squad', 'Tango', 256.0, 'AM', SETTINGS, 'Tango')   # its frequency is free again
        self.assertEqual(again['id'], 'tango')

    def test_triggers_saved_and_checked(self):
        squad = self.agents.add('squad', 'Buenos', 255.0, 'AM', SETTINGS, 'Buenos')
        body = {'events': [{'flag': '10', 'instructions': 'Call when you see the helicopter.'}],
                'actions': [{'name': 'smoke_ready', 'flag': '20', 'description': 'When the pilot sees the smoke.'}]}
        data = json.loads(self.call(f'/api/agents/{squad["id"]}', body)[1])
        self.assertEqual(data['events'], [{**body['events'][0], 'internal': False}])
        self.assertEqual(data['actions'], [{**body['actions'][0], 'value': 1, 'internal': False}])
        self.live.router.refresh.assert_called()
        bad = [({'events': [{'flag': '10 ; x', 'instructions': 'x'}]}, 'events.0'),
               ({'actions': [{'name': 'pop_smoke', 'flag': '20', 'description': 'x'}]}, 'actions.0'),   # a squad tool
               ({'actions': [body['actions'][0], body['actions'][0]]}, 'actions.1'),                     # repeated
               ({'events': 'flag 10'}, 'events')]
        for changes, field in bad:
            with self.subTest(field=field), self.assertRaises(urllib.error.HTTPError) as caught:
                self.call(f'/api/agents/{squad["id"]}', changes)
            self.assertEqual(json.loads(caught.exception.read())['field'], field)
        self.assertEqual(self.agents.get(squad['id'])['events'], data['events'])   # nothing bad was saved
        data = json.loads(self.call(f'/api/agents/{squad["id"]}', {'events': []})[1])   # actions kept
        self.assertEqual((data['events'], len(data['actions'])), ([], 1))
        with self.assertRaises(urllib.error.HTTPError) as caught:   # the ATC has no triggers
            self.call('/api/agents/atc', body)
        self.assertEqual(json.loads(caught.exception.read())['field'], 'events')
        # A condition row as the page sends it (unused fields empty): saved without them.
        page = {'when': 'casualties', 'about': '', 'zone': '', 'instructions': 'Report your losses.'}
        data = json.loads(self.call(f'/api/agents/{squad["id"]}', {'events': [page]})[1])
        self.assertEqual(data['events'], [{'when': 'casualties', 'instructions': 'Report your losses.'}])
        self.assertIn('casualties', data['conditions'])
        self.assertIn({'id': squad['id'], 'name': 'Buenos'}, data['squads'])

    def test_atc_cannot_be_deleted(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call('/api/agents/atc/delete', {})
        self.assertEqual(json.loads(caught.exception.read())['field'], 'fixed_agent')
        self.assertIsNotNone(self.agents.get('atc'))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call('/api/agents/nobody/delete', {})
        self.assertEqual(caught.exception.code, 404)

    def test_microphone_change_reaches_the_dictation(self):
        self.call('/api/audio', {'mic_device': 'Headset Microphone (Oculus Virtual Audio Device)'})
        self.assertEqual(self.live.dictation.microphones, ['Headset Microphone (Oculus Virtual Audio Device)'])
        self.call('/api/audio', {'output_device': 'Headphones (Oculus Virtual Audio Device)'})
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['audio']['output_device'], 'Headphones (Oculus Virtual Audio Device)')
        self.assertEqual(len(self.live.dictation.microphones), 1)   # only a microphone change reconnects it
        self.assertEqual(self.live.voice.configured[-1]['audio']['output_device'],
                         'Headphones (Oculus Virtual Audio Device)')

    def test_voice_test_on_the_agent(self):
        data = json.loads(self.call('/api/voice/test', {'text': 'Radio check', 'via': 'srs', 'agent': 'instructor',
                                                        'params': {'name': 'M3', 'output': 'srs'}})[1])
        self.assertEqual(data['via'], 'srs')
        text, via, params, agent = self.live.voice.tests[0]
        self.assertEqual((text, via, params, agent['id']), ('Radio check', 'srs', {'name': 'M3'}, 'instructor'))

    def test_events_count_open_pages(self):
        with urllib.request.urlopen(self.gui.url + 'api/events', timeout=5):
            deadline = time.monotonic() + 2
            while not self.gui.events.clients and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(self.gui.events.clients), 1)

    def test_stop_and_foreign_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call('/api/stop', {}, origin='http://example.com')
        self.assertEqual(caught.exception.code, 403)
        self.assertFalse(self.live.stop.is_set())
        self.call('/api/stop', {})
        self.assertTrue(self.live.stop.is_set())


class Routing(WithAgents):
    def test_each_agent_answers_with_its_own_brain_and_voice(self):
        from instructor.voice_loop import Router
        self.agents.create('tower', {'name': 'Tower', 'role': 'atc', 'active': True, 'frequency_mhz': 260.0,
                                     'modulation': 'AM', 'voice': {'name': 'F2'}})
        self.agents.reload()
        heard = []

        def make_responder(agent_id):
            responder = mock.Mock(brain=mock.Mock(instructions='old', radio='old'))
            responder.heard.side_effect = lambda text, record=True: heard.append((agent_id, text))
            return responder

        router = Router(self.agents, make_responder)
        router.heard('radio check', self.agents.get('tower'))
        router.heard('ready to start', None)   # no agent known (no radio data): the default agent
        self.assertEqual(heard, [('tower', 'radio check'), ('instructor', 'ready to start')])
        self.agents.save_mission('tower', 'You are the tower.')
        self.agents.update('tower', {'frequency_mhz': 261.0})
        router.refresh()
        tower_brain = router.responders['tower'].brain
        self.assertEqual((tower_brain.instructions, tower_brain.radio), ('You are the tower.', '261.000 AM'))
        forgotten = []
        router.forget = forgotten.append
        mechanic = self.agents.add('instructor', 'Mechanic', 262.0, 'AM', SETTINGS)
        router.heard('radio check', mechanic)
        self.agents.remove(mechanic['id'])
        router.remove(mechanic['id'])           # deleted in the GUI: no answers, its desk dropped
        router.heard('radio check again', mechanic)
        self.assertNotIn('mechanic', router.responders)
        self.assertEqual(forgotten, ['mechanic'])
        self.assertEqual(heard[-1], ('mechanic', 'radio check'))   # nobody answered the second call

    def test_shared_frequency(self):
        """Tango and Victor on 255 AM: the one named answers, "all stations" gets both in turn, an unnamed phrase goes
        on with the last one, and the others hear everything (user's design, 2026-11-03)."""
        from instructor.voice_loop import Router
        from shared.dispatch import Dispatcher
        tango = self.agents.add('squad', 'Tango', 255.0, 'AM', SETTINGS, 'Tango')
        victor = self.agents.add('squad', 'Victor', 255.0, 'AM', SETTINGS, 'Victor')
        self.agents.update(victor['id'], {'aliases': ['Green Team']})
        heard, brains = [], {}

        def make_responder(agent_id):
            responder = mock.Mock()
            responder.heard.side_effect = lambda text, record=True: heard.append((agent_id, text, record))
            brains[agent_id] = responder.brain
            return responder

        router = Router(self.agents, make_responder, Dispatcher(everyone=[['all', 'stations']]))
        router.heard('say again', tango)                           # unnamed, nobody spoke yet: nobody answers
        router.heard('tangos move to zone one', tango)        # tolerant: the gate gave the first agent
        router.heard('green team, hold position', tango)          # an alias
        router.heard('All stations, radio check', tango)
        router.on_air(victor['id'], 'Victor holding.')             # Victor spoke last
        router.heard('copy, stay there', tango)                    # unnamed: goes on with Victor
        self.assertEqual(heard, [('tango', 'tangos move to zone one', True),
                                 ('victor', 'green team, hold position', True),
                                 ('tango', 'All stations, radio check', True),
                                 ('victor', 'All stations, radio check', False),   # shown and recorded once
                                 ('victor', 'copy, stay there', True)])
        brains['victor'].overheard.assert_any_call('Pilot to Tango: tangos move to zone one')
        brains['tango'].overheard.assert_any_call('Victor: Victor holding.')
        brains['tango'].overheard.assert_any_call('Pilot to nobody named: say again')

    def test_voice_uses_the_agent_channel(self):
        from instructor.voice import agent_channel
        self.assertEqual(agent_channel(self.agents.get('instructor')),
                         {'frequencies_mhz': [251.0], 'modulations': ['AM'], 'name': 'DCS_AI_Service Instructor'})
        self.assertIsNone(agent_channel(None))


class RadarRealism(unittest.TestCase):
    def test_weak_return_near_the_limit_for_its_size(self):
        from instructor.tower_view import TowerView
        tower = TowerView(CockpitState())
        tower.tower = {'name': 'Senaki-Kolkhi', 'lat': 42.0, 'lon': 42.0}
        contact = {'lat': 42.5, 'lon': 42.0, 'alt_m': 3000, 'vx': 100, 'vz': 0, 'range_km': 88.0}
        self.assertNotIn('return', tower.track({**contact, 'dist_km': 55.0}))
        self.assertIn('weak', tower.track({**contact, 'dist_km': 80.0})['return'])
        self.assertIn('weak', tower.track({**contact, 'dist_km': 80.0}, brief=True)['return'])

    def test_calibration_logs_only_changes(self):
        from instructor import voice_loop
        entry = {'radar': 'Senaki-Kolkhi-1', 'unit': 'Bandit', 'type': 'MiG-29A', 'rcs_m2': 5, 'dist_km': 120.0,
                 'model_km': 100.0, 'model': False, 'ai': False}
        state, seen = CockpitState(), {}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(voice_loop, 'CALIBRATION_LOG', Path(tmp) / 'radar_calibration.jsonl'):
            for ai, model, dist in ((False, False, 120.0), (False, False, 110.0), (True, False, 104.0),
                                    (True, True, 99.0)):
                state.radars = {'Senaki-Kolkhi': {'calibration': [{**entry, 'ai': ai, 'model': model,
                                                                   'dist_km': dist}]}}
                voice_loop.log_radar_calibration(state, seen)
            lines = [json.loads(line) for line in voice_loop.CALIBRATION_LOG.read_text().splitlines()]
        self.assertEqual([(r['ai'], r['model'], r['dist_km']) for r in lines], [(True, False, 104.0), (True, True, 99.0)])


class Windows(unittest.TestCase):
    def test_closed_only_after_being_seen(self):
        watch = WindowWatch()
        self.assertFalse(watch.closed(0))   # not opened yet
        self.assertFalse(watch.closed(1))
        with mock.patch('instructor.voice_loop.time.monotonic', side_effect=[100.0, 100.5, 101.2]):
            self.assertFalse(watch.closed(0))   # gone since 100.0, checked at 100.5
            self.assertTrue(watch.closed(0))    # checked at 101.2: gone for WINDOW_GONE_S


class SrsCheckCache(unittest.TestCase):
    def test_checked_once_per_interval(self):
        check = SrsCheck()
        with mock.patch.object(SrsCheck, '_check', return_value={'installed': False}) as probe:
            check.get(SETTINGS)
            check.get(SETTINGS)
        self.assertEqual(probe.call_count, 1)


if __name__ == '__main__':
    unittest.main()
