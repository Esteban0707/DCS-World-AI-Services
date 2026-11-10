"""Instructor voice: Supertonic 3 TTS, spoken in order from a background queue.

Each agent (shared/agents.py) has its own voice settings and output: "srs" transmits on the agent's own frequency
through SRS ExternalAudio (instructor/srs.py); "speakers" plays on the PC output device chosen in the GUI
("audio.output_device"; '' = the system default). Without an agent, settings.json "voice" and "srs" are used (console
tools). When SRS is missing or a transmission fails, that clip falls back to the speakers so the pilot still hears it.
"""
import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
KEY_HINT = re.compile(r'\[[^\]]*\]')   # "[LCTRL +LSHIFT +C]": keyboard hints are for reading, not for the voice
APP_NAME = 'DCS_AI_Service'   # the user's name for the app (2026-11-02); SRS shows it as the transmitter
VOICES = ('F1', 'F2', 'F3', 'F4', 'F5', 'M1', 'M2', 'M3', 'M4', 'M5')
DEFAULTS = {'name': 'M1', 'speed': 1.05, 'steps': 8, 'silence_s': 0.3, 'volume': 1.0}


def speakable(text: str) -> str:
    text = KEY_HINT.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


class SpeakerOutput:
    name = 'speakers'

    def __init__(self, device: str = ''):
        self.device = device

    def play(self, samples, sample_rate: int) -> None:
        """Play and return when done. A device that is not connected plays on the system default instead."""
        import sounddevice as sd
        from shared.audio_devices import output_index
        try:
            index = output_index(self.device)
        except LookupError as exc:
            print(f'[voice] {exc}; using the default output', file=sys.stderr)
            index = None
        samples = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        with sd.OutputStream(device=index, samplerate=sample_rate, channels=1, dtype='float32') as stream:
            stream.write(samples)
            time.sleep(stream.latency)   # write() returns once the last block is queued, not played


def create_outputs(settings: dict):
    """(srs, speakers): SRS ExternalAudio, or None when SRS is not installed; the PC device chosen in the GUI."""
    speakers = SpeakerOutput(settings.get('audio', {}).get('output_device', ''))
    from instructor.srs import SrsError, SrsOutput
    try:
        srs = SrsOutput(settings['srs'], ROOT / 'logs' / 'srs_clips')
        print(f'voice output: SRS via {srs.exe}; PC device {speakers.device or "default"}')
    except SrsError as exc:
        print(f'[voice] {exc}; using the PC speakers', file=sys.stderr)
        srs = None
    return srs, speakers


def agent_channel(agent: dict | None) -> dict | None:
    """The SRS channel an agent transmits on: its own frequency, modulation and name."""
    if not agent or 'frequency_mhz' not in agent:
        return None
    return {'frequencies_mhz': [agent['frequency_mhz']], 'modulations': [agent['modulation']],
            'name': f'{APP_NAME} {agent.get("name", agent["id"])}'}


class Synthesizer:
    """Supertonic with the voice settings; loading the model takes a few seconds."""

    def __init__(self, settings: dict):
        from supertonic import TTS   # heavy import, only when the voice is used
        model_dir = settings['voice'].get('model_dir', 'models/supertonic3')
        self.tts = TTS(model='supertonic-3', model_dir=ROOT / model_dir, auto_download=False)
        self.sample_rate = self.tts.sample_rate
        self.styles = {}
        self.configure(settings)

    def configure(self, settings: dict) -> None:
        self.language = settings['voice'].get('language') or settings['language']
        self.params = {**DEFAULTS, **{k: v for k, v in settings['voice'].items() if k in DEFAULTS}}

    def synthesize(self, text: str, params: dict | None = None):
        """Samples (float32, mono) at self.sample_rate, volume applied; params override the settings (GUI test)."""
        p = {**self.params, **(params or {})}
        if p['name'] not in self.styles:
            self.styles[p['name']] = self.tts.get_voice_style(voice_name=p['name'])
        wav, _ = self.tts.synthesize(text, voice_style=self.styles[p['name']], lang=p.get('language', self.language),
                                     speed=float(p['speed']), total_steps=int(p['steps']),
                                     silence_duration=float(p['silence_s']))
        return np.clip(wav.reshape(-1) * float(p['volume']), -1.0, 1.0).astype(np.float32)


