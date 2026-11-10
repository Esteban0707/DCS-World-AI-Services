"""Offline tests for changing an agent's frequency by radio (shared/radio_change.py; user, 2026-11-05: "Tango, change
to 353"): the answer goes out on the old frequency, then the agent is on the new one, saved like a GUI change.

Run: python -m unittest discover tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from instructor import voice_loop
from shared.agents import AgentStore, channel_label
from shared.radio_change import RadioToolBox
from squad.desk import SquadDesk
from squad.tools import SquadToolBox
from shared.cockpit import CockpitState

SETTINGS = {'language': 'en', 'srs': {'frequencies_mhz': [251.0], 'modulations': ['AM']}, 'voice': {}, 'speech': {}}


class FakeBrain:
    radio = '355.000 AM'


class ChangeFrequency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = AgentStore(Path(self.tmp.name) / 'agents', settings=SETTINGS)
        self.tango = self.agents.add('squad', 'Tango', 355.0, 'AM', SETTINGS, 'Tango')
        self.box = RadioToolBox(SquadToolBox(SquadDesk(CockpitState(), self.tango)), self.agents, self.tango['id'])

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, **arguments):
        return json.loads(self.box.call('radio.change_frequency', json.dumps(arguments)))

    def test_answer_on_the_old_frequency_then_move(self):
        result = self.call(mhz=353, modulation='AM')
        self.assertTrue(result['ok'])
        self.assertIn('right after this answer', result['detail'])
        self.assertEqual(self.agents.get(self.tango['id'])['frequency_mhz'], 355.0)   # still there while it answers
        brain = FakeBrain()
        voice_loop.move_frequency(self.box, brain, self.tango['id'])                # after its transmission
        moved = self.agents.get(self.tango['id'])
        self.assertEqual((moved['frequency_mhz'], moved['modulation']), (353.0, 'AM'))
        self.assertEqual(brain.radio, channel_label(moved))
        self.assertEqual(self.agents.on_frequency(353.0e6, 0)['id'], self.tango['id'])   # the pilot reaches it there
        voice_loop.move_frequency(self.box, brain, self.tango['id'])                # nothing pending: no change
        self.assertEqual(self.agents.get(self.tango['id'])['frequency_mhz'], 353.0)

    def test_refused_outside_the_band_and_same_frequency(self):
        self.assertFalse(self.call(mhz=1200, modulation='AM')['ok'])
        self.assertFalse(self.call(mhz=40, modulation='SSB')['ok'])
        self.assertIn('already on', self.call(mhz=355, modulation='AM')['detail'])
        self.assertIsNone(self.box.pending)

    def test_the_role_tools_are_still_there(self):
        names = {t['name'] for space in self.box.tools() for t in space['tools']}
        self.assertTrue({'move_to', 'wait_for_pickup', 'change_frequency'} <= names)
        self.assertIn('change_frequency', self.box.remembered)
        self.assertIn('- change_frequency:', self.box.guide())


if __name__ == '__main__':
    unittest.main()
