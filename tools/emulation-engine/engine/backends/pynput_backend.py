"""Windows and Linux backend: posts input through pynput.

pynput wraps SendInput on Windows and the XTest extension on Linux. Hold
times are the same random durations the Quartz backend uses, so the loop
behaves identically on every platform; only the app-switcher chords differ
(Alt+Tab instead of Cmd+Tab, Ctrl+Tab instead of Cmd+Option+Right).

Linux: pynput needs an X11 session. Under Wayland the X server it connects to
is XWayland, and the compositor never delivers synthetic XTest input to native
Wayland windows — the engine would report RUNNING while nothing on screen
moves. That session check does NOT live here: backends/__init__.py runs
linux_session.check() *before* this module is imported and substitutes an
UnavailableBackend, so by the time this class is constructed the session is
already known to be a usable X11 one. See linux_session.py for why.

pynput also depends on python-xlib and evdev on Linux; evdev builds a C
extension, so pip needs a compiler and headers (install.sh prints the apt line
if that fails).
"""

import ctypes
import ctypes.util
import random
import shutil
import subprocess
import sys

from .base import (
    InputBackend, pause, KEY_HOLD_SECONDS, CLICK_HOLD_SECONDS, CHORD_HOLD_SECONDS,
    SWITCH_SHOW_SECONDS, SWITCH_TAB_HOLD_SECONDS, SWITCH_TAB_GAP_SECONDS,
    SWITCH_ACTIVATE_SECONDS,
)

try:
    from pynput import keyboard, mouse
except ImportError as err:
    script = "install.ps1" if sys.platform == "win32" else "./install.sh"
    raise ImportError(
        f"{err}\n[Error] pynput is not installed for this interpreter. "
        f"Run {script} and start the engine with "
        f"{'run.ps1' if sys.platform == 'win32' else './run.sh'}."
    ) from err

KEYS = {
    "left": keyboard.Key.left,
    "right": keyboard.Key.right,
    "up": keyboard.Key.up,
    "down": keyboard.Key.down,
    "tab": keyboard.Key.tab,
    "shift": keyboard.Key.shift,
}
# WHY Alt: the app switcher on Windows and on every common Linux desktop is
# Alt+Tab, the role Command+Tab has on macOS.
SWITCH_MODIFIER = keyboard.Key.alt
# WHY Ctrl+Tab: "next tab" in every browser on Windows and Linux, the role
# Cmd+Option+Right has in Safari and Chrome on macOS.
TAB_NEXT_MODIFIER = keyboard.Key.ctrl




class _XScreenSaverIdle:
    """Reads the X server's own idle timer through the XScreenSaver extension.

    WHY this and not a keyboard hook: X11 has no "when did the user last touch
    anything" call, but every X server that has the (universally present)
    XScreenSaver extension keeps exactly that number for the screensaver's own
    use, and XTest-injected events reset it the same way real ones do — which
    is what the governor needs, since it separates the person from the engine
    by comparing timestamps, not by asking where an event came from.

    Everything is looked up once and cached, including failure: a box without
    libXss is not going to grow one halfway through a run, and retrying the
    dlopen once a second would be pure waste.
    """

    class _Info(ctypes.Structure):
        # XScreenSaverInfo, from X11/extensions/scrnsaver.h. `idle` is
        # milliseconds since the last input event.
        _fields_ = [
            ("window", ctypes.c_ulong),
            ("state", ctypes.c_int),
            ("kind", ctypes.c_int),
            ("since", ctypes.c_ulong),
            ("idle", ctypes.c_ulong),
            ("event_mask", ctypes.c_ulong),
        ]

    def __init__(self):
        self._ready = None      # None = not tried yet, False = unavailable
        self._display = None
        self._root = None
        self._info = None
        self._xss = None

    def _setup(self):
        try:
            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xss = ctypes.CDLL(ctypes.util.find_library("Xss") or "libXss.so.1")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(self._Info)
            xss.XScreenSaverQueryInfo.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(self._Info)]
            display = x11.XOpenDisplay(None)
            if not display:
                return False
            self._display = display
            self._root = x11.XDefaultRootWindow(ctypes.c_void_p(display))
            self._info = xss.XScreenSaverAllocInfo()
            self._xss = xss
            # Prove it answers before claiming it works — the extension can be
            # absent on an otherwise healthy server, in which case QueryInfo
            # returns 0 and the struct holds rubbish.
            return bool(xss.XScreenSaverQueryInfo(
                ctypes.c_void_p(self._display), self._root, self._info))
        except Exception:
            return False

    def __call__(self):
        """Seconds since the last input, or None when unavailable."""
        if self._ready is None:
            self._ready = self._setup()
        if not self._ready:
            return None
        try:
            if not self._xss.XScreenSaverQueryInfo(
                    ctypes.c_void_p(self._display), self._root, self._info):
                return None
            return self._info.contents.idle / 1000.0
        except Exception:
            return None


def _xprintidle():
    """Fallback for a server without XScreenSaver: the `xprintidle` command,
    which prints the same number in milliseconds. Optional everywhere; absent
    on most boxes, which is why it is only reached after the library fails."""
    if shutil.which("xprintidle") is None:
        return None
    try:
        # WHY timeout=2: the same rule every subprocess in this file follows —
        # the loop only checks its stop Event between calls, so a command that
        # hangs would make /stop hang. This one is polled about once a second,
        # so its ceiling is tighter than the 5 s used for the window count.
        out = subprocess.check_output(["xprintidle"], timeout=2, stderr=subprocess.DEVNULL)
        return int(out.strip()) / 1000.0
    except Exception:
        return None


