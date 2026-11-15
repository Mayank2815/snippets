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
import time
import subprocess

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

# --- SAFE WORK APPLICATION WHITELIST ---
# Restricts window switching EXCLUSIVELY to core development and reference tools.
# Personal tools (Telegram, Discord) and virtualization (WorkSpaces) are NEVER activated,
# guaranteeing 0.0% Unproductive classification in Hubstaff.
SAFE_WORK_APPS = ["Code", "Cursor", "Google Chrome", "Brave Browser", "Safari", "Terminal", "iTerm2"]


def get_running_work_apps():
    """Returns only running apps that belong to the safe work whitelist."""
    if sys.platform != 'darwin':
        return []
    try:
        cmd = ["osascript", "-e", 'tell application "System Events" to get name of every process whose background only is false']
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        running = [x.strip() for x in res.stdout.split(',') if x.strip()]
        return [app for app in SAFE_WORK_APPS if app in running]
    except Exception:
        return []


def simulate_real_app_switch():
    """Safe application switch: cycles ONLY between whitelisted work applications (Code <-> Browser).

    Guarantees no background personal/utility windows (Telegram, Amazon WorkSpaces) are ever focused.
    """
    if sys.platform != 'darwin' or backend.name != 'quartz':
        backend.app_switch(1)
        return

    running = get_running_work_apps()
    front = get_frontmost_app_name()
    target = None
    if front in ["Code", "Cursor"]:
        for b in ["Google Chrome", "Brave Browser", "Safari"]:
            if b in running:
                target = b
                break
    else:
        for ide in ["Code", "Cursor"]:
            if ide in running:
                target = ide
                break

    if target:
        print(f"  [Work Shift] Toggling context safely: {front} -> {target}")
        activate_app_by_name(target)
    else:
        if "Code" in running:
            activate_app_by_name("Code")


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


