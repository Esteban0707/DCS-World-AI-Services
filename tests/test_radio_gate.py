"""Offline tests for the instructor frequency gate: SRS radio data -> does this PTT press go to the instructor?

Run: python -m unittest discover tests
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client.dictation import FrequencyGate, RecordedDictation
from shared.agents import AgentStore
from shared.cockpit import CockpitState
from shared.radio import describe, on_instructor_frequency, transmit_radio

SRS = {'frequencies_mhz': [251.0], 'modulations': ['AM']}


def f5e_radio(freq_hz: float, modulation: int = 0, selected: int = 1) -> dict:
    """Shaped like SRS's F-5E export: radio 0 intercom, radio 1 the AN/ARC-164 UHF."""
    return {'selected': selected, 'ptt': False, 'radios': [
        {'name': '', 'freq': 100, 'modulation': 2, 'secFreq': 0},
        {'name': 'AN/ARC-164', 'freq': freq_hz, 'modulation': modulation, 'secFreq': 0},
        {'name': 'AN/ARC-186(V)', 'freq': 124.8e6, 'modulation': 0, 'secFreq': 121.5e6}]}


class Frequency(unittest.TestCase):
    def test_tuned(self):
        self.assertTrue(on_instructor_frequency(f5e_radio(251.0e6), SRS))
        self.assertTrue(on_instructor_frequency(f5e_radio(251.0e6 + 2000), SRS))   # rounding in the cockpit

    def test_other_frequency_modulation_or_radio(self):
        self.assertFalse(on_instructor_frequency(f5e_radio(251.025e6), SRS))
        self.assertFalse(on_instructor_frequency(f5e_radio(251.0e6, modulation=1), SRS))
        self.assertFalse(on_instructor_frequency(f5e_radio(251.0e6, selected=0), SRS))    # intercom
        self.assertFalse(on_instructor_frequency(f5e_radio(251.0e6, selected=2), SRS))    # the VHF on 124.8

    def test_unknown(self):
        self.assertIsNone(on_instructor_frequency(None, SRS))

    def test_reader_started_inside_the_aircraft(self):
        # DICTATION_TEST started mid-flight on 2026-11-01 never got the 'aircraft' message, so knobs were not logged.
        state = CockpitState()
        kind = state.apply({'type': 'update', 'time': 5, 'args': {'307': 0.1},
                            'flight': {'aircraft': 'F-5E-3', 'tas_ms': 0}, 'radio': f5e_radio(251.0e6)})
        self.assertEqual(kind, 'aircraft')
        self.assertEqual((state.aircraft, state.args, state.radio['selected']), ('F-5E-3', {307: 0.1}, 1))
        kind = state.apply({'type': 'update', 'time': 6, 'args': {}, 'flight': {'aircraft': 'F-5E-3'}})
        self.assertEqual(kind, 'update')

    def test_describe(self):
        self.assertEqual(describe(f5e_radio(251.0e6)), 'AN/ARC-164 251.000 AM')

    def test_cockpit_state_keeps_radio_until_aircraft_changes(self):
        state = CockpitState()
        state.apply({'type': 'aircraft', 'aircraft': 'F-5E-3', 'time': 1})
        state.apply({'type': 'update', 'time': 2, 'args': {}, 'radio': f5e_radio(251.0e6)})
        state.apply({'type': 'update', 'time': 3, 'args': {}})
        self.assertEqual(state.radio['selected'], 1)
        state.apply({'type': 'left_aircraft', 'time': 4})
        self.assertIsNone(state.radio)
        self.assertIsNone(state.aircraft)


