"""Decide, before any input is attempted, whether this Linux session can
actually receive synthetic input — and say so in words a user can act on.

WHY this file exists at all: on Wayland, pynput's XTest injection is accepted
by XWayland and then dropped by the compositor. Nothing raises, nothing logs,
the engine reports RUNNING, and the pointer never moves. That is the single
worst failure this tool can have, because the only symptom is the absence of a
symptom. The check below runs at startup, the verdict reaches GET /status, and
POST /start refuses while it is negative, so "RUNNING but nothing moves" is not
a state the engine can be in.

The check is deliberately environment-only and pure, so the tests can exercise
every branch on any machine with no X server and no Wayland compositor.
"""

import os

# WHY an escape hatch: someone may genuinely only care about XWayland windows
# (a legacy X11 app running under a Wayland desktop), and XTest does reach
# those. Setting ENGINE_ALLOW_WAYLAND=1 lets them through — but the warning is
# still reported by /status and still shown in the console, so this downgrades
# a refusal to a visible caveat and never back to silence.
OVERRIDE_VAR = "ENGINE_ALLOW_WAYLAND"

WAYLAND_MESSAGE = (
    "Wayland session detected — synthetic input is not delivered to applications."
)
WAYLAND_REMEDY = (
    "Log out. At the login screen click your name, then the gear icon at the "
    "bottom right, and choose \"Ubuntu on Xorg\" (on other desktops: \"GNOME on "
    "Xorg\", \"Plasma (X11)\"). Log back in and run ./run.sh again. Confirm with "
    "`echo $XDG_SESSION_TYPE` — it must print x11. See the Linux section of "
    "README.md if your login screen offers no Xorg session."
)
WAYLAND_OVERRIDE_MESSAGE = (
    "Wayland session — input reaches only old X11/XWayland windows, not native "
    "Wayland apps (running anyway because " + OVERRIDE_VAR + "=1)."
)

NO_DISPLAY_MESSAGE = "No X display — DISPLAY is not set, so there is nothing to send input to."
NO_DISPLAY_REMEDY = (
    "Run the engine from the graphical session of the computer you want it to "
    "drive, not over a plain SSH connection and not from a text console."
)


class SessionVerdict:
    """The answer to 'can this session receive input, and if not, what do I say?'

    ok       — False means POST /start must refuse.
    kind     — "x11", "wayland" or "headless"; for logs and tests.
    message  — one sentence naming the problem, shown in the console.
    remedy   — the steps that fix it, shown in the console and the terminal.
    """

    def __init__(self, ok, kind, message=None, remedy=None):
        self.ok = ok
        self.kind = kind
        self.message = message
        self.remedy = remedy

    @property
    def full_text(self):
        """Message and remedy as one string, for the terminal banner."""
        return " ".join(part for part in (self.message, self.remedy) if part)

    def __repr__(self):
        return f"SessionVerdict(ok={self.ok!r}, kind={self.kind!r}, message={self.message!r})"


def check(env=None):
    """Classify the session from its environment variables alone.

    Wayland is detected from either signal, not both: a desktop can set
    WAYLAND_DISPLAY while leaving XDG_SESSION_TYPE unset or "tty" (sway and
    Hyprland started from a text console do exactly this), and that case
    produced no warning at all before. Treating either signal as decisive can
    in principle refuse an X11 session that left WAYLAND_DISPLAY lying around,
    which is a visible, overridable annoyance — the opposite mistake is the
    invisible one this whole module exists to prevent.
    """
    env = os.environ if env is None else env

    wayland_display = (env.get("WAYLAND_DISPLAY") or "").strip()
    session_type = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    display = (env.get("DISPLAY") or "").strip()
    override = (env.get(OVERRIDE_VAR) or "").strip() == "1"

    if wayland_display or session_type == "wayland":
        if override and display:
            return SessionVerdict(True, "wayland", WAYLAND_OVERRIDE_MESSAGE, WAYLAND_REMEDY)
        if override and not display:
            # Overriding does not conjure an X server to inject through.
            return SessionVerdict(False, "wayland", WAYLAND_MESSAGE + " There is also no X display to fall back to.", WAYLAND_REMEDY)
        return SessionVerdict(False, "wayland", WAYLAND_MESSAGE, WAYLAND_REMEDY)

    if not display:
        return SessionVerdict(False, "headless", NO_DISPLAY_MESSAGE, NO_DISPLAY_REMEDY)

    return SessionVerdict(True, "x11")
