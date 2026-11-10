"""The data the program builds from the user's own DCS install (tools/setup.py; never shipped: Eagle Dynamics data).

Tests that check behaviour against the real data (Senaki-Kolkhi's chart, the F-5E's cockpit, the RCS table) use
needs(): they run wherever SETUP.cmd has built that data and are skipped in a fresh copy, so no copy of the data is
kept in the tests either.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def dcs_install() -> Path:
    """The DCS folder SETUP.cmd found (settings.json "dcs.install"); DCS's default folder without settings."""
    try:
        return Path(json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))['dcs']['install'])
    except (OSError, ValueError, KeyError, TypeError):
        return Path('C:/Program Files/Eagle Dynamics/DCS World')


DCS = dcs_install()
LUAE = DCS / 'bin' / 'luae.exe'   # DCS's own Lua interpreter, for the tests that run the DCS-side scripts
CAUCASUS = ROOT / 'terrains' / 'Caucasus' / 'navigation.json'
F5E = ROOT / 'modules' / 'F-5E-3'
F5E_CONTROLS = F5E / 'controls.json'
F5E_POSITIONS = F5E / 'positions.json'
RCS_TABLE = ROOT / 'dcs' / 'aircraft_rcs.lua'


def needs(*paths: Path):
    missing = [p.relative_to(ROOT).as_posix() for p in paths if not p.exists()]
    return unittest.skipIf(missing, f'needs data built from DCS ({", ".join(missing)}): run SETUP.cmd')
