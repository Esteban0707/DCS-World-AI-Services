"""Taxi and takeoff clearances decided in code from the aircraft's position, the runway geometry and the traffic. The
model only voices the result (the 'say' phrase, word for word).

Adapted from DCS-ATC (atc/taxi.py and atc/takeoff.py; copied and rewritten with the user's permission, 2026-11-02).
Its live findings are kept: an AI arrival only goes around if the player is on the runway when it is about 1 nm out,
so the tower keeps the player off the runway while an arrival is inside the protection distance; an aircraft flaring
low over the runway counts as occupying it; traffic is checked before movement (a pilot rolling onto a runway with a
C-130 on its rollout must hear about the C-130). No taxiway routing: DCS does not expose taxiway names to scripts.

Every decision is {'status': 'cleared' | 'hold' | 'unable' | 'airborne', 'reason': English for the model, 'say': the
phrase in the radio language}; traffic holds add 'traffic' (spoken) and 'traffic_key'.
"""
from dataclasses import dataclass

from atc.geometry import NM, Field, RunwayEnd, Track, angle_diff
from atc.phrases import Speech
from instructor.field_position import HOLDING_AREA_SIDE_M, NEAR_END_M

ROLLING_KT = 30               # faster than this on a runway is a takeoff or landing roll, not taxiing
# Holding point, intersection near the threshold, or lined up. The same area the window view calls "near the threshold
# ... (holding point area)" (instructor/field_position.py): live 2026-11-03 a pilot held short facing runway 27 at
# Kolkhi, the board said "holding point area", and six takeoff requests got "taxi to holding point" (the old 300 m /
# 150 m box was smaller). Kolkhi 27's own holding point: 10 m short, 73 m to the side.
DEPARTURE_END_M = NEAR_END_M  # before the threshold (behind it, on the approach side)
DEPARTURE_INSIDE_M = NEAR_END_M          # past the threshold, along the runway (an intersection near it)
DEPARTURE_END_SIDE_M = HOLDING_AREA_SIDE_M   # beyond the runway's edge
READY_MAX_KT = 10             # stopped or creeping at the holding point
FINAL_SIDE_M = 1000           # lateral corridor of the final approach
FINAL_TRACK_TOLERANCE_DEG = 30
FINAL_MAX_AGL_FT = 3000
OVER_RUNWAY_MAX_AGL_FT = 500  # airborne over the runway below this = landing (or just departed): occupied
PROTECT_NM = 5.0              # an arrival inside this keeps a departure off the runway (DCS-ATC's default)
TRAFFIC_INFO_NM = 6           # arrivals on final within this are mentioned in a taxi clearance


@dataclass
class Arrival:
    aircraft: Track
    distance_nm: float        # to the runway-in-use threshold
    agl_ft: float


def arrivals_on_final(field: Field, end: RunwayEnd, traffic: list[Track], max_nm: float,
                      max_agl_ft: float = FINAL_MAX_AGL_FT) -> list[Arrival]:
    """Aircraft flying towards the runway-in-use threshold from behind it, aligned, within the limits; nearest first."""
    result = []
    for a in traffic:
        if not a.in_air:
            continue
        frame = field.approach_frame(end, *field.where(a))
        track = a.track_true
        if frame is None or track is None:
            continue
        behind, side = frame
        agl_ft = field.height_ft(a)
        if (0 <= behind / NM <= max_nm and abs(side) <= FINAL_SIDE_M and agl_ft <= max_agl_ft
                and angle_diff(track, end.heading_true) <= FINAL_TRACK_TOLERANCE_DEG):
            result.append(Arrival(a, behind / NM, agl_ft))
    return sorted(result, key=lambda arrival: arrival.distance_nm)


def traffic_note(arrival: Arrival, speech: Speech) -> str:
    return speech.traffic_on_final(arrival.aircraft.type_name, arrival.distance_nm)


def traffic_key(aircraft: Track, where: str) -> str:
    """One traffic reason across calls, whatever the language and the distance: "Uzi11 final"."""
    return f'{aircraft.unit} {where}'


def runway_occupant(field: Field, end: RunwayEnd, traffic: list[Track]) -> Track | None:
    """An aircraft on the runway, or over it low (flaring past the threshold, not yet down)."""
    found = field.threshold(end)
    if not found:
        return None
    for a in traffic:
        if field.runway_under(*field.where(a)) is found[0] and (not a.in_air or field.height_ft(a) <= OVER_RUNWAY_MAX_AGL_FT):
            return a
    return None


def at_departure_end(field: Field, track: Track, end: RunwayEnd) -> bool:
    """At the holding point, an intersection near the threshold, or lined up: near the runway-in-use threshold."""
    found = field.threshold(end)
    frame = field.approach_frame(end, *field.where(track))
    return (frame is not None and -DEPARTURE_INSIDE_M <= frame[0] <= DEPARTURE_END_M
            and abs(frame[1]) <= found[0].width_m / 2 + DEPARTURE_END_SIDE_M)


def from_threshold(field: Field, track: Track, end: RunwayEnd) -> str:
    """Where the aircraft is from the threshold, for the model to guide the pilot ("1250 m past the threshold of
    runway 27 along it, 40 m to its right")."""
    behind, side = field.approach_frame(end, *field.where(track))
    along = f'{round(abs(behind), -1):.0f} m {"before" if behind >= 0 else "past"} the threshold of runway {end.number}'
    return f'{along} along it, {round(abs(side), -1):.0f} m to its {"right" if side >= 0 else "left"}'


