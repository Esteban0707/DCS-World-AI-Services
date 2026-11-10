"""Map geometry and the tower's charts: which DCS map, airfields, range and bearing.

Uses terrains/<map>/navigation.json (tools/build_terrains.py). Distances in nautical miles, bearings true.
locate() and relative() are for radar positions (the mission radar, next step); the aircraft's own position is used
only to pick the tower's map and departure field and to decide whether the tower can see it (tower_view.py).
"""
import json
import math
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERRAINS = ROOT / 'terrains'
EARTH_RADIUS_NM = 3440.065
MAP_MARGIN_DEG = 1.5      # an aircraft this far outside a map's known points still counts as on that map
CARDINALS = ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest']


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


def cardinal(bearing: float) -> str:
    return CARDINALS[int((bearing + 22.5) % 360 // 45)]


COMPASS = {'north': 0, 'north-east': 45, 'northeast': 45, 'east': 90, 'south-east': 135, 'southeast': 135,
           'south': 180, 'south-west': 225, 'southwest': 225, 'west': 270, 'north-west': 315, 'northwest': 315}


def offset_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """The point this far along a bearing (flat earth: a few kilometres)."""
    angle = math.radians(bearing_deg)
    north, east = distance_m * math.cos(angle), distance_m * math.sin(angle)
    return lat + north / 111320.0, lon + east / (111320.0 * math.cos(math.radians(lat)))


def shift(place: tuple[float, float], offset_m: float, toward: str,
          heading_deg: float | None) -> tuple[float, float] | str:
    """A place moved offset_m toward a compass direction ("north", "south-west") or a clock position from the pilot's
    nose ("3" = their right, heading_deg the pilot's true heading); unchanged with no offset; else an error text."""
    if not offset_m:
        return place
    word = (toward or '').strip().lower().removesuffix("o'clock").strip()
    if word in COMPASS:
        bearing = COMPASS[word]
    elif word.isdigit() and 1 <= int(word) <= 12:
        if not isinstance(heading_deg, (int, float)):
            return 'the pilot\'s heading is unknown: give a compass direction instead'
        bearing = heading_deg + int(word) * 30
    else:
        return ('toward must be a compass direction (north, north-east, east, south-east, south, south-west, '
                'west, north-west) or a clock position 1 to 12')
    return offset_point(place[0], place[1], bearing % 360, float(offset_m))


@lru_cache(maxsize=None)
def load_maps(folder: Path = TERRAINS) -> tuple:
    return tuple(json.loads(p.read_text(encoding='utf-8')) for p in sorted(folder.glob('*/navigation.json')))


def find_map(lat: float, lon: float, maps=None) -> dict | None:
    """The map whose known points surround this position (the closest airfield decides between overlaps)."""
    candidates = []
    for terrain in maps if maps is not None else load_maps():
        b = terrain.get('bounds')
        if b and (b['lat_min'] - MAP_MARGIN_DEG <= lat <= b['lat_max'] + MAP_MARGIN_DEG and
                  b['lon_min'] - MAP_MARGIN_DEG <= lon <= b['lon_max'] + MAP_MARGIN_DEG):
            placed = [a for a in terrain['airfields'] if 'lat' in a]
            nearest = min((distance_nm(lat, lon, a['lat'], a['lon']) for a in placed), default=math.inf)
            candidates.append((nearest, terrain['map'], terrain))
    return min(candidates, key=lambda c: (c[0], c[1]))[2] if candidates else None


def relative(lat: float, lon: float, airfield: dict) -> dict:
    """Range and bearings between the aircraft and an airfield, both ways."""
    to_field = bearing_deg(lat, lon, airfield['lat'], airfield['lon'])
    from_field = (to_field + 180) % 360
    return {'distance_nm': round(distance_nm(lat, lon, airfield['lat'], airfield['lon']), 1),
            'bearing_to_airfield_true': round(to_field),
            'aircraft_seen_from_airfield': f'{cardinal(from_field)} of the field, bearing {round(from_field)} true'}


def airfield_summary(airfield: dict) -> dict:
    data = {'name': airfield['name']}
    tower = [f'{v["mhz"]} {v["modulation"]}' for band, v in airfield.get('radio', {}).items() if band != 'HF']
    if tower:
        data['tower_frequencies'] = tower
    if airfield.get('ils'):
        data['ils'] = [f'{i.get("callsign", "")} {i.get("mhz")} MHz course {i.get("course_true_deg")} true'.strip()
                       for i in airfield['ils']]
    tacan = [b['channel'] for b in airfield.get('beacons', []) if b.get('channel')]
    if tacan:
        data['tacan'] = tacan
    return data


def locate(lat: float, lon: float, count: int = 3) -> dict:
    terrain = find_map(lat, lon)
    if not terrain:
        return {'map': 'unknown', 'position': {'lat': round(lat, 4), 'lon': round(lon, 4)}}
    placed = sorted((a for a in terrain['airfields'] if 'lat' in a),
                    key=lambda a: distance_nm(lat, lon, a['lat'], a['lon']))
    return {'map': terrain['map'], 'position': {'lat': round(lat, 4), 'lon': round(lon, 4)},
            'nearest_airfields': [{**airfield_summary(a), **relative(lat, lon, a)} for a in placed[:count]]}


def airfield_info(name: str, map_name: str | None = None) -> dict:
    """An airfield by name (any of its callsigns, case and accents loose), on the given map or on any map. Chart data
    only: no range or bearing to the aircraft (that needs radar)."""
    maps = [m for m in load_maps() if m['map'] == map_name] or list(load_maps())
    wanted = simplify(name)
    for terrain in maps:
        for airfield in terrain['airfields']:
            names = [airfield['name'], *airfield.get('callsigns', [])]
            if any(wanted in simplify(n) or simplify(n) in wanted for n in names if simplify(n)):
                return {'map': terrain['map'], **airfield_summary(airfield)}
    return {'error': f'no airfield called "{name}"' + (f' on {map_name}' if map_name else '')}


def simplify(text: str) -> str:
    text = unicodedata.normalize('NFKD', text.lower())
    return ''.join(c for c in text if c.isalnum())
