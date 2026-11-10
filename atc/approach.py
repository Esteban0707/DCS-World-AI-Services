"""Arrivals: landing clearance and radar vectors to final, decided in code.

Adapted from DCS-ATC (atc/approach.py; copied and rewritten with the user's permission, 2026-11-02). The tower vectors
an arrival when the pilot asks, or when they ask to land in instrument weather (atc/conditions.py). Vectors lead to a
30-degree intercept of the extended centreline, joining it at least 4 nm from the threshold, with heights for a
3-degree path (about 300 ft per nm) on QFE. The landing clearance is given on final when the runway is free and no
arrival is ahead; a cleared arrival is sent around inside 2 nm if the runway is blocked. Heights never go back up, and
an arrival that is close in or on the wrong side is first sent out to a downwind 3 nm abeam so it never crosses the
field (DCS-ATC's Kutaisi replay). Terrain is not modelled: vector heights never go below MIN_VECTOR_FT above the field.

Frame (atc/geometry.py approach_frame): "behind" is the distance before the threshold along the extended centreline,
"side" the offset from it, positive to the right looking in the landing direction.
"""
import math
from dataclasses import dataclass

from atc import ground
from atc.geometry import NM, Field, RunwayEnd, Track, angle_diff, bearing_true
from atc.phrases import Speech
from instructor.weather import magnetic

FT_PER_NM_3DEG = 318
MIN_VECTOR_FT = 2000
MAX_VECTOR_FT = 4000
INTERCEPT_ANGLE_DEG = 30
JOIN_MIN_NM = 4          # an intercept must join the centreline at least this far out
BASE_BEHIND_NM = 10      # where the tower sends an arrival that is too close or on the wrong side
BASE_SIDE_NM = 3
DIRECT_BEHIND_NM = 12    # far out on the approach side: straight to a point on the centreline this far out
ESTABLISHED_SIDE_NM = 0.3
ESTABLISHED_TRACK_DEG = INTERCEPT_ANGLE_DEG + 5
FINAL_MAX_NM = 12        # "on final": before the threshold, within this...
FINAL_CONE_DEG = 15      # ...within this angle of the centreline seen from the threshold...
FINAL_TRACK_DEG = 45     # ...and flying roughly along it
CLEAR_WITHIN_NM = 6      # the tower clears a requested landing on final within this
GO_AROUND_WITHIN_NM = 2  # cleared, runway blocked, this close: go around
VECTOR_REPEAT_DEG = 15   # a new heading is given when the needed one differs this much from the last one...
VECTOR_MIN_GAP_S = 15    # ...but not more often than this


def on_final(field: Field, end: RunwayEnd, player: Track) -> float | None:
    """Distance to the threshold (nm) when the aircraft is airborne on final, else None."""
    if not player.in_air:
        return None
    frame = field.approach_frame(end, *field.where(player))
    if not frame:
        return None
    behind, side = frame
    if not 0 < behind / NM <= FINAL_MAX_NM or math.degrees(math.atan2(abs(side), behind)) > FINAL_CONE_DEG:
        return None
    track = player.track_true
    if track is not None and angle_diff(track, end.heading_true) > FINAL_TRACK_DEG:
        return None
    return behind / NM


def landing_clearance(field: Field, end: RunwayEnd | None, player: Track, traffic: list[Track] | None,
                      wind: str | None, qfe: str | None, speech: Speech) -> dict:
    """{'status': 'cleared' | 'continue' | 'hold' | 'unable', 'reason', 'say'}; traffic holds add 'traffic_key'."""
    unable = speech.say('unable')
    if not player.in_air:
        return {'status': 'unable', 'reason': 'the aircraft is on the ground', 'say': unable}
    if not end or not field.threshold(end):
        return {'status': 'unable', 'reason': 'no runway in use (no wind or runway data from the mission)', 'say': unable}
    runway_words = speech.runway(end.number)
    distance = on_final(field, end, player)
    if distance is None:
        # Everything for the approach in one phrase (DCS-ATC: the model left out wind and QFE when asked to add them).
        parts = [runway_words] + [p for p in (wind, qfe and speech.pressure('qfe', qfe)) if p]
        parts.append(speech.say('report_final'))
        return {'status': 'continue', 'reason': 'the aircraft is not on final yet', 'say': ', '.join(parts)}
    if traffic is None:
        return {'status': 'unable', 'reason': 'no traffic data from the mission', 'say': unable}
    occupant = ground.runway_occupant(field, end, traffic)
    if occupant:
        note = speech.say('traffic_on_runway', type=occupant.type_name)
        return {'status': 'hold', 'reason': 'traffic on the runway', 'traffic_key': ground.traffic_key(occupant, 'runway'),
                'say': f'{speech.say("continue_approach")}, {note}'}
    ahead = [a for a in ground.arrivals_on_final(field, end, traffic, distance) if a.distance_nm < distance]
    if ahead:
        note = ground.traffic_note(ahead[0], speech)
        return {'status': 'hold', 'reason': 'traffic ahead on final',
                'traffic_key': ground.traffic_key(ahead[0].aircraft, 'final'),
                'say': f'{speech.say("continue_approach")}, {speech.say("number_two")}, {note}'}
    say = speech.say('cleared_to_land', runway=runway_words)
    return {'status': 'cleared', 'say': f'{wind}, {say}' if wind else say, 'distance_nm': round(distance, 1)}


