"""Offline tests for shared frequencies (shared/dispatch.py): who a phrase is for when several agents share a channel.

Run: python -m unittest discover tests
"""
import time
import unittest

from shared import dispatch
from shared.dispatch import Dispatcher, addressed, all_stations_words

TANGO = {'id': 'tango', 'name': 'Tango'}
VICTOR = {'id': 'victor', 'name': 'Victor', 'aliases': ['Green Team']}
EVERYONE = [['all', 'stations'], ['everyone']]


class Addressed(unittest.TestCase):
    def test_by_name_alias_and_order(self):
        self.assertEqual(addressed('Tango, move to zone one', [TANGO, VICTOR], EVERYONE), ([TANGO], 'name'))
        self.assertEqual(addressed('green team hold', [TANGO, VICTOR], EVERYONE), ([VICTOR], 'name'))
        self.assertEqual(addressed('Victor and Tango, hold', [TANGO, VICTOR], EVERYONE), ([VICTOR, TANGO], 'name'))

    def test_dictation_errors_and_accents(self):
        self.assertEqual(addressed('tangos, move', [TANGO, VICTOR], EVERYONE)[0], [TANGO])
        self.assertEqual(addressed('Víctor, hold', [TANGO, VICTOR], EVERYONE)[0], [VICTOR])
        self.assertEqual(addressed('tan go', [TANGO, VICTOR], EVERYONE)[0], [])   # too far off

    def test_short_name_with_one_letter_more(self):
        axe = {'id': 'axe', 'name': 'Axe', 'role': 'vehicles'}
        self.assertEqual(addressed('Axel, hold position', [axe, TANGO], EVERYONE)[0], [axe])
        self.assertEqual(addressed('axes hold', [axe, TANGO], EVERYONE)[0], [axe])
        self.assertEqual(addressed('ax hold', [axe, TANGO], EVERYONE)[0], [])
        self.assertEqual(addressed('axels hold', [axe, TANGO], EVERYONE)[0], [])

    def test_only_at_the_start_of_the_call(self):
        late = 'Ford one one here, we are on the way, can you see Tango'
        self.assertEqual(addressed(late, [TANGO, VICTOR], EVERYONE), ([], ''))

    def test_all_stations_unless_a_name_is_said(self):
        self.assertEqual(addressed('All stations, radio check', [TANGO, VICTOR], EVERYONE), ([TANGO, VICTOR], 'all'))
        self.assertEqual(addressed('Tango, is everyone ready', [TANGO, VICTOR], EVERYONE), ([TANGO], 'name'))

    def test_words_come_from_every_locale(self):
        words = all_stations_words()
        self.assertIn(['all', 'stations'], words)
        self.assertGreater(len(words), 5)   # more than one language


class Routing(unittest.TestCase):
    def test_alone_always_answers(self):
        self.assertEqual(Dispatcher(EVERYONE).route('radio check', [TANGO]), ([TANGO], 'alone'))

    def test_follow_up_then_nobody(self):
        router = Dispatcher(EVERYONE)
        self.assertEqual(router.route('say again', [TANGO, VICTOR]), ([], ''))
        router.talked([VICTOR, TANGO], 'victor')
        self.assertEqual(router.route('say again', [TANGO, VICTOR]), ([VICTOR], 'follow_up'))
        router.last[router.key([TANGO, VICTOR])] = ('victor', time.monotonic() - dispatch.FOLLOW_UP_S - 1)
        self.assertEqual(router.route('say again', [TANGO, VICTOR]), ([], ''))


if __name__ == '__main__':
    unittest.main()
