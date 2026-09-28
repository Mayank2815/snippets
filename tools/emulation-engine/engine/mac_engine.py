"""Core Emulation Engine — generates mouse, keyboard and scroll input on this Mac.

A tiny HTTP server on 127.0.0.1:4320 exposes the engine:

    GET  /         the control console (console/index.html, next to this folder)
    GET  /status   {"status": "IDLE" | "RUNNING"}
    POST /start    start the emulation loop in a background thread
    POST /stop     ask the loop to stop after its current step

Run it with ./run.sh (or `.venv/bin/python engine/mac_engine.py`). It needs
pyobjc's Quartz bindings (see requirements.txt / install.sh) and the terminal
that launches it must be allowed under System Settings -> Privacy & Security ->
Accessibility, otherwise macOS silently drops every posted event.
"""

import sys
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess
import time
import random
import math


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
    print(f"\n[Error] Module load failed: {err}")
    print("[Error] Quartz (pyobjc) is not installed for this interpreter. Run ./install.sh and start the engine with ./run.sh.")
    sys.exit(1)

(
    CGEventCreateMouseEvent, CGEventPost, kCGHIDEventTap,
    kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
    CGEventCreate, CGEventGetLocation, CGEventCreateKeyboardEvent,
    CGEventCreateScrollWheelEvent, kCGScrollEventUnitLine
) = _quartz

# WHY 127.0.0.1: the engine drives THIS machine's mouse and keyboard, so it must
# never be reachable from the network — only pages and tools running on the same
# Mac may talk to it. The port can be overridden with PORT for local clashes,
# the bind address deliberately cannot.
BIND_HOST = '127.0.0.1'
# WHY 4320: one above task-notif's 4310 so the two local tools never collide.
DEFAULT_PORT = 4320
PORT = int(os.environ.get("PORT", DEFAULT_PORT))

# The console page lives in ../console/index.html relative to this script.
CONSOLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'console', 'index.html')

is_running = False
engine_thread = None
thread_lock = threading.Lock()

# --- FIXED ABSOLUTE TELEMETRY INJECTION KEYBOARD MATRIX ---
# Strictly bound to pure navigation safe arrow keys as requested
# WHY these key codes: macOS virtual key codes 123/124/125/126 are Left/Right/
# Down/Up arrow. Arrow keys move a caret or selection but never type a character
# or trigger a shortcut on their own, so they are the safest keys to inject.
CORE_DENSE_KEYS = [123, 124, 125, 126]
# WHY 56: virtual key code for the left Shift key — a modifier that does nothing
# on its own, mixed into the burst so it is not 100% arrow keys.
SHIFT_MODIFIER = 56
# WHY 48: virtual key code for Tab, used with Cmd held for the app switcher.
TAB_KEY = 48
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

# Global application sequence loop counter
app_cycle_index = 1

def get_mouse_pos():
    event = CGEventCreate(None)
    pointer = CGEventGetLocation(event)
    return pointer.x, pointer.y

def post_mouse_event(x, y, event_type):
    event = CGEventCreateMouseEvent(None, event_type, (x, y), 0)
    CGEventPost(kCGHIDEventTap, event)

def post_dense_keystroke(key_code):
    down = CGEventCreateKeyboardEvent(None, key_code, True)
    CGEventPost(kCGHIDEventTap, down)
    # WHY 0.012-0.025 s: a real key press is held roughly 10-25 ms. Shorter looks
    # synthetic; much longer risks the OS starting key repeat.
    time.sleep(random.uniform(0.012, 0.025))
    up = CGEventCreateKeyboardEvent(None, key_code, False)
    CGEventPost(kCGHIDEventTap, up)

def CGEventSetFlags(event, flags):
    from Quartz.CoreGraphics import CGEventSetFlags as _CGEventSetFlags
    _CGEventSetFlags(event, flags)

def get_total_visible_apps_count():
    """Queries macOS desktop server to dynamically get the count of all open active windows apps"""
    script = 'tell application "System Events" to get count of (every process whose background only is false)'
    try:
        output = subprocess.check_output(["osascript", "-e", script]).decode().strip()
        # WHY 5: if System Events refuses (no Automation permission) or answers
        # oddly, assume five visible apps so Cmd+Tab still cycles a plausible depth.
        return int(output) if output.isdigit() else 5
    except Exception:
        return 5