class PynputBackend(InputBackend):
    name = "pynput"

    def __init__(self):
        self._keyboard = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._x_idle = _XScreenSaverIdle()
        if sys.platform == "win32":
            self.platform_note = "pynput over SendInput (Windows)"
        else:
            self.platform_note = "pynput over X11/XTest (Linux; Wayland sessions do not receive synthetic input)"

    def mouse_position(self):
        x, y = self._mouse.position
        return x, y

    def seconds_since_user_input(self):
        """Seconds since any input reached this computer, or None.

        Windows: GetLastInputInfo, which reports the tick count of the last
        input in this session; the difference from GetTickCount is the idle
        time. Both are 32-bit and wrap together after 49.7 days, which the mask
        below handles rather than returning a nonsense negative once a month.
        Input posted through SendInput (which is how this backend types) resets
        it just as hardware does — that is what makes the governor's
        engine-versus-person comparison work. Unverified on real Windows
        hardware, like the rest of this file's Windows path.

        Linux: the X server's XScreenSaver idle timer, or `xprintidle` when the
        extension is missing. None when neither answers — the governor then
        simply never pauses, and /status says the idle timer is not visible so
        nobody is left believing the pause feature is working when it is not.
        """
        try:
            if sys.platform == "win32":
                class _LastInput(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

                info = _LastInput()
                info.cbSize = ctypes.sizeof(_LastInput)
                if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                    return None
                elapsed_ms = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
                return elapsed_ms / 1000.0
        except Exception:
            return None
        seconds = self._x_idle()
        return seconds if seconds is not None else _xprintidle()

    def screen_size(self):
        try:
            if sys.platform == "win32":
                user32 = ctypes.windll.user32
                # WHY 0 and 1: SM_CXSCREEN / SM_CYSCREEN, the primary display size.
                return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
            from Xlib import display  # a pynput dependency on Linux
            screen = display.Display().screen()
            return int(screen.width_in_pixels), int(screen.height_in_pixels)
        except Exception:
            # WHY 1440x900: a common laptop resolution; only used when the
            # platform cannot be asked, and the loop clamps to a fixed
            # rectangle anyway.
            return 1440, 900

    def move_mouse(self, x, y):
        self._mouse.position = (int(x), int(y))

    def click(self, x, y):
        self._mouse.position = (int(x), int(y))
        self._mouse.press(mouse.Button.left)
        pause(CLICK_HOLD_SECONDS)
        self._mouse.release(mouse.Button.left)

    def scroll(self, lines, direction):
        # pynput's dy is positive for "up", the same sign the loop uses.
        self._mouse.scroll(0, direction * lines)

    def tap_key(self, name):
        key = KEYS[name]
        self._keyboard.press(key)
        pause(random.uniform(*KEY_HOLD_SECONDS))
        self._keyboard.release(key)

    def app_switch(self, count):
        """Alt held, Tab pressed `count` times, with the same hold times as the
        Cmd+Tab sequence on macOS so the switcher advances one app per press."""
        self._keyboard.press(SWITCH_MODIFIER)
        pause(SWITCH_SHOW_SECONDS)

        for _ in range(count):
            self._keyboard.press(keyboard.Key.tab)
            pause(SWITCH_TAB_HOLD_SECONDS)
            self._keyboard.release(keyboard.Key.tab)
            pause(SWITCH_TAB_GAP_SECONDS)

        pause(SWITCH_ACTIVATE_SECONDS)
        self._keyboard.release(SWITCH_MODIFIER)

    def browser_tab_next(self):
        self._keyboard.press(TAB_NEXT_MODIFIER)
        self._keyboard.press(keyboard.Key.tab)
        pause(random.uniform(*CHORD_HOLD_SECONDS))
        self._keyboard.release(keyboard.Key.tab)
        self._keyboard.release(TAB_NEXT_MODIFIER)

    def visible_app_count(self):
        try:
            if sys.platform == "win32":
                return self._windows_window_count()
            return self._linux_window_count()
        except Exception:
            # WHY 5: same fallback as macOS — a plausible switcher depth when
            # the platform will not answer.
            return 5

    @staticmethod
    def _windows_window_count():
        """Count top-level windows that are visible and have a title, which is
        roughly what Alt+Tab shows. ctypes only, so no extra dependency."""
        user32 = ctypes.windll.user32
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        count = [0]

        def on_window(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                count[0] += 1
            return True

        if not user32.EnumWindows(EnumWindowsProc(on_window), 0):
            raise OSError("EnumWindows failed")
        return count[0] if count[0] > 0 else 5

    @staticmethod
    def _linux_window_count():
        """`wmctrl -l` prints one line per managed window. Optional: without
        wmctrl, or without a window manager (bare Xvfb), fall back to 5."""
        if shutil.which("wmctrl") is None:
            return 5
        # WHY timeout=5: same ceiling as osascript on macOS — a window manager
        # that never answers must not hang the worker, or /stop cannot end it.
        output = subprocess.check_output(["wmctrl", "-l"], timeout=5, stderr=subprocess.DEVNULL).decode()
        lines = [line for line in output.splitlines() if line.strip()]
        return len(lines) if lines else 5

    def release_modifiers(self):
        """Key-up for Alt and Ctrl, the modifiers app_switch() and
        browser_tab_next() hold. Called on shutdown; never raises."""
        for key in (SWITCH_MODIFIER, TAB_NEXT_MODIFIER):
            try:
                self._keyboard.release(key)
            except Exception as err:  # never let an input hiccup block the exit
                print(f"[Shutdown] could not release {key}: {err}")
