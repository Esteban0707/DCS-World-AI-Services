"""Model choice: a default model for every agent ("brain.model", Connections) and an agent's own ("model" in
agent.json, its Brain tab). Chosen in the GUI; running brains take a new choice at once."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import quiet_log  # noqa: F401  (the program's log goes to a temporary file)
from gui import status as gui_status
from gui.server import Gui, agent_changes, checked_model
from gui.status import Live
from instructor.brain import DEFAULT_MODEL, agent_model, create_brain, default_model
from shared.agents import AgentStore

SETTINGS = {'language': 'en', 'srs': {'frequencies_mhz': [251.0], 'modulations': ['AM']},
            'brain': {'provider': 'chatgpt', 'model': 'test-terra'}}
CATALOG = [{'slug': 'test-terra', 'display_name': 'Terra'}, {'slug': 'test-sol', 'display_name': 'Sol'}]


class Choice(unittest.TestCase):
    def test_agent_model_falls_back_to_the_default(self):
        self.assertEqual(agent_model(SETTINGS, {'model': 'test-sol'}), 'test-sol')
        self.assertEqual(agent_model(SETTINGS, {'model': ''}), 'test-terra')
        self.assertEqual(agent_model(SETTINGS, None), 'test-terra')
        self.assertEqual(default_model({}), DEFAULT_MODEL)

    def test_create_brain_takes_the_agents_model(self):
        with mock.patch('instructor.brain.ChatGptBrain') as brain:
            create_brain(SETTINGS, model='test-sol')
            create_brain(SETTINGS)
        self.assertEqual(brain.call_args_list[0].args[0]['model'], 'test-sol')
        self.assertEqual(brain.call_args_list[1].args[0]['model'], 'test-terra')
        self.assertEqual(SETTINGS['brain']['model'], 'test-terra')   # the settings are not changed

    def test_model_validation(self):
        self.assertEqual(agent_changes({'model': ' test-sol '}), {'model': 'test-sol'})
        self.assertEqual(agent_changes({'model': ''}), {'model': ''})   # back to the default
        for bad in ('has space', '../x', 'x' * 65, '-dash'):
            with self.assertRaises(ValueError):
                agent_changes({'model': bad})
        with self.assertRaises(ValueError):
            checked_model('')   # the default model itself must be a model


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'settings.json'
        self.path.write_text(json.dumps(SETTINGS), encoding='utf-8')
        self.agents = AgentStore(Path(self.tmp.name) / 'agents', settings=SETTINGS)
        self.live = Live()
        self.live.agents = self.agents
        self.asked = 0
        self.gui = Gui(self.live, port=0, settings_path=self.path, catalog=self.catalog)
        self.gui.start()

    def tearDown(self):
        self.gui.close()
        self.tmp.cleanup()

    def catalog(self, settings):
        self.asked += 1
        return CATALOG

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode('utf-8')
        request = urllib.request.Request(self.gui.url.rstrip('/') + path, data=data,
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_catalog_is_kept_until_refreshed(self):
        data = self.call('/api/models')
        self.assertEqual((data['provider'], data['default'], data['error']), ('chatgpt', 'test-terra', ''))
        self.assertEqual([m['slug'] for m in data['models']], ['test-terra', 'test-sol'])
        self.call('/api/models')
        self.assertEqual(self.asked, 1)
        self.call('/api/models/refresh')
        self.assertEqual(self.asked, 2)

    def test_agent_and_default_model(self):
        agent = self.call('/api/agents/instructor', {'model': 'test-sol'})
        self.assertEqual(agent['model'], 'test-sol')
        saved = self.call('/api/brain', {'model': 'test-sol'})
        self.assertEqual(saved['brain']['model'], 'test-sol')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['brain']['provider'], 'chatgpt')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('/api/brain', {'model': ''})
        self.assertEqual(error.exception.code, 400)

    def test_chosen_models_show_without_a_catalog(self):
        def offline(settings):
            raise OSError('no network')
        self.gui.catalog = offline
        self.agents.update('instructor', {'model': 'test-luna'})
        data = self.call('/api/models')
        self.assertEqual(data['error'], 'no network')
        self.assertEqual([m['slug'] for m in data['models']], ['test-terra', 'test-luna'])
        self.assertFalse(any(m['missing'] for m in data['models']))   # unknown, not missing: nothing to compare
        self.gui.catalog = self.catalog
        data = self.call('/api/models/refresh')
        self.assertEqual(data['models'][-1], {'slug': 'test-luna', 'display_name': 'test-luna', 'missing': True})

    def test_services_name_each_agents_model(self):
        self.agents.update('instructor', {'model': 'test-sol'})
        self.agents.add('instructor', 'Mechanic', 262.0, 'AM', SETTINGS)
        brains = {s['id']: s['brain'] for s in gui_status.services(SETTINGS, self.agents) if not s.get('soon')}
        self.assertEqual((brains['instructor'], brains['mechanic']), ('ChatGPT test-sol', 'ChatGPT test-terra'))


class Running(unittest.TestCase):
    def test_refresh_gives_each_brain_its_model(self):
        from instructor.voice_loop import Router
        with tempfile.TemporaryDirectory() as tmp:
            agents = AgentStore(Path(tmp) / 'agents', settings=SETTINGS)
            router = Router(agents, lambda agent_id: mock.Mock(brain=mock.Mock(model='old')))
            router.heard('radio check', agents.get('instructor'))
            router.refresh()   # without settings (tests, old callers): the model is left alone
            self.assertEqual(router.responders['instructor'].brain.model, 'old')
            current = {'brain': {'provider': 'chatgpt', 'model': 'test-terra'}}
            router.settings = lambda: current
            router.refresh()
            self.assertEqual(router.responders['instructor'].brain.model, 'test-terra')
            agents.update('instructor', {'model': 'test-sol'})
            router.refresh()
            self.assertEqual(router.responders['instructor'].brain.model, 'test-sol')


if __name__ == '__main__':
    unittest.main()