def on_runway_in_use(field: Field, track: Track, end: RunwayEnd) -> bool:
    """On the ground on the runway in use and not on the takeoff roll."""
    found = field.threshold(end)
    return (not track.in_air and found is not None and field.runway_under(*field.where(track)) is found[0]
            and track.ground_speed_kt <= ROLLING_KT)


def taxi_clearance(field: Field, player: Track, end: RunwayEnd | None, landed: bool, qnh: str | None,
                   speech: Speech, note: str | None = None) -> dict:
    """Departure: taxi to the holding point of the runway in use. After a landing: vacate and taxi to parking.
    qnh: as the radio says it ("1013" or "29.92"); note: spoken traffic added to a departure taxi clearance."""
    unable = speech.say('unable')
    if player.in_air:
        return {'status': 'unable', 'reason': 'the aircraft is airborne', 'say': unable}
    north, east = field.where(player)
    if not field.at_airfield(north, east):
        return {'status': 'unable', 'reason': 'the aircraft is not at this airfield', 'say': unable}
    on_runway = field.runway_under(north, east)
    if on_runway and player.ground_speed_kt > ROLLING_KT:
        return {'status': 'hold', 'reason': 'the aircraft is on its takeoff or landing roll', 'say': speech.say('stand_by')}
    if landed:
        say = speech.say('vacate_and_taxi_to_parking' if on_runway else 'taxi_to_parking')
        return {'status': 'cleared', 'kind': 'after_landing', 'say': say}
    if not end:
        return {'status': 'unable', 'reason': 'no runway in use (no wind or runway data from the mission)', 'say': unable}
    if on_runway:
        return {'status': 'hold', 'kind': 'departure', 'reason': 'the aircraft is already on the runway',
                'say': speech.say('hold_position')}
    parts = [speech.say('taxi_to_holding_point', runway=speech.runway(end.number))]
    if qnh:
        parts.append(speech.pressure('qnh', qnh))
    if note:
        parts.append(note)
    return {'status': 'cleared', 'kind': 'departure', 'say': ', '.join(parts)}


def _traffic_hold(speech: Speech, note: str, key: str) -> dict:
    return {'status': 'hold', 'reason': 'traffic', 'traffic': note, 'traffic_key': key,
            'say': f'{speech.say("hold_position")}, {note}'}


def takeoff_clearance(field: Field, player: Track, end: RunwayEnd | None, traffic: list[Track] | None,
                      wind: str | None, speech: Speech, protect_nm: float = PROTECT_NM) -> dict:
    """Holds while an arrival is within `protect_nm` of the threshold or an aircraft is on the runway. wind: spoken."""
    if player.in_air:
        # Not "unable": a pilot reporting airborne heard "not cleared for takeoff" from that (DCS-ATC, 2026-10-26).
        return {'status': 'airborne', 'reason': 'the aircraft is already airborne'}
    unable = speech.say('unable')
    north, east = field.where(player)
    if not field.at_airfield(north, east):
        return {'status': 'unable', 'reason': 'the aircraft is not at this airfield', 'say': unable}
    if not end or not field.threshold(end):
        return {'status': 'unable', 'reason': 'no runway in use (no wind or runway data from the mission)', 'say': unable}
    runway_words = speech.runway(end.number)
    if not at_departure_end(field, player, end):
        return {'status': 'hold', 'reason': 'the aircraft is not at the departure end of the runway in use: it is '
                                            + from_threshold(field, player, end),
                'say': speech.say('taxi_to_holding_point', runway=runway_words)}
    occupant = runway_occupant(field, end, traffic) if traffic is not None else None
    if occupant:
        return _traffic_hold(speech, speech.say('runway_occupied'), traffic_key(occupant, 'runway'))
    arrivals = arrivals_on_final(field, end, traffic, protect_nm) if traffic is not None else []
    if player.ground_speed_kt > READY_MAX_KT:
        if arrivals:
            return _traffic_hold(speech, traffic_note(arrivals[0], speech), traffic_key(arrivals[0].aircraft, 'final'))
        return {'status': 'hold', 'reason': 'the aircraft is moving', 'say': speech.say('hold_position')}
    if traffic is None:
        return {'status': 'unable', 'reason': 'no traffic data from the mission', 'say': unable}
    if arrivals:
        note = traffic_note(arrivals[0], speech)
        if on_runway_in_use(field, player, end):
            # Already lined up: holding there is what sends the arrival around; get them airborne now.
            return {'status': 'cleared', 'say': f'{note}, ' + speech.say('cleared_for_immediate_takeoff',
                                                                         runway=runway_words)}
        return _traffic_hold(speech, note, traffic_key(arrivals[0].aircraft, 'final'))
    say = speech.say('cleared_for_takeoff', runway=runway_words)
    return {'status': 'cleared', 'say': f'{wind}, {say}' if wind else say}


def runway_call(end: RunwayEnd, arrival: Arrival, speech: Speech) -> str:
    """The tower to a cleared pilot still on the runway while an arrival closes in (text without pilot/station)."""
    return speech.say('takeoff_or_vacate', traffic=traffic_note(arrival, speech), runway=speech.runway(end.number))