def two_agents(folder: Path) -> AgentStore:
    """The instructor on 251.000 AM (made from the old settings) and a second agent on 260.000 AM."""
    store = AgentStore(folder, settings={'srs': {'frequencies_mhz': [251.0], 'modulations': ['AM']}})
    store.create('tower', {'name': 'Tower', 'role': 'atc', 'active': True, 'frequency_mhz': 260.0,
                           'modulation': 'AM', 'voice': {'name': 'F2'}, 'speech': {'language': 'en-US'}})
    store.reload()
    return store


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agents = two_agents(Path(self.tmp.name) / 'agents')

    def tearDown(self):
        self.tmp.cleanup()

    def test_press_off_frequency_is_not_recorded(self):
        radio = {'value': f5e_radio(305.0e6)}
        gate = FrequencyGate(self.agents, lambda: radio['value'])
        self.assertEqual(gate.check(), (None, 'AN/ARC-164 305.000 AM'))
        radio['value'] = f5e_radio(251.0e6)
        agent, note = gate.check()
        self.assertEqual((agent['id'], note), ('instructor', 'AN/ARC-164 251.000 AM'))
        radio['value'] = None
        self.assertEqual(gate.check()[0]['id'], 'instructor')   # no SRS data: the default agent listens anyway

    def test_the_frequency_picks_the_agent(self):
        radio = {'value': f5e_radio(260.0e6 + 2000)}
        gate = FrequencyGate(self.agents, lambda: radio['value'])
        self.assertEqual(gate.check()[0]['id'], 'tower')
        radio['value'] = f5e_radio(260.0e6, modulation=1)
        self.assertIsNone(gate.check()[0])              # FM: nobody there
        radio['value'] = f5e_radio(260.0e6, selected=0)
        self.assertIsNone(gate.check()[0])              # the intercom never reaches an agent
        self.agents.update('tower', {'active': False})
        radio['value'] = f5e_radio(260.0e6)
        self.assertIsNone(gate.check()[0])              # switched off

    def test_each_button_its_radio(self):
        """Two radios, two agents (user's design, 2026-11-03): the second PTT button always talks on radio 2."""
        two = {'radios': [{'name': 'intercom', 'freq': 100, 'modulation': 2},
                          {'name': 'UHF', 'freq': 251.0e6, 'modulation': 0},
                          {'name': 'VHF', 'freq': 260.0e6, 'modulation': 0}], 'source': 'devices'}
        gate = FrequencyGate(self.agents, lambda: two)
        self.assertEqual(gate.check(1)[0]['id'], 'instructor')
        self.assertEqual(gate.check(2)[0]['id'], 'tower')
        self.assertEqual(gate.ptt_radio, 2)                                 # the last button pressed
        one_radio = FrequencyGate(self.agents, lambda: f5e_radio(251.0e6))
        self.assertIsNone(one_radio.check(2)[0])                            # an aircraft with one radio: nobody

    def test_gate_off_sends_other_frequencies_to_the_default_agent(self):
        gate = FrequencyGate(self.agents, lambda: f5e_radio(305.0e6), only_on_frequency=False)
        self.assertEqual(gate.check()[0]['id'], 'instructor')
        gate = FrequencyGate(self.agents, lambda: f5e_radio(260.0e6), only_on_frequency=False)
        self.assertEqual(gate.check()[0]['id'], 'tower')

    @mock.patch('client.dictation.LOG', Path(tempfile.mkdtemp()) / 'dictation.jsonl')
    def test_recorded_dictation_respects_gate(self):
        class Button:
            down = False

            def held(self):
                return self.down

        class Recorder:
            rate = 1000
            started = 0

            def start(self):
                Recorder.started += 1

            def stop(self):
                return []

        statuses = []
        gate = FrequencyGate(self.agents, lambda: f5e_radio(305.0e6))
        dictation = RecordedDictation(Button(), Recorder(), type('R', (), {'name': 'fake'})(), print,
                                      lambda kind, _: statuses.append(kind), gate)
        dictation.start()
        import time
        dictation.button.down = True
        time.sleep(0.1)
        dictation.button.down = False
        time.sleep(0.1)
        self.assertEqual(Recorder.started, 0)
        self.assertEqual(statuses, ['not_for_instructor'])


