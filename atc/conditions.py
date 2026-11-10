"""Visibility and ceiling at an airfield from the mission weather (dcs/instructor_weather.lua mission()).

Adapted from DCS-ATC (atc/weather.py; copied and rewritten with the user's permission, 2026-11-02). Instrument
conditions decide whether the tower vectors an arrival that asks to land: visibility under 5 km or a ceiling under
1500 ft above the field (the user's choice for DCS-ATC, 2026-10-26, the usual VFR minima).
Cloud presets (DCS 2.7+) keep their layers in Config/Effects/clouds.lua, copied to terrains/clouds.json by
tools/build_clouds.py; the mission's cloud base moves the whole preset up or down. A layer is a ceiling from broken
coverage up (coverage above 0.51 in DCS's presets). Missions without a preset give one layer with a density 0-10.
"""
import json
from functools import lru_cache
from pathlib import Path

from atc.geometry import FT_PER_M

CLOUDS = Path(__file__).resolve().parent.parent / 'terrains' / 'clouds.json'
IMC_VISIBILITY_M = 5000
IMC_CEILING_FT = 1500
BROKEN_COVERAGE = 0.51
LEGACY_BROKEN_DENSITY = 5


@lru_cache(maxsize=1)
def cloud_presets(path: Path = CLOUDS) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def ceiling_m(weather: dict, presets: dict | None = None) -> float | None:
    """Lowest broken-or-worse cloud bottom, metres MSL; None = no ceiling (or unknown)."""
    base, preset = weather.get('cloud_base_m'), weather.get('cloud_preset')
    if preset:
        layers = [(low, cov) for low, cov in (presets if presets is not None else cloud_presets()).get(preset, [])
                  if cov > 0]
        if not layers or not isinstance(base, (int, float)):
            return None
        shift = base - min(low for low, _ in layers)
        broken = [low + shift for low, cov in layers if cov >= BROKEN_COVERAGE]
        return min(broken) if broken else None
    density = weather.get('cloud_density')
    if isinstance(base, (int, float)) and isinstance(density, (int, float)) and density >= LEGACY_BROKEN_DENSITY:
        return base
    return None


def visibility_m(weather: dict) -> float | None:
    """Ground visibility: the general one, reduced by fog or dust when enabled."""
    values = [weather.get('visibility_m')]
    if weather.get('fog'):
        values.append(weather.get('fog_visibility_m'))
    if weather.get('dust'):
        values.append(weather.get('dust_visibility_m'))
    values = [v for v in values if isinstance(v, (int, float)) and v > 0]
    return min(values) if values else None


def conditions(weather: dict | None, elevation_m: float, presets: dict | None = None) -> dict | None:
    """For the tower's board: visibility, ceiling above the field, and whether it is instrument weather."""
    if not weather:
        return None
    vis = visibility_m(weather)
    ceiling = ceiling_m(weather, presets)
    ceiling_ft = None if ceiling is None else max(0, round((ceiling - elevation_m) * FT_PER_M, -2))
    result = {}
    if vis is not None:
        km = vis / 1000
        result['visibility'] = ('more than 10 km' if km >= 10 else f'{km:.1f} km' if km >= 1 else f'{round(vis, -2):.0f} m')
    result['ceiling'] = f'{ceiling_ft:.0f} ft above the field' if ceiling_ft is not None else 'none'
    result['instrument_conditions'] = bool((vis is not None and vis < IMC_VISIBILITY_M)
                                           or (ceiling_ft is not None and ceiling_ft < IMC_CEILING_FT))
    return result
