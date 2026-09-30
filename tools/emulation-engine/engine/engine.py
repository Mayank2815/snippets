"""Core Emulation Engine — generates mouse, keyboard and scroll input on this computer.

A tiny HTTP server on 127.0.0.1:4320 exposes the engine:

    GET  /         the control console (console/index.html, next to this folder)
    GET  /status   {"status": "IDLE" | "RUNNING" | "STOPPING", "backend": ...,
                    "platform": ..., "inputWorking": true|false, "warning": null|"..."}
    POST /start    start the emulation loop in a background thread
                   (503 when inputWorking is false — see below)
    POST /stop     ask the loop to stop after its current step

`inputWorking` is false when the backend exists but cannot actually deliver a
single event — on Linux that means a Wayland session, or no X display. In that
state POST /start is refused with 503 and `warning` carries the explanation and
the fix, which the console shows in red. WHY: on Wayland the injection is
accepted and silently discarded, so without this the engine would report
RUNNING for as long as the user cared to watch a pointer that never moves.

POST /start and /stop must carry the header `X-Engine-Control: 1` and, when
sent by a browser, come from a page served on this computer (see ALLOWED_ORIGIN_RE).

Run it with ./run.sh on macOS and Linux or run.ps1 on Windows (or
`.venv/bin/python engine/engine.py`). The input itself is posted by a backend
picked for the platform in engine/backends/: Quartz (pyobjc) on macOS, where
the terminal that launches it must be allowed under System Settings ->
Privacy & Security -> Accessibility or macOS silently drops every event;
pynput on Windows and Linux (X11 only). ENGINE_BACKEND=fake runs everything
without generating input, which is how the tests work.
"""

import sys
import os
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import random
import math

# WHY line buffering: run.sh starts the engine in the background and people
# redirect its output to a log; with the default block buffering nothing shows
# up in that log until the process exits, so "[System Shift]" lines and the
# error messages below arrive hours late. Done before any print in this file.
sys.stdout.reconfigure(line_buffering=True)

from backends import get_backend
from backends.base import pause, SLEEP_SCALE

try:
    backend = get_backend()
except (ImportError, ValueError) as err:
    print(f"\n[Error] Module load failed: {err}")
    sys.exit(1)

# WHY 127.0.0.1: the engine drives THIS machine's mouse and keyboard, so it must
# never be reachable from the network — only pages and tools running on the same
# computer may talk to it. The port can be overridden with PORT for local clashes,
# the bind address deliberately cannot.
BIND_HOST = '127.0.0.1'
# WHY 4320: one above task-notif's 4310 so the two local tools never collide.
DEFAULT_PORT = 4320
PORT = int(os.environ.get("PORT", DEFAULT_PORT))

# The console page lives in ../console/index.html relative to this script.
CONSOLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'console', 'index.html')

# WHY a per-worker Event instead of a shared boolean: /stop used to clear one
# global flag while the worker might be inside its 9.5-12.5 s end-of-cycle
# sleep; a /start in that window set the flag back to True and started a second
# thread, and the old one woke up, saw True and carried on — two loops at once.
# Each worker now owns the Event it was started with, so setting it stops that
# worker and nothing can ever revive it. Event.is_set() is atomic, so the worker
# reads it without the lock; thread_lock only guards the start/stop transitions.
stop_event = threading.Event()
engine_thread = None
thread_lock = threading.Lock()

# WHY an allow-list instead of '*': binding to loopback keeps other machines
# out, but not the user's own browser — with '*' any web page they visited
# could POST /start from JavaScript and drive their mouse. Only pages served
# from this computer (the console, or a tool on another local port) are reflected;
# every other Origin gets no CORS headers at all, so the browser blocks it.
ALLOWED_ORIGIN_RE = re.compile(r'^http://(127\.0\.0\.1|localhost)(:\d{1,5})?$')
# WHY a custom header on /start and /stop: a plain POST is a "simple request"
# that browsers send without a preflight even cross-origin; requiring a custom
# header forces the preflight, which fails for any origin not in the allow-list.
CONTROL_HEADER = 'X-Engine-Control'

# WHY 2 s: long enough for the worker to finish the event it is in the middle
# of (at most one Cmd+Tab sequence, ~0.6 s) before the process exits, short
# enough that ./run.sh's Ctrl-C still feels immediate.
SHUTDOWN_JOIN_SECONDS = 2

# WHY 503 and not 403 or 409: the caller did nothing wrong and retrying the
# same request will not help until the user changes their session, which is
# exactly "Service Unavailable". 403 would suggest a permissions header to fix
# and 409 would suggest waiting a moment, and both are already in use here for
# those meanings.
INPUT_UNAVAILABLE_STATUS = 503

