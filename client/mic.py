"""Microphone capture while push-to-talk is held: mono 16-bit samples at the device's own rate.

The device is chosen by part of its name ("mic.device" setting, e.g. "Wireless Gaming Headset"); empty means the
system default. The PC's default may be a virtual device (Voicemod...), so naming the real one is safer.
"""
import threading

import numpy as np

PREFERRED_API = 'MME'   # every Windows input device appears here, at its shared-mode rate
MME_NAME_LENGTH = 31


class MicError(Exception):
    pass


def input_devices() -> list[dict]:
    import sounddevice as sd
    apis = sd.query_hostapis()
    return [{'index': i, 'name': d['name'], 'api': apis[d['hostapi']]['name'], 'rate': int(d['default_samplerate'])}
            for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0]


def find_device(name: str) -> dict:
    devices = input_devices()
    if not name:
        import sounddevice as sd
        default = sd.default.device[0]
        return next(d for d in devices if d['index'] == default)
    wanted = name.lower()
    # The GUI stores full names ("Microphone (Wireless Gaming Headset)"); MME cuts them at 31 characters.
    matches = [d for d in devices if wanted in d['name'].lower()
               or (len(d['name']) >= MME_NAME_LENGTH and wanted.startswith(d['name'].lower()))]
    if not matches:
        raise MicError(f'microphone "{name}" not found; inputs: {sorted({d["name"] for d in devices})}')
    return next((d for d in matches if d['api'] == PREFERRED_API), matches[0])


class Recorder:
    def __init__(self, device_name: str = ''):
        self.device = find_device(device_name)
        self.rate = self.device['rate']
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd
        with self._lock:
            self._chunks = []
        self._stream = sd.InputStream(device=self.device['index'], channels=1, samplerate=self.rate, dtype='int16',
                                      callback=self._collect)
        self._stream.start()

    def _collect(self, data, frames, time, status) -> None:
        with self._lock:
            self._chunks.append(data[:, 0].copy())

    def stop(self) -> np.ndarray:
        """The recorded samples (int16, mono) since start()."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            return np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.int16)
