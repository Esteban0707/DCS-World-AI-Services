"""Windows-only: show the SRS client's main window.

With "start minimised" set, the SRS client creates no main window at all, only its tray icon. The tray icon is a
Windows Forms NotifyIcon whose hidden message window receives the tray callback message; posting a left
double-click there is what the user's own double-click on the icon does, and the client opens its window.
"""
import ctypes
import ctypes.wintypes as wt

MAIN_TITLE = 'DCS-SRS Client'
TRAY_CLASS = 'WindowsForms10.Window'
WM_TRAY_MOUSE = 0x0400 + 1024          # WM_USER + 1024: Windows Forms NotifyIcon callback message
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0201, 0x0202, 0x0203
SW_RESTORE = 9

user32 = ctypes.windll.user32


def windows_of(pid: int) -> list[tuple[int, str, str, bool]]:
    """(handle, title, class, visible) of every top-level window of a process."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            user32.GetClassNameW(hwnd, cls, 256)
            found.append((hwnd, title.value, cls.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(visit, 0)
    return found


def bring_to_front(pid: int) -> bool:
    """True when the main window is visible (it is then restored and put in front)."""
    for hwnd, title, _, visible in windows_of(pid):
        if visible and title.startswith(MAIN_TITLE):
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return True
    return False


def double_click_tray(pid: int) -> bool:
    """True when the tray icon's window was found and the double-click was posted."""
    for hwnd, _, cls, _ in windows_of(pid):
        if cls.startswith(TRAY_CLASS):
            for message in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                user32.PostMessageW(hwnd, WM_TRAY_MOUSE, 0, message)
            return True
    return False