@dataclass
class Vector:
    phase: str                  # 'positioning' | 'intercept' | 'established'
    heading_true: float | None  # None when established
    height_ft: int | None       # QFE height to fly; None when established
    behind_nm: float


def _height(remaining_nm: float) -> int:
    """3-degree path height for the distance still to fly to the threshold, in 500 ft steps, within the limits."""
    return int(min(MAX_VECTOR_FT, max(MIN_VECTOR_FT, round(remaining_nm * FT_PER_NM_3DEG / 500) * 500)))


def vector(field: Field, end: RunwayEnd, player: Track) -> Vector | None:
    north, east = field.where(player)
    frame = field.approach_frame(end, north, east)
    if not frame:
        return None
    b, s = frame[0] / NM, frame[1] / NM
    track = player.track_true
    if (1 < b <= DIRECT_BEHIND_NM + 3 and abs(s) <= ESTABLISHED_SIDE_NM and track is not None
            and angle_diff(track, end.heading_true) <= ESTABLISHED_TRACK_DEG):
        return Vector('established', None, None, b)
    join_nm = b - abs(s) / math.tan(math.radians(INTERCEPT_ANGLE_DEG))
    if join_nm >= JOIN_MIN_NM and b <= DIRECT_BEHIND_NM + 3:
        heading = end.heading_true - math.copysign(INTERCEPT_ANGLE_DEG, s)
        return Vector('intercept', heading % 360, _height(b), b)
    side_sign = math.copysign(1.0, s or 1.0)
    after_aim_nm = 0.0
    if b > DIRECT_BEHIND_NM + 3 and abs(s) < b:
        aim_behind, aim_side = DIRECT_BEHIND_NM, 0.0
    elif abs(s) < BASE_SIDE_NM - 0.5 and b < BASE_BEHIND_NM - 1:
        aim_behind, aim_side = max(b, -5.0) + 4, side_sign * BASE_SIDE_NM
        after_aim_nm = BASE_BEHIND_NM - aim_behind
    else:
        aim_behind, aim_side = BASE_BEHIND_NM, side_sign * BASE_SIDE_NM
    an, ae = field.from_approach_frame(end, aim_behind * NM, aim_side * NM)
    heading = bearing_true(north, east, an, ae)
    to_aim_nm = math.hypot(an - north, ae - east) / NM
    remaining = to_aim_nm + after_aim_nm + (BASE_BEHIND_NM if after_aim_nm else aim_behind)
    return Vector('positioning', heading, _height(remaining), b)


def turn_key(current_mag: float | None, new_mag: int) -> str:
    if current_mag is None:
        return 'fly'
    diff = (new_mag - current_mag + 540) % 360 - 180
    if abs(diff) < 5:
        return 'keep'
    return 'turn_right' if diff > 0 else 'turn_left'


def vector_phrase(end: RunwayEnd, v: Vector, player: Track, variation: float | None, first: bool, qfe: str | None,
                  height_now_ft: float | None, speech: Speech) -> str:
    """The vector call without pilot/station, e.g. "vectors for approach runway two seven. Turn right heading two four
    zero, descend to three thousand feet, QFE ..."."""
    runway_words = speech.runway(end.number)
    if v.phase == 'established':
        return speech.say('established', runway=runway_words, distance=speech.distance(v.behind_nm))
    heading_mag = round(magnetic(v.heading_true, variation) / 5) * 5 % 360 or 360
    track = player.track_true
    current = magnetic(track, variation) if track is not None else None
    parts = [f'{speech.say(turn_key(current, heading_mag))} {speech.heading(heading_mag)}']
    if v.phase == 'intercept':
        parts.append(speech.say('intercept_final', runway=runway_words))
    if v.height_ft is not None:
        height = speech.height(v.height_ft)
        if height_now_ft is None or abs(height_now_ft - v.height_ft) < 300:
            parts.append(speech.say('maintain_height', height=height))
        elif height_now_ft > v.height_ft:
            parts.append(speech.say('descend', height=height))
        else:
            parts.append(speech.say('climb', height=height))
    if first and qfe:
        parts.append(speech.pressure('qfe', qfe))
    text = ', '.join(parts)
    return f'{speech.say("vectors_for_approach", runway=runway_words)}. {text}' if first else text


@dataclass
class ArrivalState:
    """What the tower has given an arriving pilot (reset once on the ground)."""
    requested: bool = False
    vectoring: bool = False
    cleared: bool = False
    held_key: str | None = None
    go_around_called: bool = False
    last_vector: Vector | None = None
    last_vector_t: float | None = None

    def next_call(self, v: Vector, t: float) -> Vector | None:
        """The vector to give now, or None: the first vector, a new phase, or a heading change after the minimum gap;
        a height change alone waits for the next one. Heights never go back up."""
        last = self.last_vector
        if last is not None and last.height_ft is not None and v.height_ft is not None:
            v = Vector(v.phase, v.heading_true, min(v.height_ft, last.height_ft), v.behind_nm)
        if last is not None and v.phase == last.phase:
            if v.phase == 'established':
                return None
            if self.last_vector_t is not None and t - self.last_vector_t < VECTOR_MIN_GAP_S:
                return None
            if angle_diff(v.heading_true, last.heading_true) < VECTOR_REPEAT_DEG:
                return None
        self.last_vector, self.last_vector_t = v, t
        return v