def simulate_real_app_switch():
    """Sequential multi-strike layout with sustained hold times to ensure deep background windows swap context"""
    global app_cycle_index

    total_apps = get_total_visible_apps_count()

    if app_cycle_index >= total_apps:
        app_cycle_index = 1

    print(f"  [System Shift] Navigating next app in loop sequence. Open Apps Counter: {total_apps}. Striking Tab {app_cycle_index} time(s).")

    cmd_down = CGEventCreateKeyboardEvent(None, COMMAND_KEY, True)
    CGEventPost(kCGHIDEventTap, cmd_down)
    # WHY 0.08 s: gives the app switcher time to appear before the first Tab.
    time.sleep(0.08)

    for _ in range(app_cycle_index):
        tab_down = CGEventCreateKeyboardEvent(None, TAB_KEY, True)
        CGEventSetFlags(tab_down, FLAG_COMMAND)
        CGEventPost(kCGHIDEventTap, tab_down)
        # WHY 0.05 s: hold Tab long enough to register as a distinct press.
        time.sleep(0.05)

        tab_up = CGEventCreateKeyboardEvent(None, TAB_KEY, False)
        CGEventSetFlags(tab_up, FLAG_COMMAND)
        CGEventPost(kCGHIDEventTap, tab_up)
        # WHY 0.18 s: the switcher needs a beat between Tabs to advance one app
        # per press instead of collapsing them into one.
        time.sleep(0.18)

    # WHY 0.30 s: let the switcher settle on the highlighted app so releasing
    # Command actually activates it.
    time.sleep(0.30)
    cmd_up = CGEventCreateKeyboardEvent(None, COMMAND_KEY, False)
    CGEventPost(kCGHIDEventTap, cmd_up)

    app_cycle_index += 1

def hardware_browser_tab_switch():
    print("  [Browser Shift] Cycling active browser tab index natively...")
    combined_flags = FLAG_COMMAND_OPTION
    down = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, True)
    CGEventSetFlags(down, combined_flags)
    CGEventPost(kCGHIDEventTap, down)
    # WHY 0.05-0.10 s: a chord is held a little longer than a plain key so the
    # browser sees the modifiers and the arrow together.
    time.sleep(random.uniform(0.05, 0.10))
    up = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, False)
    CGEventSetFlags(up, combined_flags)
    CGEventPost(kCGHIDEventTap, up)

def simulate_vertical_scrolling():
    direction = random.choice([-1, 1])
    # WHY 4-8 lines: a short flick of a scroll wheel, not a page jump.
    scroll_lines = random.randint(4, 8)
    print(f"  [Scroll Active] Generating smooth vertical scrolling. Lines: {scroll_lines}")
    for _ in range(scroll_lines):
        if not is_running: break
        scroll_event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 1, direction)
        CGEventPost(kCGHIDEventTap, scroll_event)
        # WHY 0.15-0.30 s: the cadence of wheel notches; a tighter stream would
        # look like a trackpad gesture rather than a wheel.
        time.sleep(random.uniform(0.15, 0.30))

def bezier_point(p0_x, p0_y, p1_x, p1_y, p2_x, p2_y, p3_x, p3_y, t):
    x = (1-t)**3 * p0_x + 3*(1-t)**2 * t * p1_x + 3*(1-t) * t**2 * p2_x + t**3 * p3_x
    y = (1-t)**3 * p0_y + 3*(1-t)**2 * t * p1_y + 3*(1-t) * t**2 * p2_y + t**3 * p3_y
    return x, y

def move_humanlike_adaptive(start_x, start_y, end_x, end_y):
    distance = math.hypot(end_x - start_x, end_y - start_y)
    # WHY 18..40 steps at one per 16 px: short hops still get enough samples to
    # show a curve, long moves are capped so they do not flood the event tap.
    steps = int(max(18, min(40, distance / 16)))
    # WHY 8-15 % of the distance: enough wander in the control points to bend
    # the path visibly without swinging off screen.
    deviation = distance * random.uniform(0.08, 0.15)

    # WHY 0.25 / 0.75: standard placement of the two inner control points of a
    # cubic Bezier, one per quarter of the path, before the random offset.
    p1_x = start_x + (end_x - start_x) * 0.25 + random.uniform(-deviation, deviation)
    p1_y = start_y + (end_y - start_y) * 0.25 + random.uniform(-deviation, deviation)
    p2_x = start_x + (end_x - start_x) * 0.75 + random.uniform(-deviation, deviation)
    p2_y = start_y + (end_y - start_y) * 0.75 + random.uniform(-deviation, deviation)

    for i in range(steps + 1):
        if not is_running: break
        t = i / float(steps)
        # WHY 10t^3 - 15t^4 + 6t^5: the quintic smoothstep, so the pointer starts
        # and stops with zero velocity like a hand does.
        t_eased = 10 * t**3 - 15 * t**4 + 6 * t**5
        target_x, target_y = bezier_point(start_x, start_y, p1_x, p1_y, p2_x, p2_y, end_x, end_y, t_eased)
        post_mouse_event(target_x, target_y, kCGEventMouseMoved)
        # WHY 5-10 ms between samples: 100-200 Hz, the report rate of a USB mouse.
        time.sleep(random.uniform(0.005, 0.010))

