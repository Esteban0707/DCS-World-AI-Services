"""Add or remove the DCS-Instructor line in Saved Games/DCS/Scripts/Export.lua.

Usage: python tools/install_export.py install|uninstall|status [--saved-games "C:/Users/<you>/Saved Games/DCS"]

Only the marked line is touched; other exports (SRS etc.) stay as they are. A backup of the previous file is written
next to it before every change. DCS must be restarted (or the mission reloaded) to pick up the change.
"""
import argparse
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = '-- DCS-Instructor cockpit export'
EXPORT_SCRIPT = ROOT / 'dcs' / 'instructor_export.lua'


def install_line() -> str:
    root = str(ROOT).replace('\\', '/') + '/'
    script = EXPORT_SCRIPT.as_posix()
    return (f"pcall(function() INSTRUCTOR_DIR = [[{root}]]; dofile([[{script}]]) end) {MARKER}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=['install', 'uninstall', 'status'])
    parser.add_argument('--saved-games', default=str(Path.home() / 'Saved Games' / 'DCS'))
    args = parser.parse_args()

    export_lua = Path(args.saved_games) / 'Scripts' / 'Export.lua'
    text = export_lua.read_text(encoding='utf-8') if export_lua.exists() else ''
    lines = [line for line in text.splitlines() if MARKER not in line]
    installed = len(lines) != len(text.splitlines())

    if args.action == 'status':
        print(f'{export_lua}: {"installed" if installed else "not installed"}')
        return
    if args.action == 'install':
        lines.append(install_line())
    elif not installed:
        print(f'{export_lua}: not installed, nothing to do')
        return

    if export_lua.exists():
        backup = export_lua.with_name(f'Export.lua.backup-{time.strftime("%Y%m%d-%H%M%S")}')
        shutil.copy2(export_lua, backup)
        print(f'backup: {backup}')
    export_lua.parent.mkdir(parents=True, exist_ok=True)
    export_lua.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{export_lua}: {args.action}ed')


if __name__ == '__main__':
    main()
