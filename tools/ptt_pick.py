"""Choose the push-to-talk button: press it on any joystick and it is saved to settings.json "ptt".

Usage: python tools/ptt_pick.py            the main button (the radio the aircraft has selected, else radio 1)
       python tools/ptt_pick.py --radio2   a second button that always transmits on radio 2 ("ptt.radio2")
       python tools/ptt_pick.py --radio2 --clear   no radio 2 button
Use buttons other than SRS's own PTT, or the pilot transmits on the radio twice.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.ptt import buttons_down, joysticks   # noqa: E402

CLICK_MAX_S = 3.0


def main() -> None:
    radio2, clear = '--radio2' in sys.argv, '--clear' in sys.argv
    settings_path = ROOT / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    ptt = settings.get('ptt', {})
    if radio2 and clear:
        ptt.pop('radio2', None)
        save(settings_path, settings, ptt)
        print('saved: no radio 2 button')
        return
    found = joysticks()
    if not found:
        print('no joystick connected')
        return
    for joy in found:
        print(f'joystick {joy["id"]}: {joy["name"]} ({joy["buttons"]} buttons)')
    which = 'radio 2 (always transmits on the aircraft\'s radio 2)' if radio2 else 'the main radio'
    print(f'\npress and release the push-to-talk button for {which} (Ctrl+C to cancel)...')
    joy, button = wait_for_click(found)
    if radio2:
        if joy['name'] == ptt.get('joystick') and button == ptt.get('button'):
            print('that is already the main button: choose another one')
            return
        ptt['radio2'] = {'joystick': joy['name'], 'button': button}
    else:
        ptt.update({'joystick': joy['name'], 'button': button})
    save(settings_path, settings, ptt)
    print(f'saved: {joy["name"]}, button {button}')


def save(path: Path, settings: dict, ptt: dict) -> None:
    settings['ptt'] = ptt
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def wait_for_click(found: list[dict]) -> tuple[dict, int]:
    """The first button pressed and released within CLICK_MAX_S. Some devices report state "buttons" that switch on
    by themselves and stay on (the X-56 stick's button 15, seen 2026-11-01): those never count as a click."""
    previous = {joy['id']: buttons_down(joy['id']) for joy in found}
    pressed_at: dict[tuple[int, int], float] = {}
    while True:
        now = time.monotonic()
        for joy in found:
            down = buttons_down(joy['id'])
            for button in down - previous[joy['id']]:
                pressed_at[joy['id'], button] = now
            for button in previous[joy['id']] - down:
                started = pressed_at.pop((joy['id'], button), None)
                if started is not None and now - started <= CLICK_MAX_S:
                    return joy, button
            previous[joy['id']] = down
        time.sleep(0.01)


if __name__ == '__main__':
    main()
