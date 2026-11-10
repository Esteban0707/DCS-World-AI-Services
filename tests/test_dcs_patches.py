"""Offline tests for the DCS install patches (client/dcs_patches.py) on copies of the real DCS files, and for the
patch blocks' behaviour with DCS's own Lua interpreter (the speech mute and the native command bridge).

Run: python -m unittest discover tests
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from client import dcs_patches
from client.dcs_patches import (MISSING, ORIGINAL, OUTDATED, PATCHED, PATCHES, UNSUPPORTED, Patch, PatchError,
                                apply, inspect, remove, update_all)

from dcs_data import DCS  # noqa: E402
LUAE = DCS / 'bin' / 'luae.exe'
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(LUAE.exists() and all((DCS / p.relative).exists() for p in PATCHES),
                     "needs a DCS install (its speech.lua, radio menu script and bin/luae.exe)")
class Patches(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dcs = Path(self.temp.name) / 'DCS'
        for patch in PATCHES:
            target = self.dcs / patch.relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(DCS / patch.relative, target)
        self.backups = Path(self.temp.name) / 'backups'
        dcs_patches.LUAE = LUAE
        for patch in PATCHES:   # the user's install may already carry our blocks: start from DCS's original
            remove(self.dcs, patch)

    def tearDown(self):
        dcs_patches.LUAE = None
        self.temp.cleanup()

    def test_apply_and_remove_keep_every_other_byte(self):
        for patch in PATCHES:
            path = self.dcs / patch.relative
            original = path.read_bytes()
            self.assertEqual(inspect(self.dcs, patch).state, ORIGINAL)
            self.assertEqual(apply(self.dcs, patch, self.backups).state, PATCHED)
            patched = path.read_bytes()
            self.assertIn(f'-- DCS_AI_Service BEGIN {patch.name} {patch.version()}'.encode(), patched)
            self.assertEqual(apply(self.dcs, patch, self.backups).detail, 'already applied')
            self.assertEqual(path.read_bytes(), patched)                    # applying twice changes nothing
            self.assertEqual(remove(self.dcs, patch).state, ORIGINAL)
            self.assertEqual(path.read_bytes(), original, patch.name)
            self.assertEqual(len(list(self.backups.glob(f'{path.name}.*.orig'))), 1)

    def test_speech_block_goes_after_the_anchor(self):
        patch = PATCHES[0]
        apply(self.dcs, patch, self.backups)
        lines = (self.dcs / patch.relative).read_bytes().decode('latin-1').splitlines()
        anchor = next(i for i, line in enumerate(lines) if line.strip() == patch.anchor)
        self.assertTrue(lines[anchor + 1].strip().startswith('-- DCS_AI_Service BEGIN speech_mute'))
        self.assertTrue(lines[anchor + 1].startswith(lines[anchor][:len(lines[anchor]) - len(lines[anchor].lstrip())]))

    def test_bridge_is_appended_at_the_end(self):
        patch = PATCHES[1]
        apply(self.dcs, patch, self.backups)
        lines = (self.dcs / patch.relative).read_bytes().decode('latin-1').splitlines()
        self.assertEqual(lines[-1], '-- DCS_AI_Service END')

    def test_a_newer_block_is_outdated_and_update_refreshes_it(self):
        patch = PATCHES[0]
        apply(self.dcs, patch, self.backups)
        path = self.dcs / patch.relative
        path.write_bytes(path.read_bytes().replace(patch.version().encode(), b'000000000000'))
        self.assertEqual(inspect(self.dcs, patch).state, OUTDATED)
        states = {s.name: s.state for s in update_all(self.dcs, self.backups)}
        self.assertEqual(states, {'speech_mute': PATCHED, 'radio_bridge': PATCHED})
        self.assertEqual(path.read_bytes().count(b'DCS_AI_Service BEGIN'), 1)

    def test_a_changed_dcs_file_is_reported_not_forced(self):
        patch = PATCHES[0]
        path = self.dcs / patch.relative
        changed = path.read_bytes().replace(patch.anchor.encode(), b'local result = protocol:build(message)')
        path.write_bytes(changed)
        self.assertEqual(inspect(self.dcs, patch).state, UNSUPPORTED)
        status = {s.name: s for s in update_all(self.dcs, self.backups)}['speech_mute']
        self.assertEqual(status.state, UNSUPPORTED)
        self.assertIn('needs a review', status.detail)
        self.assertEqual(path.read_bytes(), changed)                        # nothing written

    def test_broken_lua_is_never_written(self):
        bad = Path(self.temp.name) / 'bad.lua'
        bad.write_text('if then\n', encoding='utf-8')
        patch = Patch('speech_mute', PATCHES[0].relative, bad, PATCHES[0].anchor)
        path = self.dcs / patch.relative
        before = path.read_bytes()
        with self.assertRaises(PatchError):
            apply(self.dcs, patch, self.backups)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_missing_file(self):
        (self.dcs / PATCHES[0].relative).unlink()
        self.assertEqual(inspect(self.dcs, PATCHES[0]).state, MISSING)


@unittest.skipUnless(LUAE.exists(), "needs DCS's own Lua interpreter (bin/luae.exe)")
class Blocks(unittest.TestCase):
    def run_lua(self, code: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'case.lua'
            script.write_text(code, encoding='utf-8')
            result = subprocess.run([str(LUAE), str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_speech_mute_silences_atc_flights_and_wingmen(self):
        block = (ROOT / 'dcs' / 'patches' / 'speech_mute.lua').read_text(encoding='utf-8')
        out = self.run_lua('local base = {Message = {wMsgATCNull = 100, wMsgATCMaximum = 200, wMsgLeaderToATCNull = 300,'
                           ' wMsgLeaderToATCMaximum = 400, wMsgFlightNull = 500, wMsgFlightMaximum = 600,'
                           ' wMsgLeaderNull = 700, wMsgLeaderMaximum = 800, wMsgWingmenNull = 900,'
                           ' wMsgWingmenMaximum = 1000, wMsgAWACSNull = 1100, wMsgAWACSMaximum = 1200,'
                           ' wMsgLeaderToAWACSNull = 1300, wMsgLeaderToAWACSMaximum = 1400}}\n'
                           'for _, event in ipairs({150, 350, 550, 750, 950, 1150, 1350, 50, 250, 1050, 1250}) do\n'
                           '  local message, result = {event = event}, {files = {"voice.ogg"}, duration = 2}\n'
                           + block + '\n  print(event, #result.files)\nend\n')
        # ATC, to-ATC, flight reports, leader calls, wingmen, AWACS and to-AWACS (user, 2026-11-08) muted; anything
        # else (e.g. a tanker) kept.
        self.assertEqual(out.split(), ['150', '0', '350', '0', '550', '0', '750', '0', '950', '0', '1150', '0',
                                       '1350', '0', '50', '1', '250', '1', '1050', '1', '1250', '1'])

    def run_bridge(self, case: str) -> list[str]:
        result = subprocess.run([str(LUAE), str(ROOT / 'tests' / 'lua' / 'bridge_harness.lua'), ROOT.as_posix() + '/',
                                 case], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def replies(self, lines) -> dict:
        found = [json.loads(line.split(' ', 2)[2]) for line in lines if line.startswith('REPLY 47820 ')]
        return {r['id']: (r['status'], r['detail']) for r in found}

    def test_bridge_sends_only_allowed_fresh_commands(self):
        lines = self.run_bridge('ready')
        # Started like VAICOM: after the panel's own initialize(), with the application update callback set up.
        self.assertIn('CALLBACKS BEFORE INIT 0', lines)
        self.assertEqual(lines.count('PANEL INIT'), 2)
        self.assertIn('SETUP', lines)
        self.assertIn('HIGH SPEED true', lines)
        self.assertIn('CALLBACKS 1', lines)                                  # initialized twice, one callback
        self.assertIn('BIND 127.0.0.1:47823', lines)
        self.assertEqual([l for l in lines if l.startswith(('SEND', 'TUNE'))],
                         ['TUNE Senaki-Kolkhi', 'SEND 101 to Senaki-Kolkhi',  # one radio message to the ATC
                          'TUNE Wingman 2',          # as the menu: the radio that reaches the wingman first
                          'SEND 201 with 1,false',   # wingman 2: number - 1, then the menu's own parameter
                          'TUNE Wingman 2', 'SEND 202 with 5',   # a formation: the flight (5), as the F1 menu does
                          'TUNE Wingman 2', 'SEND 204 with 5'])
        replies = self.replies(lines)
        self.assertEqual(replies['w3'], ('unavailable', 'no wingman 4 in the flight'))
        self.assertEqual(replies['w5'], ('refused', 'bad wingman 7'))
        replies = self.replies(lines)
        self.assertEqual(replies['a1'], ('sent', 'abort_takeoff to Senaki-Kolkhi'))
        self.assertEqual(replies['a2'], ('refused', 'sent moments ago'))
        self.assertEqual(replies['a3'][0], 'unavailable')                     # the other coalition's airfield
        self.assertEqual(replies['a4'], ('refused', 'request too old'))
        self.assertEqual(replies['a5'], ('refused', 'command not allowed: lua_dostring'))
        self.assertEqual(replies['?'], ('refused', 'malformed request'))
        # Airborne troops, as the menu's Descent items do it.
        self.assertEqual(replies['a6'], ('sent', 'embarking Buenos'))
        self.assertIn('EMBARK Buenos on 9001', lines)
        self.assertEqual(replies['a7'], ('unavailable', 'Ghost is not ready to embark on this helicopter (offered: Buenos)'))
        self.assertEqual(replies['a8'], ('sent', 'disembarking Buenos'))
        self.assertIn('DISEMBARK 77', lines)
        self.assertEqual(replies['a9'], ('sent', 'Buenos'))

    def test_bridge_waits_for_the_radio_menu(self):
        lines = self.run_bridge('no_menu')
        self.assertFalse([l for l in lines if l.startswith('SEND')])
        self.assertEqual(self.replies(lines)['a1'], ('unavailable', 'radio menu not ready'))


if __name__ == '__main__':
    unittest.main()