from dcs_data import LUAE  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(LUAE.exists(), "needs DCS's own Lua interpreter (bin/luae.exe)")
class ExportRadio(unittest.TestCase):
    def run_export(self, srs: str, power: str, *extra: str):
        result = subprocess.run([str(LUAE), str(ROOT / 'tests' / 'lua' / 'export_harness.lua'), ROOT.as_posix() + '/',
                                 srs, power, *extra], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = [json.loads(line[5:]) for line in result.stdout.splitlines() if line.startswith('SENT ')]
        return result.stdout, [m for m in sent if m.get('type') in ('update', 'snapshot')]

    def test_without_srs_the_cockpit_device_is_read(self):
        # As seen live 2026-11-01: SRS's table was not reachable from our export.
        stdout, updates = self.run_export('no_srs', 'on')
        self.assertIn('LOG F-5E-3: radios from cockpit devices', stdout)
        radio = updates[-1]['radio']
        self.assertEqual(radio['source'], 'cockpit')
        self.assertTrue(on_instructor_frequency(radio, SRS))
        self.assertEqual(describe(radio), 'AN/ARC-164 251.000 AM')

    def test_radio_off_is_not_the_instructor_frequency(self):
        for srs in ('no_srs', 'srs'):
            _, updates = self.run_export(srs, 'off')
            self.assertIs(on_instructor_frequency(updates[-1]['radio'], SRS), False, srs)

    def test_mi8_radios_and_its_selector(self):
        # Live 2026-11-02: a Mi-8MT had no radio list, every press went to the instructor on 251.0 and the pilot,
        # tuned elsewhere, never heard the answers. Now read as SRS's own Mi8.lua reads it.
        stdout, updates = self.run_export('no_srs', 'on', 'mi8', '0.0')
        self.assertIn('LOG Mi-8MT: radios from cockpit devices', stdout)
        radio = updates[-1]['radio']
        self.assertEqual([r['name'] for r in radio['radios']], ['intercom', 'R-863', 'JADRO-1A', 'R-828'])
        self.assertEqual(describe(radio), 'R-863 255.000 AM')
        self.assertEqual(describe(self.run_export('no_srs', 'on', 'mi8', '0.2')[1][-1]['radio']), 'R-828 40.000 FM')
        self.assertEqual(self.run_export('no_srs', 'on', 'mi8', '0.5')[1][-1]['radio']['selected'], -1)
        self.assertEqual(self.run_export('no_srs', 'on', 'mi8', '0.0', 'ics')[1][-1]['radio']['selected'], 0)

    def test_any_aircraft_radios_found_by_scanning(self):
        """Live 2026-11-02: an F-16 on Syria had no radio data; the ATC on Akrotiri's frequency was never chosen."""
        stdout, updates = self.run_export('no_srs', 'on', 'f16')
        self.assertIn('LOG F-16C_50: radio devices found 36, 38', stdout)     # 40, the ILS receiver, left out
        self.assertIn('LOG F-16C_50: radios from scanned devices', stdout)
        radio = updates[-1]['radio']
        self.assertEqual(radio['source'], 'devices')
        self.assertNotIn('selected', radio)                                  # not known
        self.assertEqual([(r['name'], r['freq']) for r in radio['radios']],
                         [('intercom', 100), ('radio 36', 305e6), ('radio 38', 127e6)])
        # One push-to-talk button per radio (user's design): the main one radio 1, "ptt.radio2" radio 2.
        self.assertEqual(describe(transmit_radio(radio, 1)), 'radio 36 305.000 AM')
        self.assertEqual(describe(transmit_radio(radio, 2)), 'radio 38 127.000 AM')
        self.assertEqual(transmit_radio({'radios': [{'freq': 100}]}, 1)['selected'], -1)
        mi8 = self.run_export('no_srs', 'on', 'mi8', '0.2')[1][-1]['radio']   # the aircraft's own selection kept
        self.assertEqual(describe(transmit_radio(mi8, 1)), 'R-828 40.000 FM')
        self.assertEqual(describe(transmit_radio(mi8, 2)), 'JADRO-1A 5.000 AM')

    def test_srs_data_copied_and_srs_result_untouched(self):
        result, updates = self.run_export('srs', 'on')
        result = type('R', (), {'stdout': result})
        self.assertEqual(updates[0]['radio']['source'], 'cockpit')   # before SRS's first frame
        radio = updates[-1]['radio']
        self.assertNotIn('source', radio)                             # SRS's own data
        self.assertEqual(radio['selected'], 1)
        self.assertEqual((radio['radios'][1]['name'], radio['radios'][1]['freq']), ('AN/ARC-164', 251000000))
        self.assertTrue(on_instructor_frequency(radio, SRS))
        self.assertIn('SRS_RESULT AN/ARC-164 251000000 selected 1', result.stdout)
        self.assertIn('LOG SRS radio data found', result.stdout)          # SR is a local, as in SRS 2.4.1.0
        self.assertIn('LOG watching SRS radios of F-5E-3', result.stdout)


if __name__ == '__main__':
    unittest.main()
