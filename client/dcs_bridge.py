"""Sends native radio commands to DCS through the bridge patched into its radio menu (dcs/patches/radio_bridge.lua,
client/dcs_patches.py): e.g. "abort_takeoff" to Senaki-Kolkhi, so DCS's own ATC releases the AI arrivals it holds
while it thinks the player departs, or "inbound" at night for the runway lights.

Runs on the pilot's PC, where DCS is: the bridge listens on 127.0.0.1 only. When the program runs elsewhere (a
Raspberry Pi), the PC side relays these requests; nothing is sent straight into DCS from another machine. DCS answers
each request on the cockpit port ({type: native, id, status, detail}: sent / refused / unavailable / error), kept in
CockpitState.native and logged by the voice loop.
"""
import itertools
import socket
import time

PORT = 47823
COMMANDS = ('abort_takeoff', 'request_taxi', 'request_takeoff', 'inbound', 'abort_inbound', 'request_landing',
            'embark_troops', 'disembark_troops', 'troops_on_board')   # the troop commands take a group name
# Orders to the player's wingmen (the F1 menu's), as dcs/patches/radio_bridge.lua WINGMAN lists them; the target is
# the wingman's number ("2"-"4") or "flight".
WINGMAN_ORDERS = ('engage_my_target', 'engage_my_enemy', 'engage_bandits', 'engage_air_defences',
                  'engage_ground_targets', 'engage_ships', 'primary_rejoin', 'primary_rtb', 'pincer_high',
                  'pincer_low', 'pincer_right', 'pincer_left', 'return_to_base', 'fly_route', 'hold_position',
                  'go_tanker', 'radar_on', 'radar_off', 'ecm_on', 'ecm_off', 'smoke_on', 'smoke_off', 'cover_me',
                  'jettison', 'rejoin', 'line_abreast', 'trail', 'wedge', 'echelon_right', 'finger_four',
                  'spread_four', 'close_formation', 'open_formation', 'close_group_formation')
COMMANDS += tuple(f'wing_{order}' for order in WINGMAN_ORDERS)


class NativeBridge:
    def __init__(self, port: int = PORT, log=print):
        self.port, self.log = port, log
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ids = itertools.count(1)
        self.prefix = f'{int(time.time()) % 100000:05d}'
        self.quiet_ids: set[str] = set()   # requests asked every few seconds (troops_on_board): answers not logged

    def send(self, command: str, airfield: str) -> str:
        """Queue one command for DCS; returns its request id (DCS's answer comes on the cockpit port)."""
        if command not in COMMANDS:
            raise ValueError(f'command not allowed: {command}')
        if not airfield or any(c in airfield for c in '\t\r\n'):
            raise ValueError('airfield')
        request_id = f'r{self.prefix}n{next(self.ids)}'
        line = f'{request_id}\t{int(time.time())}\t{command}\t{airfield}'
        self.sock.sendto(line.encode('utf-8'), ('127.0.0.1', self.port))
        if command == 'troops_on_board':
            self.quiet_ids.add(request_id)
            while len(self.quiet_ids) > 100:
                self.quiet_ids.pop()
        else:
            self.log({'event': 'native_request', 'id': request_id, 'command': command, 'airfield': airfield})
        return request_id
