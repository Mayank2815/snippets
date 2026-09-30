"""A backend that records every call and touches nothing.

Used by engine/tests/test_engine.py (ENGINE_BACKEND=fake) to run the real
server and the real loop on a machine whose mouse and keyboard must not
move — a developer laptop, CI, or this Mac while the real engine is up.
"""

from .base import InputBackend


class FakeBackend(InputBackend):
    name = "fake"
    platform_note = "fake backend: records calls, generates no input"

    # WHY 1440x900 and (100, 100): a common laptop resolution and a pointer
    # position inside the loop's target rectangle, so a test cycle looks like
    # a real one without any special-casing.
    SCREEN = (1440, 900)
    POSITION = (100, 100)

    def __init__(self):
        # Every call as (method, args) in order. Tests assert on this.
        self.calls = []
        # When a test sets this to a threading.Event, every call waits on it
        # until it is set. That parks the loop inside a step so the test can
        # observe the STOPPING state (worker alive, stop requested) instead of
        # racing a loop that finishes its cycle in milliseconds.
        self.gate = None
        self._position = self.POSITION

    def _record(self, method, *args):
        if self.gate is not None:
            self.gate.wait()
        self.calls.append((method, args))

    def mouse_position(self):
        self._record("mouse_position")
        return self._position

    def screen_size(self):
        self._record("screen_size")
        return self.SCREEN

    def move_mouse(self, x, y):
        self._record("move_mouse", x, y)
        self._position = (x, y)

    def click(self, x, y):
        self._record("click", x, y)

    def scroll(self, lines, direction):
        self._record("scroll", lines, direction)

    def tap_key(self, name):
        self._record("tap_key", name)

    def app_switch(self, count):
        self._record("app_switch", count)

    def browser_tab_next(self):
        self._record("browser_tab_next")

    def visible_app_count(self):
        self._record("visible_app_count")
        return 5

    def release_modifiers(self):
        self._record("release_modifiers")