def loop_worker():
    global is_running
    print("\n=====================================================")
    print("[Core Engine] Active Target-Stabilized Emulation Initiated.")
    print("=====================================================")

    while is_running:
        curr_x, curr_y = get_mouse_pos()
        # WHY ±300 px clamped to x 200..1100, y 200..650: a random hop that stays
        # in the middle of a 13" display (1280x800 points), away from the menu
        # bar, the Dock and the hot corners.
        target_x = max(200, min(curr_x + random.randint(-300, 300), 1100))
        target_y = max(200, min(curr_y + random.randint(-300, 300), 650))

        move_humanlike_adaptive(curr_x, curr_y, target_x, target_y)

        # WHY 16-20 strokes at 0.12-0.28 s: a burst of about 3-5 seconds of
        # keyboard activity per cycle.
        strokes = random.randint(16, 20)
        for _ in range(strokes):
            if not is_running: break
            # WHY 0.22: roughly one stroke in five is a bare Shift, so the burst
            # is not a pure run of arrow keys.
            if random.random() < 0.22:
                post_dense_keystroke(SHIFT_MODIFIER)
            else:
                post_dense_keystroke(random.choice(CORE_DENSE_KEYS))
            time.sleep(random.uniform(0.12, 0.28))
        print(f"  - Distributed {strokes} safe telemetry hits over separate execution ticks.")

        # WHY 30 / 30 / 40 %: app switch, browser tab switch and scrolling are
        # mixed so no single kind of event dominates.
        dice = random.random()
        if dice < 0.30:
            simulate_real_app_switch()
        elif 0.30 <= dice < 0.60:
            hardware_browser_tab_switch()
        else:
            simulate_vertical_scrolling()

        # WHY 0.22: about one cycle in five ends with a click where the pointer
        # already is; 0.04 s settle before and a 0.02 s hold is a light click.
        if random.random() < 0.22:
            time.sleep(0.04)
            fx, fy = get_mouse_pos()
            post_mouse_event(fx, fy, kCGEventLeftMouseDown)
            time.sleep(0.02)
            post_mouse_event(fx, fy, kCGEventLeftMouseUp)

        # --- FINAL PERFECT BRACKET TIMING ADJUSTMENT FOR 41% - 44% RE-LOCK ---
        # WHY 9.5-12.5 s: with the 3-5 s of activity above, one cycle lasts
        # about 13-17 s, which the author calibrated (see test_metrics.py) so
        # roughly 41-44 % of ten-second windows contain an action.
        time.sleep(random.uniform(9.5, 12.5))

class EngineBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def _send_cors_headers(self):
        # WHY CORS on every response: the team workbench page is served from a
        # VM, but the engine it controls runs on the viewer's own Mac. Without
        # these headers the browser blocks that cross-origin call to 127.0.0.1.
        # '*' is safe only because the server binds to loopback (see BIND_HOST).
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_console(self):
        try:
            with open(CONSOLE_PATH, 'rb') as fh:
                body = fh.read()
        except OSError as err:
            self._send_json({"error": f"console page not found: {err}"}, status=500)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        global is_running
        path = self.path.split('?', 1)[0]
        if path == '/status':
            self._send_json({"status": "RUNNING" if is_running else "IDLE"})
        elif path in ('/', '/index.html'):
            self._send_console()
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        global is_running, engine_thread
        response_data = {"success": True}

        if self.path == '/start':
            with thread_lock:
                if not is_running:
                    is_running = True
                    engine_thread = threading.Thread(target=loop_worker, daemon=True)
                    engine_thread.start()
                    response_data["message"] = "Stabilized Engine Activated"
                else: response_data = {"success": True, "message": "Engine confirmed running"}

        elif self.path =='/stop':
            with thread_lock:
                if is_running:
                    is_running = False
                    response_data["message"] = "Stabilized Engine Deactivated"
                else: response_data = {"success": False, "message": "Already idle"}

        else:
            self._send_json({"success": False, "error": "not found"}, status=404)
            return

        self._send_json(response_data)

if __name__ == "__main__":
    server = HTTPServer((BIND_HOST, PORT), EngineBridgeHandler)
    print("=========================================================")
    print(f"🚀 TARGET-CALIBRATED ENGINE V20.0 ON PORT {PORT}")
    print(f"   Console: http://{BIND_HOST}:{PORT}/")
    print("=========================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        is_running = False
        print("\nShutdown complete.")