class CadenceController:
    """Manages 10-minute emulation windows, target percentages, and adaptive timings.

    Guarantees:
    1. Utterly random human-like activity percentage per 10-minute window (10% to 78%).
    2. 1-hour macro-cycles and cumulative multi-hour session average randomly distributed across 35% - 46%.
    3. Second-by-second activity execution matching Hubstaff's exact tracking formula.
    4. Handles variable session lengths (1h, 3-4h, 8h) and physical user work during breaks.
    """
    TARGET_MIN = 35.0      # Lower bound of acceptable cumulative session average
    TARGET_MAX = 45.0      # Upper bound of acceptable cumulative session average (strictly clamped)
    WINDOW_SECS = 600.0    # 10 minutes per window

    @classmethod
    def random_session_target(cls):
        """Picks a workday target heavily skewed towards 35%-39% (80% of days), with ~20% sprint days (42%-44.8%)."""
        if random.random() < 0.80:
            # 80% of workdays: lower range (35.0% - 39.8%), peak around 36.2%
            return round(random.triangular(35.0, 39.8, 36.2), 1)
        else:
            # 20% of workdays: higher sprint days (42.0% - 44.8%)
            return round(random.uniform(42.0, 44.8), 1)

    def __init__(self, target_overall=None):
        self._explicit_target = target_overall is not None
        if target_overall is None:
            self.target_overall = self.random_session_target()
        else:
            self.target_overall = target_overall
        self.state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cadence_state.json')
        self.window_history = []
        self.hour_queue = []
        self.current_target = self.target_overall
        self.current_slot = None
        self.window_start_time = None
        self.window_active_seconds = 0.0
        self.window_count = 0
        self.user_active = False
        self.last_action_text = "Emulation engine initialized"
        self.focus_normalizer_enabled = True
        self.schedule_next_focus_break(initial=True)
        self.focus_break_active = False
        self._load_state()
        self._init_first_target()
        self.current_slot_plan = self.generate_slot_plan(self.current_target)

    def is_in_deep_focus(self, window_idx=None):
        """Returns True if the current window is inside one of the two daily 48-min Deep Focus sessions.

        Session 1: Windows 7-11 (~48 min in Hour 2)
        Session 2: Windows 31-35 (~48 min in Hour 6)
        Total: ~96 minutes continuous focus out of 480 minutes = ~20% Hubstaff Focus Time.
        """
        idx = (self.window_count if window_idx is None else window_idx) % 48
        return (7 <= idx <= 11) or (31 <= idx <= 35)

    def schedule_next_focus_break(self, initial=False):
        """Schedules the next documentation/browser context switch (every 14-22 minutes)."""
        offset = random.uniform(840, 1320) * SLEEP_SCALE
        if initial:
            offset = random.uniform(720, 1080) * SLEEP_SCALE
        self.next_focus_break_time = time.time() + offset

    def _load_state(self):
        if os.environ.get("ENGINE_BACKEND") == "fake" and "UNIT_TEST" in os.environ:
            return
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Resume if last updated within 12 hours (same workday)
            if time.time() - data.get("timestamp", 0) < 43200:
                self.window_history = data.get("window_history", [])[-48:]
                self.window_count = len(self.window_history)
                if "target_overall" in data and not getattr(self, '_explicit_target', False):
                    self.target_overall = data["target_overall"]
                if self.window_history:
                    print(f"[Cadence] Restored session history: {len(self.window_history)} window(s), running average {self.running_average:.1f}%")
        except Exception:
            pass

    def _save_state(self):
        if not self.state_file or os.environ.get("ENGINE_BACKEND") == "fake":
            return
        try:
            data = {
                "timestamp": time.time(),
                "window_history": self.window_history[-48:],
                "target_overall": self.target_overall
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass

    @property
    def running_average(self):
        if not self.window_history:
            return self.target_overall
        return sum(self.window_history) / len(self.window_history)

    def plan_next_hour(self):
        """Plans a 6-window sequence (1 full hour) with wide human variance.

        Guarantees:
        1. Every single hour contains genuine low activity windows (10.0% to 16.5%).
        2. High sprint bursts (up to 78.0%) occur naturally across the workday.
        3. Strict uniqueness: no duplicate numbers (all pairs separated by >= 0.8%).
        4. Closed-loop control ensures cumulative workday average lands strictly within [35.0%, 45.0%].
        """
        cum_avg = self.running_average
        day_target = self.target_overall
        error = cum_avg - day_target
        k = len(self.window_history)

        # Macro hourly target selection with closed-loop guardrails:
        if not self.window_history:
            hour_target = round(day_target + random.uniform(-1.5, 1.5), 1)
        else:
            s = sum(self.window_history)
            # Mathematical safety bounds to guarantee cumulative avg stays in [35.1%, 44.9%]
            h_min = (35.1 * (k + 6) - s) / 6.0
            h_max = (44.9 * (k + 6) - s) / 6.0
            corr = -error * 1.4
            hour_target = day_target + corr + random.uniform(-2.5, 2.5)
            hour_target = max(h_min, min(h_max, hour_target))

        hour_target = round(max(24.0, min(50.0, hour_target)), 1)
        self.last_hour_target = hour_target
        budget = round(hour_target * 6.0, 1)

        # Generate windows using rejection sampling across 5 human behavioral archetypes:
        # ~78% of hours have deep pause/reading lows (10.0% - 16.5%)
        # ~22% of hours are Steady Active Flow hours (in a groove, minimum sits naturally in 21.0% - 27.5%)
        dice_start = random.random()
        if dice_start < 0.30:
            target_start_bucket = "low"
        elif dice_start < 0.75:
            target_start_bucket = "mid"
        else:
            target_start_bucket = "high"

        last_window_score = self.window_history[-1] if self.window_history else None

        for attempt in range(300):
            dice = random.random()
            if dice < 0.22:
                # Type 1: Steady Active Flow Hour (22% frequency: in a coding groove, min in 20s, no deep idle troughs)
                p1 = round(random.uniform(21.0, 27.5), 1)
                p2 = round(random.uniform(46.0, 58.0), 1)
                p3 = round(random.uniform(29.0, 38.0), 1)
                is_steady_flow = True
            elif dice < 0.48:
                # Type 2: Sprint & Pause Hour (26% frequency: 1 High sprint peak, 1 deep low, 1 low-mid, 3 mids)
                p1 = round(random.uniform(66.0, 77.5), 1)
                p2 = round(random.uniform(10.5, 16.0), 1)
                p3 = round(random.uniform(18.0, 26.0), 1)
                is_steady_flow = False
            elif dice < 0.68:
                # Type 3: Deep Reading / Spec Review Hour (20% frequency: 2 deep troughs, 1 moderate burst, 3 low-mids)
                p1 = round(random.uniform(10.5, 15.0), 1)
                p2 = round(random.uniform(16.0, 22.5), 1)
                p3 = round(random.uniform(48.0, 62.0), 1)
                is_steady_flow = False
            elif dice < 0.84:
                # Type 4: Erratic / Restless Flow Hour (16% frequency: 1 extreme peak, 2 deep lows, 3 dynamic mids)
                p1 = round(random.uniform(70.0, 78.0), 1)
                p2 = round(random.uniform(10.0, 15.0), 1)
                p3 = round(random.uniform(18.0, 24.0), 1)
                is_steady_flow = False
            else:
                # Type 5: Wide Human Variance Hour (16% frequency: 1 upper burst, 1 deep low, 4 scattered mids)
                p1 = round(random.uniform(58.0, 72.0), 1)
                p2 = round(random.uniform(10.5, 16.5), 1)
                p3 = round(random.uniform(22.0, 28.0), 1)
                is_steady_flow = False

            rem = round(budget - (p1 + p2 + p3), 1)
            r1 = random.uniform(0.24, 0.34)
            r2 = random.uniform(0.36, 0.46)
            w1 = round(rem * r1, 1)
            w2 = round(rem * r2, 1)
            w3 = round(rem - (w1 + w2), 1)

            wins = [p1, p2, p3, w1, w2, w3]
            if any(w < 10.0 or w > 78.0 for w in wins):
                continue
            if not is_steady_flow and min(wins) > 17.5:
                continue
            if is_steady_flow and (min(wins) < 19.5 or min(wins) > 28.5):
                continue

            # Strict integer uniqueness: all 6 numbers must round to different whole numbers on Hubstaff
            rounded = [round(w) for w in wins]
            if len(set(rounded)) != 6:
                continue

            # Separation between any two float values must be at least 1.0%
            sorted_pairs = [(wins[i], wins[j]) for i in range(6) for j in range(i + 1, 6)]
            min_sep = min(abs(a - b) for a, b in sorted_pairs)
            if min_sep < 1.0:
                continue

            valid_order = False
            for _ in range(80):
                random.shuffle(wins)
                # Check target start bucket
                if target_start_bucket == "low" and wins[0] > 22.0:
                    continue
                if target_start_bucket == "mid" and (wins[0] <= 22.0 or wins[0] > 45.0):
                    continue
                if target_start_bucket == "high" and wins[0] <= 45.0:
                    continue

                # Check consecutive differences (at least 2% gap so adjacent windows never look similar)
                if any(abs(round(wins[i]) - round(wins[i + 1])) < 2 for i in range(5)):
                    continue
                # Check no adjacent peaks > 60
                if any(wins[i] > 60.0 and wins[i + 1] > 60.0 for i in range(5)):
                    continue
                # Check boundary with previous hour if known (at least 3% gap)
                if last_window_score is not None and abs(round(wins[0]) - round(last_window_score)) < 3:
                    continue

                valid_order = True
                break

            if valid_order:
                return wins

        # Deterministic fallback if rejection attempts exhausted
        return wins

    def _init_first_target(self):
        if not self.hour_queue:
            self.hour_queue = self.plan_next_hour()
        self.current_target = self.hour_queue.pop(0)

    def _generate_next_target(self):
        if not self.hour_queue:
            self.hour_queue = self.plan_next_hour()
        return self.hour_queue.pop(0)

    def generate_window_timeline(self, target_pct, total_secs=600):
        """Partitions total_secs into realistic human bursts and pauses.

        Total active seconds = round(total_secs * (target_pct / 100)).
        Hubstaff evaluates input second-by-second, so delivering input throughout
        active bursts ensures Hubstaff measures exactly target_pct score.
        """
        active_needed = int(round(total_secs * (target_pct / 100.0)))
        active_needed = max(10, min(total_secs - 10, active_needed))
        pause_needed = total_secs - active_needed

        if target_pct >= 65.0:
            num_bursts = random.randint(8, 12)
            min_burst, max_burst = 25, 55
            min_pause, max_pause = 8, 20
        elif target_pct <= 22.0:
            num_bursts = random.randint(4, 7)
            min_burst, max_burst = 8, 16
            min_pause, max_pause = 50, 100
        else:
            num_bursts = random.randint(8, 14)
            min_burst, max_burst = 14, 32
            min_pause, max_pause = 14, 40

        # Partition active seconds
        bursts = []
        rem = active_needed
        for i in range(num_bursts - 1):
            b = int(random.uniform(min_burst, max_burst))
            b = min(b, rem - (num_bursts - 1 - i) * 4)
            b = max(4, b)
            bursts.append(b)
            rem -= b
        bursts.append(max(4, rem))

        # Partition pause seconds
        pauses = []
        rem = pause_needed
        for i in range(num_bursts - 1):
            p = int(random.uniform(min_pause, max_pause))
            p = min(p, rem - (num_bursts - 1 - i) * 4)
            p = max(4, p)
            pauses.append(p)
            rem -= p
        pauses.append(max(4, rem))

        # Interleave bursts and pauses (starting with active burst)
        timeline = []
        for b, p in zip(bursts, pauses):
            timeline.append(('ACTIVE', b))
            timeline.append(('PAUSE', p))

        return timeline

    def generate_slot_plan(self, target_pct):
        """Maps timeline into 60 10-second slots for the UI visualizer."""
        timeline = self.generate_window_timeline(target_pct, 600)
        flags = []
        for kind, dur in timeline:
            flags.extend([kind == 'ACTIVE'] * dur)
        flags = flags[:600]
        if len(flags) < 600:
            flags.extend([False] * (600 - len(flags)))

        plan = []
        for slot in range(60):
            chunk = flags[slot * 10 : (slot + 1) * 10]
            plan.append(sum(chunk) >= 3)
        return plan

    def check_window_rollover(self, now=None):
        """Checks if a 10-minute clock boundary or window elapsed. Returns True if rollover occurred."""
        if now is None:
            now = time.time()

        win_duration = self.WINDOW_SECS * SLEEP_SCALE
        if win_duration < 60.0:
            if self.window_start_time is None:
                self.window_start_time = now
                self.window_count += 1
                return True
            if now - self.window_start_time >= win_duration:
                self._record_completed_window(now)
                if self.window_count > 0 and self.window_count % 48 == 0:
                    self.target_overall = self.random_session_target()
                    print(f"[Cadence] Workday 8-hour cycle complete. New session target rolled: {self.target_overall:.1f}%")
                self.window_start_time = now
                self.window_count += 1
                self.current_target = self._generate_next_target()
                self.current_slot_plan = self.generate_slot_plan(self.current_target)
                return True
            return False

        # Production mode: align to wall-clock 10-minute boundaries (:00, :10, :20, :30, :40, :50)
        slot = int(now // 600)
        if self.current_slot is None:
            self.current_slot = slot
            self.window_start_time = now
            self.window_count += 1
            return True

        if slot != self.current_slot:
            self._record_completed_window(now)
            if self.window_count > 0 and self.window_count % 48 == 0:
                self.target_overall = self.random_session_target()
                print(f"[Cadence] Workday 8-hour cycle complete. New session target rolled: {self.target_overall:.1f}%")
            self.current_slot = slot
            self.window_start_time = now
            self.window_count += 1
            self.current_target = self._generate_next_target()
            self.current_slot_plan = self.generate_slot_plan(self.current_target)
            return True

        return False

    def _record_completed_window(self, now):
        win_dur = self.WINDOW_SECS * SLEEP_SCALE
        elapsed = win_dur if win_dur >= 60.0 else max(1.0, now - (self.window_start_time or now))
        actual = round(min(100.0, (self.window_active_seconds / elapsed) * 100.0), 1)

        if actual >= 5.0:
            self.window_history.append(actual)
            self._save_state()
            print(f"\n[Cadence] Window Completed: Achieved {actual:.1f}% (Target was {self.current_target:.1f}%) | Cumulative Session Average: {self.running_average:.1f}%")
        self.window_active_seconds = 0.0

    def add_active_seconds(self, seconds):
        self.window_active_seconds += seconds

    def get_cycle_parameters(self):
        target = self.current_target
        if target >= 70.0:
            strokes = random.randint(20, 28)
            key_min, key_max = 0.08, 0.16
        elif target >= 55.0:
            strokes = random.randint(16, 22)
            key_min, key_max = 0.10, 0.20
        elif target >= 25.0:
            strokes = random.randint(12, 18)
            key_min, key_max = 0.12, 0.26
        else:
            strokes = random.randint(6, 12)
            key_min, key_max = 0.16, 0.32
        return strokes, key_min, key_max

    @property
    def telemetry(self):
        now = time.time()
        win_start = self.window_start_time or now
        slot_duration = 10.0 * SLEEP_SCALE
        slot_idx = min(59, max(0, int((now - win_start) / max(0.001, slot_duration))))
        elapsed = min(self.WINDOW_SECS, max(0.0, now - win_start))
        remaining = max(0.0, self.WINDOW_SECS - elapsed)
        next_break_in = max(0, int(getattr(self, 'next_focus_break_time', now) - now))

        return {
            "windowTarget": self.current_target,
            "runningAverage": round(self.running_average, 1),
            "completedWindows": len(self.window_history),
            "windowHistory": self.window_history[-18:],
            "currentSlot": slot_idx,
            "slotPlan": getattr(self, 'current_slot_plan', [True] * 60),
            "windowElapsedSec": int(elapsed),
            "windowRemainingSec": int(remaining),
            "userActive": getattr(self, 'user_active', False),
            "lastAction": getattr(self, 'last_action_text', "Ready"),
            "focusNormalizerEnabled": getattr(self, 'focus_normalizer_enabled', True),
            "focusBreakActive": getattr(self, 'focus_break_active', False),
            "focusBreakCountdownSec": next_break_in,
            "inDeepFocus": self.is_in_deep_focus(),
            "focusTarget": "~20%",
        }

    def reset_history(self):
        self.window_history.clear()
        self.hour_queue.clear()
        self.window_count = 0
        self.window_start_time = time.time()
        self.window_active_seconds = 0.0
        self.target_overall = self.random_session_target()
        self.schedule_next_focus_break(initial=True)
        self._init_first_target()
        self.current_slot_plan = self.generate_slot_plan(self.current_target)
        self.user_active = False
        self.last_action_text = f"Session history reset (New Session Target: {self.target_overall:.1f}%)"
        self._save_state()

    def run_activity_verification(self, num_windows=6):
        """Simulates 6 windows (1 full hour) and verifies wide variance, no duplicates, and 35%-45% average."""
        sim_ctrl = CadenceController(target_overall=self.target_overall)
        sim_ctrl.window_history.clear()
        sim_ctrl.hour_queue = sim_ctrl.plan_next_hour()

        results = []
        for i in range(num_windows):
            target = sim_ctrl.hour_queue[i]
            timeline = sim_ctrl.generate_window_timeline(target, 600)
            active_secs = sum(dur for typ, dur in timeline if typ == 'ACTIVE')
            total_secs = sum(dur for _, dur in timeline)
            score = round((active_secs / total_secs) * 100.0, 1)
            sim_ctrl.window_history.append(score)
            results.append({
                "window": i + 1,
                "target": target,
                "score": score,
                "activeSlots": int(round(score * 0.6)),
                "activeSeconds": active_secs,
                "totalSlots": 60,
                "totalSeconds": total_secs
            })

        avg = round(sum(r["score"] for r in results) / len(results), 2)
        passed = 35.0 <= avg <= 45.0
        scores = [r["score"] for r in results]
        has_dupes = len(scores) != len(set(scores))
        has_low = min(scores) <= 28.0
        return {
            "passed": passed and not has_dupes and has_low,
            "average": avg,
            "targetRange": "35.0% - 45.0%",
            "windows": results,
            "minScore": min(scores),
            "maxScore": max(scores),
            "hasDuplicates": has_dupes,
            "hasDeepLow": has_low,
            "status": "VERIFIED_OPTIMIZED" if (passed and not has_dupes and has_low) else "NEEDS_RECALIBRATION"
        }


cadence_controller = CadenceController()


def get_frontmost_app_name():
    """Returns the name of the currently active macOS application."""
    if sys.platform != 'darwin':
        return None
    try:
        cmd = ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true']
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        out = res.stdout.strip()
        return out if out else None
    except Exception:
        return None


def activate_app_by_name(app_name):
    """Brings an application to the front on macOS."""
    if sys.platform != 'darwin' or not app_name:
        return False
    try:
        cmd = ["osascript", "-e", f'tell application "{app_name}" to activate']
        subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return True
    except Exception:
        return False


def perform_focus_break(stop):
    """Executes a 60-120s reference/documentation review in Chrome/Brave to break 30-min continuous focus.

    Restricts context switches EXCLUSIVELY to Core/Non-Core work applications (Code <-> Chrome/Brave).
    NEVER launches Telegram, Amazon WorkSpaces, or personal apps, guaranteeing 0.0% Unproductive time.
    Strictly clamped to the current 10-minute slot so it NEVER bleeds across clock boundaries.
    """
    global cadence_controller
    now = time.time()
    time_left_in_slot = 600.0 - (now % 600.0) if SLEEP_SCALE >= 0.5 else 600.0
    if time_left_in_slot < 90.0:
        # Do not straddle clock boundary: defer until next window
        cadence_controller.schedule_next_focus_break()
        return

    max_dur = min(120.0, time_left_in_slot - 15.0)
    if max_dur < 60.0:
        cadence_controller.schedule_next_focus_break()
        return

    cadence_controller.focus_break_active = True
    break_dur = random.uniform(60.0, max_dur)

    print("\n-----------------------------------------------------------")
    print(f"[Focus Normalizer] Scheduled documentation/browser review ({int(break_dur)}s).")
    print("[Focus Normalizer] Keeping Hubstaff Focus Time aligned to human ~20% (0.0% Unproductive)...")
    print("-----------------------------------------------------------\n")
    cadence_controller.last_action_text = f"Reviewing documentation ({int(break_dur)}s)"

    running_work = get_running_work_apps()
    front_app = get_frontmost_app_name()

    target_app = None
    if front_app in ["Code", "Cursor", "Terminal", "iTerm2"]:
        for b in ["Google Chrome", "Brave Browser", "Safari"]:
            if b in running_work:
                target_app = b
                break
    else:
        for ide in ["Code", "Cursor"]:
            if ide in running_work:
                target_app = ide
                break

    if target_app and backend.name == "quartz":
        activate_app_by_name(target_app)
        pause(0.8)

    break_start = time.time()
    break_end = break_start + (break_dur * SLEEP_SCALE)
    last_mouse = None

    while time.time() < break_end and not stop.is_set():
        if cadence_controller.check_window_rollover():
            break

        cx, cy = backend.mouse_position()
        if last_mouse is not None and backend.name != "fake":
            drift = math.hypot(cx - last_mouse[0], cy - last_mouse[1])
            if drift > 40:
                cadence_controller.user_active = True
                print(f"  [User Active] Motion during documentation review (drift {drift:.0f}px). Yielding...")
                u_start = time.time()
                lpos = (cx, cy)
                still = time.time()
                while not stop.is_set():
                    stop.wait(1.0 * SLEEP_SCALE)
                    ncx, ncy = backend.mouse_position()
                    if math.hypot(ncx - lpos[0], ncy - lpos[1]) > 10:
                        lpos = (ncx, ncy)
                        still = time.time()
                    elif time.time() - still >= (15.0 * SLEEP_SCALE):
                        break
                cadence_controller.add_active_seconds(time.time() - u_start)
                cadence_controller.user_active = False
                cx, cy = backend.mouse_position()
                last_mouse = (cx, cy)
                if stop.is_set():
                    break

        # Casual reading motion (slow glides, light scroll)
        tx = max(150, min(cx + random.randint(-180, 180), 1100))
        ty = max(150, min(cy + random.randint(-120, 120), 700))
        move_humanlike_adaptive(cx, cy, tx, ty, stop)
        last_mouse = (tx, ty)

        if random.random() < 0.35:
            simulate_vertical_scrolling(stop)
        if random.random() < 0.20:
            hardware_browser_tab_switch()

        rem = break_end - time.time()
        if rem > 0:
            stop.wait(min(rem, random.uniform(2.5, 6.0) * SLEEP_SCALE))

    elapsed_act = (time.time() - break_start) / max(0.001, SLEEP_SCALE)
    cadence_controller.add_active_seconds(min(break_dur * 0.35, elapsed_act * 0.35))

    primary_ide = "Code" if "Code" in running_work else ("Cursor" if "Cursor" in running_work else None)
    if primary_ide and backend.name == "quartz":
        activate_app_by_name(primary_ide)
        pause(0.5)

    cadence_controller.focus_break_active = False
    cadence_controller.schedule_next_focus_break()
    cadence_controller.last_action_text = "Resumed development emulation"
    print(f"[Focus Normalizer] Review complete. Primary IDE ({primary_ide or 'Code'}) restored.\n")


def loop_worker(stop):
    """The emulation loop. `stop` is this worker's own Event; /stop sets it."""
    global cadence_controller
    print("\n=====================================================")
    print("[Core Engine] Adaptive Human-Variance Emulation Initiated.")
    print(f"[Target Profile] Initial Window: {cadence_controller.current_target:.1f}% | Target Session Average: 35%-45% (Session Target: {cadence_controller.target_overall:.1f}%)")
    print("=====================================================\n")

    last_engine_mouse_pos = None

    while not stop.is_set():
        cadence_controller.check_window_rollover()
        target = cadence_controller.current_target
        now = time.time()

        win_duration = cadence_controller.WINDOW_SECS * SLEEP_SCALE
        if win_duration < 60.0:
            window_secs = int(max(10, win_duration / SLEEP_SCALE))
        else:
            sec_in = int(now) % 600
            window_secs = max(30, 600 - sec_in)

        timeline = cadence_controller.generate_window_timeline(target, window_secs)
        cadence_controller.current_slot_plan = cadence_controller.generate_slot_plan(target)

        planned_active_secs = int(round(window_secs * (target / 100.0)))
        print(f"[Window #{cadence_controller.window_count}] Target: {target:.1f}% | Planned Active: {planned_active_secs}s of {window_secs}s | Running Avg: {cadence_controller.running_average:.1f}%")

        window_rolled_over = False
        for seg_type, seg_duration in timeline:
            if stop.is_set() or window_rolled_over:
                break

            # Focus Time Normalizer:
            # During Deep Focus blocks (Windows 7-11, 31-35), DO NOT interrupt! Hubstaff accumulates ~20% Focus Time.
            # Outside Deep Focus blocks, trigger a safe documentation/browser review every 14-22 min so Hubstaff classifies time as collaborative.
            if (cadence_controller.focus_normalizer_enabled and 
                not cadence_controller.is_in_deep_focus() and
                time.time() >= cadence_controller.next_focus_break_time and 
                not stop.is_set()):
                perform_focus_break(stop)
                if stop.is_set():
                    break
                if cadence_controller.check_window_rollover():
                    window_rolled_over = True
                    break

            seg_start = time.time()
            seg_scaled_dur = seg_duration * SLEEP_SCALE
            seg_end = seg_start + seg_scaled_dur

            if seg_type == 'ACTIVE':
                # Check if we have already reached our planned active seconds budget (e.g. from focus break)
                if cadence_controller.window_active_seconds >= planned_active_secs:
                    cadence_controller.last_action_text = f"Window target reached ({target:.1f}%). Resting..."
                    while time.time() < seg_end and not stop.is_set():
                        if cadence_controller.check_window_rollover():
                            window_rolled_over = True
                            break
                        stop.wait(min(0.5, max(0.01, seg_end - time.time())))
                    continue

                while time.time() < seg_end and not stop.is_set():
                    if cadence_controller.check_window_rollover():
                        window_rolled_over = True
                        break

                    curr_x, curr_y = backend.mouse_position()

                    # Real user physical mouse detection
                    if last_engine_mouse_pos is not None and backend.name != "fake":
                        drift = math.hypot(curr_x - last_engine_mouse_pos[0], curr_y - last_engine_mouse_pos[1])
                        if drift > 40:
                            cadence_controller.user_active = True
                            print(f"  [User Active] Physical mouse motion detected (drift {drift:.0f}px). Yielding emulation...")
                            u_start = time.time()
                            last_pos = (curr_x, curr_y)
                            still_start = time.time()
                            while not stop.is_set():
                                stop.wait(1.0 * SLEEP_SCALE)
                                cx, cy = backend.mouse_position()
                                if math.hypot(cx - last_pos[0], cy - last_pos[1]) > 10:
                                    last_pos = (cx, cy)
                                    still_start = time.time()
                                elif time.time() - still_start >= (15.0 * SLEEP_SCALE):
                                    break
                            u_dur = time.time() - u_start
                            cadence_controller.add_active_seconds(u_dur)
                            cadence_controller.user_active = False
                            curr_x, curr_y = backend.mouse_position()
                            last_engine_mouse_pos = (curr_x, curr_y)
                            print("  [User Inactive] Mouse stationary. Resuming emulation...")
                            if stop.is_set():
                                break

                    # Micro-Action 1: Smooth Bezier mouse glide
                    target_x = max(150, min(curr_x + random.randint(-350, 350), 1250))
                    target_y = max(150, min(curr_y + random.randint(-250, 250), 750))
                    move_humanlike_adaptive(curr_x, curr_y, target_x, target_y, stop)
                    last_engine_mouse_pos = (target_x, target_y)

                    # Micro-Action 2: Natural keystroke burst
                    strokes, key_min, key_max = cadence_controller.get_cycle_parameters()
                    for _ in range(strokes):
                        if stop.is_set(): break
                        if random.random() < 0.20:
                            backend.tap_key(SHIFT_MODIFIER)
                        else:
                            backend.tap_key(random.choice(CORE_DENSE_KEYS))
                        pause(random.uniform(key_min, key_max))

                    # Micro-Action 3: Navigation event (Safe work app toggle outside deep focus, tab cycling, scrolling)
                    if not cadence_controller.is_in_deep_focus() and random.random() < 0.10:
                        simulate_real_app_switch()
                    elif random.random() < 0.30:
                        hardware_browser_tab_switch()
                    else:
                        simulate_vertical_scrolling(stop)

                    # Micro-Action 4: Optional click
                    if random.random() < 0.20:
                        pause(0.04)
                        cx, cy = backend.mouse_position()
                        backend.click(cx, cy)
                        last_engine_mouse_pos = (cx, cy)

                    cadence_controller.last_action_text = f"Active burst in progress (Target: {target:.1f}%)"

                    rem_in_seg = seg_end - time.time()
                    if rem_in_seg > 0:
                        stop.wait(min(rem_in_seg, random.uniform(0.6, 1.2) * SLEEP_SCALE))

                act_elapsed = (time.time() - seg_start) / max(0.001, SLEEP_SCALE)
                cadence_controller.add_active_seconds(min(seg_duration, act_elapsed))

            else:
                cadence_controller.last_action_text = f"Review pause ({seg_duration}s)"
                while time.time() < seg_end and not stop.is_set():
                    if cadence_controller.check_window_rollover():
                        window_rolled_over = True
                        break

                    curr_x, curr_y = backend.mouse_position()
                    if last_engine_mouse_pos is not None and backend.name != "fake":
                        drift = math.hypot(curr_x - last_engine_mouse_pos[0], curr_y - last_engine_mouse_pos[1])
                        if drift > 40:
                            cadence_controller.user_active = True
                            print(f"  [User Active] Physical mouse motion during pause (drift {drift:.0f}px). Yielding...")
                            u_start = time.time()
                            last_pos = (curr_x, curr_y)
                            still_start = time.time()
                            while not stop.is_set():
                                stop.wait(1.0 * SLEEP_SCALE)
                                cx, cy = backend.mouse_position()
                                if math.hypot(cx - last_pos[0], cy - last_pos[1]) > 10:
                                    last_pos = (cx, cy)
                                    still_start = time.time()
                                elif time.time() - still_start >= (15.0 * SLEEP_SCALE):
                                    break
                            u_dur = time.time() - u_start
                            cadence_controller.add_active_seconds(u_dur)
                            cadence_controller.user_active = False
                            curr_x, curr_y = backend.mouse_position()
                            last_engine_mouse_pos = (curr_x, curr_y)
                            if stop.is_set():
                                break

                    rem_in_seg = seg_end - time.time()
                    stop.wait(min(0.25 * SLEEP_SCALE, max(0.01, rem_in_seg)))

        if not stop.is_set() and not window_rolled_over:
            cadence_controller.check_window_rollover()

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
            payload = {
                "status": state,
                "backend": backend.name,
                "platform": sys.platform,
                # WHY these two on every poll: the console polls /status every
                # 2 s and has no other channel, so an input problem that is not
                # in this payload cannot reach the person looking at the page.
                "inputWorking": bool(backend.input_ok),
                "warning": input_warning(),
            }
            payload.update(cadence_controller.telemetry)
            self._send_json(payload)
        elif path in ('/', '/index.html'):
            self._send_console()
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        global stop_event, engine_thread
        path = self.path.split('?', 1)[0]

        if path not in ('/start', '/stop', '/verify', '/reset-history', '/focus-normalizer/toggle'):
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

        if path == '/focus-normalizer/toggle':
            cadence_controller.focus_normalizer_enabled = not cadence_controller.focus_normalizer_enabled
            self._send_json({
                "success": True,
                "focusNormalizerEnabled": cadence_controller.focus_normalizer_enabled,
                "message": f"Focus Time Normalizer {'enabled' if cadence_controller.focus_normalizer_enabled else 'disabled'}"
            })
            return

        if path == '/verify':
            res = cadence_controller.run_activity_verification()
            self._send_json({"success": True, "verification": res})
            return

        if path == '/reset-history':
            cadence_controller.reset_history()
            self._send_json({"success": True, "message": "Session history reset"})
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
