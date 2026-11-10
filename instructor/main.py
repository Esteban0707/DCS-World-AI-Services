"""DCS-Instructor, local mode: cockpit stream + lesson + voice + pilot input in one process.

Usage: python -m instructor.main [--no-voice]
Pilot input is typed in this console for now (dictation replaces it later): short lesson commands ("continue",
"repeat", in the pilot's language from the locale file) drive the lesson, anything else goes to the brain with the
current context.
"""
import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

from instructor.brain import BRAIN_ERROR_TEXT, BrainError, create_brain
from instructor.tower_view import TowerView
from instructor.lesson import Lesson
from instructor.pilot_input import classify
from shared.cockpit import CockpitState, CockpitStream
from shared.locale import load_locale
from shared.modules import find_module

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / 'logs' / 'instructor.jsonl'


class Instructor:
    def __init__(self, settings: dict, voice):
        self.settings = settings
        self.language = settings['language']
        self.locale = load_locale(self.language)
        self.brain = create_brain(settings)
        self.voice = voice
        self.state = CockpitState()
        self.lesson: Lesson | None = None
        self.dictation = None
        self.log = LOG.open('a', encoding='utf-8')

    def record(self, kind: str, **data) -> None:
        self.log.write(json.dumps({'time': time.time(), 'model_time': self.state.time, 'kind': kind, **data},
                                  ensure_ascii=False) + '\n')
        self.log.flush()

    def say(self, text: str) -> None:
        print(f'\nINSTRUCTOR: {text}\n')
        self.record('say', text=text)
        if self.dictation:
            self.dictation.show(text)
        if self.voice:
            self.voice.say(text)

    def on_cockpit(self, message: dict) -> None:
        kind = self.state.apply(message)
        if kind == 'aircraft':
            self.start_lesson()
        if self.lesson and kind in ('update', 'snapshot'):
            self.handle(self.lesson.tick(self.state))

    def start_lesson(self) -> None:
        module = find_module(self.state.aircraft)
        path = module / 'training' / f'{self.settings["lesson"]}.json' if module else None
        if not path or not path.exists():
            print(f'no lesson for {self.state.aircraft}')
            self.lesson = None
            return
        self.lesson = Lesson(path, self.language)
        self.record('lesson_start', lesson=path.name, aircraft=self.state.aircraft)
        self.say(self.locale['lesson_started'].format(name=self.lesson.name))

    def handle(self, events) -> None:
        for event in events:
            if event.kind == 'say':
                self.say(event.text)
            elif event.kind == 'highlight':
                print(f'   >> {self.locale["look_at"].format(controls=", ".join(event.controls))}')
                self.record('highlight', controls=event.controls)
            elif event.kind == 'wait':
                print(f'   >> {self.locale["waiting_for_pilot"]}')
            elif event.kind == 'unsupported':
                print(f'   [paused at "{event.rule}": cannot check {event.text} outside the training mission]')
                self.record('unsupported', rule=event.rule, conditions=event.text)
            elif event.kind == 'finished':
                self.say(self.locale['lesson_finished'])
            else:
                self.record(event.kind, rule=event.rule, controls=event.controls)

    def on_pilot(self, text: str) -> None:
        intent = classify(text, self.locale)
        self.record('pilot', text=text, intent=intent)
        if intent == 'continue' and self.lesson:
            self.handle(self.lesson.pilot_continue(self.state.time))
        elif intent == 'repeat':
            last = self.lesson.last_text if self.lesson else ''
            self.say(last or self.locale['nothing_to_repeat'])
        else:
            context = self.lesson.context(self.state) if self.lesson else TowerView(self.state).situation()
            try:
                answer = self.brain.ask(text, context, self.language)
            except BrainError as exc:
                self.record('brain_error', provider=self.brain.name, question=text, kind=exc.kind, error=str(exc))
                self.say(self.locale[BRAIN_ERROR_TEXT.get(exc.kind, 'brain_error')])
                return
            self.record('brain', provider=self.brain.name, question=text, answer=answer)
            self.say(answer)


def read_console(lines: queue.Queue) -> None:
    for line in sys.stdin:
        if line.strip():
            lines.put(line.strip())


def print_dictation_status(kind: str, detail: str) -> None:
    print(f'   [dictation {kind}] {detail}'.rstrip())


def start_dictation(settings: dict, locale: dict, lines: queue.Queue, state: CockpitState):
    """Push-to-talk dictation feeds the same pilot input as the console; without it, typing still works."""
    if settings.get('speech', {}).get('provider', 'none') == 'none':
        print('dictation off ("speech.provider" is "none"); type to talk to the instructor')
        return None
    if 'ptt' not in settings:
        print('dictation off: no push-to-talk button yet (run utilities\\PTT_PICK.cmd); type to talk to the instructor')
        return None
    from client.dictation import create_dictation
    try:
        dictation = create_dictation(settings, locale, on_text=lambda text, agent=None: lines.put(text), on_status=print_dictation_status,
                                     radio_state=lambda: state.radio)
        dictation.start()
    except Exception as exc:
        print(f'dictation off: {exc}')
        return None
    ptt = settings['ptt']
    print(f'dictation on ({dictation.name}): hold {ptt["joystick"]} button {ptt["button"]} and speak')
    return dictation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--no-voice', action='store_true', help='text only, no Supertonic')
    parser.add_argument('--port', type=int, help='cockpit stream port (default: settings.json)')
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    settings = json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))
    if args.port:
        settings['cockpit_port'] = args.port
    LOG.parent.mkdir(parents=True, exist_ok=True)
    voice = None
    if not args.no_voice:
        from instructor.voice import Voice
        voice = Voice(settings)
    instructor = Instructor(settings, voice)
    stream = CockpitStream(settings['cockpit_port'], timeout=0.1)
    pilot_lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=read_console, args=(pilot_lines,), daemon=True).start()
    instructor.dictation = start_dictation(settings, instructor.locale, pilot_lines, instructor.state)
    print(f'instructor ready: waiting for DCS on port {settings["cockpit_port"]}; type here to talk to the instructor')

    while True:
        message = stream.receive()
        if message:
            instructor.on_cockpit(message)
        while not pilot_lines.empty():
            instructor.on_pilot(pilot_lines.get())


if __name__ == '__main__':
    main()
