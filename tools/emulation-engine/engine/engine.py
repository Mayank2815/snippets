"""Core Emulation Engine — generates mouse, keyboard and scroll input on this computer.

A tiny HTTP server on 127.0.0.1:4320 exposes the engine:

    GET  /         the control console (console/index.html, next to this folder)
    GET  /status   {"status": "IDLE" | "RUNNING" | "STOPPING", "backend": ...,
                    "platform": ..., "inputWorking": true|false, "warning": null|"...",
                    "mode": null | "BURST" | "STANDARD" | "READING" | "THINKING",
                    "pausedForUser": true|false, "governor": {...}|null,
                    "runSecondsRemaining": null|number, "maxRunHours": number,
                    "clickEnabled": true|false}
    POST /start    start the emulation loop in a background thread
                   (503 when inputWorking is false — see below)
    POST /stop     ask the loop to stop after its current step

How much input it generates is not left to chance. An activity tracker scores a
ten-minute window as 60 blocks of ten seconds and counts a block as active if
any input landed in it; `governor.ActivityGovernor` gives each window a random
budget of those blocks, never lets one exceed a hard ceiling, paces the spending
across the window, and counts the *person's* own keystrokes against the same
budget. See engine/governor.py, and engine/test_metrics.py for the three-hour
simulation the numbers were tuned against.

The engine also gets out of the way. When it sees input it cannot have
generated itself it stops entirely and says "paused — you are using this
computer", resuming once the person has been quiet again. And it stops for good
after ENGINE_MAX_HOURS (3 by default), so a forgotten run does not carry on all
night.

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
from backends.base import (
    pause, engine_time, SLEEP_SCALE, KEY_HOLD_SECONDS, CLICK_HOLD_SECONDS,
    CHORD_HOLD_SECONDS, SWITCH_SHOW_SECONDS, SWITCH_TAB_HOLD_SECONDS,
    SWITCH_TAB_GAP_SECONDS, SWITCH_ACTIVATE_SECONDS,
)
import governor as governor_module
from governor import ActivityGovernor

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
# global flag while the worker might be inside its end-of-cycle sleep; a /start
# in that window set the flag back to True and started a second thread, and the
# old one woke up, saw True and carried on — two loops at once.
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

# --- HOW LONG A RUN LASTS ---------------------------------------------------
# WHY a limit at all: the engine has no idea whether anyone is still there, and
# the failure mode of forgetting it is that it drives the machine all night.
# Keep Alive (tools/keep-alive) solved the identical problem the identical way
# — it keeps an instance awake for a fixed number of hours from each press of
# Start, precisely so a forgotten toggle cannot defeat the thing the limit
# exists for.
# WHY 3 hours: it is the span the activity band was specified and simulated
# over, so it is also the span the engine's promises about its own numbers are
# actually measured across. ENGINE_MAX_HOURS overrides it; 0 means no limit,
# for someone who has decided they want that.
DEFAULT_MAX_RUN_HOURS = 3.0


def _max_run_hours():
    raw = os.environ.get("ENGINE_MAX_HOURS", "").strip()
    if not raw:
        return DEFAULT_MAX_RUN_HOURS
    try:
        hours = float(raw)
    except ValueError:
        print(f"[Warning] ENGINE_MAX_HOURS={raw!r} is not a number; using {DEFAULT_MAX_RUN_HOURS}.")
        return DEFAULT_MAX_RUN_HOURS
    return max(0.0, hours)


MAX_RUN_HOURS = _max_run_hours()

# --- CLICKING ---------------------------------------------------------------
# WHY off by default: the click lands wherever the pointer happens to be, in
# whatever window is in front. That is a link, a Send button, a tab's close
# box, a Delete in a confirmation dialog. Every other thing the engine does is
# reversible — an arrow key moves a caret, a scroll scrolls back — and a click
# is the one that is not. It stays in the code, and stays tested, because on a
# screen someone has deliberately parked on a blank document it is the most
# human thing in the loop; it just is not something to switch on by accident.
ALLOW_CLICK = os.environ.get("ENGINE_ALLOW_CLICK", "").strip() == "1"

# --- BEHAVIOUR PROFILES -----------------------------------------------------
# WHY profiles at all: one fixed cadence makes every cycle look the same, and a
# constant rhythm is the easiest thing in the world to spot. A real person types
# in bursts, reads slowly, and disappears into a call. The loop picks one of
# these per cycle, so the gaps between actions vary the way a person's do.
#
# The profiles decide the *texture* of a cycle. Whether a cycle runs at all is
# the governor's decision — see engine/governor.py.
#
# strokes     — how many keystrokes in the burst
# key_gap     — seconds between keystrokes
# cycle_sleep — seconds of quiet at the end of the cycle
MODE_PROFILES = {
    # Head down and typing: lots of keys, close together, short pause after.
    "BURST":    {"strokes": (24, 36), "key_gap": (0.08, 0.18), "cycle_sleep": (3.0, 6.0)},
    # The middle of the road, and what the loop used to do all the time.
    "STANDARD": {"strokes": (14, 20), "key_gap": (0.12, 0.28), "cycle_sleep": (5.0, 9.0)},
    # Reading the screen: the odd arrow key, long gaps, a long pause after.
    "READING":  {"strokes": (5, 10),  "key_gap": (0.30, 0.60), "cycle_sleep": (8.0, 14.0)},
    # Away from the keyboard entirely — see THINKING_PAUSE below.
    "THINKING": {"strokes": (0, 0),   "key_gap": (0.0, 0.0),   "cycle_sleep": (0.0, 0.0)},
}

# WHY these weights (one entry per draw): STANDARD three times, READING four,
# BURST twice, THINKING once in ten.
#
# WHY the quiet stretches inside the profiles got SHORTER when the governor
# arrived, and why THINKING got rarer. Before the governor, the cycle_sleep
# ranges were the only thing holding the activity rate down, so they had to be
# long (11.5-15.5 s for STANDARD, 16-24 s for READING) and THINKING had to come
# up one draw in six. That is no longer their job. The governor decides how
# many of a window's 60 blocks get used and refuses claims until the pace is
# right, so the profiles are free to describe what a person at a keyboard
# actually does — and they *have* to, because if the loop's own natural rate
# falls near the target the governor stops being the thing in control and the
# activity rate goes back to being an accident of the profile mix. Measured:
# with the old sleeps the loop's unconstrained rate was 51 % against a target
# averaging 46 %, so the governor barely bound and the three-hour average came
# out at 40.2 % with runs as low as 35.1 %. With these it is 70 % against the
# same target, the governor binds everywhere, and the average is 42.9 % with no
# run outside the band in 600.
#
# THINKING survives at one in ten because it still does something the governor
# does not: the governor's own quiet is an even trickle (that is exactly what
# pacing produces), whereas THINKING makes one long unmistakably human gap. At
# one in six it cost about two and a half points of the average for nothing,
# because it threw away pro-rata opportunity the governor had already granted.
MODE_POOL = ["BURST", "BURST", "STANDARD", "STANDARD", "STANDARD",
             "READING", "READING", "READING", "READING", "THINKING"]

# WHY 40-70 s: long enough to read as a call or a corridor conversation rather
# than a pause between keystrokes. Roughly one cycle in ten, so on average
# about a minute away in every ten cycles.
THINKING_PAUSE = (40.0, 70.0)

# --- WHERE THE POINTER GOES -------------------------------------------------
# WHY a fraction of the real screen and not a fixed rectangle: the loop used to
# clamp to x 200-1100, y 200-650 — numbers written for a 13" 1280x800 display.
# On the 1470x956 Mac this is developed on that is 29 % of the screen, all of it
# in the top left, so the pointer never once visited the right-hand third or the
# bottom quarter. A pointer that only ever lives in one corner is a tell.
# WHY 10 % inset with a 60 px floor: it has to clear the macOS menu bar and the
# Dock, the Windows taskbar, and every desktop's hot corners, at any size. On a
# 1470x956 screen that is a 147 px side inset and a 95 px top and bottom one —
# comfortably past a menu bar (~38 px), a Dock (~80 px) and a taskbar (~48 px)
# — and it still leaves 64 % of the screen reachable. The floor stops a small
# or scaled display from insetting itself down to nothing.
TARGET_INSET_FRACTION = 0.10
TARGET_MIN_INSET_PX = 60
# WHY 35 % of the rectangle: the old fixed ±300 px hop was a third of a 900 px
# wide rectangle and is a fifth of a 4K one, so on a big screen the pointer
# would crawl and take minutes to cross. Scaling the hop keeps the *character*
# of the movement — a few hops to cross the working area — identical at any
# size.
MOVE_HOP_FRACTION = 0.35

# --- MOVEMENT, SCROLL AND DICE ----------------------------------------------
# Named so engine/test_metrics.py can model a cycle's duration from the real
# numbers instead of a copy of them.
# WHY 18..40 steps at one per 16 px: short hops still get enough samples to
# show a curve, long moves are capped so they do not flood the event tap.
MOVE_STEPS = (18, 40)
MOVE_PIXELS_PER_STEP = 16
# WHY 5-10 ms between samples: 100-200 Hz, the report rate of a USB mouse.
MOVE_SAMPLE_GAP = (0.005, 0.010)
# WHY 8-15 % of the distance: enough wander in the control points to bend the
# path visibly without swinging off screen.
MOVE_DEVIATION_FRACTION = (0.08, 0.15)
# WHY 4-8 lines: a short flick of a scroll wheel, not a page jump.
SCROLL_LINES = (4, 8)
# WHY 0.15-0.30 s: the cadence of wheel notches; a tighter stream would look
# like a trackpad gesture rather than a wheel.
SCROLL_GAP = (0.15, 0.30)
# WHY 0.22: roughly one stroke in five is a bare Shift, so the burst is not a
# pure run of arrow keys.
SHIFT_PROBABILITY = 0.22
# WHY 0.22: about one cycle in five ends with a click where the pointer already
# is (only when ENGINE_ALLOW_CLICK=1 — see ALLOW_CLICK above).
CLICK_PROBABILITY = 0.22
# WHY 30 / 30 / 40 %: app switch, browser tab switch and scrolling are mixed so
# no single kind of event dominates.
APP_SWITCH_PROBABILITY = 0.30
TAB_SWITCH_PROBABILITY = 0.30
# WHY 0.04 s: a settle before the click, so it does not arrive in the same
# instant the pointer stopped.
CLICK_SETTLE_SECONDS = 0.04
# The neutral fallback every backend uses when the window manager will not say
# how many apps are open. Named here because the cycle-duration model needs it.
DEFAULT_APP_COUNT = 5

# WHY 1 s: how often the loop looks up while it is waiting — at the idle timer
# (so a person who starts typing is noticed within a second) and at the
# governor (so a budget that has just come free is used promptly). A tenth of
# the governor's ten-second block, and cheap enough to do all day: on every
# platform the idle read is one library call.
GOVERNOR_POLL_SECONDS = 1.0

# The profile the loop is in right now, or None when it is not running or when
# the governor is holding it quiet. Read by GET /status. WHY it is reported at
# all: THINKING does nothing for up to 75 seconds, and a console that just says
# RUNNING while the pointer sits still is indistinguishable from a hang — the
# exact "looks broken, says fine" problem the Wayland guard exists to prevent.
# A plain string assignment is atomic in CPython, so these need no lock.
current_mode = None
# True while the loop is standing down because the person is using the machine.
paused_for_user = False
# True while the governor is refusing claims — budget spent, or ahead of pace.
governor_holding = False
# The running worker's governor, or None when idle. /status reads its snapshot.
current_governor = None
# engine_time() at which the current run stops itself, or None when idle.
run_deadline = None


def target_rectangle(width, height):
    """The (left, top, right, bottom) the pointer is allowed to visit, derived
    from the real screen size rather than assumed. See TARGET_INSET_FRACTION."""
    inset_x = max(TARGET_MIN_INSET_PX, int(width * TARGET_INSET_FRACTION))
    inset_y = max(TARGET_MIN_INSET_PX, int(height * TARGET_INSET_FRACTION))
    left, right = inset_x, width - inset_x
    top, bottom = inset_y, height - inset_y
    if right <= left or bottom <= top:
        # A display smaller than twice the floor inset — a tiny virtual screen
        # in a container, or a platform that reported something absurd. Use the
        # whole thing rather than an inverted rectangle the clamp would then
        # collapse to a single point.
        return 0, 0, max(1, width - 1), max(1, height - 1)
    return left, top, right, bottom


def pointer_hop(rect):
    """How far a single hop may move the pointer, in x and y — a fraction of
    the reachable rectangle rather than a fixed pixel count. See
    MOVE_HOP_FRACTION."""
    left, top, right, bottom = rect
    return (max(1, int((right - left) * MOVE_HOP_FRACTION)),
            max(1, int((bottom - top) * MOVE_HOP_FRACTION)))


def next_pointer_target(x, y, rect, hop):
    """Where the pointer goes next: a random hop from where it is, clamped
    into the reachable rectangle. Separate from the loop so a test can iterate
    it a few hundred times and check the whole rectangle really gets visited —
    which is the thing that was silently untrue when the rectangle was a fixed
    1280x800 one."""
    left, top, right, bottom = rect
    hop_x, hop_y = hop
    return (max(left, min(x + random.randint(-hop_x, hop_x), right)),
            max(top, min(y + random.randint(-hop_y, hop_y), bottom)))


def act(gov, call, *args):
    """Generate one piece of input, if the governor allows it right now.

    Every single thing that reaches the operating system goes through here, so
    the block budget cannot be spent behind the governor's back. `claim()`
    decides and records in one step; `note_engine_input()` afterwards stamps
    the moment the call *returned*, which is what keeps the engine from
    mistaking its own two-second app switch for the user arriving.

    Returns False when the claim was refused, which the callers use to cut a
    burst short at a block boundary rather than run past the ceiling.

    The one gap, stated plainly: a *single* backend call that straddles a block
    boundary — an app switch is the only one long enough, at up to ~1.3 s — puts
    input into a block that was never claimed, so the accounting can be one
    block light. It costs at most one block, and only matters at all if a window
    were already sitting on the ceiling, which no measured run comes near (the
    worst window over 2,000 simulated three-hour runs is 34 of 39). Claiming
    per-event inside the backends would close it and would mean the backend
    layer knowing about the governor, which is a worse trade.
    """
    if not gov.claim():
        return False
    call(*args)
    gov.note_engine_input()
    return True


def simulate_real_app_switch(gov):
    """Sequential multi-strike layout with sustained hold times to ensure deep background windows swap context"""
    global app_cycle_index

    total_apps = backend.visible_app_count()

    if app_cycle_index >= total_apps:
        app_cycle_index = 1

    print(f"  [System Shift] Navigating next app in loop sequence. Open Apps Counter: {total_apps}. Striking Tab {app_cycle_index} time(s).")

    act(gov, backend.app_switch, app_cycle_index)

    app_cycle_index += 1

def hardware_browser_tab_switch(gov):
    print("  [Browser Shift] Cycling active browser tab index natively...")
    act(gov, backend.browser_tab_next)

def simulate_vertical_scrolling(gov, stop):
    direction = random.choice([-1, 1])
    scroll_lines = random.randint(*SCROLL_LINES)
    print(f"  [Scroll Active] Generating smooth vertical scrolling. Lines: {scroll_lines}")
    for _ in range(scroll_lines):
        if stop.is_set(): break
        if not act(gov, backend.scroll, 1, direction): break
        pause(random.uniform(*SCROLL_GAP))

def bezier_point(p0_x, p0_y, p1_x, p1_y, p2_x, p2_y, p3_x, p3_y, t):
    x = (1-t)**3 * p0_x + 3*(1-t)**2 * t * p1_x + 3*(1-t) * t**2 * p2_x + t**3 * p3_x
    y = (1-t)**3 * p0_y + 3*(1-t)**2 * t * p1_y + 3*(1-t) * t**2 * p2_y + t**3 * p3_y
    return x, y

def move_humanlike_adaptive(gov, start_x, start_y, end_x, end_y, stop):
    distance = math.hypot(end_x - start_x, end_y - start_y)
    low_steps, high_steps = MOVE_STEPS
    steps = int(max(low_steps, min(high_steps, distance / MOVE_PIXELS_PER_STEP)))
    deviation = distance * random.uniform(*MOVE_DEVIATION_FRACTION)

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
        if not act(gov, backend.move_mouse, target_x, target_y): break
        pause(random.uniform(*MOVE_SAMPLE_GAP))


def quiet_wait(stop, gov, seconds):
    """Wait, in poll-sized slices, watching for the person and for /stop.

    Returns True if it waited the whole time, False if it came back early
    because the loop must react now — /stop, or the person starting to type.

    WHY not one long stop.wait(): the end-of-cycle quiet is up to 24 s and a
    THINKING pause up to 75 s, and a person who sits down in the middle of one
    must not have to wait it out before the engine notices them.
    """
    remaining = seconds
    while remaining > 0:
        slice_seconds = min(GOVERNOR_POLL_SECONDS, remaining)
        if stop.wait(slice_seconds * SLEEP_SCALE):
            return False
        remaining -= slice_seconds
        gov.observe_user_input()
        if gov.user_is_active():
            return False
    return True


def loop_worker(stop, gov=None, deadline=None):
    """The emulation loop. `stop` is this worker's own Event; /stop sets it."""
    print("\n=====================================================")
    print("[Core Engine] Active Target-Stabilized Emulation Initiated.")
    print("=====================================================")

    global current_mode, paused_for_user, governor_holding, current_governor, run_deadline

    if gov is None:
        gov = ActivityGovernor(engine_time, read_idle=backend.seconds_since_user_input)
    gov.begin()
    current_governor = gov

    width, height = backend.screen_size()
    rect = target_rectangle(width, height)
    left, top, right, bottom = rect
    hop_x, hop_y = pointer_hop(rect)
    print(f"  [Pointer Field] {width}x{height} screen; targets stay inside "
          f"x {left}-{right}, y {top}-{bottom} (hops up to {hop_x}x{hop_y} px).")

    if deadline is None:
        deadline = (engine_time() + MAX_RUN_HOURS * 3600.0) if MAX_RUN_HOURS > 0 else None
    run_deadline = deadline
    if deadline is not None:
        print(f"  [Run Limit] stopping automatically after {MAX_RUN_HOURS:g} hour(s).")

    try:
        while not stop.is_set():
            if deadline is not None and engine_time() >= deadline:
                print(f"[Core Engine] Reached the {MAX_RUN_HOURS:g} hour run limit — stopping.")
                break

            gov.observe_user_input()
            if gov.user_is_active():
                if not paused_for_user:
                    print("  [Paused] You are using this computer — standing down until you stop.")
                paused_for_user = True
                current_mode = None
                governor_holding = False
                stop.wait(GOVERNOR_POLL_SECONDS * SLEEP_SCALE)
                continue
            if paused_for_user:
                print("  [Resumed] You have been quiet for a while — carrying on.")
                paused_for_user = False

            # A fresh profile every cycle. Picking per cycle rather than sticking
            # with one for a while is deliberate: the point is that no two
            # consecutive cycles have to look alike.
            mode = random.choice(MODE_POOL)

            if mode == "THINKING":
                current_mode = mode
                governor_holding = False
                macro_pause = random.uniform(*THINKING_PAUSE)
                print(f"  [Behaviour: THINKING] Away from the keyboard for {int(macro_pause)}s.")
                quiet_wait(stop, gov, macro_pause)
                continue

            # The governor's decision, and the only place a cycle is refused
            # outright. Asked before any input is generated so a refused cycle
            # costs nothing at all.
            if not gov.claim():
                if not governor_holding:
                    snap = gov.snapshot()
                    print(f"  [Governor] Holding: {snap['windowUsedBlocks']} of "
                          f"{snap['windowTargetBlocks']} blocks used in this 10-minute window.")
                governor_holding = True
                current_mode = None
                stop.wait(GOVERNOR_POLL_SECONDS * SLEEP_SCALE)
                continue
            governor_holding = False

            profile = MODE_PROFILES[mode]
            current_mode = mode
            print(f"  [Behaviour: {mode}] Processing execution matrix wave.")

            curr_x, curr_y = backend.mouse_position()
            target_x, target_y = next_pointer_target(curr_x, curr_y, rect, (hop_x, hop_y))

            move_humanlike_adaptive(gov, curr_x, curr_y, target_x, target_y, stop)

            # Both the count and the gap come from this cycle's profile, so the
            # keyboard burst is 2 s of hammering or 5 s of idle tapping depending
            # on which one was drawn.
            strokes = random.randint(*profile["strokes"])
            sent = 0
            for _ in range(strokes):
                if stop.is_set(): break
                key = SHIFT_MODIFIER if random.random() < SHIFT_PROBABILITY else random.choice(CORE_DENSE_KEYS)
                if not act(gov, backend.tap_key, key): break
                sent += 1
                pause(random.uniform(*profile["key_gap"]))
            print(f"  - Distributed {sent} safe telemetry hits over separate execution ticks.")

            dice = random.random()
            if dice < APP_SWITCH_PROBABILITY:
                simulate_real_app_switch(gov)
            elif dice < APP_SWITCH_PROBABILITY + TAB_SWITCH_PROBABILITY:
                hardware_browser_tab_switch(gov)
            else:
                simulate_vertical_scrolling(gov, stop)

            if ALLOW_CLICK and random.random() < CLICK_PROBABILITY:
                pause(CLICK_SETTLE_SECONDS)
                fx, fy = backend.mouse_position()
                act(gov, backend.click, fx, fy)

            # The end-of-cycle quiet, again from this cycle's profile, waited in
            # poll-sized slices so the person and /stop are both noticed at once.
            quiet_wait(stop, gov, random.uniform(*profile["cycle_sleep"]))
    finally:
        current_mode = None
        paused_for_user = False
        governor_holding = False
        current_governor = None
        run_deadline = None
    print("[Core Engine] Emulation loop ended.")


