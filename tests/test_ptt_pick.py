"""Offline test for the push-to-talk picker: a real click wins over a state button that switches on and stays on.

Run: python -m unittest discover tests
"""
import unittest
from unittest import mock

from tools import ptt_pick

STICK = {'id': 0, 'name': 'Saitek Pro Flight X-56 Rhino Stick', 'buttons': 17}


class PickButton(unittest.TestCase):
    def test_click_wins_over_button_that_stays_on(self):
        # As seen live 2026-11-01: button 15 switches on by itself and stays on; the pilot then clicks button 6.
        states = iter([set(), {15}, {15}, {6, 15}, {6, 15}, {15}])
        with mock.patch.object(ptt_pick, 'buttons_down', lambda _: next(states)), \
                mock.patch.object(ptt_pick.time, 'sleep'):
            joy, button = ptt_pick.wait_for_click([STICK])
        self.assertEqual((joy['name'], button), (STICK['name'], 6))


if __name__ == '__main__':
    unittest.main()
