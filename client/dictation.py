"""Dictation: the pilot holds the push-to-talk button and speaks; the recognized text is handed on.

Providers ("speech.provider" setting, chosen in the GUI later):
- "chrome": Chrome's own free dictation (client/chrome_dictation.py). Chrome listens to the microphone itself.
- "google_cloud": Google Cloud Speech-to-Text with an API key (client/speech.py); we record the microphone.
- "none": off.
Whisper (local) is planned as another recorded provider.
"""
import json
import queue
import threading
import time
from pathlib import Path

from client.mic import Recorder
from client.ptt import RadioButtons
from client.speech import create_recognizer
from shared.radio import describe, selected_radio, transmit_radio

LOG = Path(__file__).resolve().parent.parent / 'logs' / 'dictation.jsonl'

POLL_S = 0.02            # 50 Hz button polling
MIN_SPEECH_S = 0.3       # shorter presses are taken as accidental
PROVIDERS = ('chrome', 'google_cloud', 'none')


class FrequencyGate:
    """Which agent hears this press: the active agent on the frequency of the radio selected in SRS, like a real
    radio (shared/agents.py; user's design: the frequency is the agent's on-switch). Checked when the button is
    pressed. With "ptt.instructor_frequency_only" off, or without SRS radio data (DCS or SRS not running), a press on
    no agent's frequency goes to the default agent (the instructor) and says so."""

    def __init__(self, agents, radio_state, only_on_frequency: bool = True):
        self.agents, self.radio_state, self.only_on_frequency = agents, radio_state, only_on_frequency
        self.last_radio = None
        self.ptt_radio = 1   # the radio of the last button pressed (shared/radio.transmit_radio)

    def check(self, ptt_radio: int = 1) -> tuple[dict | None, str]:
        """ptt_radio: which push-to-talk button: 1 the main one, 2 "ptt.radio2" (the aircraft's radio 2)."""
        self.ptt_radio = ptt_radio
        radio = transmit_radio(self.radio_state() if self.radio_state else None, ptt_radio)
        self.last_radio = radio
        selected = selected_radio(radio)
        if radio and selected and radio.get('selected') != 0:   # 0 is the intercom
            agent = self.agents.on_frequency(selected.get('freq', 0), selected.get('modulation'))
            if agent:
                return agent, describe(radio)
        if radio and self.only_on_frequency:
            return None, describe(radio)
        agent = self.agents.default()
        note = 'no radio data: listening anyway' if not radio else f'{describe(radio)}: frequency gate off'
        return agent, note


def create_dictation(settings: dict, locale: dict, on_text, on_status=print, radio_state=None, agents=None):
    """The configured dictation, ready to start(); None when it is off. radio_state() returns the cockpit's SRS radio
    data (CockpitState.radio); agents (shared.agents.AgentStore) says who is on which frequency. on_text(text, agent)
    gets each phrase with the agent the pilot was talking to."""
    speech = settings.get('speech', {})
    provider = speech.get('provider', 'none')
    if provider == 'none':
        return None
    ptt = settings['ptt']
    button = RadioButtons(ptt, on_status)
    gate = FrequencyGate(agents, radio_state, ptt.get('instructor_frequency_only', True)) if agents else None
    microphone = settings.get('mic', {}).get('device', '')
    if provider == 'chrome':
        from client.chrome_dictation import ChromeDictation
        return ChromeDictation(button, speech, microphone, locale['dictation_page'], on_text, on_status, gate)
    hints = [word for words in locale['intents'].values() for word in words]
    return RecordedDictation(button, Recorder(microphone), create_recognizer(settings, hints), on_text, on_status,
                             gate)


def log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'time': time.time(), **entry}, ensure_ascii=False) + '\n')


NO_GATE = {'id': None}   # dictation without agents (DICTATION_TEST-style use): every press is heard


def press_agent(gate, on_status, ptt_radio: int = 1) -> dict | None:
    """Called on each press (ptt_radio: which button, 1 or 2): the agent that hears this transmission, or None
    (logged with the radio seen)."""
    agent, note = gate.check(ptt_radio) if gate else (NO_GATE, '')
    log({'event': 'press', 'allowed': agent is not None, 'agent': (agent or {}).get('id'), 'note': note,
         'ptt_radio': ptt_radio, 'radio': selected_radio(gate.last_radio) if gate else None})
    if agent is None:
        on_status('not_for_instructor', note)
    else:
        on_status('listening', f'{agent.get("name", "")} {note}'.strip())
    return agent


class RecordedDictation:
    """Record while the button is held, recognize on release. Recognition runs in its own thread so the button keeps
    being watched while a network call is in flight; results arrive in the order they were spoken."""

    def __init__(self, button, recorder, recognizer, on_text, on_status=print, gate=None):
        """on_text(text) gets each recognized phrase; on_status(kind, detail) gets listening/heard/error notes."""
        self.button, self.recorder, self.recognizer, self.gate = button, recorder, recognizer, gate
        self.name = recognizer.name
        self.on_text, self.on_status = on_text, on_status
        self.clips: queue.Queue = queue.Queue()

    def set_microphone(self, name: str) -> None:
        """Another microphone, chosen in the GUI: used from the next press on."""
        self.recorder = Recorder(name)

    def start(self) -> None:
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._recognize, daemon=True).start()

    def show(self, text: str) -> None:
        pass   # no window of its own

    def _watch(self) -> None:
        recording, was_held, agent = False, False, None
        while True:
            held = self.button.held()   # 0, or the radio of the button held (RadioButtons)
            if held and not was_held:
                agent = press_agent(self.gate, self.on_status, int(held))
                if agent is not None:
                    self.recorder.start()
                    recording = True
            elif recording and not held:
                samples = self.recorder.stop()
                recording = False
                if len(samples) >= MIN_SPEECH_S * self.recorder.rate:
                    self.clips.put((samples, agent))
                else:
                    self.on_status('too_short', '')
            was_held = held
            time.sleep(POLL_S)

    def _recognize(self) -> None:
        while True:
            samples, agent = self.clips.get()
            start = time.time()
            try:
                text = self.recognizer.recognize(samples, self.recorder.rate)
            except Exception as exc:   # a recognition failure must never stop the instructor
                self.on_status('error', str(exc))
                continue
            seconds = len(samples) / self.recorder.rate
            self.on_status('heard', f'"{text}" ({seconds:.1f} s audio, {time.time() - start:.1f} s recognition)')
            if text:
                self.on_text(text, agent)
