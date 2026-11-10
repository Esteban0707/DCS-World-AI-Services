"""The GUI without the instructor: settings, devices and voice tests while DCS is not running.

Usage: python tools/gui.py [--no-window]
When the instructor (DCS-Instructor.bat) is running it already serves the GUI: this only opens its window. Otherwise
it serves the GUI itself (Status shows "not running") until its window is closed (or Ctrl+C).
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.chrome_dictation import instance_running, open_window   # noqa: E402
from gui.server import DEFAULT_PORT, Gui   # noqa: E402
from instructor.voice_loop import GUI_WINDOW_SIZE, WindowWatch, load_settings   # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--no-window', action='store_true', help='serve only; open the address in any browser')
    args = parser.parse_args()
    port = load_settings().get('gui', {}).get('port', DEFAULT_PORT)
    url = f'http://127.0.0.1:{port}/'
    serving = not instance_running(port)
    gui = None
    if serving:
        gui = Gui(port=port)
        gui.start()
        print(f'GUI at {url} (close its window or Ctrl+C to stop)')
    else:
        print(f'the instructor already serves the GUI at {url}')
    if not args.no_window:
        open_window(url, size=GUI_WINDOW_SIZE)
    if not serving:
        return
    watch = WindowWatch()
    try:
        while not gui.live.stop.is_set() and not watch.closed(len(gui.events.clients)):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    gui.events.push('close')
    time.sleep(0.5)


if __name__ == '__main__':
    main()
