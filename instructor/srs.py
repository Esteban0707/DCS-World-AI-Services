"""SRS voice output: transmits instructor audio on a radio frequency with SRS's own ExternalAudio tool.

The SRS install is found wherever it is: the "srs.path" setting if given, otherwise the folder the SRS installer
records in the Windows registry (HKCU\\Software\\DCS-SR-Standalone\\SRPathStandalone), otherwise the default
Program Files folders. ExternalAudio only connects to an SRS server on this PC (127.0.0.1), on "srs.port".
"""
import csv
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EXTERNAL_AUDIO = Path('ExternalAudio') / 'DCS-SR-ExternalAudio.exe'
REGISTRY_KEY, REGISTRY_VALUE = r'Software\DCS-SR-Standalone', 'SRPathStandalone'
DEFAULT_DIRS = [Path(os.environ.get(v, '')) / 'DCS-SimpleRadio-Standalone'
                for v in ('ProgramFiles', 'ProgramFiles(x86)') if os.environ.get(v)]
TIMEOUT_EXTRA = 30.0   # seconds allowed beyond the clip length (connecting, encoding)


class SrsError(Exception):
    pass


def registry_dir() -> Path | None:
    if sys.platform != 'win32':
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            return Path(winreg.QueryValueEx(key, REGISTRY_VALUE)[0])
    except OSError:
        return None


def find_external_audio(configured: str = '') -> Path | None:
    """ExternalAudio executable of the SRS install, or None when SRS is not installed."""
    candidates = [Path(configured)] if configured else [d for d in [registry_dir(), *DEFAULT_DIRS] if d]
    for folder in candidates:
        exe = folder if folder.suffix.lower() == '.exe' else folder / EXTERNAL_AUDIO
        if exe.is_file():
            return exe
    return None


SRS_APPS = {'server': Path('Server') / 'SRS-Server.exe', 'client': Path('Client') / 'SR-ClientRadio.exe'}


def process_ids(exe: Path) -> list[int]:
    if sys.platform != 'win32':
        return []
    result = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {exe.name}', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    return [int(row[1]) for row in csv.reader(result.stdout.splitlines())
            if len(row) > 1 and row[0].lower() == exe.name.lower()]


def is_running(exe: Path) -> bool:
    return bool(process_ids(exe))


def open_apps(external_audio: Path, apps=('server', 'client')) -> dict[str, str]:
    """Start the SRS apps of the install that holds this ExternalAudio, unless already running.

    Returns each app's status: started, running or missing. Each app starts in its own folder, where SRS keeps its
    configuration, and keeps running after this process exits.
    """
    install = external_audio.parent.parent
    status = {}
    for app in apps:
        exe = install / SRS_APPS[app]
        if not exe.is_file():
            status[app] = 'missing'
        elif is_running(exe):
            status[app] = 'running'
        else:
            subprocess.Popen([str(exe)], cwd=exe.parent, close_fds=True,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            status[app] = 'started'
    return status


def show_client(external_audio: Path, timeout: float = 20.0) -> bool:
    """Bring the SRS client's main window to the front, even when it started hidden in the tray."""
    if sys.platform != 'win32':
        return False
    from instructor import srs_window
    exe = external_audio.parent.parent / SRS_APPS['client']
    deadline, clicked = time.time() + timeout, set()
    while time.time() < deadline:
        for pid in process_ids(exe):
            if srs_window.bring_to_front(pid):
                return True
            if pid not in clicked and srs_window.double_click_tray(pid):
                clicked.add(pid)   # once: a second double-click could toggle the window away again
        time.sleep(0.5)
    return False


CLIENT_CONFIG = Path('Client') / 'global.cfg'
CLIENT_DEVICE_KEYS = {'output': 'AudioOutputDeviceId', 'input': 'AudioInputDeviceId'}


def client_audio(external_audio: Path) -> dict:
    """The audio devices the SRS client of this install uses, read from its global.cfg (Windows endpoint ids; empty
    = Windows default). The instructor's voice on the radio is heard through the client's output device.
    Returns {'output': {'id', 'name'}, 'input': {...}}; name None = Windows default or a device not connected."""
    from shared.audio_devices import windows_endpoints
    path = external_audio.parent.parent / CLIENT_CONFIG
    values = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
    endpoints = {**windows_endpoints('Render'), **windows_endpoints('Capture')}
    return {kind: {'id': values.get(key, ''), 'name': endpoints.get(values.get(key, ''))}
            for kind, key in CLIENT_DEVICE_KEYS.items()}


def command(exe: Path, clip: Path, srs: dict) -> list[str]:
    frequencies = srs['frequencies_mhz']
    modulations = srs.get('modulations') or ['AM'] * len(frequencies)
    return [str(exe),
            f'--file={clip}',
            '--freqs=' + ','.join(f'{f:g}' for f in frequencies),
            '--modulations=' + ','.join(m.upper() for m in modulations),
            f'--coalition={srs.get("coalition", 2)}',
            f'--port={srs.get("port", 5002)}',
            f'--name={srs.get("name", "DCS-Instructor").replace(" ", "-")}',
            f'--volume={srs.get("volume", 1.0):g}']


OGG_BLOCK = 4096


def write_ogg(path: Path, samples, sample_rate: int) -> None:
    """OGG Vorbis in blocks. One soundfile.write of a clip of about 12 s or longer kills the whole process with a stack
    overflow inside libsndfile 1.2.2 (Windows 0xC00000FD; seen twice live 2026-11-01 after long dictated phrases, and
    reproduced: 5 s fine, 12 s and 30 s crash). Writing in blocks avoids it."""
    import soundfile as sf
    with sf.SoundFile(path, 'w', sample_rate, 1, format='OGG', subtype='VORBIS') as f:
        for start in range(0, len(samples), OGG_BLOCK):
            f.write(samples[start:start + OGG_BLOCK])


class SrsOutput:
    name = 'srs'

    def __init__(self, srs: dict, temp_dir: Path):
        self.srs = srs
        self.exe = find_external_audio(srs.get('path', ''))
        if not self.exe:
            raise SrsError('SRS ExternalAudio not found (set "srs.path" in settings.json)')
        self.temp_dir = temp_dir
        self.last_output = ''
        temp_dir.mkdir(parents=True, exist_ok=True)

    def play(self, samples, sample_rate: int, channel: dict | None = None) -> None:
        """Transmit one clip; returns when SRS has finished sending it, so clips never overlap. channel: an agent's
        own frequencies_mhz / modulations / name, instead of settings "srs"."""
        srs = {**self.srs, **(channel or {})}
        handle, name = tempfile.mkstemp(suffix='.ogg', dir=self.temp_dir)
        os.close(handle)
        clip = Path(name)
        try:
            write_ogg(clip, samples, sample_rate)
            timeout = len(samples) / sample_rate + TIMEOUT_EXTRA
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            try:
                result = subprocess.run(command(self.exe, clip, srs), capture_output=True, text=True,
                                        errors='replace', timeout=timeout, creationflags=flags)
            except subprocess.TimeoutExpired as exc:
                raise SrsError(f'ExternalAudio did not finish in {timeout:.0f} s') from exc
            # What ExternalAudio printed last (diagnostics: instructor/voice.py logs it with each clip).
            self.last_output = ' | '.join(result.stdout.strip().splitlines()[-3:])[-400:]
            errors = [line for line in result.stdout.splitlines() if '|ERROR|' in line]
            if result.returncode or errors:
                raise SrsError('; '.join(errors) or f'ExternalAudio exit code {result.returncode}')
        finally:
            clip.unlink(missing_ok=True)
