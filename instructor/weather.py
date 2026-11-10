"""The tower's weather for its airfield, from the mission (dcs/instructor_weather.lua): ground wind, runway in use,
QNH/QFE and temperature, as a tower gives them (wind and runways magnetic once the local variation is known).

DCS conventions (checked in DCS-ATC's live captures, read for reference only): the wind vector is where the air goes
(x north, z east); a runway's course is minus the heading of its named end, in radians.
Grid north (live 2026-11-02): DCS's x axis, and so its wind, runway courses, velocities and the aircraft's own heading,
point to the map's grid north, which on its transverse Mercator projection differs from true north (about 7 degrees at
Senaki). Positions are lat/lon (true). Mixing the two put the far end of Senaki's runway 120 m sideways, over the
parking. So a runway's true heading comes from its two ends in lat/lon when the hook sends them, and the difference
(convergence = true - grid) turns DCS's other vectors to true. The local variation the export gives (grid heading -
magnetic heading) is relative to grid north; true variation = that + convergence.
"""
import math

from instructor import geo

KT_PER_MS = 1.943844
PA_PER_HPA, PA_PER_INHG = 100.0, 3386.389
CALM_KT = 2.0
KEEP_RUNWAY_TAILWIND_KT = 5.0   # the runway in use is kept until the tailwind on it exceeds this


def wind_from(north_ms: float, east_ms: float) -> tuple[float, float]:
    """(direction the wind comes from, true degrees; speed kt) from the air's movement."""
    return math.degrees(math.atan2(-east_ms, -north_ms)) % 360, math.hypot(north_ms, east_ms) * KT_PER_MS


def qnh_pa(qfe_pa: float, elevation_m: float) -> float:
    """Sea-level pressure from field pressure with the standard atmosphere (ICAO barometric formula)."""
    return qfe_pa / (1 - 2.25577e-5 * elevation_m) ** 5.25588


MIN_END_SPAN_M = 100.0   # runway ends closer than this (bad data, a test double) cannot give a heading


def grid_heading(runway: dict) -> float | None:
    course = runway.get('course')
    return math.degrees(-course) % 360 if isinstance(course, (int, float)) else None


def true_heading(runway: dict) -> float | None:
    """The named end's true heading: from its two ends in lat/lon, else (older hook data) the grid course."""
    points = [runway.get(k) for k in ('start_lat', 'start_lon', 'end_lat', 'end_lon')]
    if all(isinstance(v, (int, float)) for v in points) and \
            geo.distance_nm(*points) * 1852 >= MIN_END_SPAN_M:
        return geo.bearing_deg(*points)
    return grid_heading(runway)


def convergence(runways: list[dict]) -> float:
    """True minus grid north at the field (degrees), from its runways; 0 when unknown."""
    for runway in runways or []:
        grid, true = grid_heading(runway), true_heading(runway)
        if grid is not None and true is not None and true != grid:
            return (true - grid + 180) % 360 - 180
    return 0.0


def to_true(north: float, east: float, convergence_deg: float) -> tuple[float, float]:
    """A DCS vector (grid north/east components) turned to true north/east."""
    c = math.radians(convergence_deg)
    return north * math.cos(c) - east * math.sin(c), north * math.sin(c) + east * math.cos(c)


def runway_ends(runways: list[dict]) -> list[tuple[str, float]]:
    """(end number, true heading) for both ends of each runway whose DCS name is a number ("09", "27L")."""
    ends = []
    for runway in runways:
        name = str(runway.get('name', ''))
        digits = name.rstrip('LRC')
        heading = true_heading(runway)
        if not digits.isdigit() or heading is None:
            continue
        side = {'L': 'R', 'R': 'L', 'C': 'C'}.get(name[len(digits):], '')
        opposite = (int(digits) + 18 - 1) % 36 + 1
        ends += [(f'{int(digits):02d}{name[len(digits):]}', heading), (f'{opposite:02d}{side}', (heading + 180) % 360)]
    return ends


def components(heading: float, wind_dir: float, wind_kt: float) -> tuple[float, float]:
    """(headwind, crosswind) in kt on a runway heading; negative headwind = tailwind."""
    angle = math.radians(wind_dir - heading)
    return wind_kt * math.cos(angle), abs(wind_kt * math.sin(angle))


def runway_in_use(ends, wind_dir: float, wind_kt: float, current: str | None = None):
    """The end with the most headwind; the current one is kept in calm or until its tailwind is too strong."""
    if not ends:
        return None
    by_number = dict(ends)
    if current in by_number:
        headwind = components(by_number[current], wind_dir, wind_kt)[0]
        if wind_kt < CALM_KT or -headwind <= KEEP_RUNWAY_TAILWIND_KT:
            return current, by_number[current]
    return max(ends, key=lambda end: components(end[1], wind_dir, wind_kt)[0])


def magnetic(true_deg: float, variation: float | None) -> int:
    return round((true_deg - (variation or 0.0)) % 360) or 360


def report(field: dict, variation: float | None, current_runway: str | None = None) -> dict:
    """What the tower says about its weather. variation: grid heading - magnetic heading from the export (east
    positive), None if unknown (headings stay true)."""
    reference = 'magnetic' if variation is not None else 'true'
    conv = convergence(field.get('runways') or [])
    variation = None if variation is None else variation + conv     # true variation, for true headings
    data = {}
    north, east = field.get('wind_north'), field.get('wind_east')
    wind_dir = wind_kt = None
    if isinstance(north, (int, float)) and isinstance(east, (int, float)):
        wind_dir, wind_kt = wind_from(*to_true(north, east, conv))
        data['wind'] = ('calm' if wind_kt < CALM_KT else
                        f'from {magnetic(wind_dir, variation):03d} {reference} at {round(wind_kt)} kt')
    ends = runway_ends(field.get('runways') or [])
    if ends and wind_dir is not None:
        number, heading = runway_in_use(ends, wind_dir, wind_kt, current_runway)
        headwind, crosswind = components(heading, wind_dir, wind_kt)
        data['runway_in_use'] = {'runway': number, f'heading_{reference}': magnetic(heading, variation),
                                 'headwind_kt' if headwind >= 0 else 'tailwind_kt': round(abs(headwind)),
                                 'crosswind_kt': round(crosswind)}
    pressure, elevation = field.get('pressure_pa'), field.get('elevation_m')
    if isinstance(pressure, (int, float)) and isinstance(elevation, (int, float)):
        qnh = qnh_pa(pressure, elevation)
        data['qnh'] = f'{round(qnh / PA_PER_HPA)} hPa, {qnh / PA_PER_INHG:.2f} inHg'
        data['qfe'] = f'{round(pressure / PA_PER_HPA)} hPa, {pressure / PA_PER_INHG:.2f} inHg'
        data['field_elevation_ft'] = round(elevation * 3.280840)
    temperature = field.get('temperature_k')
    if isinstance(temperature, (int, float)):
        data['temperature_c'] = round(temperature - 273.15)
    if ends:
        data['runways'] = [number for number, _ in ends]
    return data
