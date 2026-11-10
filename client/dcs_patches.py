"""DCS install patches, applied and refreshed by DCS_AI_Service with the user's approval (user, 2026-11-02: "Update"
in our program re-applies everything after a DCS update; when DCS changed a file so that a patch no longer fits, the
patch reports "unsupported" and is reviewed by hand).

- speech_mute (Scripts/Speech/speech.lua): silences DCS's own ATC voice (dcs/patches/speech_mute.lua), inserted after
  the line that builds each message's sound.
- radio_bridge (Scripts/UI/RadioCommandDialogPanel/RadioCommandDialogsPanel.lua): the native command bridge
  (dcs/patches/radio_bridge.lua, VAICOM style), appended at the end of the file, where the panel's locals it uses
  (data, selectAndTuneCommunicator) are in scope.

Each patch is one marked block; every other byte of the file is kept (latin-1 round trip). Before a write: one backup
per distinct original file, labelled with the DCS version (backups/dcs/), and a syntax check of the edited file with
DCS's own bin/luae.exe (nothing is written if it fails). The block carries a hash of its source, so a newer block in
this project shows as "outdated" and Update refreshes it. This runs on the pilot's PC (where DCS is), never on a
remote instructor (Raspberry Pi).
"""
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ROOT / 'backups' / 'dcs'
CHECKER = ROOT / 'tools' / 'dcs' / 'check_syntax.lua'
BEGIN, END = '-- DCS_AI_Service BEGIN', '-- DCS_AI_Service END'

ORIGINAL, PATCHED, OUTDATED, UNSUPPORTED, BROKEN, MISSING = (
    'original', 'patched', 'outdated', 'unsupported', 'broken', 'missing')


class PatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Patch:
    name: str
    relative: str          # inside the DCS install
    source: Path           # the block's Lua, in this project
    anchor: str | None     # insert after the only line equal to this (stripped); None = append at the end
    requires: tuple = ()   # regexes the file must contain for the block to work (checked before writing)

    def block(self) -> list[str]:
        text = self.source.read_text(encoding='utf-8')
        return [f'{BEGIN} {self.name} {self.version()} (managed by DCS_AI_Service: Update/Remove from the program)',
                *text.rstrip('\n').split('\n'), END]

    def version(self) -> str:
        return hashlib.sha256(self.source.read_bytes()).hexdigest()[:12]


PATCHES = (
    Patch('speech_mute', 'Scripts/Speech/speech.lua', ROOT / 'dcs' / 'patches' / 'speech_mute.lua',
          'local result = protocol:make(message)', (r'base\.Message\.wMsgATCNull',)),
    Patch('radio_bridge', 'Scripts/UI/RadioCommandDialogPanel/RadioCommandDialogsPanel.lua',
          ROOT / 'dcs' / 'patches' / 'radio_bridge.lua', None,
          (r'^local data = \{', r'^local function selectAndTuneCommunicator\(', r'data\.pComm:sendRawMessage\(',
           r'^function initialize\(')),
)


@dataclass
class Status:
    name: str
    state: str
    detail: str = ''


def dcs_version(dcs: Path) -> str:
    """The installed DCS version from autoupdate.cfg, or 'unknown'."""
    try:
        text = (dcs / 'autoupdate.cfg').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 'unknown'
    found = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    return found.group(1) if found else 'unknown'


def _read(path: Path) -> tuple[list[str], str]:
    text = path.read_bytes().decode('latin-1')   # every byte maps to one character: untouched lines round-trip
    newline = '\r\n' if '\r\n' in text else '\n'
    return text.split(newline), newline


def _block_range(lines: list[str], patch: Patch):
    begins = [i for i, line in enumerate(lines) if line.strip().startswith(f'{BEGIN} {patch.name} ')]
    if not begins:
        return None
    ends = [i for i, line in enumerate(lines) if line.strip() == END and i > begins[0]]
    if len(begins) != 1 or not ends:
        raise PatchError(f'{patch.name}: the block markers are damaged; repair DCS to restore the file')
    return begins[0], ends[0]


def _without_block(lines: list[str], patch: Patch) -> list[str]:
    found = _block_range(lines, patch)
    if found is None:
        return lines
    start = found[0]
    if patch.anchor is None and start > 0 and lines[start - 1] == '':
        start -= 1   # the separator line an appended block brings
    return lines[:start] + lines[found[1] + 1:]


def _anchor_index(lines: list[str], patch: Patch) -> int:
    found = [i for i, line in enumerate(lines) if line.strip() == patch.anchor]
    if len(found) != 1:
        raise PatchError(f'{patch.name}: expected the line "{patch.anchor}" once, found it {len(found)} times: DCS '
                         f'changed {patch.relative}, the patch needs a review')
    return found[0]


def _check_requires(lines: list[str], patch: Patch) -> None:
    text = '\n'.join(lines)
    for pattern in patch.requires:
        if not re.search(pattern, text, re.M):
            raise PatchError(f'{patch.name}: {patch.relative} no longer has "{pattern}": DCS changed it, the patch '
                             f'needs a review')


