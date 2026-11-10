"""Live check of the switch position table: flip switches in DCS and confirm what the summary says.

Usage: python tools/positions_check.py   (utilities/POSITIONS_CHECK.cmd; close INSTRUCTOR / DCS-Instructor first: same port)
Each switch change is printed with its label as modules/<id>/positions.json reads it. For an unverified one, answer
in this console: Enter or "y" = the label is right (marked verified); "n" = wrong (its positions are reversed and
marked verified); "s" = skip. Answers go to positions_overrides.json and the table is rebuilt at once.
"""
import json
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.cockpit import CockpitState, CockpitStream          # noqa: E402
from shared.cockpit_summary import CockpitSummary, load_positions   # noqa: E402
from shared.modules import find_module                           # noqa: E402
from tools.build_positions import build                          # noqa: E402


def save_answer(module_dir: Path, arg: int, entry: dict, correct: bool) -> None:
    path = module_dir / 'positions_overrides.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'args': {}}
    when = time.strftime('%Y-%m-%d')
    if correct:
        override = {'source': f'live check {when}: label confirmed in the cockpit'}
    else:
        values = [p['value'] for p in entry['positions']]
        labels = [p['label'] for p in entry['positions']]
        override = {'source': f'live check {when}: rule order was wrong, reversed',
                    'positions': [{'value': v, 'label': l} for v, l in zip(values, reversed(labels))]}
    data['args'][str(arg)] = override
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (module_dir / 'positions.json').write_text(json.dumps(build(module_dir), indent=1, ensure_ascii=False) + '\n',
                                               encoding='utf-8')


def main() -> None:
    settings = json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))
    try:
        stream = CockpitStream(settings['cockpit_port'], timeout=0.2)
    except OSError as exc:
        raise SystemExit(f'cockpit port {settings["cockpit_port"]} is in use: close the instructor first ({exc})')
    answers: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [answers.put(line.strip().lower()) for line in sys.stdin], daemon=True).start()
    print('waiting for DCS... flip switches; answer Enter/y = right, n = wrong (reverse), s = skip')

    state, summary, module_dir, pending = CockpitState(), None, None, None
    while True:
        message = stream.receive()
        if message:
            kind = state.apply(message)
            if kind == 'aircraft':
                module_dir = find_module(state.aircraft)
                summary = CockpitSummary(load_positions(module_dir))
                print(f'aircraft {state.aircraft}, module {module_dir.name if module_dir else "none"}, '
                      f'{len(summary.positions)} switches known')
            if summary and message.get('args'):
                for change in summary.update(message['args']):
                    if change.old is None:
                        continue
                    value = message['args'].get(str(change.arg), message['args'].get(change.arg))
                    mark = '' if change.verified else '   <- right? [Enter/y, n, s]'
                    print(f'{change.text()}  (arg {change.arg} = {value:.3f}){mark}')
                    if not change.verified:
                        pending = change
        while not answers.empty():
            answer = answers.get()
            if not pending or answer == 's':
                pending = None
                continue
            correct = answer in ('', 'y', 'yes')
            if correct or answer in ('n', 'no'):
                save_answer(module_dir, pending.arg, summary.positions[pending.arg], correct)
                summary = CockpitSummary(load_positions(module_dir))
                summary.labels = {}   # relearn current positions with the corrected table
                print(f'   saved: {pending.name} {"confirmed" if correct else "reversed"}')
            pending = None


if __name__ == '__main__':
    main()
