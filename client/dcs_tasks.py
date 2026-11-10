"""Orders for the agents' AI ground groups, sent to the mission through our GUI hook (dcs/instructor_hook.lua task
port, dcs/instructor_tasks.lua): move, wait for pickup, hold, rules of engagement, smoke; which groups to report; and
the mission flags of the agents' Triggers (which to report, which may be set, set one).

Runs on the pilot's PC, next to DCS (127.0.0.1 only); a remote program (Raspberry Pi) relays through the PC side.
The hook answers each order on the cockpit port ({type: task, id, ok, detail}) and reports the watched groups and flags
once a second ({type: groups, groups, zones}, {type: flags, flags}); all are kept in CockpitState.
"""
import itertools
import re
import socket
import time

PORT = 47824
ACTIONS = {'move': 4, 'wait_pickup': 4, 'hold': 1, 'roe': 2, 'smoke': 2, 'attack': 2, 'watch': 1, 'flags': 2,
           'set_flag': 2, 'drive': 6, 'carry_pickup': 5, 'carry_dropoff': 5,   # vehicles (squad/desk.py)
           'fire': 5, 'cease_fire': 1,   # artillery (artillery/desk.py)
           'sail': 4, 'steer': 4, 'ship_hold': 1, 'ship_attack': 2, 'ship_aid': 7,   # ships (ships/desk.py)
           # Scripted wingmen (wingman/desk.py); the hook adds the player's unit as the leader where needed.
           'wing_start': 2, 'wing_follow': 4, 'wing_escort': 1, 'wing_attack': 3, 'wing_engage_zone': 7, 'wing_bomb': 7, 'wing_fly': 6, 'wing_orbit': 5,
           'wing_land': 2, 'wing_land_at': 5, 'wing_pickup': 4, 'wing_dropoff': 4, 'wing_roe': 2}
FLAG_NAME = re.compile(r'^[A-Za-z0-9_-]{1,40}$')   # as the hook checks them
WATCH_FORGET_S = 15.0   # squads ask every 5 s (squad/desk.py WATCH_EVERY_S)


class TaskChannel:
    def __init__(self, port: int = PORT, log=print):
        self.port, self.log = port, log
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ids = itertools.count(1)
        self.prefix = f'{int(time.time()) % 100000:05d}'
        self.watched: dict[str, tuple[list[str], float]] = {}   # owner -> (groups, when asked)

    def send(self, action: str, *args) -> str:
        """One order; returns its request id (the hook's answer comes on the cockpit port)."""
        if ACTIONS.get(action) != len(args):
            raise ValueError(f'action not allowed: {action}/{len(args)}')
        fields = [f'{a:.7f}' if isinstance(a, float) else str(a) for a in args]
        empty_allowed = action == 'flags'   # an empty list of watched or settable flags
        if any((not f and not empty_allowed) or any(c in f for c in '\t\r\n') for f in fields):
            raise ValueError('argument')
        request_id = f't{self.prefix}n{next(self.ids)}'
        line = '\t'.join([request_id, str(int(time.time())), action, *fields])
        self.sock.sendto(line.encode('utf-8'), ('127.0.0.1', self.port))
        if action not in ('watch', 'flags'):
            self.log({'event': 'task_request', 'id': request_id, 'action': action, 'args': fields})
        return request_id

    def watch(self, groups: list[str], owner: str = '') -> None:
        """The groups one agent wants reported. The hook keeps one list for all, so every agent's groups go together;
        an agent that stopped asking (switched off, deleted) drops out after WATCH_FORGET_S."""
        now = time.monotonic()
        self.watched[owner] = (list(groups), now)
        self.watched = {k: v for k, v in self.watched.items() if now - v[1] <= WATCH_FORGET_S}
        union = list(dict.fromkeys(g for names, _ in self.watched.values() for g in names))
        if union:
            self.send('watch', '|'.join(union))

    def flags(self, watched: list[str], settable: list[str]) -> str | None:
        """Every agent's flags at once (the hook keeps one list): those reported every second and those that may be
        set. Resent now and then, like watch: the hook forgets them when DCS restarts."""
        names = [str(n) for n in [*watched, *settable]]
        if not all(FLAG_NAME.match(n) for n in names):
            raise ValueError('flag name')
        if not names:
            return None
        return self.send('flags', '|'.join(map(str, watched)), '|'.join(map(str, settable)))

    def set_flag(self, name: str, value: int = 1) -> str:
        if not FLAG_NAME.match(str(name)):
            raise ValueError('flag name')
        return self.send('set_flag', str(name), int(value))
