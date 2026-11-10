"""Second step of SETUP.cmd (run by .venv's Python after tools/bootstrap.ps1): everything the program needs on this PC.

Usage: .venv\\Scripts\\python.exe tools/setup.py   (safe to run again: each step is checked, and redone when missing
or left incomplete by an interrupted run)

1. settings.json from settings.example.json (language from Windows: Spanish if Windows is in Spanish, else English).
2. The DCS install folder (Windows registry, else asked) and Saved Games/DCS.
3. The voice model (tools/setup_tts.py, downloaded once from Hugging Face and proven by a test synthesis).
4. Everything that comes from the user's own DCS install (Eagle Dynamics data, never shipped): per aircraft owned,
   the cockpit tables (tools/build_module.py, build_positions.py), the training lessons (tools/build_training.py, one
   per training mission) and the manuals library (tools/build_library.py); then the maps' airfields and navaids
   (tools/build_terrains.py), the cloud presets (build_clouds.py) and the aircraft radar cross sections
   (build_rcs.py).
5. Asked first: the export line and the GUI hook in Saved Games/DCS (tools/install_export.py, tools/install_hook.py).
6. Asked first, optional: the DCS install patches (client/dcs_patches.py: DCS's own ATC voice muted, native radio
   menu bridge).
7. Asked first: the push-to-talk button (tools/ptt_pick.py) and the ChatGPT sign-in (tools/chatgpt_login.py).
8. A last check of what stays manual: SRS and Google Chrome.

Every step is checked again after it ran. The end says what failed (exit code 1: "Setup INCOMPLETE") and what the
user still has to do before flying (a step skipped, a program to install).
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared import settings as settings_file   # noqa: E402

PYTHON = sys.executable
EXAMPLE = ROOT / 'settings.example.json'
DCS_KEYS = (r'Software\Eagle Dynamics\DCS World', r'Software\Eagle Dynamics\DCS World OpenBeta')
STEAM_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 223750'
SAVED_GAMES_NAMES = ('DCS', 'DCS.openbeta')


class Report:
    """failed: a step that did not work (setup incomplete). todo: what the user still has to do before flying."""

    def __init__(self):
        self.failed: list[str] = []
        self.todo: list[str] = []

    def fail(self, text: str) -> None:
        self.failed.append(text)
        print(f'  FAILED: {text}')

    def later(self, text: str) -> None:
        self.todo.append(text)
        print(f'  still to do: {text}')

    def summary(self) -> int:
        print('\n' + '=' * 72)
        if self.failed:
            print('Setup INCOMPLETE. What failed (fix it and run SETUP.cmd again, it continues from here):')
            print('\n'.join(f'  - {text}' for text in self.failed))
        else:
            print('Setup complete.')
        if self.todo:
            print('Before flying:')
            print('\n'.join(f'  - {text}' for text in self.todo))
        if not self.failed:
            print('Start the program with DCS-Instructor.bat (open DCS yourself).')
        return 1 if self.failed else 0


def step(title: str) -> None:
    print(f'\n--- {title}')


def ask(question: str, default: bool) -> bool:
    hint = 'Y/n' if default else 'y/N'
    while True:
        answer = input(f'{question} [{hint}] ').strip().lower()
        if not answer:
            return default
        if answer in ('y', 'yes', 's', 'si', 'sí'):
            return True
        if answer in ('n', 'no'):
            return False


def run(*args: str) -> bool:
    """A project tool with this Python, in the project folder; its output goes straight to this window."""
    return subprocess.run([PYTHON, *args], cwd=ROOT).returncode == 0


# --- 1. settings ------------------------------------------------------------------------------------------------
def windows_locale() -> str:
    """The language Windows is shown in, e.g. "es-ES"; with the regional variant of the region setting when both are
    the same language (Spanish Windows, region Mexico: "es-MX"). "" when unknown. The region alone is not enough: a
    Spanish Windows may have the region set to the United States (seen on the author's PC)."""
    if sys.platform != 'win32':
        return ''
    import ctypes
    kernel32 = ctypes.windll.kernel32
    ui, region = ctypes.create_unicode_buffer(85), ctypes.create_unicode_buffer(85)
    if not kernel32.LCIDToLocaleName(kernel32.GetUserDefaultUILanguage(), ui, 85, 0):
        return ''
    kernel32.GetUserDefaultLocaleName(region, 85)
    same_language = region.value.split('-')[0].lower() == ui.value.split('-')[0].lower()
    return region.value if same_language else ui.value


def language_settings(locale_name: str) -> dict:
    """Spanish for a Spanish Windows, English otherwise; the dictation keeps the regional variant."""
    if locale_name.lower().startswith('es'):
        return {'language': 'es', 'voice': {'language': 'es'}, 'speech': {'language': locale_name}}
    return {'language': 'en', 'voice': {'language': 'en'}, 'speech': {'language': 'en-US'}}


def ensure_settings() -> dict:
    step('Settings')
    if settings_file.PATH.exists():
        print(f'  {settings_file.PATH.name} already exists: kept')
        return settings_file.load()
    shutil.copyfile(EXAMPLE, settings_file.PATH)
    settings = settings_file.update(language_settings(windows_locale()))
    print(f'  created {settings_file.PATH.name} (language: {settings["language"]})')
    return settings


# --- 2. DCS -----------------------------------------------------------------------------------------------------
def is_dcs_install(folder: Path) -> bool:
    return (folder / 'bin' / 'luae.exe').exists() or (folder / 'bin' / 'DCS.exe').exists()


def registry_dcs_folders() -> list[Path]:
    if sys.platform != 'win32':
        return []
    import winreg
    found = []
    for hive, key, value in [(winreg.HKEY_CURRENT_USER, k, 'Path') for k in DCS_KEYS] + \
                            [(winreg.HKEY_LOCAL_MACHINE, STEAM_KEY, 'InstallLocation')]:
        try:
            with winreg.OpenKey(hive, key) as handle:
                found.append(Path(winreg.QueryValueEx(handle, value)[0]))
        except OSError:
            continue
    return found


def ensure_dcs(settings: dict, report: Report) -> dict:
    step('DCS World')
    dcs = settings.get('dcs') or {}
    install = Path(dcs['install']) if dcs.get('install') else None
    if not (install and is_dcs_install(install)):
        install = next((p for p in registry_dcs_folders() if is_dcs_install(p)), None)
        while not install:
            typed = input('  DCS World install folder not found. Paste its path (the folder that holds bin\\): ')
            candidate = Path(typed.strip().strip('"'))
            if is_dcs_install(candidate):
                install = candidate
            else:
                print(f'  {candidate} does not look like a DCS install (no bin\\DCS.exe)')
    saved = Path(dcs['saved_games']) if dcs.get('saved_games') else None
    if not (saved and saved.exists()):
        saved = next((Path.home() / 'Saved Games' / n for n in SAVED_GAMES_NAMES
                      if (Path.home() / 'Saved Games' / n).exists()), None)
    changes = {'install': install.as_posix()}
    if saved and saved.name != 'DCS':   # empty means the default Saved Games/DCS
        changes['saved_games'] = saved.as_posix()
    settings = settings_file.update({'dcs': changes})
    print(f'  install: {install}')
    print(f'  saved games: {saved or "not found"}')
    if not saved:
        report.later('start DCS once (it creates Saved Games\\DCS), then run SETUP.cmd again')
    return settings


# --- 3. voice ---------------------------------------------------------------------------------------------------
def ensure_voice(settings: dict, report: Report) -> None:
    step('Voice model (Supertonic 3)')
    from tools import setup_tts
    reason = setup_tts.ready()
    if not reason:
        print('  ready')
        return
    print(f'  {reason}: downloading from Hugging Face (resumes a cut download), then one test per voice...')
    run('tools/setup_tts.py', settings.get('voice', {}).get('language') or settings['language'])
    reason = setup_tts.ready()
    if reason:
        report.fail(f'voice model not ready ({reason}); check the internet connection')
    else:
        print('  ready')


# --- 4. aircraft data from DCS ----------------------------------------------------------------------------------
def ensure_aircraft_data(settings: dict, report: Report) -> None:
    step('Aircraft data from your DCS install')
    from tools import build_module, build_training
    from tools.build_library import stale
    from tools.preflight import libraries
    install = Path(settings['dcs']['install'])
    modules = []   # (module folder, its folder under Mods/aircraft, module.json) of the aircraft installed in DCS
    for module_json in sorted((ROOT / 'modules').glob('*/module.json')):
        config = json.loads(module_json.read_text(encoding='utf-8'))
        source = config.get('dcs_source_folder', '')
        if not source:
            continue
        if (install / source).exists():
            modules.append((module_json.parent, Path(source).name, config))
        else:
            print(f'  {module_json.parent.name}: aircraft not installed in DCS ({source}), skipped')
    for module, source, _ in modules:
        missing = build_module.missing(module)   # the lessons name their controls from controls.json: first
        if missing:
            print(f'  {module.name}: cockpit data to build ({", ".join(missing)})')
            if run('tools/build_module.py', '--dcs', str(install), '--source', source, '--module', module.name):
                run('tools/build_positions.py', module.name)
            missing = build_module.missing(module)
        if missing:
            report.fail(f'{module.name} cockpit data not built: {", ".join(missing)}')
            continue
        print(f'  {module.name}: cockpit data ready')
        missing = build_training.missing(install, source, module)
        if missing:
            print(f'  {module.name}: {len(missing)} training lessons to build')
            run('tools/build_training.py', '--dcs', str(install), '--source', source, '--module', module.name)
            missing = build_training.missing(install, source, module)
        if missing:
            report.fail(f'{module.name} training lessons not built: {", ".join(missing)}')
        else:
            print(f'  {module.name}: {len(build_training.missions(install, source))} training lessons ready')
    errors = {values['module']: values['error'] for _, values in libraries(settings, log=lambda t: print(f'  {t}'))}
    for module, _, config in modules:
        if not config.get('library'):
            continue
        reason = stale(module)
        if reason:
            report.fail(f'{module.name} manuals library not built: {errors.get(module.name, reason)}')
        else:
            print(f'  {module.name}: manuals library ready')


def ensure_world_data(settings: dict, report: Report) -> None:
    """Maps (airfields, frequencies, navaids), cloud presets, aircraft radar cross sections: from the DCS install."""
    step('Maps, weather and radar data from your DCS install')
    from tools import build_clouds, build_rcs, build_terrains
    install = Path(settings['dcs']['install'])
    maps = [t.name for t in build_terrains.installed_maps(install)]
    missing = build_terrains.missing(install)
    if missing:
        print(f'  maps to build: {", ".join(missing)}')
        run('tools/build_terrains.py', '--dcs', str(install), *[a for m in missing for a in ('--map', m)])
        missing = build_terrains.missing(install)
    if not maps or len(missing) == len(maps):
        report.fail('no map data built: the ATC and the instructor know no airfield')
    elif missing:   # DCS's files of one map could not be read: the others still work
        report.later(f'map data could not be built for {", ".join(missing)}: no ATC there (see the error above)')
    print(f'  maps ready: {len(maps) - len(missing)} of {len(maps)}')
    for name, tool, ready in (('cloud presets', 'tools/build_clouds.py', build_clouds.built),
                              ('aircraft radar cross sections', 'tools/build_rcs.py', build_rcs.built)):
        if not ready():
            run(tool, str(install))
        if ready():
            print(f'  {name} ready')
        else:
            report.fail(f'{name} not built')


# --- 5. Saved Games/DCS -----------------------------------------------------------------------------------------
def moved(folder: Path) -> list[tuple[str, dict]]:
    """Export line or hook installed from another copy of this folder (the project was moved): installed again."""
    from tools import install_export, install_hook
    here = ROOT.as_posix() + '/'
    found = []
    for key, path, marker in (('export_missing', folder / 'Scripts' / 'Export.lua', install_export.MARKER),
                              ('hook_missing', folder / 'Scripts' / 'Hooks' / install_hook.HOOK_NAME,
                               install_hook.MARKER)):
        text = path.read_text(encoding='utf-8', errors='replace') if path.exists() else ''
        if marker in text and here not in text:
            found.append((key, {'path': str(path)}))
    return found


def ensure_export_and_hook(settings: dict, report: Report) -> None:
    step('DCS export and hook (Saved Games/DCS/Scripts)')
    from tools.preflight import installed, saved_games
    folder = saved_games(settings)
    missing = installed(settings) + moved(folder)
    if not missing:
        print('  already installed')
        return
    print(f'  The instructor reads the cockpit through one line in {folder}\\Scripts\\Export.lua (other exports such\n'
          f'  as SRS are kept, a backup is written first) and one hook file in Scripts\\Hooks\\. Both are removed\n'
          f'  with: tools\\install_export.py uninstall and tools\\install_hook.py uninstall.')
    if not ask('  Install them now?', True):
        report.later('install the DCS export and hook: run SETUP.cmd again and answer yes')
        return
    for key, _ in missing:
        tool = 'tools/install_export.py' if key == 'export_missing' else 'tools/install_hook.py'
        run(tool, 'install', '--saved-games', str(folder))
    for _, values in installed(settings) + moved(folder):
        report.fail(f'not installed: {values["path"]}')


# --- 6. DCS install patches -------------------------------------------------------------------------------------
def offer_dcs_patches(settings: dict, report: Report) -> None:
    step('Optional: DCS install patches')
    from client import dcs_patches
    install = Path(settings['dcs']['install'])
    states = dcs_patches.status_all(install)
    for status in states:
        print(f'  {status.name}: {status.state}')
    if all(status.state == dcs_patches.PATCHED for status in states):
        return
    print('  These edit two files inside the DCS install: speech.lua (mutes DCS\'s own ATC voice, so only this\n'
          '  program\'s ATC talks) and RadioCommandDialogsPanel.lua (lets the agents use the native radio menu).\n'
          '  Backups go to backups\\dcs\\, remove with utilities\\DCS_PATCHES.cmd remove. A DCS update undoes them\n'
          '  (run DCS_PATCHES.cmd update again). Modified DCS files may fail the integrity check of some\n'
          '  multiplayer servers. Close DCS first.')
    if not ask('  Apply them now?', False):
        return
    for status in dcs_patches.update_all(install):
        print(f'  {status.name}: {status.state}{"  " + status.detail if status.detail else ""}')
        if status.state != dcs_patches.PATCHED:
            report.fail(f'DCS patch {status.name}: {status.state} {status.detail}'.strip())


# --- 7. push-to-talk and the brain ------------------------------------------------------------------------------
def offer_ptt(report: Report) -> None:
    step('Push-to-talk button')
    ptt = settings_file.load().get('ptt')
    if ptt:
        print(f'  already set: {ptt["joystick"]}, button {ptt["button"]} (change it with utilities\\PTT_PICK.cmd)')
        return
    print('  Pick a joystick button that is NOT your SRS push-to-talk.')
    if ask('  Choose it now?', True):
        run('tools/ptt_pick.py')
    if 'ptt' not in settings_file.load():
        report.later('choose the push-to-talk button: utilities\\PTT_PICK.cmd')


def offer_sign_in(report: Report) -> None:
    step('Brain: Sign in with ChatGPT')
    if (settings_file.load().get('brain') or {}).get('account'):
        print('  already signed in (sign out with utilities\\CHATGPT_LOGIN.cmd --sign-out)')
        return
    print('  The agents think with your own ChatGPT plan (OpenAI\'s "Sign in with ChatGPT" for open-source apps,\n'
          '  no API key). Their answers count toward your plan usage. A browser window opens to sign in.')
    if ask('  Sign in now?', True):
        run('tools/chatgpt_login.py')
    if not (settings_file.load().get('brain') or {}).get('account'):
        report.later('sign in with ChatGPT: utilities\\CHATGPT_LOGIN.cmd')


# --- 8. what stays manual ---------------------------------------------------------------------------------------
def check_manual(settings: dict, report: Report) -> None:
    step('Other programs')
    from client.chrome_dictation import chrome_path
    from instructor.srs import find_external_audio
    srs = find_external_audio(settings.get('srs', {}).get('path', ''))
    chrome = chrome_path()
    print(f'  SRS (DCS-SimpleRadio-Standalone): {srs.parent if srs else "not found"}')
    print(f'  Google Chrome (dictation):        {chrome or "not found"}')
    if not srs:
        report.later('install SRS (DCS-SimpleRadio-Standalone)')
    if not chrome:
        report.later('install Google Chrome')


def main() -> int:
    report = Report()
    settings = ensure_settings()
    settings = ensure_dcs(settings, report)
    ensure_voice(settings, report)
    ensure_aircraft_data(settings, report)
    ensure_world_data(settings, report)
    ensure_export_and_hook(settings, report)
    offer_dcs_patches(settings, report)
    offer_ptt(report)
    offer_sign_in(report)
    check_manual(settings_file.load(), report)
    return report.summary()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nSetup INCOMPLETE: cancelled. Run SETUP.cmd again to continue.')
        sys.exit(1)
