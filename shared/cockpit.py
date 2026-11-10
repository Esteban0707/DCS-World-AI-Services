"""Live cockpit state built from the messages of dcs/instructor_export.lua."""
import json
import socket
import time
from dataclasses import dataclass, field

DEFAULT_PORT = 47820
REPLIES_KEPT = 50
SENSOR_PREFIX = 'sensor:'   # as dcs/instructor_tasks.lua names a controller's sensor in the watched groups


@dataclass
class CockpitState:
    aircraft: str | None = None
    time: float = 0.0                       # DCS model time, seconds
    args: dict[int, float] = field(default_factory=dict)
    flight: dict = field(default_factory=dict)
    radio: dict | None = None               # SRS's view of the radios: {selected, ptt, radios: [...]}; None = unknown
    callsign: str | None = None             # from the mission, via dcs/instructor_hook.lua ("Enfield 1-1")
    pilot: str | None = None                # the player's name in DCS (its multiplayer nickname), same message
    radars: dict | None = None              # airfield name -> {radars, contacts} (dcs/instructor_radar.lua)
    radar_unavailable: str | None = None    # why the mission radars cannot be read (multiplayer client...)
    radar_at: float | None = None           # time.monotonic() of the last radar message
    weather: dict | None = None             # airfield name -> wind, pressure, runways (dcs/instructor_weather.lua)
    weather_at: float | None = None         # time.monotonic() of the last weather message
    mission_weather: dict | None = None     # visibility, fog, clouds of the mission (dcs/instructor_weather.lua)
    traffic: list | None = None             # every aircraft of the mission, player flagged (dcs/instructor_traffic.lua)
    traffic_at: float | None = None         # time.monotonic() of the last traffic message
    native: dict | None = None              # DCS's last answer to a native command (dcs/patches/radio_bridge.lua)
    replies: dict = field(default_factory=dict)   # request id -> answer (native commands and mission tasks)
    groups: dict | None = None              # the agents' AI groups (dcs/instructor_tasks.lua status)
    zones: list | None = None               # the mission's trigger zones: name, lat, lon, radius_m
    groups_at: float | None = None
    sensors: dict = field(default_factory=dict)   # a controller's radar groups (same message as groups)
    wingmen: list | None = None             # the player's wingmen, units 2-4 of its group (same message as groups)
    flight_radio: dict | None = None        # the player's group radio in the Mission Editor: {mhz, modulation}
    flags: dict | None = None               # mission flags the agents listen to: name -> value (dcs/instructor_tasks.lua)
    flags_at: float | None = None
    # Squads carried by the agents' AI helicopters and vehicles (wingman/desk.py, vehicles/desk.py pick_up /
    # drop_off, read by squad/desk.py): squad group -> {carrier: group, ride, stage: pickup | aboard | drop, lat, lon}.
    transport: dict = field(default_factory=dict)
    # The agents' vehicles with a troop compartment by their DCS group (vehicles/desk.py): a squad asks its ride to
    # pick it up or let it out (user, 2026-11-05: "Tango asks Bison, Bison does it on its side").
    carriers: dict = field(default_factory=dict)
    # Squads let out by a ride whose getting out DCS did not confirm (vehicles/desk.py): squad group -> carrier group;
    # the squad then says it may still be aboard (squad/desk.py watch_carrier).
    unconfirmed_drops: dict = field(default_factory=dict)
    # The agents' names by their DCS group (set by the wingman and squad desks): "Cobra, pick up Tango" works whether
    # the pilot uses the agent's name or the group's name in the mission.
    agent_names: dict = field(default_factory=dict)
    # Smoke the agents popped (shared/smoke.py): DCS gives no way to read smoke back, but we put it there.
    smokes: list = field(default_factory=list)

    def apply(self, message: dict) -> str:
        """Update from one export message; returns its type. A reader started while the pilot is already in the
        aircraft never gets the 'aircraft' message (sent once, on entering), so a different aircraft name in the flight
        data counts as one too."""
        kind = message.get('type', '')
        if kind == 'callsign':   # sent by the GUI hook, not the export: no model time
            self.callsign = message.get('callsign') or None
            self.pilot = message.get('pilot') or None
            return kind
        if kind == 'radar':      # GUI hook, once per second
            airfields = message.get('airfields')
            self.radars = airfields if isinstance(airfields, dict) else {}   # an empty Lua table arrives as []
            self.radar_unavailable = message.get('unavailable')
            self.radar_at = time.monotonic()
            return kind
        if kind == 'weather':    # GUI hook, every 10 s
            airfields = message.get('airfields')
            self.weather = airfields if isinstance(airfields, dict) else {}
            mission = message.get('mission')
            self.mission_weather = mission if isinstance(mission, dict) else None
            self.weather_at = time.monotonic()
            return kind
        if kind in ('native', 'task'):   # answers to client/dcs_bridge.py and client/dcs_tasks.py, by request id
            if kind == 'native':
                self.native = message
            self.replies[str(message.get('id'))] = message
            while len(self.replies) > REPLIES_KEPT:
                self.replies.pop(next(iter(self.replies)))
            return kind
        if kind == 'groups':     # GUI hook, once per second: the agents' AI groups and the mission's zones
            groups, zones, flight = message.get('groups'), message.get('zones'), message.get('flight')
            groups = groups if isinstance(groups, dict) else {}
            # A controller's sensors ("sensor:<group>", controller/desk.py) are kept apart: never a unit in sight.
            self.sensors = {n[len(SENSOR_PREFIX):]: g for n, g in groups.items() if n.startswith(SENSOR_PREFIX)}
            self.groups = {n: g for n, g in groups.items() if not n.startswith(SENSOR_PREFIX)}
            self.zones = zones if isinstance(zones, list) else []
            self.wingmen = flight if isinstance(flight, list) else []
            radio = message.get('flight_radio')
            self.flight_radio = radio if isinstance(radio, dict) else None
            self.groups_at = time.monotonic()
            return kind
        if kind == 'flags':      # GUI hook, once per second: the watched mission flags
            flags = message.get('flags')
            # An empty Lua table is encoded as a list: no flags.
            self.flags = {str(k): v for k, v in flags.items()} if isinstance(flags, dict) else {}
            self.flags_at = time.monotonic()
            return kind
        if kind == 'traffic':    # GUI hook, once per second
            aircraft = message.get('aircraft')
            self.traffic = aircraft if isinstance(aircraft, list) else []   # an empty Lua table may arrive as {}
            self.traffic_at = time.monotonic()
            return kind
        flying = (message.get('flight') or {}).get('aircraft')
        if kind in ('update', 'snapshot') and flying and flying != self.aircraft:
            self.apply({'type': 'aircraft', 'aircraft': flying, 'time': message.get('time', self.time)})
            self.apply({**message, 'type': kind})
            return 'aircraft'
        if kind == 'aircraft':
            self.aircraft, self.args, self.flight, self.radio = message['aircraft'], {}, {}, None
        elif kind == 'left_aircraft':
            self.aircraft, self.radio, self.callsign = None, None, None
        if 'time' in message:
            self.time = message['time']
        for key, value in message.get('args', {}).items():
            self.args[int(key)] = value
        if 'flight' in message:
            self.flight = message['flight']
        if 'radio' in message:
            self.radio = message['radio']
        return kind


class CockpitStream:
    """Blocking UDP reader of the export stream (localhost only)."""

    def __init__(self, port: int = DEFAULT_PORT, timeout: float | None = None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', port))
        self.sock.settimeout(timeout)

    def receive(self) -> dict | None:
        try:
            data, _ = self.sock.recvfrom(65535)
        except socket.timeout:
            return None
        return json.loads(data.decode('utf-8'))