# --- FIXED ABSOLUTE TELEMETRY INJECTION KEYBOARD MATRIX ---
# Strictly bound to pure navigation safe arrow keys as requested
# WHY arrows: they move a caret or selection but never type a character or
# trigger a shortcut on their own, so they are the safest keys to inject. The
# backend maps these names to its platform's key codes (123-126 on macOS).
CORE_DENSE_KEYS = ["left", "right", "down", "up"]
# WHY shift: a modifier that does nothing on its own, mixed into the burst so
# it is not 100% arrow keys (key code 56 on macOS).
SHIFT_MODIFIER = "shift"

# Global application sequence loop counter
app_cycle_index = 1


def simulate_real_app_switch():
    """Sequential multi-strike layout with sustained hold times to ensure deep background windows swap context"""
    global app_cycle_index

    total_apps = backend.visible_app_count()

    if app_cycle_index >= total_apps:
        app_cycle_index = 1

    print(f"  [System Shift] Navigating next app in loop sequence. Open Apps Counter: {total_apps}. Striking Tab {app_cycle_index} time(s).")

    backend.app_switch(app_cycle_index)

    app_cycle_index += 1

def hardware_browser_tab_switch():
    print("  [Browser Shift] Cycling active browser tab index natively...")
    backend.browser_tab_next()

def simulate_vertical_scrolling(stop):
    direction = random.choice([-1, 1])
    # WHY 4-8 lines: a short flick of a scroll wheel, not a page jump.
    scroll_lines = random.randint(4, 8)
    print(f"  [Scroll Active] Generating smooth vertical scrolling. Lines: {scroll_lines}")
    for _ in range(scroll_lines):
        if stop.is_set(): break
        backend.scroll(1, direction)
        # WHY 0.15-0.30 s: the cadence of wheel notches; a tighter stream would
        # look like a trackpad gesture rather than a wheel.
        pause(random.uniform(0.15, 0.30))

def bezier_point(p0_x, p0_y, p1_x, p1_y, p2_x, p2_y, p3_x, p3_y, t):
    x = (1-t)**3 * p0_x + 3*(1-t)**2 * t * p1_x + 3*(1-t) * t**2 * p2_x + t**3 * p3_x
    y = (1-t)**3 * p0_y + 3*(1-t)**2 * t * p1_y + 3*(1-t) * t**2 * p2_y + t**3 * p3_y
    return x, y

def move_humanlike_adaptive(start_x, start_y, end_x, end_y, stop):
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
        if stop.is_set(): break
        t = i / float(steps)
        # WHY 10t^3 - 15t^4 + 6t^5: the quintic smoothstep, so the pointer starts
        # and stops with zero velocity like a hand does.
        t_eased = 10 * t**3 - 15 * t**4 + 6 * t**5
        target_x, target_y = bezier_point(start_x, start_y, p1_x, p1_y, p2_x, p2_y, end_x, end_y, t_eased)
        backend.move_mouse(target_x, target_y)
        # WHY 5-10 ms between samples: 100-200 Hz, the report rate of a USB mouse.
        pause(random.uniform(0.005, 0.010))

