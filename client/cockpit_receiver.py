"""Receive the cockpit stream from dcs/instructor_export.lua and show it readably.

Usage: python -m client.cockpit_receiver [--port 47820]
Prints each cockpit control that changes, by its name from the active module's controls.json, plus a short flight
line every few seconds. Every message is also appended to logs/cockpit.jsonl.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / 'logs' / 'cockpit.jsonl'


def load_control_names(aircraft: str) -> dict[int, str]:
    """Cockpit argument -> control name(s) for the aircraft, or {} without a module."""
    path = ROOT / 'modules' / aircraft / 'controls.json'
    if not path.exists():
        return {}
    names: dict[int, list[str]] = {}
    for control in json.loads(path.read_text(encoding='utf-8'))['controls']:
        for arg in set(control['arg']):
            names.setdefault(arg, []).append(control['hint'])
    return {arg: ' / '.join(sorted(set(hints))) for arg, hints in names.items()}


def flight_line(flight: dict) -> str:
    def fmt(key, scale=1.0, digits=0):
        value = flight.get(key)
        return '-' if value is None else f'{value * scale:.{digits}f}'
    return (f'IAS {fmt("ias_ms", 1.943844)} kt  ALT {fmt("alt_msl_m", 3.28084)} ft  AoA {fmt("aoa_deg", 1, 1)}  '
            f'RPM L {fmt("rpm_left")} R {fmt("rpm_right")}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=47820)
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # show lines immediately even when not attached to a terminal

    LOG.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1', args.port))
    print(f'listening on 127.0.0.1:{args.port}, log {LOG}')

    names: dict[int, str] = {}
    values: dict[int, float] = {}
    next_flight_line = 0.0
    with LOG.open('a', encoding='utf-8') as log:
        while True:
            data, _ = sock.recvfrom(65535)
            message = json.loads(data.decode('utf-8'))
            log.write(json.dumps({'received': time.time(), **message}) + '\n')
            log.flush()
            kind = message.get('type')
            if kind == 'aircraft':
                names = load_control_names(message['aircraft'])
                values = {}
                print(f'aircraft: {message["aircraft"]}  (cockpit args exported: {message["arg_count"]}, '
                      f'named by module: {len(names)})')
            elif kind in ('update', 'snapshot'):
                for key, value in message.get('args', {}).items():
                    arg = int(key)
                    if arg in values and abs(values[arg] - value) > 0.001:
                        print(f'  arg {arg:4d} = {value:7.3f}  (was {values[arg]:.3f})  {names.get(arg, "?")}')
                    values[arg] = value
                if time.time() >= next_flight_line:
                    next_flight_line = time.time() + 5
                    print(flight_line(message.get('flight', {})))
            else:
                print(kind)


if __name__ == '__main__':
    main()
