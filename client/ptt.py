"""Push-to-talk on a joystick button (Windows joystick API, winmm: up to 16 devices and 32 buttons each).

Joysticks are chosen by name (part of the product name Windows shows, e.g. "X-56 Rhino Stick") so the setting
survives devices being plugged in a different order. Buttons are numbered from 1, as in Windows' "Game controllers"
panel. The pilot PC runs DCS, so this is Windows-only.
"""
import ctypes
import ctypes.wintypes as wt
import sys

JOY_RETURNBUTTONS = 0x80
JOYERR_NOERROR = 0
OEM_KEY = r'System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM\VID_{:04X}&PID_{:04X}'


class JOYINFOEX(ctypes.Structure):
    _fields_ = [(name, wt.DWORD) for name in (
        'dwSize', 'dwFlags', 'dwXpos', 'dwYpos', 'dwZpos', 'dwRpos', 'dwUpos', 'dwVpos', 'dwButtons',
        'dwButtonNumber', 'dwPOV', 'dwReserved1', 'dwReserved2')]


class JOYCAPSW(ctypes.Structure):
    _fields_ = ([('wMid', wt.WORD), ('wPid', wt.WORD), ('szPname', wt.WCHAR * 32)]
                + [(name, wt.UINT) for name in (
                    'wXmin', 'wXmax', 'wYmin', 'wYmax', 'wZmin', 'wZmax', 'wNumButtons', 'wPeriodMin', 'wPeriodMax',
                    'wRmin', 'wRmax', 'wUmin', 'wUmax', 'wVmin', 'wVmax', 'wCaps', 'wMaxAxes', 'wNumAxes',
                    'wMaxButtons')]
                + [('szRegKey', wt.WCHAR * 32), ('szOEMVxD', wt.WCHAR * 260)])


class PttError(Exception):
    pass


def _winmm():
    if sys.platform != 'win32':
        raise PttError('joystick push-to-talk is Windows-only')
    return ctypes.windll.winmm


def _oem_name(vendor: int, product: int) -> str:
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, OEM_KEY.format(vendor, product)) as key:
                return winreg.QueryValueEx(key, 'OEMName')[0]
        except OSError:
            continue
    return ''


def joysticks() -> list[dict]:
    """Connected joysticks: winmm id, product name and button count."""
    winmm, found = _winmm(), []
    for joy_id in range(winmm.joyGetNumDevs()):
        info = JOYINFOEX(dwSize=ctypes.sizeof(JOYINFOEX), dwFlags=JOY_RETURNBUTTONS)
        if winmm.joyGetPosEx(joy_id, ctypes.byref(info)) != JOYERR_NOERROR:
            continue   # id not connected
        caps = JOYCAPSW()
        winmm.joyGetDevCapsW(joy_id, ctypes.byref(caps), ctypes.sizeof(caps))
        found.append({'id': joy_id, 'name': _oem_name(caps.wMid, caps.wPid) or caps.szPname,
                      'buttons': caps.wNumButtons})
    return found


def buttons_down(joy_id: int) -> set[int]:
    """Buttons held right now, numbered from 1; empty if the joystick is gone."""
    info = JOYINFOEX(dwSize=ctypes.sizeof(JOYINFOEX), dwFlags=JOY_RETURNBUTTONS)
    if _winmm().joyGetPosEx(joy_id, ctypes.byref(info)) != JOYERR_NOERROR:
        return set()
    return {bit + 1 for bit in range(32) if info.dwButtons >> bit & 1}


class JoystickButton:
    def __init__(self, joystick: str, button: int):
        self.joystick, self.button = joystick, button
        self.joy_id = self._find()

    def _find(self) -> int:
        for joy in joysticks():
            if self.joystick.lower() in joy['name'].lower():
                return joy['id']
        raise PttError(f'joystick "{self.joystick}" not found; run tools/ptt_pick.py')

    def held(self) -> bool:
        return self.button in buttons_down(self.joy_id)


class RadioButtons:
    """The push-to-talk buttons, one per radio as in SRS (user's design, 2026-11-03): settings "ptt" {joystick,
    button} is the main one (the radio the aircraft has selected, else radio 1); optional "ptt.radio2" {joystick
    (default: the same), button} always transmits on radio 2. held() -> 0, or the radio of the button held."""

    def __init__(self, ptt: dict, on_status=print):
        self.buttons = [(JoystickButton(ptt['joystick'], ptt['button']), 1)]
        second = ptt.get('radio2') or {}
        if second.get('button'):
            try:
                self.buttons.append((JoystickButton(second.get('joystick') or ptt['joystick'], second['button']), 2))
            except PttError as exc:   # the main button still works
                on_status('error', f'radio 2 push-to-talk: {exc}')

    def held(self) -> int:
        return next((radio for button, radio in self.buttons if button.held()), 0)
