"""Where on the airfield the aircraft is, as someone at the tower window sees it: on a runway (near which end), at the
end of a runway (the holding point area), or elsewhere on the field (apron, taxiway).

Runway geometry comes from the mission through the hook (dcs/instructor_weather.lua: centre lat/lon, course, length,
width). The geometry rules are adapted from DCS-ATC's taxi code (read for reference only, nothing imported): offsets
along and across the runway, with margins beyond its edges and ends.
"""
import math

from instructor.weather import runway_ends

M_PER_DEG_LAT = 111_320.0
SIDE_MARGIN_M = 10          # beyond the runway edge still counts as on the runway
END_MARGIN_M = 60           # beyond each end still counts as on the runway
NEAR_END_M = 400            # within this of a runway end (along the runway) is "near the threshold"
HOLDING_AREA_SIDE_M = 200   # off the runway but this close to its side, near an end: the holding point area


def offsets(lat: float, lon: float, runway: dict, heading: float) -> tuple[float, float]:
    """(along, across) in metres from the runway centre, along = towards `heading` (true degrees)."""
    north = (lat - runway['lat']) * M_PER_DEG_LAT
    east = (lon - runway['lon']) * M_PER_DEG_LAT * math.cos(math.radians(runway['lat']))
    h = math.radians(heading)
    return north * math.cos(h) + east * math.sin(h), -north * math.sin(h) + east * math.cos(h)


def position_on_field(lat: float, lon: float, runways: list[dict]) -> str | None:
    """'on runway 27 near its threshold', 'on runway 27/09 mid-field', 'near the threshold of runway 27, off the
    runway (holding point area)', 'elsewhere on the field (apron or taxiway)'; None without runway geometry."""
    placed = [r for r in runways if all(isinstance(r.get(k), (int, float)) for k in ('lat', 'lon', 'length_m',
                                                                                     'width_m'))]
    found = None
    for runway in placed:
        ends = runway_ends([runway])
        if not ends:
            continue
        half_length, half_width = runway['length_m'] / 2, runway['width_m'] / 2
        # Along the first end's heading, its threshold (where takeoffs on it start) is at along = -half_length.
        (first, heading), (second, _) = ends
        along, across = offsets(lat, lon, runway, heading)
        near = first if along < -half_length + NEAR_END_M else second if along > half_length - NEAR_END_M else None
        on_runway = abs(along) <= half_length + END_MARGIN_M and abs(across) <= half_width + SIDE_MARGIN_M
        if on_runway:
            return (f'on runway {near} near its threshold' if near else
                    f'on runway {first}/{second}, between its ends')
        if near and abs(across) <= half_width + HOLDING_AREA_SIDE_M and abs(along) <= half_length + NEAR_END_M:
            found = found or f'near the threshold of runway {near}, off the runway (holding point area)'
    if found:
        return found
    return 'elsewhere on the field (apron or taxiway)' if placed else None