class Voice:
    """Speaks phrases in order. Each phrase may come from an agent (shared/agents.py): then it uses that agent's voice
    settings and, by SRS, that agent's frequency; without an agent, the settings.json "voice" and "srs"."""

    def __init__(self, settings: dict):
        self.synth = Synthesizer(settings)
        self.default_via = settings['voice'].get('output', 'speakers')
        self.srs, self.speakers = create_outputs(settings)
        self.lock = threading.Lock()   # one clip at a time: the queue and the GUI's tests never overlap
        self.queue: queue.Queue[tuple[str, dict | None]] = queue.Queue()
        self.log = None   # record -> None: each clip's timings and outcome (the voice loop logs them)
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def sample_rate(self) -> int:
        return self.synth.sample_rate

    @property
    def output(self):
        """The default output (settings "voice.output"), used by the console tools."""
        return self.srs if self.default_via == 'srs' and self.srs else self.speakers

    def configure(self, settings: dict) -> None:
        """New audio settings from the GUI (PC device...), used from the next phrase on."""
        with self.lock:
            self.synth.configure(settings)
            self.default_via = settings['voice'].get('output', 'speakers')
            self.srs, self.speakers = create_outputs(settings)

    def say(self, text: str, agent: dict | None = None) -> None:
        text = speakable(text)
        if text:
            self.queue.put((text, agent))

    def test(self, text: str, via: str, params: dict | None = None, agent: dict | None = None) -> dict:
        """GUI test: speak text now by 'srs' (on the agent's frequency) or 'speakers' with these voice params;
        returns the timings."""
        with self.lock:
            started = time.perf_counter()
            samples = self.synth.synthesize(speakable(text), params)
            synthesis_s = time.perf_counter() - started
            if via == 'srs':
                if self.srs is None:
                    raise RuntimeError('SRS is not installed')
                self.srs.play(samples, self.sample_rate, agent_channel(agent))
                name = self.srs.name
            else:
                self.speakers.play(samples, self.sample_rate)
                name = self.speakers.name
        return {'synthesis_s': round(synthesis_s, 2), 'audio_s': round(len(samples) / self.sample_rate, 2),
                'via': name}

    def _run(self) -> None:
        while True:
            text, agent = self.queue.get()
            try:
                with self.lock:
                    self._speak(text, agent)
            except Exception as exc:   # a voice failure must never stop the lesson
                print(f'[voice error] {exc}', file=sys.stderr)
            finally:
                self.queue.task_done()

    def _speak(self, text: str, agent: dict | None) -> None:
        # Every clip is logged (self.log, set by the voice loop): live 2026-11-04 the voice went silent in a flight
        # with no error anywhere; this says whether a clip was made, how long each step took and what SRS answered.
        record = {'event': 'voice', 'agent': (agent or {}).get('id'), 'chars': len(text), 'waiting': self.queue.qsize()}
        started = time.perf_counter()
        try:
            params = (agent or {}).get('voice') or {}
            samples = self.synth.synthesize(text, params)
            record.update(synthesis_s=round(time.perf_counter() - started, 2),
                          audio_s=round(len(samples) / self.sample_rate, 2))
            via = params.get('output', self.default_via)
            if via == 'srs' and self.srs:
                played = time.perf_counter()
                try:
                    self.srs.play(samples, self.sample_rate, agent_channel(agent))
                    record.update(via='srs', srs_s=round(time.perf_counter() - played, 2),
                                  srs_said=getattr(self.srs, 'last_output', ''))
                    return
                except Exception as exc:
                    record['srs_error'] = str(exc)
                    print(f'[voice] SRS failed ({exc}); playing on the PC device', file=sys.stderr)
            self.speakers.play(samples, self.sample_rate)
            record['via'] = 'speakers'
        except Exception as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            record['total_s'] = round(time.perf_counter() - started, 2)
            if self.log:
                try:
                    self.log(record)
                except Exception:
                    pass
