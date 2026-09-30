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
import random
import shutil
import subprocess
import sys

from .base import InputBackend, pause

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


class PynputBackend(InputBackend):
    name = "pynput"

    def __init__(self):
        self._keyboard = keyboard.Controller()
        self._mouse = mouse.Controller()
        if sys.platform == "win32":
            self.platform_note = "pynput over SendInput (Windows)"
        else:
            self.platform_note = "pynput over X11/XTest (Linux; Wayland sessions do not receive synthetic input)"

    def mouse_position(self):
        x, y = self._mouse.position
        return x, y

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
        # WHY 0.02 s: a light click — the button is held just long enough to
        # register as a press rather than a bounce.
        pause(0.02)
        self._mouse.release(mouse.Button.left)

    def scroll(self, lines, direction):
        # pynput's dy is positive for "up", the same sign the loop uses.
        self._mouse.scroll(0, direction * lines)

    def tap_key(self, name):
        key = KEYS[name]
        self._keyboard.press(key)
        # WHY 0.012-0.025 s: a real key press is held roughly 10-25 ms. Shorter looks
        # synthetic; much longer risks the OS starting key repeat.
        pause(random.uniform(0.012, 0.025))
        self._keyboard.release(key)

    def app_switch(self, count):
        """Alt held, Tab pressed `count` times, with the same hold times as the
        Cmd+Tab sequence on macOS so the switcher advances one app per press."""
        self._keyboard.press(SWITCH_MODIFIER)
        # WHY 0.08 s: gives the app switcher time to appear before the first Tab.
        pause(0.08)

        for _ in range(count):
            self._keyboard.press(keyboard.Key.tab)
            # WHY 0.05 s: hold Tab long enough to register as a distinct press.
            pause(0.05)
            self._keyboard.release(keyboard.Key.tab)
            # WHY 0.18 s: the switcher needs a beat between Tabs to advance one app
            # per press instead of collapsing them into one.
            pause(0.18)

        # WHY 0.30 s: let the switcher settle on the highlighted app so releasing
        # the modifier actually activates it.
        pause(0.30)
        self._keyboard.release(SWITCH_MODIFIER)

    def browser_tab_next(self):
        self._keyboard.press(TAB_NEXT_MODIFIER)
        self._keyboard.press(keyboard.Key.tab)
        # WHY 0.05-0.10 s: a chord is held a little longer than a plain key so the
        # browser sees the modifier and Tab together.
        pause(random.uniform(0.05, 0.10))
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
