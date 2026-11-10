"""Build the navigation data of each DCS map (terrains/<map>/navigation.json) from the DCS install, read-only.

Usage (Windows, where DCS lives):  python tools/build_terrains.py --dcs "E:/DCS World" [--map Caucasus ...]
DCS's own Lua interpreter (bin/luae.exe) reads each terrain's Beacons.lua and Radio.lua (tools/dcs/dump_terrain.lua).
Per airfield: name, position, tower/ground/approach frequencies, ILS courses; plus the map's other navaids (VOR,
TACAN, NDB...). Airfield positions come from the airfield's own beacons on or by the runway (localizer, glide slope,
TACAN, airport homer); outer/inner marker homers lie kilometres out on the approach and are used only when nothing
else exists. An airfield with radios but no beacon has no position and is listed as such.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DUMPER = ROOT / 'tools' / 'dcs' / 'dump_terrain.lua'
ON_FIELD = {'ILS_LOCALIZER', 'ILS_GLIDESLOPE', 'ICLS_LOCALIZER', 'ICLS_GLIDESLOPE', 'PRMG_LOCALIZER',
            'PRMG_GLIDESLOPE', 'TACAN', 'AIRPORT_HOMER', 'AIRPORT_HOMER_WITH_MARKER', 'RSBN', 'VOR', 'VOR_DME', 'DME'}
LOCALIZERS = {'ILS_LOCALIZER', 'ICLS_LOCALIZER', 'PRMG_LOCALIZER'}
AIRFIELD_ID = re.compile(r'^(airfield\d+)_')


def dump(luae: Path, dcs: Path, terrain: Path) -> dict:
    result = subprocess.run([str(luae), str(DUMPER), terrain.relative_to(dcs).as_posix()], cwd=dcs,
                            capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
    try:
        return json.loads(result.stdout.decode('utf-8'))
    except UnicodeDecodeError:   # some terrain files are Windows-1252 text (e.g. Nevada's place names)
        return json.loads(result.stdout.decode('cp1252', 'replace'))


def frequency_text(hz: float, kind: str) -> dict:
    return {'khz': round(hz / 1e3, 1)} if kind in ('HOMER', 'AIRPORT_HOMER', 'AIRPORT_HOMER_WITH_MARKER',
                                                   'NAUTICAL_HOMER', 'ILS_FAR_HOMER', 'ILS_NEAR_HOMER',
                                                   'BROADCAST_STATION') else {'mhz': round(hz / 1e6, 3)}


def beacon_record(beacon: dict, names: dict) -> dict | None:
    geo = beacon.get('positionGeo') or {}
    if 'latitude' not in geo:
        return None
    kind = names['beacon_types'].get(str(beacon.get('type')), str(beacon.get('type')))
    record = {'name': beacon.get('display_name', ''), 'type': kind, 'callsign': beacon.get('callsign', ''),
              'lat': round(geo['latitude'], 6), 'lon': round(geo['longitude'], 6),
              **frequency_text(beacon.get('frequency', 0), kind)}
    if kind == 'TACAN' and beacon.get('channel'):
        record['channel'] = f'{beacon["channel"]}X'
    if kind in LOCALIZERS and isinstance(beacon.get('direction'), (int, float)):
        record['course_true_deg'] = round(beacon['direction'] % 360, 1)
    return record


def build(data: dict) -> dict:
    names = data['names']
    by_field: dict[str, list] = {}
    navaids = []
    for beacon in data['beacons']:
        record = beacon_record(beacon, names)
        if not record:
            continue
        match = AIRFIELD_ID.match(beacon.get('beaconId', ''))
        (by_field.setdefault(match.group(1), []) if match else navaids).append(record)

    airfields = []
    for radio in data['radio']:
        match = AIRFIELD_ID.match(radio.get('radioId', ''))
        if not match:
            continue
        field_id = match.group(1)
        callsigns = [values[0] for entry in radio.get('callsign', []) for values in entry.values()]
        beacons = by_field.pop(field_id, [])
        located = [b for b in beacons if b['type'] in ON_FIELD] or beacons
        airfield = {'id': field_id, 'name': (beacons[0]['name'] if beacons else '') or (callsigns or [field_id])[0],
                    'callsigns': callsigns, 'roles': radio.get('role', [])}
        if located:
            airfield['lat'] = round(sum(b['lat'] for b in located) / len(located), 5)
            airfield['lon'] = round(sum(b['lon'] for b in located) / len(located), 5)
        frequencies = radio.get('frequency') or {}   # an empty Lua table arrives as []
        airfield['radio'] = {band: {'mhz': round(pair[1] / 1e6, 3),
                                    'modulation': names['modulations'].get(str(pair[0]), str(pair[0]))}
                             for band, pair in sorted(frequencies.items())} if isinstance(frequencies, dict) else {}
        airfield['ils'] = [{k: b[k] for k in ('callsign', 'mhz', 'course_true_deg') if k in b}
                           for b in beacons if b['type'] in LOCALIZERS]
        airfield['beacons'] = [{k: v for k, v in b.items() if k not in ('name', 'lat', 'lon')} for b in beacons
                               if b['type'] not in LOCALIZERS]
        airfields.append(airfield)
    for leftover in by_field.values():   # beacons of airfields without a radio entry
        navaids.extend(leftover)
    points = [(a['lat'], a['lon']) for a in airfields if 'lat' in a] + [(n['lat'], n['lon']) for n in navaids]
    bounds = ({'lat_min': min(p[0] for p in points), 'lat_max': max(p[0] for p in points),
               'lon_min': min(p[1] for p in points), 'lon_max': max(p[1] for p in points)} if points else None)
    return {'bounds': bounds, 'airfields': airfields, 'navaids': navaids}


def installed_maps(dcs: Path) -> list[Path]:
    folder = dcs / 'Mods' / 'terrains'
    return sorted(p for p in folder.iterdir() if (p / 'Beacons.lua').exists()) if folder.is_dir() else []


def missing(dcs: Path, out: Path = ROOT / 'terrains') -> list[str]:
    """The maps installed in DCS without a readable navigation.json (never built, or a write cut short):
    tools/setup.py."""
    found = []
    for terrain in installed_maps(dcs):
        try:
            if json.loads((out / terrain.name / 'navigation.json').read_text(encoding='utf-8'))['airfields']:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            pass
        found.append(terrain.name)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dcs', required=True, help='DCS World install folder')
    parser.add_argument('--map', action='append', help='terrain folder name (default: all installed)')
    args = parser.parse_args()
    dcs = Path(args.dcs)
    luae = dcs / 'bin' / 'luae.exe'
    terrains = [dcs / 'Mods' / 'terrains' / m for m in args.map] if args.map else installed_maps(dcs)
    for terrain in terrains:
        try:
            navigation = build(dump(luae, dcs, terrain))
        except (RuntimeError, ValueError, KeyError) as exc:
            print(f'{terrain.name}: skipped ({exc})')
            continue
        out = ROOT / 'terrains' / terrain.name
        out.mkdir(parents=True, exist_ok=True)
        navigation = {'map': terrain.name, 'source': f'Mods/terrains/{terrain.name} Beacons.lua + Radio.lua, '
                                                     f'tools/build_terrains.py', **navigation}
        (out / 'navigation.json').write_text(json.dumps(navigation, indent=1, ensure_ascii=False) + '\n',
                                             encoding='utf-8')
        fields = navigation['airfields']
        unplaced = [a['name'] for a in fields if 'lat' not in a]
        print(f'{terrain.name}: {len(fields)} airfields ({len(unplaced)} without position'
              f'{": " + ", ".join(unplaced[:6]) if unplaced else ""}), {len(navigation["navaids"])} navaids')


if __name__ == '__main__':
    main()
