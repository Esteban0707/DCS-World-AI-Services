"""Offline tests for the one-click setup (tools/setup.py): language from Windows, DCS folder check, moved project,
and that an interrupted or partial install is never taken as done (voice model, training lessons, library).

Run: python -m unittest discover tests
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from shared.locale import load_locale
from tools import (build_clouds, build_library, build_module, build_rcs, build_terrains, build_training,
                   install_export, install_hook, preflight, setup, setup_tts)


class Setup(unittest.TestCase):
    def test_language_follows_windows(self):
        self.assertEqual(setup.language_settings('es-MX'),
                         {'language': 'es', 'voice': {'language': 'es'}, 'speech': {'language': 'es-MX'}})
        self.assertEqual(setup.language_settings('de-DE')['language'], 'en')
        self.assertEqual(setup.language_settings('')['speech'], {'language': 'en-US'})

    def test_dcs_install_needs_its_bin_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertFalse(setup.is_dcs_install(folder))
            (folder / 'bin').mkdir()
            (folder / 'bin' / 'DCS.exe').write_bytes(b'')
            self.assertTrue(setup.is_dcs_install(folder))

    def test_export_and_hook_of_a_moved_project_are_installed_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = Path(tmp)
            (saved / 'Scripts' / 'Hooks').mkdir(parents=True)
            export_lua = saved / 'Scripts' / 'Export.lua'
            hook = saved / 'Scripts' / 'Hooks' / install_hook.HOOK_NAME
            self.assertEqual(setup.moved(saved), [])   # not installed: preflight reports that, not moved()
            export_lua.write_text(f'dofile([[D:/old/dcs/instructor_export.lua]]) {install_export.MARKER}\n')
            hook.write_text(f'{install_hook.MARKER}\nloadfile([[D:/old/dcs/instructor_hook.lua]])\n')
            self.assertEqual([key for key, _ in setup.moved(saved)], ['export_missing', 'hook_missing'])
            export_lua.write_text(install_export.install_line() + '\n')
            hook.write_text(install_hook.hook_text())
            self.assertEqual(setup.moved(saved), [])

    def test_the_end_says_incomplete_when_a_step_failed(self):
        out = io.StringIO()
        with redirect_stdout(out):
            report = setup.Report()
            report.later('install Google Chrome')
            self.assertEqual(report.summary(), 0)
            report.fail('voice model not ready')
            self.assertEqual(report.summary(), 1)
        self.assertIn('Setup complete.', out.getvalue())
        self.assertIn('Setup INCOMPLETE', out.getvalue())
        self.assertIn('- voice model not ready', out.getvalue())


class VoiceModel(unittest.TestCase):
    def test_ready_only_when_every_file_is_there_and_it_spoke(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(setup_tts, 'MODEL_DIR', Path(tmp)), \
                mock.patch.object(setup_tts, 'READY', Path(tmp) / 'ready.json'), \
                mock.patch.object(setup_tts, 'expected_voices', return_value={'M1', 'M2'}):
            model = Path(tmp)
            (model / 'voice_styles').mkdir()
            (model / 'voice_styles' / 'M1.json').write_text('{}')
            self.assertIn('model files missing', setup_tts.ready())   # what an early stop leaves
            for path in setup_tts.required_files():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            (model / 'onnx' / 'vocoder.onnx').write_bytes(b'')   # present but empty: Supertonic would not refetch it
            self.assertIn('onnx/vocoder.onnx', setup_tts.damaged())
            (model / 'onnx' / 'vocoder.onnx').write_bytes(b'x')
            (model / '.cache').mkdir()
            (model / '.cache' / 'vocoder.onnx.incomplete').write_bytes(b'')
            self.assertEqual(setup_tts.ready(), 'a download was interrupted')
            (model / '.cache' / 'vocoder.onnx.incomplete').unlink()
            self.assertEqual(setup_tts.ready(), 'voices missing: M2')   # the ATC's default voice
            (model / 'voice_styles' / 'M2.json').write_text('{}')
            self.assertIsNone(setup_tts.damaged())
            self.assertEqual(setup_tts.ready(), setup_tts.NEVER_TESTED)
            setup_tts.READY.write_text(json.dumps({'voices': ['M1', 'F4']}))
            self.assertEqual(setup_tts.ready(), 'voices missing: F4')
            setup_tts.READY.write_text(json.dumps({'voices': ['M1', 'M2']}))
            self.assertIsNone(setup_tts.ready())

    def test_expected_voices_are_the_defaults_and_the_configured_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'agents' / 'atc').mkdir(parents=True)
            (root / 'settings.json').write_text(json.dumps({'voice': {'name': 'F4'}}))
            (root / 'agents' / 'atc' / 'agent.json').write_text(json.dumps({'voice': {'name': 'F2'}}))
            self.assertEqual(setup_tts.expected_voices(root), {'M1', 'M2', 'F4', 'F2'})


class TrainingLessons(unittest.TestCase):
    def test_every_mission_needs_a_complete_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            dcs, module = Path(tmp) / 'dcs', Path(tmp) / 'module'
            training = dcs / 'Mods' / 'aircraft' / 'X 2024' / 'Missions' / 'Training'
            training.mkdir(parents=True)
            for name in ('X Cold Start', 'X Landing', 'X Bombs'):
                (training / f'{name}.miz').write_bytes(b'')
            (module / 'training').mkdir(parents=True)
            (module / 'training' / 'x_cold_start.json').write_text(json.dumps({'rules': [{'index': 1}]}))
            (module / 'training' / 'x_landing.json').write_text('{"rules": [{"ind')   # cut off mid-write
            self.assertEqual(build_training.missing(dcs, 'X 2024', module), ['X Bombs', 'X Landing'])


class DataFromDcs(unittest.TestCase):
    """The Eagle Dynamics data SETUP.cmd builds instead of shipping: a missing or cut file is built again."""

    def test_cockpit_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp)
            self.assertEqual(build_module.missing(module),
                             ['controls.json', 'autostart.json', 'positions.json', 'export_args.lua'])
            (module / 'controls.json').write_text(json.dumps({'controls': [{'id': 'GEN'}]}))
            (module / 'autostart.json').write_text(json.dumps({'steps': [{'device': 1}]}))
            (module / 'positions.json').write_text('{"args": {"38')            # cut off mid-write
            (module / 'export_args.lua').write_text('return {0, 8, 10')        # the same
            self.assertEqual(build_module.missing(module), ['positions.json', 'export_args.lua'])
            (module / 'positions.json').write_text(json.dumps({'args': {'387': {}}}))
            (module / 'export_args.lua').write_text('return {0, 8, 10}\n')
            self.assertEqual(build_module.missing(module), [])

    def test_maps_clouds_and_radar_cross_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            dcs, out = Path(tmp) / 'dcs', Path(tmp) / 'terrains'
            for name in ('Caucasus', 'Syria'):
                (dcs / 'Mods' / 'terrains' / name).mkdir(parents=True)
                (dcs / 'Mods' / 'terrains' / name / 'Beacons.lua').write_text('')
            (dcs / 'Mods' / 'terrains' / 'NoBeacons').mkdir()   # not a map the tools can read
            (out / 'Caucasus').mkdir(parents=True)
            (out / 'Caucasus' / 'navigation.json').write_text(json.dumps({'airfields': [{'name': 'Senaki'}]}))
            self.assertEqual(build_terrains.missing(dcs, out), ['Syria'])
            self.assertEqual(build_terrains.missing(Path(tmp) / 'no DCS here', out), [])
            clouds, rcs = Path(tmp) / 'clouds.json', Path(tmp) / 'aircraft_rcs.lua'
            self.assertFalse(build_clouds.built(clouds) or build_rcs.built(rcs))
            clouds.write_text(json.dumps({'Preset1': [[840, 0.3]]}))
            rcs.write_text('DCSInstructorRadar_RCS = {\n    ["F-5E-3"] = 3,\n')   # cut before the closing brace
            self.assertTrue(build_clouds.built(clouds))
            self.assertFalse(build_rcs.built(rcs))
            rcs.write_text('DCSInstructorRadar_RCS = {\n    ["F-5E-3"] = 3,\n}\n')
            self.assertTrue(build_rcs.built(rcs))

    def test_the_launcher_says_run_setup_when_data_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(preflight, 'ROOT', Path(tmp)), \
                mock.patch.object(build_terrains, 'missing', return_value=['Syria']), \
                mock.patch.object(build_clouds, 'built', return_value=True), \
                mock.patch.object(build_rcs, 'built', return_value=False):
            (Path(tmp) / 'modules').mkdir()
            self.assertEqual(preflight.dcs_data({'dcs': {'install': tmp}}),
                             [('dcs_data_missing', {'what': 'map Syria, radar cross sections'})])
        for language in ('en', 'es'):
            self.assertIn('{what}', load_locale(language)['checks']['dcs_data_missing'])


class Library(unittest.TestCase):
    def module(self, tmp: str) -> Path:
        module = Path(tmp) / 'X-1'
        (module / 'library' / 'manual').mkdir(parents=True)
        (module / 'module.json').write_text(json.dumps({'library': [{'id': 'manual', 'source': 'Doc/x.pdf'}]}))
        (module / 'library' / 'manual' / '1.txt').write_text('text')
        index = {'books': [{'id': 'manual', 'source': 'Doc/x.pdf', 'sections': [{'file': 'manual/1.txt'}]}]}
        (module / 'library' / 'index.json').write_text(json.dumps(index))
        return module

    def test_a_damaged_library_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = self.module(tmp)
            self.assertIsNone(build_library.stale(module))
            (module / 'library' / 'manual' / '1.txt').unlink()
            self.assertEqual(build_library.stale(module), 'section files missing')
            (module / 'library' / 'index.json').write_text('{"books": [{"id": "man')
            self.assertEqual(build_library.stale(module), 'index unreadable')
            (module / 'library' / 'index.json').write_text(json.dumps({'books': []}))
            self.assertEqual(build_library.stale(module), 'a manual is missing')

    def test_aircraft_not_installed_in_dcs_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            modules = Path(tmp) / 'modules'
            (modules / 'X-1').mkdir(parents=True)
            (modules / 'X-1' / 'module.json').write_text(json.dumps(
                {'dcs_source_folder': 'Mods/aircraft/X 2024', 'library': [{'id': 'manual', 'source': 'Doc/x.pdf'}]}))
            with mock.patch.object(preflight, 'ROOT', Path(tmp)):
                self.assertEqual(preflight.libraries({'dcs': {'install': str(Path(tmp) / 'dcs')}}, log=lambda _: 0),
                                 [])


if __name__ == '__main__':
    unittest.main()
