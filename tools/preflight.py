"""Checks before the instructor starts (tools/launcher.py): the DCS export line and GUI hook are installed, the data
built from the DCS install by SETUP.cmd is there, and every aircraft module's library is up to date (rebuilt here
when a document was added or changed).

Nothing is installed into Saved Games/DCS from here: that stays a user decision (tools/install_export.py,
tools/install_hook.py). Problems are returned as (locale key under "checks", values) for the launcher to show.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import build_library, install_export, install_hook   # noqa: E402


def saved_games(settings: dict) -> Path:
    configured = (settings.get('dcs') or {}).get('saved_games')
    return Path(configured) if configured else Path.home() / 'Saved Games' / 'DCS'


def installed(settings: dict) -> list[tuple[str, dict]]:
    """What is missing in Saved Games/DCS for the instructor to see the aircraft."""
    problems = []
    folder = saved_games(settings)
    export_lua = folder / 'Scripts' / 'Export.lua'
    if not (export_lua.exists() and install_export.MARKER in export_lua.read_text(encoding='utf-8', errors='replace')):
        problems.append(('export_missing', {'path': str(export_lua)}))
    hook = folder / 'Scripts' / 'Hooks' / install_hook.HOOK_NAME
    if not (hook.exists() and install_hook.MARKER in hook.read_text(encoding='utf-8', errors='replace')):
        problems.append(('hook_missing', {'path': str(hook)}))
    return problems


def libraries(settings: dict, log=print) -> list[tuple[str, dict]]:
    """Rebuild each module's library when it is stale; returns the problems. A module whose aircraft is not installed
    in DCS is skipped: its manuals are not there (a pilot without that aircraft)."""
    problems = []
    dcs = Path((settings.get('dcs') or {}).get('install') or '')
    for module in sorted(p.parent for p in (ROOT / 'modules').glob('*/module.json')):
        source = json.loads((module / 'module.json').read_text(encoding='utf-8')).get('dcs_source_folder')
        if source and not (dcs / source).exists():
            continue
        reason = build_library.stale(module)
        if not reason:
            continue
        log(f'library {module.name}: {reason}, rebuilding')
        try:
            index = build_library.build(dcs, module)
            log(f'library {module.name}: {len(index["books"])} books')
        except Exception as exc:   # a broken document must not keep the instructor from starting
            problems.append(('library_failed', {'module': module.name, 'error': str(exc)}))
    return problems


def dcs_data(settings: dict) -> list[tuple[str, dict]]:
    """The data built from the DCS install by SETUP.cmd (never shipped) that is not there: setup not finished, or a
    map or aircraft installed in DCS since. Not rebuilt here: some of it takes DCS's own Lua interpreter a while."""
    from tools import build_clouds, build_module, build_rcs, build_terrains
    dcs = Path((settings.get('dcs') or {}).get('install') or '')
    what = [f'map {name}' for name in build_terrains.missing(dcs)]
    for module_json in sorted((ROOT / 'modules').glob('*/module.json')):
        source = json.loads(module_json.read_text(encoding='utf-8')).get('dcs_source_folder')
        if source and (dcs / source).exists() and build_module.missing(module_json.parent):
            what.append(f'{module_json.parent.name} cockpit')
    what += [name for name, ready in (('clouds', build_clouds.built), ('radar cross sections', build_rcs.built))
             if not ready()]
    return [('dcs_data_missing', {'what': ', '.join(what)})] if what else []


def run(settings: dict, log=print) -> list[tuple[str, dict]]:
    return installed(settings) + dcs_data(settings) + libraries(settings, log)


if __name__ == '__main__':
    import json
    found = run(json.loads((ROOT / 'settings.json').read_text(encoding='utf-8')))
    print('\n'.join(f'{key}: {values}' for key, values in found) or 'all good')
