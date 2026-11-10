"""Offline tests: run the F-5E cold-start lesson with a virtual pilot, and the pilot input classifier.

Run: python -m unittest discover tests
"""
import unittest
from pathlib import Path

from instructor.lesson import Lesson
from instructor.pilot_input import classify
from shared.cockpit import CockpitState
from shared.locale import load_locale

ROOT = Path(__file__).resolve().parent.parent
COLD_START = ROOT / 'modules' / 'F-5E-3' / 'training' / 'f_5e_training_cold_start_taxi_takeoff.json'


def virtual_pilot(lesson: Lesson, state: CockpitState) -> None:
    """Do what the lesson asks: set the cockpit arguments of the first pending rule whose other conditions hold."""
    for rule in lesson.rules:
        if rule['index'] in lesson.fired:
            continue
        checks = [c for c in rule['conditions'] if c['predicate'] == 'c_argument_in_range']
        others = [c for c in rule['conditions'] if c['predicate'] not in ('c_argument_in_range', 'or')]
        if checks and all(lesson._condition(c, state) is True for c in others):
            for c in checks:
                state.args[c['arg']] = (c['min'] + c['max']) / 2
            return


@unittest.skipUnless(COLD_START.exists(), 'run SETUP.cmd (tools/build_training.py) for the F-5E-3 lessons')
class ColdStartLesson(unittest.TestCase):
    def test_virtual_pilot_reaches_engine_start(self):
        lesson = Lesson(COLD_START, 'es')
        state = CockpitState(aircraft='F-5E-3')
        said, highlighted, unsupported = [], [], []
        for _ in range(20000):                      # 4000 s of model time at 5 Hz
            state.time += 0.2
            for event in lesson.tick(state):
                if event.kind == 'say':
                    said.append(event.rule)
                elif event.kind == 'highlight':
                    highlighted.extend(event.controls)
                elif event.kind == 'unsupported':
                    unsupported.append(event.rule)
            if lesson.waiting_flag is not None:
                lesson.pilot_continue(state.time)
            virtual_pilot(lesson, state)
            if unsupported:
                break
        print(f'\nfired {len(lesson.fired)}/{len(lesson.rules)} rules, said {len(said)} texts, '
              f'highlighted {len(highlighted)} controls; first pause: {unsupported[:1]!a}\nsaid: {said!a}')
        # !a: DCS's rule names hold a Cyrillic letter ('С'), which a console in the Windows code page cannot print.
        # A fast pilot sets the battery before the lesson's 8 s highlight timer, so check what was said instead.
        for step in ('BATT', 'GEN', 'PUMP', 'AIR'):
            self.assertTrue(any(name.startswith(step) for name in said), step)
        self.assertIn('Left Engine Start Button - Push to start', highlighted)
        self.assertGreater(len(lesson.fired), 40)

    def test_spanish_text(self):
        lesson = Lesson(COLD_START, 'es')
        state = CockpitState(aircraft='F-5E-3', time=10.0)
        lesson.tick(state)
        state.time = 20.0
        events = lesson.tick(state)
        english = Lesson(COLD_START, 'en')
        state_en = CockpitState(aircraft='F-5E-3', time=10.0)
        english.tick(state_en)
        state_en.time = 20.0
        said_en = [e.text for e in english.tick(state_en) if e.kind == 'say']
        said = [e.text for e in events if e.kind == 'say']
        self.assertTrue(said)
        self.assertNotEqual(said, said_en)   # the same lesson step, in the other locale's words


class PilotInput(unittest.TestCase):
    def test_commands_and_questions(self):
        en = load_locale('en')
        self.assertEqual(classify('Ready.', en), 'continue')
        self.assertEqual(classify('go ahead', en), 'continue')
        self.assertEqual(classify('say again', en), 'repeat')
        self.assertEqual(classify('What is the yaw damper for?', en), 'question')
        self.assertEqual(classify('ready but what does the boost pump do', en), 'question')

    def test_another_language_from_its_locale(self):
        # Every phrase of the Spanish locale file is a command in that language (no Spanish written in the tests).
        es = load_locale('es')
        for intent, phrases in es['intents'].items():
            for phrase in phrases:
                self.assertEqual(classify(phrase.capitalize() + '.', es), intent, phrase)


if __name__ == '__main__':
    unittest.main()