def _with_block(lines: list[str], patch: Patch) -> list[str]:
    lines = _without_block(lines, patch)
    _check_requires(lines, patch)
    if patch.anchor is None:   # one separator line, then the block: removing both gives back the exact file
        return lines + [''] + patch.block()
    index = _anchor_index(lines, patch)
    indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
    return lines[:index + 1] + [indent + line if line else line for line in patch.block()] + lines[index + 1:]


def inspect(dcs: Path, patch: Patch) -> Status:
    path = dcs / patch.relative
    if not path.exists():
        return Status(patch.name, MISSING, str(path))
    lines, _ = _read(path)
    try:
        found = _block_range(lines, patch)
        if found:
            current = lines[found[0]].strip().split()
            return Status(patch.name, PATCHED if patch.version() in current else OUTDATED)
        original = lines
        _check_requires(original, patch)
        if patch.anchor is not None:
            _anchor_index(original, patch)
    except PatchError as exc:
        return Status(patch.name, BROKEN if 'markers' in str(exc) else UNSUPPORTED, str(exc))
    return Status(patch.name, ORIGINAL)


LUAE: Path | None = None   # tests point this at the real interpreter while patching copies of DCS's files


def _luae(dcs: Path) -> Path:
    lua = LUAE or dcs / 'bin' / 'luae.exe'
    if not lua.exists():
        raise PatchError(f'no Lua interpreter at {lua}: refusing to write unchecked code')
    return lua


def _write(dcs: Path, patch: Patch, lines: list[str], newline: str) -> None:
    path = dcs / patch.relative
    staged = path.with_name(path.name + '.dcs_ai_service.tmp')
    staged.write_bytes(newline.join(lines).encode('latin-1'))
    try:
        result = subprocess.run([str(_luae(dcs)), str(CHECKER), str(staged)], capture_output=True, text=True,
                                timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0:
            raise PatchError(f'{patch.name}: the edited file does not load, nothing was written: '
                             f'{result.stderr.strip() or result.returncode}')
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _backup(dcs: Path, patch: Patch, lines: list[str], newline: str, folder: Path) -> Path:
    data = newline.join(lines).encode('latin-1')
    copy = folder / f'{Path(patch.relative).name}.{dcs_version(dcs)}.{hashlib.sha256(data).hexdigest()[:12]}.orig'
    folder.mkdir(parents=True, exist_ok=True)
    if not copy.exists():
        copy.write_bytes(data)
    return copy


def apply(dcs: Path, patch: Patch, backups: Path = BACKUPS) -> Status:
    """Insert or refresh the block. Safe to repeat (after every DCS update)."""
    if inspect(dcs, patch).state == PATCHED:
        return Status(patch.name, PATCHED, 'already applied')
    path = dcs / patch.relative
    if not path.exists():
        raise PatchError(f'{patch.name}: {path} not found')
    lines, newline = _read(path)
    original = _without_block(lines, patch)
    _backup(dcs, patch, original, newline, backups)
    _write(dcs, patch, _with_block(original, patch), newline)
    return inspect(dcs, patch)


def remove(dcs: Path, patch: Patch) -> Status:
    """Delete the block; DCS's own code stays as it was."""
    path = dcs / patch.relative
    if not path.exists():
        raise PatchError(f'{patch.name}: {path} not found')
    lines, newline = _read(path)
    if _block_range(lines, patch) is not None:
        _write(dcs, patch, _without_block(lines, patch), newline)
    return inspect(dcs, patch)


def status_all(dcs: Path) -> list[Status]:
    return [inspect(dcs, patch) for patch in PATCHES]


def update_all(dcs: Path, backups: Path = BACKUPS) -> list[Status]:
    """The program's "Update": apply or refresh every patch; one that no longer fits is reported, not forced."""
    result = []
    for patch in PATCHES:
        try:
            result.append(apply(dcs, patch, backups))
        except PatchError as exc:
            result.append(Status(patch.name, inspect(dcs, patch).state, str(exc)))
    return result


def remove_all(dcs: Path) -> list[Status]:
    result = []
    for patch in PATCHES:
        try:
            result.append(remove(dcs, patch))
        except PatchError as exc:
            result.append(Status(patch.name, inspect(dcs, patch).state, str(exc)))
    return result


def install_folder(settings: dict) -> Path:
    return Path((settings.get('dcs') or {}).get('install') or 'C:/Program Files/Eagle Dynamics/DCS World')


def main() -> None:
    """utilities\\DCS_PATCHES.cmd status|update|remove"""
    import argparse
    import json
    parser = argparse.ArgumentParser(description='DCS install patches of DCS_AI_Service')
    parser.add_argument('action', choices=['status', 'update', 'remove'])
    parser.add_argument('--dcs', help='DCS install folder (default: settings "dcs.install")')
    args = parser.parse_args()
    dcs = Path(args.dcs) if args.dcs else install_folder(json.loads((ROOT / 'settings.json').read_text(encoding='utf-8')))
    action = {'status': status_all, 'update': update_all, 'remove': remove_all}[args.action]
    print(f'DCS {dcs_version(dcs)} at {dcs}')
    for status in action(dcs):
        print(f'  {status.name}: {status.state}{"  " + status.detail if status.detail else ""}')
    if args.action != 'status':
        print('Restart DCS for the change to take effect.')


if __name__ == '__main__':
    main()
