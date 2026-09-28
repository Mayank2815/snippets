"""macOS backend: posts input through Quartz event taps (pyobjc).

This is the original engine code, moved here unchanged apart from being
wrapped in the InputBackend contract. It needs pyobjc's Quartz bindings (see
requirements.txt / install.sh) and the terminal that launches the engine must
be allowed under System Settings -> Privacy & Security -> Accessibility,
otherwise macOS silently drops every posted event.
"""

import os
import random
import subprocess
import sys

from .base import InputBackend, pause


def _import_quartz():
    from Quartz.CoreGraphics import (
        CGEventCreateMouseEvent, CGEventPost, kCGHIDEventTap,
        kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        CGEventCreate, CGEventGetLocation, CGEventCreateKeyboardEvent,
        CGEventCreateScrollWheelEvent, kCGScrollEventUnitLine
    )
    return (
        CGEventCreateMouseEvent, CGEventPost, kCGHIDEventTap,
        kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        CGEventCreate, CGEventGetLocation, CGEventCreateKeyboardEvent,
        CGEventCreateScrollWheelEvent, kCGScrollEventUnitLine
    )


try:
    try:
        _quartz = _import_quartz()
    except ImportError:
        # --- LEGACY ENVIRONMENT PATH BOOTSTRAP ---
        # WHY: the engine was originally launched by a Node process with the bare
        # system `python3`, where pyobjc lived only in the user's Python 3.9
        # site-packages and was not always on sys.path. Inside the .venv that
        # install.sh creates the import above succeeds and this block never runs;
        # the legacy paths are only appended (never put first) so they can never
        # shadow the packages the venv installed.
        user_site_packages = os.path.expanduser("~/Library/Python/3.9/lib/python/site-packages")
        command_line_packages = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/site-packages"
        for legacy_path in (user_site_packages, command_line_packages):
            if legacy_path not in sys.path:
                sys.path.append(legacy_path)
        _quartz = _import_quartz()
except ImportError as err:
    raise ImportError(
        f"{err}\n[Error] Quartz (pyobjc) is not installed for this interpreter. "
        "Run ./install.sh and start the engine with ./run.sh."
    ) from err

(
    CGEventCreateMouseEvent, CGEventPost, kCGHIDEventTap,
    kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
    CGEventCreate, CGEventGetLocation, CGEventCreateKeyboardEvent,
    CGEventCreateScrollWheelEvent, kCGScrollEventUnitLine
) = _quartz

# --- FIXED ABSOLUTE TELEMETRY INJECTION KEYBOARD MATRIX ---
# Strictly bound to pure navigation safe arrow keys as requested
# WHY these key codes: macOS virtual key codes 123/124/125/126 are Left/Right/
# Down/Up arrow. Arrow keys move a caret or selection but never type a character
# or trigger a shortcut on their own, so they are the safest keys to inject.
# WHY 56: virtual key code for the left Shift key — a modifier that does nothing
# on its own, mixed into the burst so it is not 100% arrow keys.
# WHY 48: virtual key code for Tab, used with Cmd held for the app switcher.
KEY_CODES = {
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "shift": 56,
    "tab": 48,
}
TAB_KEY = KEY_CODES["tab"]
# WHY 124: virtual key code for Right arrow, used with Cmd+Option to move to the
# next browser tab.
RIGHT_ARROW = 124
# WHY 55: virtual key code for the left Command key.
COMMAND_KEY = 55
# WHY 1048576 (0x100000): kCGEventFlagMaskCommand. A Tab event must carry the
# Command flag or the app switcher treats it as a plain Tab keypress.
FLAG_COMMAND = 1048576
# WHY 1572864 (0x180000): kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate,
# i.e. Cmd+Option — Cmd+Option+Right is "next tab" in Safari and Chrome.
FLAG_COMMAND_OPTION = 1572864


def CGEventSetFlags(event, flags):
    from Quartz.CoreGraphics import CGEventSetFlags as _CGEventSetFlags
    _CGEventSetFlags(event, flags)


def post_mouse_event(x, y, event_type):
    event = CGEventCreateMouseEvent(None, event_type, (x, y), 0)
    CGEventPost(kCGHIDEventTap, event)


