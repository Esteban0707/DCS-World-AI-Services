"""Coordinates as a pilot reads them out from the F10 map or the aircraft (user, 2026-11-07: "the artillery needs
coordinates"): MGRS ("38T KM 12345 67890", "38TKM1234567890") or latitude / longitude in degrees, degrees and minutes
or degrees, minutes and seconds ("N42 15.300 E041 47.200", "N 42 15 18 E 41 47 12", "42.2550, 41.7867").

parse(text) -> (lat, lon) or None; to_mgrs(lat, lon) -> "38T KM 12345 67890" (an artillery read-back). WGS84 UTM by
the USGS series (Snyder, Map Projections - A Working Manual, 1987): well under a metre inside a zone. The Norway and
Svalbard zone exceptions are left out (no DCS map is there).
"""
import math
import re

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)
EP2 = E2 / (1 - E2)
K0 = 0.9996
FALSE_EASTING, FALSE_NORTHING_SOUTH = 500000.0, 10000000.0
BANDS = 'CDEFGHJKLMNPQRSTUVWX'            # 8 degree latitude bands from 80 S (X is 12 degrees)
COLUMNS = ('ABCDEFGH', 'JKLMNPQR', 'STUVWXYZ')   # 100 km column letters by zone (1, 2, 3, then again)
ROWS = 'ABCDEFGHJKLMNPQRSTUV'             # 100 km row letters, repeating every 2000 km; even zones start at F

MGRS = re.compile(r'^\s*(\d{1,2})\s*([C-HJ-NP-X])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*(\d*)\s*(\d*)\s*$')
DECIMAL = re.compile(r'^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$')
NUMBERS = r'(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?'
HEMISPHERE_FIRST = re.compile(rf'^\s*([NS])\s*{NUMBERS}\s*[,;]?\s*([EW])\s*{NUMBERS}\s*$')
HEMISPHERE_AFTER = re.compile(rf'^\s*{NUMBERS}\s*([NS])\s*[,;]?\s*{NUMBERS}\s*([EW])\s*$')


def meridian_arc(lat: float) -> float:
    e4, e6 = E2 * E2, E2 * E2 * E2
    return A * ((1 - E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat
                - (3 * E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat)
                + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat)
                - (35 * e6 / 3072) * math.sin(6 * lat))


def central_meridian(zone: int) -> float:
    return math.radians(zone * 6 - 183)


def to_utm(lat: float, lon: float, zone: int | None = None) -> tuple[int, float, float]:
    """(zone, easting, northing); northing with 10 000 km added south of the equator."""
    zone = zone or int((lon + 180) // 6) % 60 + 1
    phi = math.radians(lat)
    n = A / math.sqrt(1 - E2 * math.sin(phi) ** 2)
    t, c = math.tan(phi) ** 2, EP2 * math.cos(phi) ** 2
    a = math.cos(phi) * (math.radians(lon) - central_meridian(zone))
    easting = K0 * n * (a + (1 - t + c) * a ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * EP2) * a ** 5 / 120)
    northing = K0 * (meridian_arc(phi) + n * math.tan(phi) * (
        a * a / 2 + (5 - t + 9 * c + 4 * c * c) * a ** 4 / 24 + (61 - 58 * t + t * t + 600 * c - 330 * EP2) * a ** 6 / 720))
    return zone, easting + FALSE_EASTING, northing + (FALSE_NORTHING_SOUTH if lat < 0 else 0.0)


def from_utm(zone: int, easting: float, northing: float, south: bool) -> tuple[float, float]:
    x, y = easting - FALSE_EASTING, northing - (FALSE_NORTHING_SOUTH if south else 0.0)
    e4, e6 = E2 * E2, E2 * E2 * E2
    mu = y / K0 / (A * (1 - E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256))
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = EP2 * cos1 ** 2, tan1 ** 2
    n1 = A / math.sqrt(1 - E2 * sin1 ** 2)
    r1 = A * (1 - E2) / (1 - E2 * sin1 ** 2) ** 1.5
    d = x / (n1 * K0)
    lat = phi1 - (n1 * tan1 / r1) * (d * d / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * EP2) * d ** 4 / 24
                                     + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * EP2 - 3 * c1 * c1) * d ** 6 / 720)
    lon = central_meridian(zone) + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                                    + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * EP2 + 24 * t1 * t1) * d ** 5 / 120) / cos1
    return math.degrees(lat), math.degrees(lon)


def band_letter(lat: float) -> str:
    return BANDS[min(max(int((lat + 80) // 8), 0), len(BANDS) - 1)]


def to_mgrs(lat: float, lon: float, digits: int = 5) -> str:
    zone, easting, northing = to_utm(lat, lon)
    column = COLUMNS[(zone - 1) % 3][int(easting // 100000) - 1]
    row = ROWS[(int(northing // 100000) + (5 if zone % 2 == 0 else 0)) % 20]
    scale = 10 ** (5 - digits)
    e, n = int(easting % 100000) // scale, int(northing % 100000) // scale
    return f'{zone}{band_letter(lat)} {column}{row} {e:0{digits}d} {n:0{digits}d}'


def from_mgrs(text: str) -> tuple[float, float] | None:
    match = MGRS.match(text.upper())
    if not match:
        return None
    zone, band, column, row, first, second = match.groups()
    zone = int(zone)
    if not 1 <= zone <= 60:
        return None
    digits = first + second
    if len(digits) % 2 or len(digits) > 10 or (second and len(first) != len(second)):
        return None
    half = len(digits) // 2
    scale = 10 ** (5 - half) if half else 0
    east_m = int(digits[:half]) * scale if half else 0
    north_m = int(digits[half:]) * scale if half else 0
    columns = COLUMNS[(zone - 1) % 3]
    if column not in columns:
        return None
    easting = (columns.index(column) + 1) * 100000 + east_m
    northing = ((ROWS.index(row) - (5 if zone % 2 == 0 else 0)) % 20) * 100000 + north_m
    # The row letters repeat every 2000 km: add blocks until the point is in its latitude band.
    band_lat = (BANDS.index(band) - 10) * 8
    south = band < 'N'
    band_northing = math.floor(to_utm(band_lat, zone * 6 - 183, zone)[2] / 100000) * 100000
    while northing < band_northing:
        northing += 2000000
    return from_utm(zone, easting, northing, south)


def degrees(parts: tuple, hemisphere: str) -> float | None:
    numbers = [float(p) for p in parts if p]
    if not numbers or any(n < 0 for n in numbers) or any(n >= 60 for n in numbers[1:]):
        return None
    value = numbers[0] + sum(n / 60 ** i for i, n in enumerate(numbers[1:], start=1))
    return -value if hemisphere in 'SW' else value


def parse(text: str) -> tuple[float, float] | None:
    """(lat, lon) from MGRS or latitude / longitude text; None if it is neither."""
    text = str(text or '').strip()
    found = from_mgrs(text)
    if found:
        return found
    clean = re.sub(r"[°º'′\"″]", ' ', text.upper()).replace('´', ' ')
    lat = lon = None
    match = DECIMAL.match(clean)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
    elif HEMISPHERE_FIRST.match(clean):
        g = HEMISPHERE_FIRST.match(clean).groups()
        lat, lon = degrees(g[1:4], g[0]), degrees(g[5:8], g[4])
    elif HEMISPHERE_AFTER.match(clean):
        g = HEMISPHERE_AFTER.match(clean).groups()
        lat, lon = degrees(g[0:3], g[3]), degrees(g[4:7], g[7])
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon
