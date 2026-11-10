"""Offline tests for the aircraft library: PDF text cleaning, building books from documents, search, reading sections
in parts, and the library tools (instructor/library.py, tools/build_library.py).

Run: python -m unittest discover tests
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from instructor.library import Library, Shelf, split_parts, stem
from instructor.tools import ToolBox
from shared.cockpit import CockpitState
from tools import build_library as build
from tools import install_export, install_hook, preflight

ROOT = Path(__file__).resolve().parent.parent
REAL_LIBRARY = ROOT / 'modules' / 'F-5E-3' / 'library'

MANUAL = """# 8 NORMAL PROCEDURES
Put the aircraft in cold start position.
## 8.1 Engine Start
1. Set battery switch to upper position - BATT.
2. Set left and right generator switches to L GEN, R GEN.
## 8.7 Landing
Final approach speed is set by angle of attack. Extend the drag chute after touchdown.
# 11 FLIGHT AND OPERATIONAL LIMITATIONS
Maximum exhaust gas temperature 685 C.
"""


class Cleaning(unittest.TestCase):
    def test_letter_spaced_headings(self):
        self.assertEqual(build.unspace('B e f o r e  S t a r t'), 'Before Start')
        self.assertEqual(build.unspace('C A D C / P i t o t- S t a t i c S y s t e m M a l f u n c t i o n'),
                         'CADC/Pitot-Static System Malfunction')
        self.assertEqual(build.unspace('1 2 3 4 5 6'), '1 2 3 4 5 6')               # a table row stays
        self.assertEqual(build.unspace('Set the switch to A or B'), 'Set the switch to A or B')

    def test_symbols_and_look_alike_letters(self):
        self.assertEqual(build.plain('Maximum: 685С; МК-82'), 'Maximum: 685°C; MK-82')   # Cyrillic С, М, К

    def test_page_cleaning(self):
        page = ('DIGITAL COMBAT SIMULATOR F-5E\nŨ\n +,/*)Ũ-/,\nB e f o r e  S t a r t\n'
                '1. Set battery switch to upper\nposition – BATT (mouse\nbutton or\n|RCtrl + RShift + B|).\n'
                ' Flap Lever - FULL;\nThe com-\npressor spins.')
        self.assertEqual(build.clean_page(page).splitlines(), [
            'Before Start', '1. Set battery switch to upper position – BATT (mouse button or |RCtrl + RShift + B|).',
            '- Flap Lever - FULL;', 'The compressor spins.'])


class Build(unittest.TestCase):
    def build_module(self, folder: Path) -> Path:
        module = folder / 'X-1'
        (module / 'documents').mkdir(parents=True)
        (module / 'module.json').write_text('{"id": "X-1", "dcs_aircraft_names": ["X-1"]}', encoding='utf-8')
        (module / 'documents' / 'My Manual.md').write_text(MANUAL, encoding='utf-8')
        (module / 'documents' / 'notes.txt').write_text('Squadron notes: taxi at 57 percent rpm.', encoding='utf-8')
        (module / 'documents' / 'ignored.docx').write_text('not read', encoding='utf-8')
        build.build(folder, module)
        return module

    def test_documents_become_books_with_numbered_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = self.build_module(Path(tmp))
            index = json.loads((module / 'library' / 'index.json').read_text(encoding='utf-8'))
            self.assertEqual([b['id'] for b in index['books']], ['user-my-manual', 'user-notes'])
            sections = {s['id']: s for s in index['books'][0]['sections']}
            self.assertEqual(list(sections), ['8', '8.1', '8.7', '11'])
            self.assertEqual((sections['8.1']['parent'], sections['8']['parent']), ('8', None))
            text = (module / 'library' / sections['8.1']['file']).read_text(encoding='utf-8')
            self.assertIn('battery switch', text)
            self.assertNotIn('Landing', text)                  # each section ends where the next heading starts

    def test_search_read_and_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Library(self.build_module(Path(tmp)) / 'library')
            found = library.search('battery switch')['results']
            self.assertEqual((found[0]['book'], found[0]['section'], found[0]['title']),
                             ('user-my-manual', '8.1', 'Engine Start'))
            self.assertEqual(library.search('drag chutes')['results'][0]['section'], '8.7')   # plural stemmed
            self.assertEqual(library.search('taxi rpm')['results'][0]['book'], 'user-notes')
            self.assertEqual(library.search('zzz')['results'], [])
            for asked in ('8.1', '8.1 Engine Start', 'engine start'):
                self.assertEqual(library.read(asked)['section'], '8.1')
            chapter = library.read('8', book='user-my-manual')
            self.assertEqual(chapter['subsections'], ['8.1  Engine Start', '8.7  Landing'])
            self.assertIn('error', library.read('9.9'))
            contents = library.contents('user-my-manual')['books'][0]['contents'].splitlines()
            self.assertEqual(contents[:2], ['8  NORMAL PROCEDURES', '  8.1  Engine Start'])
            self.assertIn('error', library.contents('nope'))

    def test_toolbox_reads_the_library_of_the_aircraft_flown(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = self.build_module(Path(tmp))

            class Tower:
                state = CockpitState(aircraft='X-1')
            box = ToolBox(Tower(), Shelf(lambda aircraft: module if aircraft == 'X-1' else None))
            names = [d['name'] for d in box.tools()[0]['tools']]
            self.assertTrue({'search_library', 'read_section', 'library_contents'} <= set(names))
            self.assertEqual(json.loads(box.call('instructor.read_section', '{"section": "8.7"}'))['title'], 'Landing')
            self.assertEqual(json.loads(box.call('search_library', '{"query": "exhaust gas"}'))['results'][0]['section'],
                             '11')
            Tower.state = CockpitState(aircraft='Y-2')
            self.assertIn('no library for Y-2', json.loads(box.call('search_library', '{"query": "x"}'))['error'])


class Preflight(unittest.TestCase):
    def test_library_rebuilt_only_when_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = Build().build_module(Path(tmp))
            self.assertIsNone(build.stale(module))
            new = module / 'documents' / 'extra.txt'
            new.write_text('Formation notes.', encoding='utf-8')
            self.assertEqual(build.stale(module), 'documents added or removed')
            build.build(Path(tmp), module)
            self.assertIsNone(build.stale(module))
            index = module / 'library' / 'index.json'
            os.utime(new, (index.stat().st_mtime + 10, index.stat().st_mtime + 10))
            self.assertEqual(build.stale(module), 'a document changed')
            shutil.rmtree(module / 'library')
            self.assertEqual(build.stale(module), 'not built yet')

    def test_missing_dcs_files_are_reported_never_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = {'dcs': {'saved_games': tmp}}
            self.assertEqual([key for key, _ in preflight.installed(settings)], ['export_missing', 'hook_missing'])
            self.assertFalse((Path(tmp) / 'Scripts').exists())             # nothing written into Saved Games
            (Path(tmp) / 'Scripts' / 'Hooks').mkdir(parents=True)
            (Path(tmp) / 'Scripts' / 'Export.lua').write_text(f'x {install_export.MARKER}\n', encoding='utf-8')
            (Path(tmp) / 'Scripts' / 'Hooks' / install_hook.HOOK_NAME).write_text(install_hook.hook_text(),
                                                                                  encoding='utf-8')
            self.assertEqual(preflight.installed(settings), [])

    def test_every_check_has_its_text_in_every_language(self):
        for path in (ROOT / 'locales').glob('*.json'):
            checks = json.loads(path.read_text(encoding='utf-8'))['checks']
            self.assertTrue({'export_missing', 'hook_missing', 'library_failed', 'footer'} <= set(checks), path.name)


class Parts(unittest.TestCase):
    def test_long_sections_come_in_parts(self):
        text = '\n'.join(f'line {n} ' + 'word ' * 99 for n in range(40))   # 4000 words
        parts = split_parts(text)
        self.assertEqual(len(parts), 3)
        self.assertEqual('\n'.join(parts), text)

    def test_stemming(self):
        self.assertEqual([stem(w) for w in ('switches', 'lights', 'batteries', 'pass', 'gas')],
                         ['switch', 'light', 'battery', 'pass', 'gas'])


@unittest.skipUnless((REAL_LIBRARY / 'index.json').exists(), 'run tools/build_library.py for the F-5E-3')
class RealManual(unittest.TestCase):
    """The F-5E flight manual as built from DCS's PDF: the questions a student asks find the right sections."""

    @classmethod
    def setUpClass(cls):
        cls.library = Library(REAL_LIBRARY)

    def sections(self, query):
        return [(r['book'], r['section']) for r in self.library.search(query)['results']]

    def test_procedures_are_found(self):
        self.assertEqual(self.sections('engine start')[0], ('flight-manual', '8.1'))
        self.assertIn(('flight-manual', '6.9'), self.sections('drag chute'))
        self.assertIn(('flight-manual', '12'), self.sections('landing gear alternate extension'))
        self.assertIn(('flight-manual', '8.5'), self.sections('takeoff speed'))

    def test_engine_start_reads_with_its_pages(self):
        section = self.library.read('8.1', 'flight-manual')
        self.assertEqual(section['title'], 'Engine Start')
        self.assertTrue(section['text'].startswith('[page 189]'))
        self.assertIn('Set battery switch to upper position', section['text'])


if __name__ == '__main__':
    unittest.main()
