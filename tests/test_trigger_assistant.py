"""Offline tests for the Triggers assistant (gui/trigger_assistant.py): its tools only change the draft rows, it never
saves, and it sees what it needs (the agent, its role's tools, the rows, the flags other agents use).

Run: python -m unittest discover tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from gui.trigger_assistant import DraftToolBox, TriggerAssistant, clean_draft
from instructor.brain import Brain, BrainError
from shared.agents import AgentStore

SETTINGS = {'language': 'en', 'srs': {'frequencies_mhz': [251.0], 'modulations': ['AM']}, 'voice': {}, 'speech': {}}


class Tools(unittest.TestCase):
    def setUp(self):
        self.box = DraftToolBox('squad')

    def call(self, tool, **args):
        return json.loads(self.box.call(f'triggers.{tool}', json.dumps(args)))

    def test_add_and_remove_rows(self):
        self.assertTrue(self.call('add_event', flag='10', instructions='Call if you see the helicopter.')['ok'])
        result = self.call('add_action', name='smoke_ready', flag='20', description='When the pilot sees the smoke.')
        self.assertEqual(result['actions'], [{'name': 'smoke_ready', 'flag': '20',
                                              'description': 'When the pilot sees the smoke.', 'internal': False}])
        self.assertTrue(self.call('remove_event', index=1)['ok'])
        self.assertEqual(self.box.draft['events'], [])
        self.assertFalse(self.call('remove_action', index=5)['ok'])

    def test_refusals_explained_to_the_model(self):
        self.assertIn('role', self.call('add_action', name='pop_smoke', flag='20', description='x')['error'])
        self.call('add_action', name='go', flag='21', description='x')
        self.assertIn('already', self.call('add_action', name='go', flag='22', description='y')['error'])
        self.assertFalse(self.call('add_event', flag='10; x', instructions='x')['ok'])
        self.assertFalse(self.call('add_action', name='Smoke Ready', flag='20', description='x')['ok'])
        self.assertIn('unknown tool', self.call('set_flag', flag='1')['error'])

    def test_condition_event(self):
        result = self.call('add_condition_event', when='squad_lost', about='tango', instructions='Say Tango is gone.')
        self.assertEqual(result['events'], [{'when': 'squad_lost', 'about': 'tango', 'zone': '',
                                             'instructions': 'Say Tango is gone.'}])   # as the page sends rows
        self.assertFalse(self.call('add_condition_event', when='zone_reached', instructions='x')['ok'])   # no zone
        self.assertEqual(clean_draft({'events': result['events']})['events'], result['events'])

    def test_page_rows_cleaned(self):
        draft = clean_draft({'events': [{'flag': 10, 'instructions': 'x', 'extra': 'dropped'}, 'junk'],
                             'actions': None})
        self.assertEqual(draft, {'events': [{'flag': '10', 'instructions': 'x', 'internal': False}], 'actions': []})


class ScriptedBrain(Brain):
    """Plays the model: calls the given tools on the toolbox, then answers."""
    name = 'scripted'

    def __init__(self, toolbox, script):
        self.toolbox, self.script, self.asked, self.agent_name = toolbox, script, [], ''

    def ask(self, question, context, language):
        self.asked.append((question, context))
        for name, args in self.script:
            self.toolbox.call(name, json.dumps(args))
        return 'Done: an event on flag 10. In the editor: UNIT INSIDE ZONE -> FLAG ON 10. Check it and press Save.'


class Assistant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = AgentStore(Path(self.tmp.name) / 'agents', settings=SETTINGS)
        self.squad = self.agents.add('squad', 'Buenos', 255.0, 'AM', SETTINGS, 'Buenos')
        mechanic = self.agents.add('instructor', 'Mechanic', 252.0, 'AM', SETTINGS)
        self.agents.update(mechanic['id'], {'events': [{'flag': '5', 'instructions': 'x'}]})
        self.brains = []

        def factory(settings, toolbox, radio, instructions):
            self.assertIn('Triggers assistant', instructions)
            brain = ScriptedBrain(toolbox, [('triggers.add_event', {'flag': '10', 'instructions': 'Call me.'})])
            self.brains.append(brain)
            return brain

        self.assistant = TriggerAssistant(lambda: SETTINGS, self.agents, factory)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fills_the_draft_and_never_saves(self):
        draft = {'events': [], 'actions': [{'name': 'smoke_ready', 'flag': '20', 'description': 'x'}]}
        result = self.assistant.ask(self.squad['id'], 'when they enter the LZ I set flag 10, have them call me', draft)
        self.assertEqual(result['draft']['events'], [{'flag': '10', 'instructions': 'Call me.', 'internal': False}])
        self.assertEqual(len(result['draft']['actions']), 1)                     # the maker's unsaved row kept
        self.assertIn('Save', result['reply'])
        self.assertNotIn('events', self.agents.get(self.squad['id']))            # nothing saved
        question, context = self.brains[0].asked[0]
        self.assertEqual(context['agent'], {'name': 'Buenos', 'role': 'squad'})
        self.assertIn('pop_smoke', [t['name'] for t in context['role_tools']])
        self.assertEqual(context['actions'][0]['row'], 1)
        mechanic = next(o for o in context['other_agents'] if o['agent'] == 'Mechanic')
        self.assertEqual(mechanic, {'agent': 'Mechanic', 'actions': [],
                                    'events': [{'flag': '5', 'internal': False, 'instructions': 'x'}]})
        self.assertNotIn('ATC', [o['agent'] for o in context['other_agents']])   # fixed: no triggers
        self.assertEqual(self.brains[0].agent_name, 'Buenos')

    def test_one_conversation_per_agent_until_reset(self):
        self.assistant.ask(self.squad['id'], 'hello', {})
        self.assistant.ask(self.squad['id'], 'otra cosa', {})
        self.assertEqual(len(self.brains), 1)
        self.assistant.reset(self.squad['id'])
        self.assistant.ask(self.squad['id'], 'de nuevo', {})
        self.assertEqual(len(self.brains), 2)

    def test_refused(self):
        with self.assertRaisesRegex(ValueError, 'fixed_agent'):
            self.assistant.ask('atc', 'hello', {})
        with self.assertRaisesRegex(ValueError, 'message'):
            self.assistant.ask(self.squad['id'], '   ', {})

    def test_brain_error_comes_back_as_a_kind(self):
        class Limited(Brain):
            def ask(self, question, context, language):
                raise BrainError('usage limit', 'limit')

        assistant = TriggerAssistant(lambda: SETTINGS, self.agents, lambda *args: Limited())
        result = assistant.ask(self.squad['id'], 'hello', {'events': [{'flag': '1', 'instructions': 'x'}]})
        self.assertEqual((result['error'], len(result['draft']['events'])), ('limit', 1))


if __name__ == '__main__':
    unittest.main()
