"""DCS_AI_Service (the DCS-Instructor project) without console windows (run with pythonw): SRS server and client, Chrome dictation, voice loop.

Usage: pythonw tools/launcher.py   (DCS-Instructor.bat, the one file to double-click; DCS is opened by the user)
First the checks (tools/preflight.py): DCS export and hook installed, aircraft libraries up to date (rebuilt if a
document was added); a problem is shown in a message box and the instructor starts anyway. Then it opens the SRS
server and client if they are not running and brings the client window to the front, opens the Chrome dictation
window and the GUI window (gui/server.py, http://127.0.0.1:47822/), and runs the voice loop
(instructor/voice_loop.py), whose black box records the flight and every cockpit control touched (logs/sessions/).
Closing the GUI window (or the dictation window), or Stop in the GUI, stops everything and closes the other window.
Output goes to logs/launcher.log. A second launch while one runs only reopens its GUI window.
"""
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / 'logs' / 'launcher.log'


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = LOG.open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = out   # pythonw has no console
    print(f'\n=== {time.strftime("%Y-%m-%d %H:%M:%S")} DCS-Instructor starting')
    try:
        from client.chrome_dictation import instance_running, open_window
        from instructor.voice_loop import load_settings, run
        settings = load_settings()
        speech = settings.get('speech', {})
        if speech.get('provider') == 'chrome' and instance_running(speech.get('port', 47821)):
            # Already running (e.g. a window was closed and it is still stopping, or just hidden): show its GUI
            # window again instead of failing on the ports.
            from gui.server import DEFAULT_PORT
            from instructor.voice_loop import GUI_WINDOW_SIZE
            print('already running: reopening its GUI window')
            open_window(f'http://127.0.0.1:{settings.get("gui", {}).get("port", DEFAULT_PORT)}/', size=GUI_WINDOW_SIZE)
            return
        from tools.preflight import run as preflight
        problems = preflight(settings)
        for key, values in problems:
            print(f'check: {key} {values}')
        if problems:
            from shared.locale import load_locale
            checks = load_locale(settings['language'])['checks']
            show_problems([checks[key].format(**values) for key, values in problems], checks['footer'])
        run(settings, voice=True, open_srs=True, stop_when_window_closed=True, gui=True)
    except SystemExit as exc:   # the voice loop's start-up problems (no push-to-talk yet, a port in use...)
        print(f'stopped: {exc}')
        if str(exc) and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'DCS-Instructor', 0x10)
    except Exception:
        traceback.print_exc()
    finally:
        print(f'=== {time.strftime("%Y-%m-%d %H:%M:%S")} DCS-Instructor stopped')


def show_problems(problems: list[str], footer: str) -> None:
    """A Windows message box in its own thread, so the instructor keeps starting while it is open."""
    if sys.platform != 'win32':
        return
    import ctypes
    text = '\n\n'.join(problems + [footer])
    threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(None, text, 'DCS-Instructor', 0x30),
                     daemon=True).start()


if __name__ == '__main__':
    main()