def cycle_seconds(mode, rng):
    """(input_seconds, quiet_seconds) for one cycle of `mode`.

    The duration model engine/test_metrics.py steps its virtual clock with. It
    is composed from the same module-level constants the loop itself uses —
    every number here is imported, none is written down twice — so retuning a
    profile or a hold time moves the simulation with it.

    It is still a *model*: it assumes no claim is refused mid-cycle and that
    every backend call takes its nominal time. Both make the simulated cycle a
    little longer than a truncated real one, which is the conservative
    direction (a longer cycle touches more blocks, so the simulation cannot
    flatter the ceiling). Change this function whenever you change the order or
    the contents of a cycle in loop_worker.
    """
    if mode == "THINKING":
        return 0.0, rng.uniform(*THINKING_PAUSE)

    profile = MODE_PROFILES[mode]

    # The curved pointer move: steps+1 samples, each a gap apart.
    steps = rng.randint(*MOVE_STEPS)
    active = (steps + 1) * rng.uniform(*MOVE_SAMPLE_GAP)

    # The keystroke burst: each key is a hold plus a gap.
    strokes = rng.randint(*profile["strokes"])
    active += strokes * (rng.uniform(*KEY_HOLD_SECONDS) + rng.uniform(*profile["key_gap"]))

    # One of an app switch, a browser-tab switch or a scroll.
    dice = rng.random()
    if dice < APP_SWITCH_PROBABILITY:
        taps = rng.randint(1, max(1, DEFAULT_APP_COUNT - 1))
        active += (SWITCH_SHOW_SECONDS
                   + taps * (SWITCH_TAB_HOLD_SECONDS + SWITCH_TAB_GAP_SECONDS)
                   + SWITCH_ACTIVATE_SECONDS)
    elif dice < APP_SWITCH_PROBABILITY + TAB_SWITCH_PROBABILITY:
        active += rng.uniform(*CHORD_HOLD_SECONDS)
    else:
        active += rng.randint(*SCROLL_LINES) * rng.uniform(*SCROLL_GAP)

    if ALLOW_CLICK and rng.random() < CLICK_PROBABILITY:
        active += CLICK_SETTLE_SECONDS + CLICK_HOLD_SECONDS

    return active, rng.uniform(*profile["cycle_sleep"])


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
            running = state == "RUNNING"
            gov = current_governor
            deadline = run_deadline
            self._send_json({
                "status": state,
                "backend": backend.name,
                "platform": sys.platform,
                # WHY these two on every poll: the console polls /status every
                # 2 s and has no other channel, so an input problem that is not
                # in this payload cannot reach the person looking at the page.
                "inputWorking": bool(backend.input_ok),
                "warning": input_warning(),
                # None unless a loop is actually running, so a stale mode from
                # the last run can never be shown next to IDLE.
                "mode": current_mode if running else None,
                # WHY the governor is in here at all: the whole tool now makes a
                # specific numeric promise about how active it will look, and
                # the console is the only surface anyone looks at. A promise
                # nobody can check is not a promise.
                "governor": gov.snapshot(holding=governor_holding) if (running and gov is not None) else None,
                "pausedForUser": bool(paused_for_user) if running else False,
                "runSecondsRemaining": max(0.0, deadline - engine_time()) if (running and deadline is not None) else None,
                "maxRunHours": MAX_RUN_HOURS,
                "clickEnabled": ALLOW_CLICK,
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
    left, top, right, bottom = target_rectangle(width, height)
    print("=========================================================")
    print(f"🚀 TARGET-CALIBRATED ENGINE V20.0 ON PORT {PORT}")
    print(f"   Console: http://{BIND_HOST}:{PORT}/")
    print(f"   Backend: {backend.name} on {sys.platform} ({backend.platform_note}); screen {width}x{height}")
    print(f"   Pointer stays inside x {left}-{right}, y {top}-{bottom}")
    low, high = governor_module.WINDOW_TARGET_BLOCKS
    print(f"   Activity: {low}-{high} of {governor_module.BLOCKS_PER_WINDOW} ten-second blocks per "
          f"10-minute window, hard ceiling {governor_module.CEILING_BLOCKS} "
          f"({governor_module.CEILING_PERCENT:g}%)")
    print(f"   Clicking: {'ON (ENGINE_ALLOW_CLICK=1)' if ALLOW_CLICK else 'off — set ENGINE_ALLOW_CLICK=1 to enable'}")
    print(f"   Run limit: {('%g hour(s)' % MAX_RUN_HOURS) if MAX_RUN_HOURS > 0 else 'none (ENGINE_MAX_HOURS=0)'}")
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
