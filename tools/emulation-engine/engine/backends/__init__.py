"""Picks the input backend for this platform.

    darwin -> quartz   (pyobjc Quartz event taps, the original implementation)
    win32  -> pynput   (SendInput through pynput)
    linux  -> pynput   (XTest through pynput; X11 sessions only)

ENGINE_BACKEND=fake|quartz|pynput overrides the choice. WHY an override: the
unit tests and CI start the whole engine — HTTP server, loop, shutdown — with
the fake backend so they never touch real input, and a developer can force
pynput on a Mac to check that path without a second machine.

On Linux the session is checked *before* pynput is imported (see
linux_session.py). A session that cannot receive synthetic input — Wayland, or
no X display — yields an UnavailableBackend instead: the server still starts,
so the console can show the user what is wrong and how to fix it, and
POST /start refuses. It is the one platform where a backend can construct
perfectly and still deliver nothing.
"""

import importlib.util
import os
import sys

from .base import InputBackend  # noqa: F401  (re-exported for type checks and tests)

_BY_PLATFORM = {
    "darwin": "quartz",
    "win32": "pynput",
    "linux": "pynput",
}

VALID_BACKENDS = ("fake", "quartz", "pynput")


def backend_name_for(platform=None, override=None):
    """Resolve the backend name without importing anything: the override
    when given, else the platform default. Raises ValueError for an unknown
    override or an unsupported platform so the engine can print one clear
    line and exit instead of failing deep inside an import."""
    override = (override if override is not None else os.environ.get("ENGINE_BACKEND", "")).strip().lower()
    if override:
        if override not in VALID_BACKENDS:
            raise ValueError(f"ENGINE_BACKEND={override!r} is not one of {', '.join(VALID_BACKENDS)}")
        return override
    platform = platform if platform is not None else sys.platform
    # sys.platform is "linux" on Python 3 but was "linux2" on 2; startswith
    # also covers "linux-armv7l"-style values some distributions report.
    if platform.startswith("linux"):
        platform = "linux"
    try:
        return _BY_PLATFORM[platform]
    except KeyError:
        raise ValueError(
            f"no input backend for platform {platform!r} (supported: darwin, win32, linux); "
            "set ENGINE_BACKEND=fake to run the server without generating input"
        ) from None


def _pynput_backend():
    """Build the pynput backend, turning a Linux session that cannot receive
    input into an UnavailableBackend rather than a crash or a silent no-op."""
    from .unavailable import UnavailableBackend

    on_linux = sys.platform.startswith("linux")

    if on_linux:
        from . import linux_session
        verdict = linux_session.check()
        if not verdict.ok:
            return UnavailableBackend("pynput", verdict.message, verdict.remedy)

    # WHY find_spec before the import: pynput's Linux import connects to the X
    # server and, when that fails, raises ImportError with the *X* problem in
    # the message. Deciding "is it installed?" from that ImportError told users
    # on a Wayland-only session to run ./install.sh again, which never helped.
    # find_spec answers the install question on its own.
    if importlib.util.find_spec("pynput") is None:
        script = "install.ps1" if sys.platform == "win32" else "./install.sh"
        run = "run.ps1" if sys.platform == "win32" else "./run.sh"
        raise ImportError(
            f"pynput is not installed for this interpreter. Run {script} and start the engine with {run}."
        )

    try:
        from .pynput_backend import PynputBackend
        backend = PynputBackend()
    except Exception as err:
        # pynput is installed, so this is the display refusing the connection —
        # an X server that died, a DISPLAY pointing somewhere unreachable, a
        # missing X authority cookie. Naming that is far more useful than
        # either a traceback or advice to reinstall.
        if not on_linux:
            raise
        return UnavailableBackend(
            "pynput",
            f"Could not connect to the X display {os.environ.get('DISPLAY', '')!r}: {err}",
            "Check that you are running this from the graphical session of this computer and that "
            "an X server is running. `xdpyinfo` should print the display's details; if it does not, "
            "the engine cannot reach it either.",
        )

    if on_linux:
        # A usable-but-caveated session (ENGINE_ALLOW_WAYLAND=1) keeps
        # input_ok True and still carries its warning to /status.
        backend.input_error = verdict.message
        backend.input_remedy = verdict.remedy
    return backend


def get_backend():
    """Import and instantiate the backend for this run.

    Raises ImportError (with a message naming what to install) when the
    platform library is missing, and ValueError for an unusable choice.
    Never raises for a Linux session that simply cannot receive input —
    that returns an UnavailableBackend so the console can explain it.
    """
    name = backend_name_for()
    if name == "fake":
        from .fake import FakeBackend
        return FakeBackend()
    if name == "quartz":
        from .quartz import QuartzBackend
        return QuartzBackend()
    return _pynput_backend()
