"""Builds terrains/clouds.json from DCS's Config/Effects/clouds.lua (read-only): each cloud preset's layers as
[bottom m MSL, coverage 0-1], for the ATC's ceiling (atc/conditions.py). Data, so the program needs no DCS install at
run time (it may run on a Raspberry Pi). Parsing adapted from DCS-ATC (atc/weather.py, with the user's permission).

Run: .venv\\Scripts\\python.exe tools\\build_clouds.py [DCS install]   (default: settings "dcs.install")
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'terrains' / 'clouds.json'


def parse(text: str) -> dict[str, list[list[float]]]:
    headers = list(re.finditer(r'\n\t\t(\w+)\s*=\s*\{\s*\n\s*visibleInGUI', text))
    presets = {}
    for i, header in enumerate(headers):
        body = text[header.end():headers[i + 1].start() if i + 1 < len(headers) else len(text)]
        presets[header.group(1)] = [[float(low), float(cov)] for low, cov in re.findall(
            r'altitudeMin\s*=\s*([-\d.]+).*?coverage\s*=\s*([-\d.]+)', body, re.S)]
    return presets


def built(path: Path = OUT) -> bool:
    """The presets are there and readable: tools/setup.py."""
    try:
        return bool(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return False


def main():
    if len(sys.argv) > 1:
        install = Path(sys.argv[1])
    else:
        install = Path(json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))['dcs']['install'])
    presets = parse((install / 'Config' / 'Effects' / 'clouds.lua').read_text(encoding='utf-8', errors='replace'))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(presets, indent=1) + '\n', encoding='utf-8')
    print(f'{len(presets)} cloud presets -> {OUT}')


if __name__ == '__main__':
    main()
