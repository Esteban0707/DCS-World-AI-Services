"""Offline tests for the switch position table, the cockpit summary and the black box.

Run: python -m unittest discover tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from instructor.blackbox import BlackBox
from shared.cockpit import CockpitState
from shared.cockpit_summary import CockpitSummary
from tools.build_positions import build, entries_for, fit_labels, split_hint
from tools.positions_check import save_answer

from dcs_data import F5E_CONTROLS, F5E_POSITIONS, needs

ROOT = Path(__file__).resolve().parent.parent


def control(cid, hint, arg, lims, values, classes):
    return {'id': cid, 'hint': hint, 'arg': [arg] * len(lims), 'arg_lim': lims, 'arg_value': values,
            'class': classes}


GENERATOR = control('GEN', 'Left Generator Switch, L GEN/OFF/RESET', 388, [[-1, 0], [0, 1]], [-1, 1],
                    ['class_type.TUMB'] * 2)
RADAR = control('RDR', 'Radar Mode Selector Switch, OFF/STBY/OPER/TEST', 316, [[0, 0.3], [0, 0.3]], [-0.1, 0.1],
                ['class_type.TUMB'] * 2)
BREAKER = control('CB', 'CB PITOT HEATER, ON/OFF', 231, [[0, 1], [0, 1]], [1, 0], ['class_type.TUMB'] * 2)
START = control('START', 'Left Engine Start Button - Push to start engine', 357, [[0, 1]], [1], ['class_type.BTN'])
SIGHT = control('SGT', 'Sight Mode Selector, OFF/MSL/A/A1 GUNS/A/A2 GUNS/MAN', 40, [[0, 0.4], [0, 0.4]],
                [-0.1, 0.1], ['class_type.TUMB'] * 2)


def labels(entry):
    return [(p['value'], p['label']) for p in entry['positions']]


class PositionTable(unittest.TestCase):
    def test_hint_split(self):
        self.assertEqual(split_hint('Left Generator Switch, L GEN/OFF/RESET'),
                         ('Left Generator Switch', ['L GEN', 'OFF', 'RESET']))
        self.assertEqual(split_hint('Master Caution Button - Push to reset'), ('Master Caution Button', []))
        self.assertEqual(fit_labels(['OFF', 'MSL', 'A', 'A1 GUNS', 'A', 'A2 GUNS', 'MAN'], 5),
                         ['OFF', 'MSL', 'A/A1 GUNS', 'A/A2 GUNS', 'MAN'])

    def test_rules(self):
        self.assertEqual(labels(entries_for(GENERATOR)[0]), [(1.0, 'L GEN'), (0.0, 'OFF'), (-1.0, 'RESET')])
        self.assertEqual(labels(entries_for(RADAR)[0]), [(0.0, 'OFF'), (0.1, 'STBY'), (0.2, 'OPER'), (0.3, 'TEST')])
        self.assertEqual(labels(entries_for(BREAKER)[0]), [(0.0, 'ON'), (1.0, 'OFF')])
        self.assertEqual(entries_for(START)[0]['kind'], 'button')
        self.assertEqual(labels(entries_for(SIGHT)[0])[2], (0.2, 'A/A1 GUNS'))

    def test_overrides_and_live_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp)
            (module / 'controls.json').write_text(json.dumps({'controls': [GENERATOR, BREAKER]}), encoding='utf-8')
            (module / 'positions_overrides.json').write_text(json.dumps(
                {'args': {'388': {'source': 'lesson check'}}}), encoding='utf-8')
            table = build(module)['args']
            self.assertTrue(table['388']['verified'])
            self.assertFalse(table['231']['verified'])
            save_answer(module, 231, table['231'], correct=False)
            fixed = json.loads((module / 'positions.json').read_text(encoding='utf-8'))['args']['231']
            self.assertEqual(labels(fixed), [(0.0, 'OFF'), (1.0, 'ON')])
            self.assertTrue(fixed['verified'])

    @needs(F5E_CONTROLS)
    def test_f5e_table_is_built_from_the_module(self):
        table = build(ROOT / 'modules' / 'F-5E-3')['args']
        self.assertEqual(labels(table['387']), [(1.0, 'BATT'), (0.0, 'OFF')])
        self.assertTrue(table['387']['verified'])
        self.assertTrue(table['83']['outside_visible'])     # landing gear lever


class Summary(unittest.TestCase):
    def summary(self):
        positions = {e['arg']: e for c in (GENERATOR, RADAR, START) for e in entries_for(c)}
        return CockpitSummary(positions)

    def test_settled_changes_only(self):
        summary = self.summary()
        self.assertEqual(summary.update({'388': 0, '316': 0, '357': 0})[0].old, None)   # starting state
        self.assertEqual(summary.update({'388': 0.32}), [])                            # mid-animation
        change = summary.update({'388': 1.0})[0]
        self.assertEqual(change.text(), 'Left Generator Switch: OFF -> L GEN')
        self.assertEqual(summary.update({'388': 0.999}), [])                           # same position
        self.assertEqual(summary.update({'316': 0.1})[0].new, 'STBY')

    def test_buttons_report_presses_only(self):
        summary = self.summary()
        summary.update({'357': 0})
        self.assertEqual([c.new for c in summary.update({'357': 1})], ['PRESSED'])
        self.assertEqual(summary.update({'357': 0}), [])
        self.assertNotIn('Left Engine Start Button', summary.state())


class Recorder(unittest.TestCase):
    @needs(F5E_CONTROLS, F5E_POSITIONS)
    def test_session_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            box, state = BlackBox(Path(tmp)), CockpitState()
            flight = {'aircraft': 'F-5E-3', 'ias_ms': 0.0, 'alt_msl_m': 10.0, 'lat': 41.6, 'lon': 41.6}
            messages = [
                {'type': 'aircraft', 'aircraft': 'F-5E-3', 'time': 0.2},
                {'type': 'snapshot', 'time': 0.4, 'flight': flight, 'args': {'387': 0, '388': 0}},
                {'type': 'update', 'time': 3.0, 'flight': flight, 'args': {'387': 0.32}},
                {'type': 'update', 'time': 3.2, 'flight': flight, 'args': {'387': 1}},
            ]
            for message in messages:
                box.on_cockpit(state.apply(message), message, state)
            box.said('pilot', 'battery on', state.time)
            box.on_cockpit(state.apply({'type': 'left_aircraft', 'time': 9.0}), {'type': 'left_aircraft'}, state)
            records = [json.loads(line) for line in box.path.read_text(encoding='utf-8').splitlines()]
            kinds = [r['kind'] for r in records]
            self.assertEqual(kinds[0], 'start')
            self.assertEqual(kinds[-1], 'end')
            start = next(r for r in records if r['kind'] == 'start_switches')
            self.assertEqual(start['switches']['Battery Switch'], 'OFF')
            switches = [r for r in records if r['kind'] == 'switch']
            self.assertEqual([(s['name'], s['old'], s['new'], s['verified']) for s in switches],
                             [('Battery Switch', 'OFF', 'BATT', True)])
            self.assertIn({'kind': 'pilot', 'text': 'battery on'},
                          [{k: r[k] for k in ('kind', 'text')} for r in records if r['kind'] == 'pilot'])
            sample = next(r for r in records if r['kind'] == 'flight')
            self.assertEqual((sample['altitude_msl_ft'], sample['lat']), (33, 41.6))

    def test_agent_groups_and_who_is_aboard(self):
        # Live 2026-11-05: nothing in the logs could tell whether Tango got out of Bison.
        with tempfile.TemporaryDirectory() as tmp:
            box, state = BlackBox(Path(tmp)), CockpitState()
            message = {'type': 'aircraft', 'aircraft': 'F-5E-3', 'time': 0.0}
            box.on_cockpit(state.apply(message), message, state)
            state.transport['Tango'] = {'carrier': 'Bison', 'ride': 'vehicle', 'stage': 'aboard'}
            groups = {'type': 'groups', 'groups': {
                'Bison': {'exists': True, 'alive': 1, 'size': 1, 'lat': 41.6123456, 'lon': 41.6, 'speed_ms': 3.14,
                          'ground': {'types': {'BTR-80': 1}, 'descent_on_board': 4}},
                'Tango': {'exists': True, 'alive': 4, 'size': 4, 'lat': 41.6, 'lon': 41.6}}}
            for _ in range(2):   # the second one within GROUPS_S is not written
                box.on_cockpit(state.apply(groups), groups, state)
            box.stop(state.time)
            records = [json.loads(line) for line in box.path.read_text(encoding='utf-8').splitlines()]
            written = [r for r in records if r['kind'] == 'groups']
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0]['groups']['Bison'], {'exists': True, 'alive': 1, 'size': 1, 'lat': 41.61235,
                                                             'lon': 41.6, 'speed_ms': 3.1, 'descent_on_board': 4})
            self.assertEqual(written[0]['transport']['Tango']['stage'], 'aboard')

    @needs(F5E_CONTROLS, F5E_POSITIONS)
    def test_raw_controls_recorder(self):
        """Everything touched in the cockpit, raw: settled values only, labels where known, wheels without labels."""
        with tempfile.TemporaryDirectory() as tmp:
            box, state = BlackBox(Path(tmp)), CockpitState()
            flight = {'aircraft': 'F-5E-3', 'lat': 41.6, 'lon': 41.6}
            messages = [
                {'type': 'aircraft', 'aircraft': 'F-5E-3', 'time': 0.0},
                {'type': 'snapshot', 'time': 0.2, 'flight': flight, 'args': {'387': 0, '328': 0.5}},
                {'type': 'update', 'time': 3.0, 'flight': flight, 'args': {'387': 0.32}},   # mid-animation
                {'type': 'update', 'time': 3.1, 'flight': flight, 'args': {'387': 1}},
                {'type': 'update', 'time': 3.5, 'flight': flight, 'args': {'328': 0.6}},    # UHF 10 MHz wheel
                {'type': 'update', 'time': 4.0, 'flight': flight, 'args': {}},
                {'type': 'snapshot', 'time': 5.0, 'flight': flight, 'args': {'206': 0}},   # first seen: not a touch
                {'type': 'update', 'time': 6.0, 'flight': flight, 'args': {}},
            ]
            for message in messages:
                box.on_cockpit(state.apply(message), message, state)
            box.stop(state.time)
            records = [json.loads(line) for line in box.path.read_text(encoding='utf-8').splitlines()]
            start = next(r for r in records if r['kind'] == 'start_controls')['controls']
            self.assertEqual(start['387'], {'control': 'Battery Switch', 'value': 0, 'label': 'OFF', 'verified': True})
            controls = [(r['arg'], r['old'], r['new'], r.get('label')) for r in records if r['kind'] == 'control']
            self.assertEqual(controls, [(387, 0, 1, 'BATT'), (328, 0.5, 0.6, None)])


if __name__ == '__main__':
    unittest.main()