def loop_worker(stop):
    """The emulation loop. `stop` is this worker's own Event; /stop sets it."""
    print("\n=====================================================")
    print("[Core Engine] Active Target-Stabilized Emulation Initiated.")
    print("=====================================================")

    while not stop.is_set():
        curr_x, curr_y = backend.mouse_position()
        # WHY ±300 px clamped to x 200..1100, y 200..650: a random hop that stays
        # in the middle of a 13" display (1280x800 points), away from the menu
        # bar, the Dock and the hot corners.
        target_x = max(200, min(curr_x + random.randint(-300, 300), 1100))
        target_y = max(200, min(curr_y + random.randint(-300, 300), 650))

        move_humanlike_adaptive(curr_x, curr_y, target_x, target_y, stop)

        # WHY 16-20 strokes at 0.12-0.28 s: a burst of about 3-5 seconds of
        # keyboard activity per cycle.
        strokes = random.randint(16, 20)
        for _ in range(strokes):
            if stop.is_set(): break
            # WHY 0.22: roughly one stroke in five is a bare Shift, so the burst
            # is not a pure run of arrow keys.
            if random.random() < 0.22:
                backend.tap_key(SHIFT_MODIFIER)
            else:
                backend.tap_key(random.choice(CORE_DENSE_KEYS))
            pause(random.uniform(0.12, 0.28))
        print(f"  - Distributed {strokes} safe telemetry hits over separate execution ticks.")

        # WHY 30 / 30 / 40 %: app switch, browser tab switch and scrolling are
        # mixed so no single kind of event dominates.
        dice = random.random()
        if dice < 0.30:
            simulate_real_app_switch()
        elif 0.30 <= dice < 0.60:
            hardware_browser_tab_switch()
        else:
            simulate_vertical_scrolling(stop)

        # WHY 0.22: about one cycle in five ends with a click where the pointer
        # already is; 0.04 s settle before (the 0.02 s hold is in the backend).
        if random.random() < 0.22:
            pause(0.04)
            fx, fy = backend.mouse_position()
            backend.click(fx, fy)

        # --- FINAL PERFECT BRACKET TIMING ADJUSTMENT FOR 41% - 44% RE-LOCK ---
        # WHY 9.5-12.5 s: with the 3-5 s of activity above, one cycle lasts
        # about 13-17 s, which the author calibrated by hand so roughly 41-44 %
        # of ten-second windows contain an action. test_metrics.py is the older
        # model of this cadence, not the source of these numbers; see lessons.md.
        # WHY stop.wait() and not time.sleep(): /stop wakes the worker at once
        # instead of leaving it asleep for up to 12.5 s.
        # WHY the scale: the same ENGINE_FAST factor pause() applies (tests only).
        stop.wait(random.uniform(9.5, 12.5) * SLEEP_SCALE)
    print("[Core Engine] Emulation loop ended.")


def engine_state():
    """IDLE, RUNNING or STOPPING. Call with thread_lock held."""
    if engine_thread is None or not engine_thread.is_alive():
        return "IDLE"
    return "STOPPING" if stop_event.is_set() else "RUNNING"


def input_warning():
    """The backend's caveat as one string, or None when there is nothing to say.

    Set on both a refused backend (input_ok False) and a usable-but-caveated
    one, so the console has something to show in either case.
    """
    parts = [part for part in (backend.input_error, backend.input_remedy) if part]
    return " ".join(parts) if parts else None

class EngineBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def _origin_allowed(self):
        """True when the request has no Origin (curl, same-origin GETs in some
        browsers) or an Origin on the allow-list; False for any other page."""
        origin = self.headers.get('Origin')
        return origin is None or ALLOWED_ORIGIN_RE.match(origin) is not None

    def _send_cors_headers(self):
        # WHY CORS at all: a page on another *local* port (e.g. a dashboard on
        # 127.0.0.1:4310) may control the engine, and the browser blocks that
        # cross-origin call unless the engine says it is allowed. The Origin is
        # reflected only when it is on ALLOWED_ORIGIN_RE; otherwise no CORS
        # header is sent and the browser refuses the response. Loopback binding
        # alone is NOT what makes this safe — the user's own browser is on
        # loopback too, which is exactly the caller the allow-list keeps out.
        origin = self.headers.get('Origin')
        if not self._origin_allowed():
            return
        if origin is not None:
            self.send_header('Access-Control-Allow-Origin', origin)
            # WHY Vary: the answer differs per Origin, so a cache must not
            # hand one origin's response to another.
            self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, ' + CONTROL_HEADER)
        # WHY Allow-Private-Network: Chrome adds a second preflight check when
        # a public page calls a loopback address and refuses without this.
        self.send_header('Access-Control-Allow-Private-Network', 'true')

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
        path = self.path.split('?', 1)[0]
        if path == '/status':
            with thread_lock:
                state = engine_state()
            self._send_json({
                "status": state,
                "backend": backend.name,
                "platform": sys.platform,
                # WHY these two on every poll: the console polls /status every
                # 2 s and has no other channel, so an input problem that is not
                # in this payload cannot reach the person looking at the page.
                "inputWorking": bool(backend.input_ok),
                "warning": input_warning(),
            })
        elif path in ('/', '/index.html'):
            self._send_console()
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        global stop_event, engine_thread
        path = self.path.split('?', 1)[0]

        if path not in ('/start', '/stop'):
            self._send_json({"success": False, "error": "not found"}, status=404)
            return
        # WHY 403 for both checks: the request reached a real endpoint but the
        # caller is not one we trust — a page off the allow-list, or a plain
        # cross-site POST that skipped the preflight. 400 would suggest a
        # malformed request the caller should fix and retry; it should not.
        if not self._origin_allowed():
            self._send_json({"success": False, "message": "origin not allowed"}, status=403)
            return
        if self.headers.get(CONTROL_HEADER) != '1':
            self._send_json({"success": False, "message": f"missing {CONTROL_HEADER}: 1 header"}, status=403)
            return

        # WHY before the lock and before any state check: /start must be
        # refused outright when the backend cannot deliver input, whatever the
        # engine state is. Pretending to run is the failure this guards. /stop
        # is deliberately still allowed — it is always safe and always idle here.
        if path == '/start' and not backend.input_ok:
            self._send_json({
                "success": False,
                "message": input_warning() or "input is not available on this session",
                "inputWorking": False,
            }, status=INPUT_UNAVAILABLE_STATUS)
            return

        with thread_lock:
            state = engine_state()
            if path == '/start':
                if state == "RUNNING":
                    response = {"success": True, "message": "Engine confirmed running"}, 200
                elif state == "STOPPING":
                    # The previous worker is still finishing its current step;
                    # starting now would run two loops (see stop_event's WHY).
                    response = {"success": False, "message": "stopping, try again in a moment"}, 409
                else:
                    stop_event = threading.Event()
                    engine_thread = threading.Thread(target=loop_worker, args=(stop_event,), daemon=True)
                    engine_thread.start()
                    response = {"success": True, "message": "Stabilized Engine Activated"}, 200
            else:  # /stop
                if state == "RUNNING":
                    stop_event.set()
                    response = {"success": True, "message": "Stabilized Engine Deactivated"}, 200
                elif state == "STOPPING":
                    response = {"success": True, "message": "Already stopping"}, 200
                else:
                    response = {"success": False, "message": "Already idle"}, 200

        payload, status = response
        self._send_json(payload, status=status)


