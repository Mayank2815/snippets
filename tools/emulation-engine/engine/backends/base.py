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


def engine_time():
    """A monotonic clock on the same scale as pause().

    WHY: the activity governor measures ten-second blocks of wall time, but
    ENGINE_FAST=1 shrinks every sleep in the loop by 100. Left on the real
    clock, a whole fast-mode test run would sit inside a single block and the
    governor would behave nothing like it does in production. Dividing by the
    same factor keeps the governor's blocks in exactly the same proportion to
    the loop's cadence at either speed, so the tests exercise the real
    behaviour rather than a degenerate corner of it.
    """
    return time.monotonic() / SLEEP_SCALE


# --- shared hold times ------------------------------------------------------
# WHY these live here and not in each backend: both quartz.py and
# pynput_backend.py used to carry their own copy of every one of these numbers
# with the same WHY comment beside it, which is two places to change and one
# place to forget. They are also the only honest source for the cycle-duration
# model engine/test_metrics.py simulates against — a simulator that re-declared
# them would drift out of date exactly like the old one did.
#
# WHY 0.012-0.025 s: a real key press is held roughly 10-25 ms. Shorter looks
# synthetic; much longer risks the OS starting key repeat.
KEY_HOLD_SECONDS = (0.012, 0.025)
# WHY 0.02 s: a light click — the button is held just long enough to register
# as a press rather than a bounce.
CLICK_HOLD_SECONDS = 0.02
# WHY 0.05-0.10 s: a chord is held a little longer than a plain key so the
# browser sees the modifiers and the key together.
CHORD_HOLD_SECONDS = (0.05, 0.10)
# The app-switcher sequence. These are not decoration: shorter values make the
# switcher collapse the presses into one, or never appear at all.
# WHY 0.08 s: gives the app switcher time to appear before the first Tab.
SWITCH_SHOW_SECONDS = 0.08
# WHY 0.05 s: hold Tab long enough to register as a distinct press.
SWITCH_TAB_HOLD_SECONDS = 0.05
# WHY 0.18 s: the switcher needs a beat between Tabs to advance one app per
# press instead of collapsing them into one.
SWITCH_TAB_GAP_SECONDS = 0.18
# WHY 0.30 s: let the switcher settle on the highlighted app so releasing the
# modifier actually activates it.
SWITCH_ACTIVATE_SECONDS = 0.30

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

    # WHY these three: a backend can be constructed and still be unable to
    # deliver a single event — the Wayland case, where XTest accepts everything
    # and the compositor drops it. engine.py reports them on GET /status and
    # refuses POST /start while input_ok is False, so the engine can never sit
    # in RUNNING while nothing moves. input_error/input_remedy are also set on
    # a *usable* backend when there is a caveat worth showing (see
    # ENGINE_ALLOW_WAYLAND), in which case input_ok stays True.
    input_ok = True
    # One sentence naming the problem, shown in the console.
    input_error = None
    # The steps that fix it, shown in the console and the terminal.
    input_remedy = None

    def mouse_position(self):
        """Return the pointer position as an (x, y) tuple in screen pixels
        (points on macOS)."""
        raise NotImplementedError

    def seconds_since_user_input(self):
        """Seconds since ANY input last reached this computer, or None when
        the platform cannot say.

        This counts the engine's own synthetic input too — every platform's
        idle timer is reset by it — so a caller cannot read this as "seconds
        since the *person* did something". `governor.ActivityGovernor` does
        that separation by comparing the answer against when the engine itself
        last acted; see observe_user_input() there.

        Returning None must always stay an option: a Linux box without the
        XScreenSaver extension and without xprintidle genuinely cannot answer,
        and guessing a number there would make the engine pause at random.
        """
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
