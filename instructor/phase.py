"""The phase of the flight as the tower judges it: parked, taxiing, stopped after taxiing, takeoff roll, airborne,
approach, landing roll, taxiing in, parked after landing (user's choice 2026-11-01, "option B": computed by code, sent as a hint).

Only what a tower can tell from outside: on the ground or airborne, moving or not (ground speed from the change of
position, so wind on a parked aircraft's airspeed does not count), and near the field, low and descending.
"""
from instructor import geo

ON_GROUND_AGL_M = 4.5        # the export's AGL is the height of the aircraft's reference point (F-5E at rest: 1.5 m)
MOVING_KT = 3.0
ROLL_KT = 40.0               # faster than any taxi: takeoff or landing roll
APPROACH_NM = 10.0
APPROACH_AGL_FT = 3000.0
DESCENDING_FPM = -300.0
SPEED_WINDOW_S = 1.0         # ground speed measured over at least this much model time
KT_PER_MS, FT_PER_M = 1.943844, 3.280840


class PhaseTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.has_flown = False
        self.has_taxied = False
        self.samples: list[tuple[float, float, float]] = []   # (model time, lat, lon)
        self.ground_speed_kt: float | None = None

    def update(self, flight: dict, now: float | None) -> None:
        """Feed every flight update (shared.cockpit.CockpitState.flight and its model time)."""
        lat, lon, agl = flight.get('lat'), flight.get('lon'), flight.get('alt_agl_m')
        if isinstance(agl, (int, float)) and agl >= ON_GROUND_AGL_M:
            self.has_flown = True
        if not all(isinstance(v, (int, float)) for v in (lat, lon, now)):
            return
        if self.samples and now < self.samples[-1][0]:   # model time went back: a new mission
            self.reset()
        self.samples.append((now, lat, lon))
        while len(self.samples) > 2 and now - self.samples[1][0] >= SPEED_WINDOW_S:
            self.samples.pop(0)
        start = self.samples[0]
        if now - start[0] >= SPEED_WINDOW_S:
            self.ground_speed_kt = geo.distance_nm(start[1], start[2], lat, lon) * 3600 / (now - start[0])
            self.has_taxied = self.has_taxied or self.ground_speed_kt >= MOVING_KT

    def phase(self, flight: dict, tower: dict | None) -> str:
        agl = flight.get('alt_agl_m')
        if not isinstance(agl, (int, float)):
            return 'unknown'
        if agl >= ON_GROUND_AGL_M:
            return 'approach' if self.approaching(flight, tower) else 'airborne'
        speed = self.ground_speed_kt
        if speed is None:
            return 'on the ground'
        if speed >= ROLL_KT:
            return 'landing roll' if self.has_flown else 'takeoff roll'
        if speed >= MOVING_KT:
            return 'taxiing in after landing' if self.has_flown else 'taxiing'
        if self.has_flown:
            return 'parked after landing'
        # Stopped after moving (e.g. holding short of the runway) is not parked (live 2026-11-01: "parked" at the
        # holding point kept the instructor from letting the student go).
        return 'stopped after taxiing' if self.has_taxied else 'parked'

    @staticmethod
    def approaching(flight: dict, tower: dict | None) -> bool:
        lat, lon, agl, vvi = (flight.get(k) for k in ('lat', 'lon', 'alt_agl_m', 'vvi_ms'))
        if not tower or not all(isinstance(v, (int, float)) for v in (lat, lon, agl, vvi)):
            return False
        return (geo.distance_nm(lat, lon, tower['lat'], tower['lon']) <= APPROACH_NM
                and agl * FT_PER_M <= APPROACH_AGL_FT and vvi * FT_PER_M * 60 <= DESCENDING_FPM)