def shutdown_engine():
    """Stop the loop, give it SHUTDOWN_JOIN_SECONDS to finish its step, then
    release the switcher modifier. Shared by main() and the tests."""
    with thread_lock:
        stop_event.set()
        worker = engine_thread
    if worker is not None and worker.is_alive():
        print("\n[Shutdown] waiting for the emulation loop to finish its step...")
        worker.join(SHUTDOWN_JOIN_SECONDS)
    # Always, even when no loop ran: it is one harmless event and it is
    # the only thing standing between a killed worker and a stuck modifier.
    backend.release_modifiers()


# True once a shutdown has begun. See _on_sigterm.
_shutting_down = False


def _on_sigterm(signum, frame):
    # WHY raise KeyboardInterrupt: run.sh's trap sends SIGTERM; funnelling it
    # into the same path as Ctrl-C gives one shutdown sequence for both.
    global _shutting_down
    if _shutting_down:
        # WHY ignore the second one: a SIGTERM arriving while the first is
        # still being handled lands wherever the interpreter happens to be —
        # in practice inside backend.release_modifiers(), where it escapes that
        # method's `except Exception` (KeyboardInterrupt is not an Exception)
        # and prints a traceback INSTEAD of releasing the held modifier. That
        # is the one thing shutdown exists to do, and a stuck Alt affects every
        # later click and keystroke in the session. run.sh's trap fires on both
        # INT and TERM, so a Ctrl-C there sends two; supervisors often do too.
        # Shutdown is bounded by SHUTDOWN_JOIN_SECONDS, and SIGKILL still works
        # if it ever did hang.
        return
    _shutting_down = True
    raise KeyboardInterrupt

def main():
    signal.signal(signal.SIGTERM, _on_sigterm)
    server = HTTPServer((BIND_HOST, PORT), EngineBridgeHandler)
    width, height = backend.screen_size()
    print("=========================================================")
    print(f"🚀 TARGET-CALIBRATED ENGINE V20.0 ON PORT {PORT}")
    print(f"   Console: http://{BIND_HOST}:{PORT}/")
    print(f"   Backend: {backend.name} on {sys.platform} ({backend.platform_note}); screen {width}x{height}")
    print("=========================================================")
    # WHY repeat the warning here as a block: run.sh opens the console in a
    # browser, so the terminal is usually behind it — but someone who started
    # the engine by hand, or who is reading a redirected log, has only this.
    # The console shows the same text in red on the page they are looking at.
    if not backend.input_ok:
        print()
        print("  !!  INPUT UNAVAILABLE — the engine will not start the loop  !!")
        print(f"  {backend.input_error}")
        if backend.input_remedy:
            print()
            print(f"  {backend.input_remedy}")
        print()
        print("  The console at the address above says the same thing. POST /start")
        print(f"  is refused with HTTP {INPUT_UNAVAILABLE_STATUS} until this is fixed.")
        print("=========================================================")
    elif backend.input_error:
        print()
        print(f"  Warning: {backend.input_error}")
        print("=========================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutdown_engine()
        print("Shutdown complete.")

if __name__ == "__main__":
    main()
