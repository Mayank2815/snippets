"""The contract every input backend implements.

The emulation loop in engine/engine.py never talks to the operating system
directly; it calls the methods below on whatever `get_backend()` returned.
A backend is the only place that knows how a key press or a mouse move is
posted on its platform (Quartz on macOS, pynput on Windows and Linux) — the
loop's cadence, probabilities and target rectangle live in the loop and are
the same everywhere.

Every method that generates input must:

- hold keys and buttons for the same random durations the original macOS code
  used (the numbers are copied into each backend with their WHY comments),
- swallow platform errors the loop cannot do anything about (a missing
  Automation permission, a window manager that will not answer) and fall back
  to a sensible value, exactly as the macOS code did, and
- never block for long: the loop checks its stop Event between calls, so a
  backend call that hangs makes /stop hang too.
"""

import os
import time

# WHY a single scale for every sleep: the loop sleeps between keystrokes and
# the backends sleep while holding a key. ENGINE_FAST=1 shrinks both by the
# same factor so the unit tests run a whole cycle in well under a second
# while exercising exactly the same code path as a real run. 0.01 keeps the
# relative ordering of every delay (a 9.5 s pause is still the longest wait).
SLEEP_SCALE = 0.01 if os.environ.get("ENGINE_FAST") == "1" else 1.0


def pause(seconds):
    """time.sleep() honouring ENGINE_FAST. Use it for every hold and gap."""
    time.sleep(seconds * SLEEP_SCALE)


# The semantic key names the loop uses; each backend maps them to its own
# key codes. Arrow keys and Shift never type a character or trigger a
# shortcut on their own, which is what makes them safe to inject.
KEY_NAMES = ("left", "right", "up", "down", "tab", "shift")


class InputBackend:
    """Base class. Subclasses override everything; the bodies here only
    document the contract and raise so a half-implemented backend fails
    loudly at the first call instead of silently doing nothing."""

    # Short identifier reported by GET /status ("quartz", "pynput", "fake").
    name = "base"
    # One human sentence for the startup banner: what drives input and what
    # the user has to grant or install for it to work.
    platform_note = ""

    def mouse_position(self):
        """Return the pointer position as an (x, y) tuple in screen pixels
        (points on macOS)."""
        raise NotImplementedError

    def screen_size(self):
        """Return the main display size as a (width, height) tuple. Falls back
        to a plausible laptop size when the platform cannot say."""
        raise NotImplementedError

    def move_mouse(self, x, y):
        """Move the pointer to (x, y) without pressing anything."""
        raise NotImplementedError

    def click(self, x, y):
        """Left click at (x, y): button down, a short hold, button up."""
        raise NotImplementedError

    def scroll(self, lines, direction):
        """Post `lines` wheel notches; direction is +1 (up) or -1 (down)."""
        raise NotImplementedError

    def tap_key(self, name):
        """Press and release one of KEY_NAMES with a realistic hold time."""
        raise NotImplementedError

    def app_switch(self, count):
        """Hold the platform's app-switcher modifier (Command on macOS, Alt
        elsewhere), press Tab `count` times, release the modifier — with the
        hold times the switcher needs to advance one app per press."""
        raise NotImplementedError

    def browser_tab_next(self):
        """The "next browser tab" chord: Cmd+Option+Right on macOS, Ctrl+Tab
        on Windows and Linux."""
        raise NotImplementedError

    def visible_app_count(self):
        """How many apps/windows the app switcher would cycle through. Any
        failure returns 5 so app_switch() still walks a plausible depth."""
        raise NotImplementedError

    def release_modifiers(self):
        """Post a key-up for every modifier app_switch() holds. Called on
        shutdown in case the worker was killed mid-switch: a synthetic
        modifier that is never released stays held for the whole session and
        every later click and keystroke gets it added. Must never raise."""
        raise NotImplementedError
