"""Airfield geometry for the ATC: runways, their ends and thresholds, and aircraft positions around them.

Adapted from DCS-ATC (atc/facts.py, taxi.py, takeoff.py, approach.py; copied and rewritten with the user's permission,
2026-11-02, nothing imported). DCS-ATC worked in DCS's map metres (x north, z east); here every position is lat/lon
(the export and the hook send lat/lon) and is turned into metres north/east of the airfield's reference point, a flat
frame that is exact enough within the 30 nm an ATC cares about.

Runway data comes from the mission through the hook (dcs/instructor_weather.lua): name, course, length, width, the
centre's and both ends' lat/lon. Headings here are true: DCS's course and velocities are relative to its map's grid
north and are turned to true first (instructor/weather.py true_heading, convergence; atc/desk.py track_of).
"""
import math
from dataclasses import dataclass

from instructor.weather import convergence, runway_ends

M_PER_DEG_LAT = 111_320.0
NM = 1852.0
FT_PER_M = 3.280840
KT_PER_MS = 1.943844
AT_AIRFIELD_M = 5000          # on the ground within this of the field reference point or a runway centre
RUNWAY_SIDE_MARGIN_M = 10     # beyond the runway edge still counts as on the runway
RUNWAY_END_MARGIN_M = 60      # beyond each runway end still counts as on the runway


def local(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """(north, east) metres of a position from a reference point."""
    return ((lat - ref_lat) * M_PER_DEG_LAT,
            (lon - ref_lon) * M_PER_DEG_LAT * math.cos(math.radians(ref_lat)))


def bearing_true(from_n: float, from_e: float, to_n: float, to_e: float) -> float:
    return math.degrees(math.atan2(to_e - from_e, to_n - from_n)) % 360


def angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


@dataclass(frozen=True)
class RunwayEnd:
    number: str              # "27", "09", "27L"
    heading_true: float


@dataclass(frozen=True)
class Runway:
    """One physical runway; `name` is the number of the end at `heading_true`. Centre in the field's frame."""
    name: str
    heading_true: float
    length_m: float
    width_m: float
    north: float
    east: float
    ends: tuple[RunwayEnd, RunwayEnd]


@dataclass
class Track:
    """An aircraft as the ATC sees it: the player or any other traffic. Velocity in m/s (north, east, up)."""
    lat: float
    lon: float
    alt_m: float
    vn: float = 0.0
    ve: float = 0.0
    vu: float = 0.0
    in_air: bool = False
    type_name: str = ''
    callsign: str = ''
    unit: str = ''
    side: int | None = None
    helicopter: bool = False
    track_hint: float | None = None    # a true track when no velocity is known (the aircraft's heading)

    @property
    def ground_speed_kt(self) -> float:
        return math.hypot(self.vn, self.ve) * KT_PER_MS

    @property
    def track_true(self) -> float | None:
        """None when too slow for a meaningful track (and no hint)."""
        if math.hypot(self.vn, self.ve) >= 20 / KT_PER_MS:
            return math.degrees(math.atan2(self.ve, self.vn)) % 360
        return self.track_hint

    @property
    def label(self) -> str:
        return self.callsign or self.unit or self.type_name


class Field:
    """One airfield's geometry from the hook's weather entry (lat, lon, elevation_m, runways)."""

    def __init__(self, name: str, data: dict):
        self.name, self.data = name, data
        self.lat, self.lon = data['lat'], data['lon']
        self.elevation_m = data.get('elevation_m') or 0.0
        self.convergence = convergence(data.get('runways') or [])   # true - grid north (instructor/weather.py)
        self.runways: list[Runway] = []
        for raw in data.get('runways') or []:
            ends = runway_ends([raw])
            if not ends or not all(isinstance(raw.get(k), (int, float)) for k in ('lat', 'lon', 'length_m', 'width_m')):
                continue
            north, east = self.frame_of(raw['lat'], raw['lon'])
            (first, heading), (second, opposite) = ends
            self.runways.append(Runway(first, heading, raw['length_m'], raw['width_m'], north, east,
                                       (RunwayEnd(first, heading), RunwayEnd(second, opposite))))

    def frame_of(self, lat: float, lon: float) -> tuple[float, float]:
        return local(lat, lon, self.lat, self.lon)

    def ends(self) -> list[RunwayEnd]:
        return [end for runway in self.runways for end in runway.ends]

    def threshold(self, end: RunwayEnd) -> tuple[Runway, float, float] | None:
        """(runway, threshold north, east) of a runway end: where takeoffs start and landings in that direction aim."""
        for runway in self.runways:
            if end in runway.ends:
                h = math.radians(end.heading_true)
                half = runway.length_m / 2
                return runway, runway.north - half * math.cos(h), runway.east - half * math.sin(h)
        return None

    def runway_under(self, north: float, east: float) -> Runway | None:
        for runway in self.runways:
            h = math.radians(runway.heading_true)
            dn, de = north - runway.north, east - runway.east
            along = dn * math.cos(h) + de * math.sin(h)
            across = -dn * math.sin(h) + de * math.cos(h)
            if (abs(along) <= runway.length_m / 2 + RUNWAY_END_MARGIN_M
                    and abs(across) <= runway.width_m / 2 + RUNWAY_SIDE_MARGIN_M):
                return runway
        return None

    def at_airfield(self, north: float, east: float) -> bool:
        points = [(0.0, 0.0)] + [(r.north, r.east) for r in self.runways]
        return min(math.hypot(pn - north, pe - east) for pn, pe in points) <= AT_AIRFIELD_M

    def approach_frame(self, end: RunwayEnd, north: float, east: float) -> tuple[float, float] | None:
        """(behind, side) metres for a runway end: behind = distance before the threshold along the extended
        centreline (positive on the approach side), side = offset, positive to the right looking in the landing
        direction."""
        found = self.threshold(end)
        if not found:
            return None
        _, tn, te = found
        h = math.radians(end.heading_true)
        behind = -((north - tn) * math.cos(h) + (east - te) * math.sin(h))
        side = -(north - tn) * math.sin(h) + (east - te) * math.cos(h)
        return behind, side

    def from_approach_frame(self, end: RunwayEnd, behind_m: float, side_m: float) -> tuple[float, float]:
        _, tn, te = self.threshold(end)
        h = math.radians(end.heading_true)
        return (tn - behind_m * math.cos(h) - side_m * math.sin(h), te - behind_m * math.sin(h) + side_m * math.cos(h))

    def where(self, track: Track) -> tuple[float, float]:
        return self.frame_of(track.lat, track.lon)

    def distance_nm(self, track: Track) -> float:
        north, east = self.where(track)
        return math.hypot(north, east) / NM

    def height_ft(self, track: Track) -> float:
        """Height above the field, what a QFE altimeter reads."""
        return (track.alt_m - self.elevation_m) * FT_PER_M
