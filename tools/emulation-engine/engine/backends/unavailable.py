"""A backend that stands in for one that cannot deliver input.

WHY a backend object and not an exit: when the engine cannot generate input —
a Wayland session, no X display, an X server that will not answer — the useful
thing is for the server to come up anyway and *say so* on the page the user is
already looking at. Exiting leaves ./run.sh printing a line in a terminal
behind the browser, and the console just shows OFFLINE, which reads like "the
engine crashed" rather than "your session cannot be driven, here is the fix".

Nothing ever calls the input methods: engine.py refuses POST /start while
backend.input_ok is False. They raise rather than pass so that a future caller
that forgets the check fails loudly instead of silently doing nothing — which
is the exact bug this class was added to prevent.
"""

from .base import InputBackend


class UnavailableBackend(InputBackend):
    input_ok = False

    # WHY 1440x900 and (0, 0): the startup banner prints screen_size(), and the
    # loop never runs, so these only have to be harmless and non-crashing.
    SCREEN = (1440, 900)

    def __init__(self, name, error, remedy=None):
        # Report the backend that *would* have been used, so /status and the
        # console still say "pynput" and the user is not told about a backend
        # name that appears nowhere in the docs.
        self.name = name
        self.input_error = error
        self.input_remedy = remedy
        self.platform_note = f"input unavailable: {error}"

    def _refuse(self, what):
        raise RuntimeError(
            f"{what} was called on an unavailable backend ({self.input_error}); "
            "engine.py must refuse /start while backend.input_ok is False"
        )

    def mouse_position(self):
        self._refuse("mouse_position")

    def seconds_since_user_input(self):
        """None, not a refusal: the loop never runs on this backend, but the
        governor's snapshot is still built for /status, and answering "this
        platform cannot say" is both true and harmless."""
        return None

    def screen_size(self):
        return self.SCREEN

    def move_mouse(self, x, y):
        self._refuse("move_mouse")

    def click(self, x, y):
        self._refuse("click")

    def scroll(self, lines, direction):
        self._refuse("scroll")

    def tap_key(self, name):
        self._refuse("tap_key")

    def app_switch(self, count):
        self._refuse("app_switch")

    def browser_tab_next(self):
        self._refuse("browser_tab_next")

    def visible_app_count(self):
        # WHY 5 and not a refusal: the same neutral fallback every backend uses
        # when the window manager will not answer, and it reads nothing and
        # touches nothing.
        return 5

    def release_modifiers(self):
        """Nothing was ever held, so there is nothing to release. Must not
        raise: shutdown_engine() calls this unconditionally."""
        return
