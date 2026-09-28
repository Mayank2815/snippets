"""Picks the input backend for this platform.

    darwin -> quartz   (pyobjc Quartz event taps, the original implementation)
    win32  -> pynput   (SendInput through pynput)
    linux  -> pynput   (XTest through pynput; X11 sessions only)

ENGINE_BACKEND=fake|quartz|pynput overrides the choice. WHY an override: the
unit tests and CI start the whole engine — HTTP server, loop, shutdown — with
the fake backend so they never touch real input, and a developer can force
pynput on a Mac to check that path without a second machine.
"""

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


def get_backend():
    """Import and instantiate the backend for this run.

    Raises ImportError (with a message naming what to install) when the
    platform library is missing, and ValueError for an unusable choice.
    """
    name = backend_name_for()
    if name == "fake":
        from .fake import FakeBackend
        return FakeBackend()
    if name == "quartz":
        from .quartz import QuartzBackend
        return QuartzBackend()
    from .pynput_backend import PynputBackend
    return PynputBackend()
