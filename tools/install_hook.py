"""Add or remove the DCS-Instructor GUI hook: Saved Games/DCS/Scripts/Hooks/DCSInstructorHook.lua.

Usage: python tools/install_hook.py install|uninstall|status [--saved-games "C:/Users/<you>/Saved Games/DCS"]

The hook file is one line that loads dcs/instructor_hook.lua from this project (it sends the player's callsign from
the mission; the export API has none). Uninstall deletes that file only. DCS must be restarted to pick it up.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_NAME = 'DCSInstructorHook.lua'
HOOK_SCRIPT = ROOT / 'dcs' / 'instructor_hook.lua'
MARKER = '-- DCS-Instructor GUI hook'


def hook_text() -> str:
    """The project folder goes in as the chunk's argument: DCS gives each hook file its own environment, so a global
    set here would not be seen by the loaded script (seen live 2026-11-01)."""
    root = str(ROOT).replace('\\', '/') + '/'
    return (f'{MARKER} (tools/install_hook.py): loads the project\'s dcs/instructor_hook.lua\n'
            f'local ok, err = pcall(function() assert(loadfile([[{HOOK_SCRIPT.as_posix()}]]))([[{root}]]) end)\n'
            f"if not ok then log.write('DCS-Instructor', log.ERROR, 'hook failed: ' .. tostring(err)) end\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=['install', 'uninstall', 'status'])
    parser.add_argument('--saved-games', default=str(Path.home() / 'Saved Games' / 'DCS'))
    args = parser.parse_args()
    hook = Path(args.saved_games) / 'Scripts' / 'Hooks' / HOOK_NAME

    if args.action == 'status':
        print(f'{hook}: {"installed" if hook.exists() and MARKER in hook.read_text(encoding="utf-8") else "not installed"}')
    elif args.action == 'install':
        if hook.exists() and MARKER not in hook.read_text(encoding='utf-8'):
            raise SystemExit(f'{hook} exists and is not ours: not touched')
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(hook_text(), encoding='utf-8')
        print(f'installed {hook} (restart DCS)')
    else:
        if hook.exists() and MARKER in hook.read_text(encoding='utf-8'):
            hook.unlink()
            print(f'removed {hook}')
        else:
            print('nothing to remove')


if __name__ == '__main__':
    main()
