"""Chrome dictation: Google's free speech recognition as built into Chrome (Web Speech API), driven by the PTT.

A small local web server serves web/dictation.html to a Chrome app window with this project's own profile
(runtime/chrome-profile), started with --use-fake-ui-for-media-stream so the microphone is granted without a prompt.
Push-to-talk presses go to the page as server-sent events (Chrome slows down the timers of a page hidden behind DCS);
the page dictates while the button is held and posts the text to /heard on release. Needs Chrome and internet.
"""
import json
import queue
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from client.dictation import NO_GATE, log, press_agent

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / 'web' / 'dictation.html'
CHROME_PROFILE = ROOT / 'runtime' / 'chrome-profile'
CHROME_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
POLL_S = 0.01
KEEPALIVE_S = 2   # a closed window is noticed when the next keepalive cannot be written


class ChromeDictationError(Exception):
    pass


class Events:
    """Server-sent events to every open page."""

    def __init__(self):
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue()
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients.remove(q)

    def push(self, event: str, **data) -> None:
        with self.lock:
            for q in self.clients:
                q.put({'event': event, **data})


class ExclusiveServer(ThreadingHTTPServer):
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second process bind the same port silently while the
    # first keeps receiving the requests. Refuse instead.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def chrome_path() -> str | None:
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, CHROME_KEY) as key:
                return winreg.QueryValueEx(key, '')[0]
        except OSError:
            continue
    return None


def open_window(url: str, size: tuple[int, int] = (460, 360)) -> None:
    """A page of ours (dictation, GUI) in a Chrome app window with this project's own profile."""
    chrome = chrome_path()
    if not chrome:
        raise ChromeDictationError('Google Chrome not found; Chrome dictation needs it')
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([chrome, f'--user-data-dir={CHROME_PROFILE}', '--use-fake-ui-for-media-stream',
                      '--no-first-run', '--no-default-browser-check', f'--window-size={size[0]},{size[1]}',
                      f'--app={url}'])


def instance_running(port: int) -> bool:
    """Another instructor already serves the dictation page on this port."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


class ChromeDictation:
    name = 'chrome'

    def __init__(self, button, config: dict, microphone: str, page_text: dict, on_text, on_status, gate=None):
        self.button, self.gate = button, gate
        self.language = config.get('language', 'es-US')
        self.port = config.get('port', 47821)
        self.microphone = microphone
        self.page_text = page_text
        self.on_text, self.on_status = on_text, on_status
        self.events = Events()
        self.ptt_down = False
        self.press_number = 0
        self.press_agents: dict[int, dict] = {}
        try:
            self.server = ExclusiveServer(('127.0.0.1', self.port), self._handler())
        except OSError as exc:
            raise ChromeDictationError(f'port {self.port} is in use: another instructor is running') from exc
        self.url = f'http://127.0.0.1:{self.port}/'

    def start(self, open_window: bool = True) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._watch_ptt, daemon=True).start()
        if open_window:
            self.open_window()

    def open_window(self) -> None:
        open_window(self.url)

    def set_microphone(self, name: str) -> None:
        """Another microphone, chosen in the GUI: the page reconnects to it at once."""
        self.microphone = name
        self.events.push('microphone', name=name)

    def show(self, text: str) -> None:
        """Show an instructor line in the dictation window."""
        self.events.push('instructor', text=text)

    def close_windows(self) -> None:
        """The program is stopping: the dictation window closes itself."""
        self.events.push('close')

    def _watch_ptt(self) -> None:
        listening = False   # this press goes to an agent (the frequency gate found one)
        while True:
            down = self.button.held()   # 0, or the radio of the button held (client/ptt.RadioButtons)
            if down and not self.ptt_down:
                agent = press_agent(self.gate, self.on_status, int(down))
                listening = agent is not None
                if listening:
                    # Each press carries its number and the agent's dictation language; the page sends the number
                    # back with the text, so the answer comes from the agent the pilot was talking to.
                    self.press_number += 1
                    self.press_agents[self.press_number] = agent
                    language = (agent.get('speech') or {}).get('language') or self.language
                    self.events.push('ptt', down=True, press=self.press_number, lang=language)
            elif self.ptt_down and not down and listening:
                self.events.push('ptt', down=False)
                listening = False
            self.ptt_down = down
            time.sleep(POLL_S)

    def _status(self) -> dict:
        return {'pages': len(self.events.clients), 'language': self.language, 'microphone': self.microphone,
                'text': self.page_text}

    def _heard(self, data: dict) -> None:
        text, info = str(data.get('text') or '').strip(), data.get('info', {})
        agent = self.press_agents.pop(info.get('press'), None) or NO_GATE
        log({'event': 'heard', 'text': text, 'agent': agent.get('id'), 'info': info})
        self.on_status('heard', f'"{text}" ({info.get("held_ms", 0) / 1000:.1f} s held)')
        if text:
            self.on_text(text, agent)

    def _page_log(self, data: dict) -> None:
        log({'event': 'page', **data})
        if data.get('error'):
            self.on_status('error', json.dumps(data, ensure_ascii=False))

    def _handler(self):
        dictation = self
        origin = f'http://127.0.0.1:{self.port}'

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == '/':
                    self._send(PAGE.read_bytes(), 'text/html; charset=utf-8')
                elif self.path == '/status':
                    self._send(json.dumps(dictation._status(), ensure_ascii=False).encode('utf-8'),
                               'application/json')
                elif self.path == '/events':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    q = dictation.events.subscribe()
                    try:
                        while True:
                            try:
                                item = q.get(timeout=KEEPALIVE_S)
                                self.wfile.write(f'data: {json.dumps(item, ensure_ascii=False)}\n\n'.encode('utf-8'))
                            except queue.Empty:
                                self.wfile.write(b': keepalive\n\n')
                            self.wfile.flush()
                    except OSError:
                        pass
                    finally:
                        dictation.events.unsubscribe(q)
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.headers.get('Origin', '') not in ('', origin) or self.path not in ('/heard', '/log'):
                    self.send_error(403)
                    return
                data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                self.send_response(204)
                self.end_headers()
                if self.path == '/heard':
                    threading.Thread(target=dictation._heard, args=(data,), daemon=True).start()
                else:
                    dictation._page_log(data)

            def log_message(self, *args):
                pass

        return Handler
