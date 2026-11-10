"""Offline tests for the SRS voice output: finding ExternalAudio, its command line, and the clip it is given.

Run: python -m unittest discover tests
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

from instructor.srs import (EXTERNAL_AUDIO, SRS_APPS, SrsError, SrsOutput, command, find_external_audio,
                            is_running, open_apps)

SRS = {'frequencies_mhz': [251.0, 30.5], 'modulations': ['am', 'FM'], 'coalition': 1, 'port': 5010,
       'name': 'DCS Instructor', 'volume': 0.8}


def fake_install(root: Path) -> Path:
    exe = root / EXTERNAL_AUDIO
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')
    return exe


class FindExternalAudio(unittest.TestCase):
    def test_configured_folder_or_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = fake_install(Path(tmp))
            self.assertEqual(find_external_audio(tmp), exe)
            self.assertEqual(find_external_audio(str(exe)), exe)
            self.assertIsNone(find_external_audio(str(Path(tmp) / 'missing')))

    def test_registry_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = fake_install(Path(tmp))
            with mock.patch('instructor.srs.registry_dir', return_value=Path(tmp)):
                self.assertEqual(find_external_audio(), exe)


class OpenApps(unittest.TestCase):
    def test_starts_only_what_is_installed_and_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = fake_install(Path(tmp))
            for app in ('server', 'client'):
                (Path(tmp) / SRS_APPS[app]).parent.mkdir()
                (Path(tmp) / SRS_APPS[app]).write_bytes(b'')
            with mock.patch('instructor.srs.is_running', side_effect=lambda e: e.name == 'SRS-Server.exe'), \
                    mock.patch('instructor.srs.subprocess.Popen') as popen:
                self.assertEqual(open_apps(exe), {'server': 'running', 'client': 'started'})
            popen.assert_called_once()
            self.assertEqual(popen.call_args.kwargs['cwd'], Path(tmp) / 'Client')
            (Path(tmp) / SRS_APPS['client']).unlink()
            with mock.patch('instructor.srs.is_running', return_value=True):
                self.assertEqual(open_apps(exe)['client'], 'missing')

    @unittest.skipUnless(sys.platform == 'win32', 'tasklist is Windows-only')
    def test_is_running(self):
        self.assertTrue(is_running(Path(sys.executable)))
        self.assertFalse(is_running(Path('no-such-program-4711.exe')))


class Transmit(unittest.TestCase):
    def test_command_line(self):
        cmd = command(Path('srs.exe'), Path('clip.ogg'), SRS)
        self.assertEqual(cmd, ['srs.exe', '--file=clip.ogg', '--freqs=251,30.5', '--modulations=AM,FM',
                               '--coalition=1', '--port=5010', '--name=DCS-Instructor', '--volume=0.8'])

    def test_play_sends_ogg_clip_and_removes_it(self):
        seen = {}

        def run(cmd, **_):
            clip = Path(cmd[1].split('=', 1)[1])
            seen['info'] = sf.info(clip)
            seen['clip'] = clip
            return subprocess.CompletedProcess(cmd, 0, stdout='INFO|Finished - Closing\n', stderr='')

        with tempfile.TemporaryDirectory() as tmp:
            fake_install(Path(tmp))
            output = SrsOutput({**SRS, 'path': tmp}, Path(tmp) / 'clips')
            with mock.patch('instructor.srs.subprocess.run', side_effect=run):
                output.play(np.zeros(4410, dtype=np.float32), 44100)
            self.assertEqual(seen['info'].format, 'OGG')
            self.assertAlmostEqual(seen['info'].duration, 0.1, places=2)
            self.assertFalse(seen['clip'].exists())

    def test_connection_error_is_reported(self):
        log = '2026-11-01 06:40:19.0556|ERROR|Could not connect to server\n'
        with tempfile.TemporaryDirectory() as tmp:
            fake_install(Path(tmp))
            output = SrsOutput({**SRS, 'path': tmp}, Path(tmp) / 'clips')
            done = subprocess.CompletedProcess([], 0, stdout=log, stderr='')
            with mock.patch('instructor.srs.subprocess.run', return_value=done):
                with self.assertRaisesRegex(SrsError, 'Could not connect'):
                    output.play(np.zeros(441, dtype=np.float32), 44100)

    def test_long_clip_does_not_crash_the_process(self):
        # One soundfile.write of >= ~12 s of OGG Vorbis killed the process (0xC00000FD in libsndfile) twice live on
        # 2026-11-01. Run in a child process so a crash fails this test instead of killing the test run.
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / 'long.ogg'
            code = ('import sys, numpy as np; from pathlib import Path; from instructor.srs import write_ogg; '
                    'n = 44100 * 30; rng = np.random.default_rng(1); '
                    'x = (0.2 * np.sin(np.arange(n) * 2 * np.pi * 220 / 44100) + 0.05 * rng.standard_normal(n)); '
                    'write_ogg(Path(sys.argv[1]), x.astype("float32"), 44100)')
            result = subprocess.run([sys.executable, '-c', code, str(clip)], capture_output=True, text=True,
                                    timeout=120, cwd=Path(__file__).resolve().parent.parent)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertAlmostEqual(sf.info(clip).duration, 30.0, places=1)

    def test_missing_install(self):
        with self.assertRaises(SrsError):
            SrsOutput({**SRS, 'path': 'Z:/no/srs/here'}, Path(tempfile.gettempdir()))


if __name__ == '__main__':
    unittest.main()
