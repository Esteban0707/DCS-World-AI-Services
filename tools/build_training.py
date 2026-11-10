"""Convert an aircraft's official DCS training missions into instructor lesson files.

Usage (Windows, where DCS lives):
    python tools/build_training.py --dcs "E:/DCS World" --source "F-5E 2024" --module F-5E-3

For every Missions/Training/*.miz of the aircraft, writes modules/<module>/training/<slug>.json: the mission's
trigger rules in order, with the conditions the instructor must check (cockpit argument ranges, flags, timers,
zones, speed...) and the actions (instructor text in every language the mission ships, voice clip, cockpit
highlight). Nothing is invented: it is the training authors' own sequence. Never modifies the DCS install.
"""
import argparse
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

try:
    from build_module import write_export_args          # run as a script (tools/ is on the path)
except ImportError:
    from tools.build_module import write_export_args    # imported from the project folder (tools/setup.py)

ROOT = Path(__file__).resolve().parent.parent
DUMPER = ROOT / 'tools' / 'dcs' / 'dump_lua_table.lua'
LANGUAGE_FOLDERS = {'DEFAULT': 'en', 'ES': 'es', 'DE': 'de', 'RU': 'ru', 'CN': 'cn', 'FR': 'fr', 'CS': 'cs', 'JP': 'ja', 'KO': 'ko'}
DROP_KEYS = {'ClassKey', 'KeyDict_text', 'text', 'unit', 'volume', 'flag_back', 'clearview', 'seconds_delay'}


def lua_table(luae: Path, path: Path, name: str):
    result = subprocess.run([str(luae), str(DUMPER), str(path), name], capture_output=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
    return json.loads(result.stdout.decode('utf-8'))


def slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def simplify(entry: dict, texts: dict, resources: dict, control_names: dict) -> dict:
    """Keep what the instructor needs from a condition or action; resolve texts, sounds and control names."""
    out = {k: v for k, v in entry.items() if k not in DROP_KEYS and v not in ('', None)}
    key = entry.get('KeyDict_text')
    if key:
        out['text'] = {lang: table[key] for lang, table in texts.items() if table.get(key)}
    if entry.get('predicate') == 'a_out_sound' and entry.get('file'):
        out['file'] = resources.get(entry['file'], entry['file'])
    if entry.get('predicate') == 'c_argument_in_range':
        out = {'predicate': 'c_argument_in_range', 'arg': entry['argument'], 'min': entry['_min'], 'max': entry['_max'],
               'control': control_names.get(entry['argument'])}
    if entry.get('element_name'):
        out['control'] = control_names.get(entry['element_name'])
    return out


def convert(luae: Path, miz: Path, control_names: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix='dcs-instructor-') as tmp, zipfile.ZipFile(miz) as archive:
        tmp = Path(tmp)
        names = set(archive.namelist())
        archive.extract('mission', tmp)
        mission = lua_table(luae, tmp / 'mission', 'mission')
        texts, resources = {}, {}
        for folder, lang in LANGUAGE_FOLDERS.items():
            member = f'l10n/{folder}/dictionary'
            if member in names:
                archive.extract(member, tmp)
                texts[lang] = lua_table(luae, tmp / member, 'dictionary')
        if 'l10n/DEFAULT/mapResource' in names:
            archive.extract('l10n/DEFAULT/mapResource', tmp)
            resources = lua_table(luae, tmp / 'l10n/DEFAULT/mapResource', 'mapResource')

    rules = []
    for index, rule in enumerate(mission.get('trigrules', []), start=1):
        rules.append({
            'index': index,
            'name': re.sub(r'-{2,}', ' ', rule.get('comment', '')).strip(),
            'type': rule.get('predicate'),
            'conditions': [simplify(c, texts, resources, control_names) for c in rule.get('rules', [])],
            'actions': [simplify(a, texts, resources, control_names) for a in rule.get('actions', [])],
        })
    description = mission.get('descriptionText', '')
    return {
        'source': {'mission': miz.name, 'generated_by': 'tools/build_training.py'},
        'languages': sorted(texts),
        'description': {lang: table.get(description, description) for lang, table in texts.items()},
        'rules': rules,
    }


def missions(dcs: Path, source: str) -> list[Path]:
    return sorted((dcs / 'Mods' / 'aircraft' / source / 'Missions' / 'Training').glob('*.miz'))


def missing(dcs: Path, source: str, module_dir: Path) -> list[str]:
    """The aircraft's training missions without a complete lesson file (none, unreadable or without rules), so an
    interrupted build is finished by running it again (tools/setup.py)."""
    found = []
    for miz in missions(dcs, source):
        path = module_dir / 'training' / f'{slug(miz.stem)}.json'
        try:
            if json.loads(path.read_text(encoding='utf-8'))['rules']:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            pass
        found.append(miz.stem)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dcs', required=True, help='DCS World install folder')
    parser.add_argument('--source', required=True, help='folder name under Mods/aircraft')
    parser.add_argument('--module', required=True, help='module folder name under modules/')
    args = parser.parse_args()

    dcs = Path(args.dcs)
    luae = dcs / 'bin' / 'luae.exe'
    module_dir = ROOT / 'modules' / args.module
    controls = json.loads((module_dir / 'controls.json').read_text(encoding='utf-8'))['controls']
    # Lookups by element id (highlights) and by argument (range checks).
    control_names = {c['id']: c['hint'] for c in controls}
    for c in controls:
        for arg in c['arg']:
            control_names.setdefault(arg, c['hint'])

    out_dir = module_dir / 'training'
    out_dir.mkdir(parents=True, exist_ok=True)
    for miz in missions(dcs, args.source):
        lesson = convert(luae, miz, control_names)
        path = out_dir / f'{slug(miz.stem)}.json'
        temp = path.with_suffix('.json.tmp')   # written whole, then moved: never half a lesson
        temp.write_text(json.dumps(lesson, indent=1, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path)
        checks = sum(1 for r in lesson['rules'] for c in r['conditions'] if c['predicate'] == 'c_argument_in_range')
        print(f'{path.name}: {len(lesson["rules"])} rules, {checks} cockpit checks, languages {lesson["languages"]}')
    print(f'export_args.lua: {write_export_args(module_dir)} cockpit args')


if __name__ == '__main__':
    main()
