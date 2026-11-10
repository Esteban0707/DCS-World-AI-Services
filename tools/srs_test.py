"""Live check of the instructor voice output: speaks the locale's test phrase through the configured output.

Usage: python tools/srs_test.py [--no-open]
With "voice.output": "srs", it first opens the SRS server and client of the detected install (unless they are
already running, or --no-open) and brings the client window to the front (it may start hidden in the tray), so they
can be configured; then each Enter transmits the phrase on the instructor
frequency (settings.json "srs"). Any SRS error is printed and the phrase plays on the speakers instead.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from instructor.srs import SrsOutput, open_apps, show_client   # noqa: E402
from instructor.voice import Voice   # noqa: E402
from shared.locale import load_locale   # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--no-open', action='store_true', help='do not start the SRS server and client')
    args = parser.parse_args()
    settings = json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))
    voice = Voice(settings)
    if isinstance(voice.output, SrsOutput) and not args.no_open:
        for app, status in open_apps(voice.output.exe).items():
            print(f'SRS {app}: {status}')
        if not show_client(voice.output.exe):
            print('SRS client window did not appear: open it from its tray icon')
        srs = settings['srs']
        print(f'\nIn SRS: server running, client connected to 127.0.0.1:{srs["port"]}, '
              f'radio on {srs["frequencies_mhz"]} MHz {srs["modulations"]}, coalition {srs["coalition"]}.')
    phrase = load_locale(settings['language'])['tts_test']
    while True:
        if input('\nEnter = transmit the test phrase, q = quit: ').strip().lower() == 'q':
            break
        start = time.time()
        voice.say(phrase)
        voice.queue.join()
        print(f'done in {time.time() - start:.1f} s')


if __name__ == '__main__':
    main()