class QuartzBackend(InputBackend):
    name = "quartz"
    platform_note = "macOS Quartz event taps; the terminal app needs the Accessibility permission"

    def mouse_position(self):
        event = CGEventCreate(None)
        pointer = CGEventGetLocation(event)
        return pointer.x, pointer.y

    def screen_size(self):
        try:
            from Quartz.CoreGraphics import CGMainDisplayID, CGDisplayBounds
            bounds = CGDisplayBounds(CGMainDisplayID())
            return int(bounds.size.width), int(bounds.size.height)
        except Exception:
            # WHY 1280x800: the 13" display the loop's target rectangle was
            # tuned for (see the clamp in loop_worker).
            return 1280, 800

    def move_mouse(self, x, y):
        post_mouse_event(x, y, kCGEventMouseMoved)

    def click(self, x, y):
        post_mouse_event(x, y, kCGEventLeftMouseDown)
        # WHY 0.02 s: a light click — the button is held just long enough to
        # register as a press rather than a bounce.
        pause(0.02)
        post_mouse_event(x, y, kCGEventLeftMouseUp)

    def scroll(self, lines, direction):
        scroll_event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 1, direction * lines)
        CGEventPost(kCGHIDEventTap, scroll_event)

    def tap_key(self, name):
        key_code = KEY_CODES[name]
        down = CGEventCreateKeyboardEvent(None, key_code, True)
        CGEventPost(kCGHIDEventTap, down)
        # WHY 0.012-0.025 s: a real key press is held roughly 10-25 ms. Shorter looks
        # synthetic; much longer risks the OS starting key repeat.
        pause(random.uniform(0.012, 0.025))
        up = CGEventCreateKeyboardEvent(None, key_code, False)
        CGEventPost(kCGHIDEventTap, up)

    def app_switch(self, count):
        """Sequential multi-strike layout with sustained hold times to ensure deep background windows swap context"""
        cmd_down = CGEventCreateKeyboardEvent(None, COMMAND_KEY, True)
        CGEventPost(kCGHIDEventTap, cmd_down)
        # WHY 0.08 s: gives the app switcher time to appear before the first Tab.
        pause(0.08)

        for _ in range(count):
            tab_down = CGEventCreateKeyboardEvent(None, TAB_KEY, True)
            CGEventSetFlags(tab_down, FLAG_COMMAND)
            CGEventPost(kCGHIDEventTap, tab_down)
            # WHY 0.05 s: hold Tab long enough to register as a distinct press.
            pause(0.05)

            tab_up = CGEventCreateKeyboardEvent(None, TAB_KEY, False)
            CGEventSetFlags(tab_up, FLAG_COMMAND)
            CGEventPost(kCGHIDEventTap, tab_up)
            # WHY 0.18 s: the switcher needs a beat between Tabs to advance one app
            # per press instead of collapsing them into one.
            pause(0.18)

        # WHY 0.30 s: let the switcher settle on the highlighted app so releasing
        # Command actually activates it.
        pause(0.30)
        cmd_up = CGEventCreateKeyboardEvent(None, COMMAND_KEY, False)
        CGEventPost(kCGHIDEventTap, cmd_up)

    def browser_tab_next(self):
        combined_flags = FLAG_COMMAND_OPTION
        down = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, True)
        CGEventSetFlags(down, combined_flags)
        CGEventPost(kCGHIDEventTap, down)
        # WHY 0.05-0.10 s: a chord is held a little longer than a plain key so the
        # browser sees the modifiers and the arrow together.
        pause(random.uniform(0.05, 0.10))
        up = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, False)
        CGEventSetFlags(up, combined_flags)
        CGEventPost(kCGHIDEventTap, up)

    def visible_app_count(self):
        """Queries macOS desktop server to dynamically get the count of all open active windows apps"""
        script = 'tell application "System Events" to get count of (every process whose background only is false)'
        try:
            # WHY timeout=5: the first call can pop macOS's Automation permission
            # dialog, which blocks osascript until someone clicks; without a timeout
            # the worker would hang there and /stop could never end the loop.
            output = subprocess.check_output(["osascript", "-e", script], timeout=5).decode().strip()
            # WHY 5: if System Events refuses (no Automation permission) or answers
            # oddly, assume five visible apps so Cmd+Tab still cycles a plausible depth.
            return int(output) if output.isdigit() else 5
        except Exception:
            return 5

    def release_modifiers(self):
        """Post a Command key-up. Called on shutdown in case the worker was killed
        mid Cmd+Tab: a synthetic modifier that is never released stays held for the
        whole login session, and every later click and keystroke gets Cmd added."""
        try:
            CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, COMMAND_KEY, False))
        except Exception as err:  # never let a Quartz hiccup block the exit
            print(f"[Shutdown] could not post Command key-up: {err}")
