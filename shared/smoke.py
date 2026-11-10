"""Smoke the agents popped, shared by every agent that could see it (user, 2026-11-05: Tango marked its position
with green smoke and Bison, next to it, could not see it).

DCS gives no way to read smoke back (trigger.action.smoke returns nothing and raises no event), but the smoke is ours
(dcs/instructor_tasks.lua T.smoke, at the group's leader): where, what colour, who and when are kept here. DCS's
smoke lasts about five minutes. It is seen farther than a unit: SEEN_FROM_GROUND_M from the ground, SEEN_FROM_AIR_M
from an aircraft. The terrain is not checked yet.
"""
import time

from instructor import geo

LASTS_S = 300.0
SEEN_FROM_GROUND_M = 5000.0
SEEN_FROM_AIR_M = 10000.0
COLORS = ('green', 'red', 'white', 'orange', 'blue')


def add(state, color: str, lat: float, lon: float, by: str) -> None:
    state.smokes = [s for s in active(state) if s['by'] != by or s['color'] != color]   # popped again: the new one
    state.smokes.append({'color': color, 'lat': lat, 'lon': lon, 'by': by, 'at': time.monotonic()})


def active(state) -> list[dict]:
    now = time.monotonic()
    return [s for s in getattr(state, 'smokes', None) or [] if now - s['at'] < LASTS_S]


def in_sight(state, lat: float, lon: float, range_m: float) -> list[dict]:
    """The smoke seen from (lat, lon), nearest first: colour, who popped it, distance, direction, how long ago; lat /
    lon kept for the desks (never shown to the model)."""
    now, seen = time.monotonic(), []
    for smoke in active(state):
        distance_m = geo.distance_nm(lat, lon, smoke['lat'], smoke['lon']) * 1852
        if distance_m <= range_m:
            seen.append({'color': smoke['color'], 'popped_by': smoke['by'], 'distance_m': round(distance_m, -1),
                         'direction': geo.cardinal(geo.bearing_deg(lat, lon, smoke['lat'], smoke['lon'])),
                         'seconds_ago': round(now - smoke['at']), 'lat': smoke['lat'], 'lon': smoke['lon']})
    return sorted(seen, key=lambda s: s['distance_m'])


def shown(seen: list[dict]) -> list[dict]:
    """For the model: no coordinates."""
    return [{k: v for k, v in s.items() if k not in ('lat', 'lon')} for s in seen]


def find(seen: list[dict], words: str) -> dict | None:
    """The smoke a place name means ("smoke", "the green smoke", "Tango's smoke"): one of the seen ones, nearest
    first; None when the words are not about smoke or none fits."""
    wanted = geo.simplify(words)
    if 'smoke' not in wanted:
        return None
    colors = [c for c in COLORS if c in wanted]
    fitting = [s for s in seen if not colors or s['color'] in colors]
    by_name = [s for s in fitting if geo.simplify(s['popped_by']) and geo.simplify(s['popped_by']) in wanted]
    found = by_name or fitting
    return found[0] if found else None
