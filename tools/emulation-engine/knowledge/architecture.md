# Architecture

One process, two threads, one HTML file, one input backend per platform.

```
 browser (console/index.html, or another page served from this computer)
      |  GET /status every 2 s, POST /start, POST /stop
      |  (CORS: Origin reflected only for http://127.0.0.1 / http://localhost;
      |   POSTs must carry X-Engine-Control: 1)
      v
 +----------------------------- engine.py ------------------------------+
 |  main thread: HTTPServer on 127.0.0.1:4320  (EngineBridgeHandler)    |
 |      GET  /          -> reads ../console/index.html, serves it       |
 |      GET  /status    -> {"status": IDLE|RUNNING|STOPPING,            |
 |                          "backend": "quartz", "platform": "darwin",  |
 |                          "inputWorking": true, "warning": null,      |
 |                          "mode": ..., "pausedForUser": false,        |
 |                          "governor": {...}, "clickEnabled": false,   |
 |                          "runSecondsRemaining": 9123.4}              |
 |      POST /start     -> new stop Event + loop_worker thread; 409     |
 |                         while the previous worker is still alive;    |
 |                         503 when the backend cannot deliver input    |
 |      POST /stop      -> sets the worker's Event; loop exits after    |
 |                         its current step                             |
 |      OPTIONS *       -> 200 + CORS headers (preflight)               |
 |      SIGTERM/Ctrl-C  -> stop, join worker 2 s, release modifiers     |
 |                                                                      |
 |  engine thread (daemon): loop_worker(stop) while not stop.is_set()   |
 |      every action is gated by governor.claim() ---------+            |
 |      every OS call goes through the backend object ------------+     |
 |                                                        |       |     |
 |  +----------------- engine/governor.py ----------------v-+     |     |
 |  | ActivityGovernor(clock, read_idle, rng)               |     |     |
 |  |   claim()                may the engine act right now?|     |     |
 |  |   observe_user_input()   was that the PERSON typing?  |     |     |
 |  |   user_is_active()       then stand down entirely     |     |     |
 |  |   snapshot()             what /status and the console |     |     |
 |  |                          show                         |     |     |
 |  +-------------------------------------------------------+     |     |
 +----------------------------------------------------------------|-----+
                                                                  v
 +--------------------- engine/backends/ (get_backend()) ---------------+
 |  base.py    the contract: mouse_position, screen_size, move_mouse,   |
 |             click, scroll, tap_key, app_switch, browser_tab_next,    |
 |             visible_app_count, release_modifiers, name, platform_note,|
 |             input_ok, input_error, input_remedy                      |
 |  quartz.py  darwin -> CGEventPost -> kCGHIDEventTap -> macOS input   |
 |  pynput_backend.py  win32 -> SendInput | linux -> X11/XTest          |
 |  linux_session.py   x11 / wayland / headless, from the environment   |
 |  unavailable.py     input_ok=False stand-in when the session cannot  |
 |                     receive input; every input method raises         |
 |  fake.py    records every call, generates nothing (tests, CI)        |
 +----------------------------------------------------------------------+
```

## Behaviour profiles

`MODE_POOL` and `MODE_PROFILES` in `engine.py` drive one draw per cycle. Three
profiles differ only in numbers (how many keystrokes, the gap between them, the
quiet afterwards) and run the normal cycle; `THINKING` is the exception and
returns before any backend call, waiting 45-75 s on the worker's own stop event
so `/stop` still ends it at once.

The drawn profile is published in `GET /status` as `mode` and rendered by the
console in words. That is not decoration: `THINKING` produces no input for up
to a minute, and `RUNNING` next to a still pointer is the same "says fine,
looks broken" shape as the Wayland failure below. Naming the profile is what
separates the two for whoever is watching the page.

## The activity governor

`engine/governor.py`. This is the part that makes the tool's central promise
true by construction rather than by hoping.

### What a tracker actually measures

Not effort — **boxes**. Ten minutes is cut into 60 blocks of ten seconds, and a
block is "active" if any input at all arrived during it. One keystroke ticks a
block exactly as hard as a hundred. Your score is the percentage ticked. Every
number in this module is in those units, because reasoning in any other unit
would be reasoning about the wrong thing.

### The five rules

1. **A hard ceiling (`CEILING_BLOCKS` = 39, i.e. 65 %).** Checked on every
   claim, separately from the window target, so it survives any retuning of the
   draw. It is checked over a **rolling** ten minutes, not only the windows the
   engine lines up with — a tracker does not have to share our clock.
2. **A wildly different budget per window, dealt from a bag.**
   `WINDOW_TARGET_BAG` holds nine budgets in three groups — quiet (6, 12, 18
   blocks = 10 %, 20 %, 30 %), middling (24, 30) and busy (34, 35, 36, 36) —
   each nudged by ±2 blocks of jitter. See "the bag's fixed sum" below for why
   this does not cost the average anything.
3. **The deal order is constrained, not merely shuffled.** A quiet budget and a
   busy one must both appear in every `DEAL_SPACING` (4) consecutive draws; two
   consecutive budgets must differ by at least 2 blocks; three consecutive must
   span at least 4. All three are checked **across the seam between one bag and
   the next**, which is where a plain shuffle puts eight similar windows in a
   row. See "why the deal order is the whole guarantee".
4. **Pacing, with a per-window shape.** At block *e* of 60, at most
   `ceil(target x (e/60) ** shape)` blocks may have been spent. The shape is
   drawn per window — below 1 front-loads, above 1 back-loads — and its swing
   shrinks to nothing as the budget approaches the ceiling, because
   back-loading a busy window spends into the next window's first half and the
   rolling ceiling would simply refuse it.
5. **The person's input counts against the same budget.** Otherwise the engine
   adds to a score that is already being earned, and the combined figure leaves
   the band from below.

### The bag's fixed sum is what buys wide windows for free

This is the property the first version of the governor had and did not use. **A
bag has a fixed sum**, so every full cycle of it averages the same thing
whatever order it is dealt in. The individual windows may therefore swing as
violently as you like — 10 % to 60 % — and the long-run average does not move.

The first version narrowed the bag to 22-33 blocks to protect the average,
which was solving a problem the bag had already solved, and the cost was that
the engine never produced a quiet window at all. Three hours is eighteen
windows, which is exactly two cycles of the nine-value bag, which is why the
run-to-run spread of the average is now only ±2 points (sd 0.54) despite
individual windows spanning 7 %-63 %.

Nine values is also not an accident: the tracker's complaint was about 90
minutes, which is nine of our windows, so one full cycle of the bag fits inside
every stretch long enough to be flagged.

### Why the deal order is the whole guarantee

The variance promise ("any nine consecutive windows span at least 25 points")
is not a measurement that happened to come out well — it follows from rule 3.
If a quiet budget appears in every four consecutive draws, then any nine
consecutive windows contain two whole disjoint runs of four, so they contain a
quiet window; the same argument gives a busy one; and the range follows from
the gap between the two groups.

The rules are enforced by rejection sampling — shuffle the bag, apply the
jitter, check, retry — which cannot bias *which* values are dealt, only their
order and their nudge, both symmetric about the value. So the fixed sum
survives. Measured: zero fallbacks in 2,000 runs, and the bag's dealt mean
matches its designed mean to within 0.2 blocks.

The jitter is drawn **here**, with the order, and not at the moment a budget is
handed out. That is deliberate: two values two blocks apart, jittered +2 and
−2, come out identical, and measured, that was happening to about one adjacent
pair in a hundred. A rule checked before the jitter is a rule about something
the window never runs on.

### Telling the person's input from the engine's own

There is no API anywhere that says "the user did this". Every platform offers
one number — seconds since the last input of *any* kind — and the engine's own
synthetic events reset it exactly as real ones do. So the separation is by
timestamp:

```
last_input_at = now - backend.seconds_since_user_input()
it_was_the_person  <=>  last_input_at > engine_last_input_at + MARGIN
```

Two details make that work, and getting either wrong is fatal in an obvious
way. An engine that reads its own input as the user's pauses **forever**; one
that never notices the user **never pauses**.

- `note_engine_input()` stamps the engine's clock **after** each backend call
  returns, not before. An app switch holds a modifier for up to two seconds and
  the OS stamps its idle timer at the end of that; with the stamp taken first,
  the engine would read its own switch as somebody sitting down.
- `begin()` stamps the engine's clock at the moment the run starts. Pressing
  Start in the console *is* user input, and so is the keystroke that launched
  `run.sh` — without this the engine pauses the instant it starts, which reads
  exactly like a broken button.

The consequence worth knowing: the engine can only see the person during its
**own quiet stretches**. Mid-burst, its events mask theirs. That is not worth
closing — the loop is quiet for most of every cycle, and a masked keystroke
lands in a block the engine is ticking anyway. Measured in simulation, about
one human block in ten is missed this way.

### Pacing stops front-loading; only the rolling check stops catch-up

Pro-rata pacing limits running *ahead*. It does not limit running *behind* and
then catching up — and a window whose first half was a long quiet pause is
entitled to spend its whole budget in the second half. Put two such windows
side by side and the ten minutes spanning the boundary holds 45 ticked blocks
(75 %) while **both fixed windows read comfortably under the ceiling**. Measured
at exactly that before `_rolling_used()` existed. Hence rule 1 being a rolling
check: it is the only form of the ceiling that means what the requirement says.

### Where the numbers came from

`engine/test_metrics.py` steps a virtual clock through three simulated hours of
the real loop driving the real governor, and does it hundreds of times in about
a second. Every constant it uses is imported from `engine.py` and
`governor.py`; it declares none of its own. Measured over 2,000 runs at the
shipped settings: average **42.78 %**, spread 40.83 %-44.35 %, sd 0.54, worst
single window 63.3 %, worst rolling window 65.0 %, **none outside the 38-47 %
band and none over the ceiling**; tightest 90 minutes **28.3 points of range**
and 10.0 of standard deviation against floors of 25 and 8; 22.2 % of all
windows under 25 %; every ten-point band from 0 % to 63 % populated.

### Why the profiles were retuned at the same time

If the loop's own unconstrained rate sits near the target, the governor barely
binds and the activity rate goes back to being an accident of the profile mix.
Measured: with the old cycle_sleep ranges the loop's natural rate was 51 %
against a target averaging 46 %, and the three-hour average came out at 40.2 %
with runs as low as 35.1 %. With the shorter ranges it is 70 % against the same
target, the governor binds everywhere, and the average is where it was tuned to
be. THINKING dropped from one draw in six to one in ten for the same reason: it
used to be the mechanism that held the rate down, the governor is that now, and
at one in six it cost about two and a half points of the average by throwing
away pro-rata opportunity that had already been granted.

### Why THINKING had to ask permission (2026-09-29)

Re-measuring the loop's shortfall while widening the bag produced a flat
answer: **THINKING was the entire shortfall.** With it removed from the pool the
loop hits its budget exactly — 30 of 30, 33 of 33, 39 of 39, every window, at
every level. With it in, a budget of 36 realised 32.4 on average and as little
as 18.

That mattered twice over. It capped the reachable rate near 55 %, which is what
made a third of windows at 10 % arithmetically impossible; and it made a busy
window's score *unpredictable*, which is what widened the run-average spread
until whole three-hour runs fell under the 38 % floor.

So `choose_mode` in `engine.py` now asks `gov.pause_is_affordable(seconds)`
first: after this pause, will the window still have at least as many blocks
left as it has budget left to spend in them? In a quiet window the answer is
always yes; in a busy one it is usually no, and the loop draws a working
profile instead. The shortfall went to ~0.01 blocks at every budget, the
run-average sd from 1.1 to 0.54, and the quiet windows got the long human gaps
— which is where a person's long gaps actually are.

## Pausing while the person works

`loop_worker` polls `governor.observe_user_input()` once a second — at the top
of every iteration and inside every wait, via `quiet_wait()`, which is why a
45-second THINKING pause does not delay noticing somebody sitting down. While
`user_is_active()` the loop generates nothing at all, `/status` says
`pausedForUser: true`, and the console says "Paused — you are using this
computer". It resumes after `USER_ACTIVE_QUIET_SECONDS` (45 s) of real quiet.

The person's blocks still tick the budget throughout, so the score is shared
rather than stacked.

`seconds_since_user_input()` is part of the backend contract:

| Platform | How |
|---|---|
| macOS | `CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateCombinedSessionState, kCGAnyInputEventType)` — the combined state sees hardware and posted events alike, which is what "has anything happened here" means |
| Windows | `GetLastInputInfo` against `GetTickCount`, masked to 32 bits so the 49.7-day wrap does not produce a negative |
| Linux | the X server's XScreenSaver idle timer through `ctypes`, falling back to the `xprintidle` command |
| fake | a settable value, `None` by default |

**`None` must stay an option.** A Linux box with neither XScreenSaver nor
xprintidle genuinely cannot answer, and guessing a number there would make the
engine pause at random. `None` means the engine never pauses, and
`/status` carries `userInputVisible: false` so the console can say so rather
than implying a feature is working when it is inert.

## The run limit

A run stops itself after `MAX_RUN_HOURS` (`ENGINE_MAX_HOURS`, default 3; 0
disables it). `loop_worker` checks the deadline at the top of each iteration and
returns, which leaves `engine_state()` reporting IDLE because the thread is
gone. `/status` carries `runSecondsRemaining` and the console renders it as
"stops in 2h 41m".

Three hours is the span the activity band is specified and simulated over, so it
is also the span over which the engine's promises about its own numbers have
actually been measured. `tools/keep-alive` solved the identical problem the
identical way and for the identical reason: an idle limit exists to save
something, and a forgotten toggle should not be able to defeat it.

## Where the pointer may go

`target_rectangle(width, height)` derives the reachable area from the real
screen: 10 % in from each edge with a 60 px floor, which clears the macOS menu
bar and Dock, the Windows taskbar and every desktop's hot corners at any size,
and leaves about 64 % of the screen reachable. `pointer_hop()` scales the hop
between targets to 35 % of the rectangle, so crossing a 4K display takes the
same handful of hops a laptop one does. Both are separate functions precisely so
a test can iterate `next_pointer_target()` a few hundred times and assert that
all nine regions of a 3x3 grid really do get visited.

The old rectangle was a fixed `x 200-1100, y 200-650`, written for a 13"
1280x800 display: 29 % of a 1470x956 screen, all of it top-left, and less again
on anything larger.

## Clicking

`ALLOW_CLICK` (`ENGINE_ALLOW_CLICK=1`) is **off** by default. Everything else
the loop does is reversible; a click is not — it lands on whatever is in front,
which can be a link, a Send button, a tab's close box or a Delete in a dialog.
The code path and its tests stay, because on a screen someone has deliberately
parked on something blank it is the most human thing in the loop. `/status`
reports `clickEnabled` so the console can say which it is.

## The backend layer

`engine.py` holds the loop — the behaviour profiles, the probabilities, the
target rectangle, every timing constant — and never talks to the operating
system directly. Everything platform-specific sits behind one object obtained once at
import time:

```python
from backends import get_backend
backend = get_backend()
```

`backends/__init__.py` picks by `sys.platform`: `darwin` -> `quartz`, `win32`
and `linux` -> `pynput`. The environment variable
`ENGINE_BACKEND=fake|quartz|pynput` overrides that, which is how the tests run
the whole engine on a developer's Mac without moving the real pointer. An
unknown override or an unsupported platform raises `ValueError`, and a missing
platform library raises `ImportError`; `engine.py` catches both and exits with
one clear line rather than a traceback from deep inside an import.

### Can this session actually receive input?

Every backend also answers three questions the loop never asks but the *user*
needs: `input_ok`, `input_error` and `input_remedy`. They exist because of one
platform, Linux, where a backend can construct perfectly and still deliver
nothing: under Wayland the XTest calls succeed and the compositor discards
them, so the engine would sit in RUNNING while the pointer never moves.

`backends/linux_session.py` classifies the session from its environment alone —
`x11`, `wayland` or `headless` — before pynput is imported. It is a pure
function, which is what lets the tests cover every branch on a machine with no
X server and no compositor. Either Wayland signal (`WAYLAND_DISPLAY`, or
`XDG_SESSION_TYPE=wayland`) is decisive: a desktop can set one and not the
other, and refusing an X11 session that left a stale variable around is a
visible, overridable annoyance, while the opposite mistake is invisible.

When the verdict is negative, `get_backend()` returns an `UnavailableBackend`
rather than raising. The server therefore still starts, which is the point: the
console — the page `run.sh` just opened in the user's browser — is the only
surface they are looking at, so it has to be the surface that explains the
problem. `/status` carries `inputWorking: false` and a `warning` holding both
the cause and the fix, `POST /start` answers **503** before touching the lock
or the thread state, the console paints a red "INPUT UNAVAILABLE" panel with
Start disabled, and the startup banner prints the same text for anyone reading
a log. `UnavailableBackend`'s input methods raise rather than pass, so a future
caller that forgets to check `input_ok` fails loudly instead of silently doing
nothing — the exact bug the class was added to prevent.

`ENGINE_ALLOW_WAYLAND=1` keeps `input_ok` True while leaving the warning in
place, for someone who only drives XWayland windows. It turns a refusal into a
visible caveat, never into silence.

### What differs per platform, and what does not

The loop calls the same ten methods everywhere. Only these differ:

| Concept              | macOS (`quartz`)                    | Windows / Linux (`pynput`)     |
|----------------------|-------------------------------------|--------------------------------|
| app switcher         | Command held + Tab x n              | Alt held + Tab x n             |
| next browser tab     | Cmd+Option+Right (flags `0x180000`) | Ctrl+Tab                       |
| key identity         | virtual key codes 123–126, 56, 48   | `pynput.keyboard.Key` members  |
| window count         | `osascript` System Events, timeout 5 s | Windows: `EnumWindows` via ctypes; Linux: `wmctrl -l` lines |
| modifiers released on shutdown | Command                   | Alt and Ctrl                   |

Everything else is shared: the hold times (12–25 ms per key, 80/50/180/300 ms
through an app switch, 50–100 ms for a chord, 20 ms for a click), the fallback
of **5** when the window count cannot be determined, and the requirement that a
backend call never blocks for long — the loop only checks its stop Event
between calls, so a hanging backend call makes `/stop` hang too.

The loop refers to keys by **name** (`'left'`, `'right'`, `'up'`, `'down'`,
`'tab'`, `'shift'`) and each backend maps those to its own codes. That is what
let the macOS key-code table move out of the loop without changing behaviour.

### The clock scale

`engine_time()` is `time.monotonic()` divided by `SLEEP_SCALE`. The governor
measures ten-second blocks of wall time, and `ENGINE_FAST=1` shrinks every sleep
in the loop by 100; left on the real clock, a whole fast-mode test run would sit
inside a single block and the governor would behave nothing like it does in
production. Dividing by the same factor keeps the governor's blocks in exactly
the same proportion to the loop's cadence at either speed.

### The sleep scale

`backends/base.py` exports `pause()` and `SLEEP_SCALE`. Every hold and gap in
the loop and in the backends goes through `pause()`, which multiplies by
`SLEEP_SCALE` — `1.0` normally, `0.01` when `ENGINE_FAST=1`. The end-of-cycle
`stop.wait()` applies the same factor. That is purely a test lever: it lets
`engine/tests/test_engine.py` exercise a real cycle in milliseconds instead of
the real 13–17 seconds, on exactly the same code path. It is never set in
normal use.

## The HTTP server

`http.server.HTTPServer` with a `BaseHTTPRequestHandler` subclass. It is
single-threaded, which is fine: every request is answered in microseconds and
the slow work happens on the engine thread. `log_message` is silenced so the
2-second status poll does not spam the terminal.

`BIND_HOST` is hard-wired to `127.0.0.1`. The engine drives *this* machine's
input, so it must never be reachable from the network. `PORT` can be
overridden through the `PORT` environment variable (run.sh passes it through)
for the rare case 4320 is taken; the bind address cannot.

### CORS and who may control the engine

Loopback binding keeps other machines out, but **not the user's own browser**
— it is on loopback too. With the old `Access-Control-Allow-Origin: *`, any
web page the user happened to visit could `fetch('http://127.0.0.1:4320/start',
{method: 'POST'})` and drive their mouse; a bare POST is a "simple request"
that browsers send without asking first. Two things close that (both in
`EngineBridgeHandler`):

1. **An Origin allow-list.** `ALLOWED_ORIGIN_RE` accepts `http://127.0.0.1`
   and `http://localhost`, with or without a port. When the request's
   `Origin` matches, it is reflected back as `Access-Control-Allow-Origin`
   (plus `Vary: Origin`, `Access-Control-Allow-Methods: GET, POST, OPTIONS`,
   `Access-Control-Allow-Headers: Content-Type, X-Engine-Control` and
   `Access-Control-Allow-Private-Network: true` for Chrome's local-network
   preflight). A request with **no** `Origin` (curl, the console's own
   same-origin calls) is allowed and simply gets no `Allow-Origin` line. Any
   other Origin gets **no CORS headers at all**, so the browser refuses to
   hand the response to that page — and `POST /start` or `/stop` from such an
   origin is additionally answered `403 {"success": false, "message": "origin
   not allowed"}`.
2. **A required custom header on the two POSTs.** `/start` and `/stop`
   return `403 {"success": false, "message": "missing X-Engine-Control: 1
   header"}` without `X-Engine-Control: 1`. A custom header turns the POST
   into a preflighted request, so a disallowed page never even gets to send
   it: its `OPTIONS` comes back without the allow headers and the browser
   stops there. 403 rather than 400 because the request is well-formed; the
   *caller* is what is not accepted.

`GET /status` and `GET /` stay readable from allowed origins (another local
tool on, say, `127.0.0.1:4310` can show the engine's state). A page served
from a remote host — the team workbench on its VM, for instance — can no
longer read `/status` cross-origin; it has to embed the engine's own console
(an iframe of `http://127.0.0.1:4320/`, which is same-origin to the engine) or
probe with an opaque `no-cors` fetch that only says "something answered".

## State

Three module globals: `stop_event` (a `threading.Event` owned by the current
worker), `engine_thread` (the current worker, if any) and `thread_lock`
(guards the start/stop transitions and the state read). `app_cycle_index`
remembers how many Tabs the next Cmd+Tab should press so successive switches
walk deeper into the app list instead of bouncing between the same two apps.

The state reported by `/status` is derived, not stored — `engine_state()`:
no thread or a dead thread is **IDLE**; a live thread whose Event is clear is
**RUNNING**; a live thread whose Event is set is **STOPPING**. `/status` also
returns `backend` (`quartz`, `pynput` or `fake`) and `platform`
(`sys.platform`), which the console shows in its header so you can tell at a
glance which input path is live.

Stop is cooperative: `/stop` sets the worker's Event. Every loop and inner
loop checks `stop.is_set()`, and the end-of-cycle pause is `stop.wait(...)`
rather than `time.sleep`, so the worker exits within the current step — a few
hundred milliseconds in the mouse move, up to one keystroke interval in the
burst, one Cmd+Tab sequence at worst. `Event.is_set()` is atomic, which is
why the worker can read it without the lock.

**Why an Event per worker and not a boolean.** With a shared `is_running`
flag, `/stop` cleared it while the worker could be inside its long sleep; a
`/start` in that window set it back to `True` and started a second thread,
and the old thread woke, saw `True` and carried on — two loops posting input
at once. Now each worker is handed the Event it was started with and nothing
ever clears an Event, so a stopped worker cannot be revived. `/start` while
the previous thread is still alive answers `409 {"success": false, "message":
"stopping, try again in a moment"}` and `/status` says `STOPPING`, which the
console renders as "Stopping…" with both buttons disabled.

**Shutdown.** Ctrl-C and SIGTERM (what `run.sh`'s trap sends) take the same
path: close the socket, set the Event, join the worker for up to 2 s, then
call `backend.release_modifiers()` regardless. Only the **first** signal starts
that — `_on_sigterm` sets `_shutting_down` and ignores every later one, because
`run.sh` traps INT and TERM both and a second `KeyboardInterrupt` landed inside
`release_modifiers()`, sailed past its `except Exception` (a `KeyboardInterrupt`
is not one) and skipped the release, leaving the modifier held for the rest of
the session — if the worker was killed in
the middle of an app switch, the synthetic modifier (Command on macOS,
Alt/Ctrl elsewhere) would otherwise stay held for the rest of the login
session. The thread is a daemon, so a worker that has not finished in 2 s never
blocks the exit. `shutdown_engine()` is a named function precisely so the tests
can call the same path the signal handler does.

## The loop (`loop_worker`)

Once per run: read the screen size, derive the reachable rectangle and the hop
size, and set the deadline.

Each iteration:

0. Poll the idle timer. If the person is working, stand down and poll again in
   a second — nothing below happens at all.
1. Draw a profile. `THINKING` waits 40–70 s (in poll-sized slices) and starts
   over without spending any budget.
2. Ask `governor.claim()`. A refusal means the window's budget is spent or the
   loop is ahead of pace: wait a second and ask again. This is the only place a
   cycle is refused outright, and it is asked before any input is generated so a
   refused cycle costs nothing.
3. Read the pointer, pick a target with `next_pointer_target()` and call
   `move_humanlike_adaptive()`.
4. The profile's keystroke burst: 78 % a random arrow key, 22 % a bare Shift.
5. One of three, by a dice roll: `simulate_real_app_switch()` (30 %),
   `hardware_browser_tab_switch()` (30 %), `simulate_vertical_scrolling()` (40 %).
6. 22 % chance of a left click where the pointer already is — **only when
   `ENGINE_ALLOW_CLICK=1`**.
7. The profile's end-of-cycle quiet, waited in poll-sized slices so the person
   and `/stop` are both noticed at once.

**Every single thing that reaches the operating system goes through `act()`**,
which asks `governor.claim()` first and calls `note_engine_input()` after. That
is what makes the ceiling hold: a burst that crosses a block boundary with no
budget left is cut short mid-burst rather than running past the limit.

### What each `simulate_*` / helper does

- **`move_humanlike_adaptive(start, end)`** — a cubic Bezier from start to end
  whose two inner control points sit at 25 % and 75 % of the path and are
  nudged by ±8–15 % of the distance. It is sampled 18–40 times (about one
  sample per 16 px) with a quintic smoothstep easing so the pointer starts and
  stops gently, calling `backend.move_mouse()` 5–10 ms apart.
- **`backend.tap_key(name)`** — key down, hold 12–25 ms, key up. Only the
  arrows and Shift are ever used here (virtual key codes 123–126 and 56 on
  macOS; `Key.left` … `Key.shift` under pynput).
- **`simulate_real_app_switch()`** — asks `backend.visible_app_count()` how
  many windows/apps are open, then `backend.app_switch(app_cycle_index)`, which
  holds the switcher modifier, presses Tab that many times and releases. The
  index then advances, wrapping when it reaches the app count.
- **`hardware_browser_tab_switch()`** — `backend.browser_tab_next()`:
  Cmd+Option+Right on macOS (which Safari and Chrome bind to "next tab"),
  Ctrl+Tab on Windows and Linux.
- **`simulate_vertical_scrolling()`** — 4–8 single-notch `backend.scroll()`
  calls in a random direction, 150–300 ms apart.
- **`backend.visible_app_count()`** — the AppleScript query on macOS with a 5 s
  timeout so the Automation permission dialog cannot hang the worker;
  `EnumWindows` + `IsWindowVisible` + `GetWindowTextLength` via ctypes on
  Windows (no extra dependency); `wmctrl -l` line count on Linux when wmctrl is
  installed. All three fall back to 5 on any failure.

None of the emulation numbers changed during the extraction or the
cross-platform work; each one has a `WHY` comment next to it in the source.

## The console page

`console/index.html` is one file with inline CSS and JavaScript so it needs no
build and can be served by `http.server` as-is. The engine resolves it
relative to its own file (`../console/index.html`) so it works from any
current directory. The page polls `/status` every 2 s, renders OFFLINE / IDLE /
RUNNING / Stopping…, enables Start only when IDLE and Stop only when RUNNING
(neither while stopping), sends `X-Engine-Control: 1` on its POSTs, and shows
the last error under the buttons.

It derives one more state the engine never reports in `status`: when
`inputWorking` is false it shows **INPUT UNAVAILABLE** with a red dot, a red
panel carrying the `warning` text, and Start disabled — showing IDLE there
would be a lie, since pressing Start only produces a 503. A `warning` with
`inputWorking` still true (the `ENGINE_ALLOW_WAYLAND` case) paints the same
panel amber and leaves the buttons alone. The warning is inserted with
`textContent`, never `innerHTML`, so the page cannot grow a way to inject
markup into itself. An engine from before this field existed sends no
`inputWorking` at all, and `undefined` is deliberately not treated as false.

`tools/workbench/index.html` embeds this console in an iframe and shows its own
one-line pill from the same `/status` poll; it reads `inputWorking` too, so the
pill does not say "idle" next to a console that says the machine cannot be
driven. It reads the engine address from
`location.origin` (normal case), or from `?engine=http://127.0.0.1:4321` if
opened from disk or pointed at a different port. The palette is copied from the
task-notif dashboard so the tools look related.

## The scripts

`install.sh` serves **macOS and Linux** and refuses anything else (pointing at
`install.ps1`). On macOS it checks that the Command Line Tools are present when
`python3` is only Apple's stub (and says to run `xcode-select --install`),
proves `import Quartz.CoreGraphics` works from the venv, and prints the
Accessibility steps.

On Linux it runs a **preflight before doing any work**: it checks `ensurepip`,
a C compiler, `Python.h` and `linux/input.h`, and prints a single install
command naming whatever is missing. All three are needed on a stock Ubuntu
desktop — Ubuntu splits `venv` out of `python3`, and pynput's `evdev`
dependency ships as source with **no wheels for any architecture** (x86_64
measured, not assumed), so everyone compiles it. Without the preflight a fresh
machine failed twice in a row with a different install line each time.

The command itself comes from **`packages.sh`**, a sourced, side-effect-free
bash file with three lookups: `pkg_manager` (which of apt, dnf, pacman or
zypper is on PATH), `pkg_name` (what an abstract requirement is called there —
`python3-dev` on Debian, `python3-devel` on Fedora and openSUSE, part of plain
`python` on Arch) and `pkg_install_command` (the whole sorted, deduplicated
line). `install.sh` asks for requirements, never for package names, in all five
places it used to hard-code apt. When no manager is recognised it prints the
requirements in plain words and continues rather than naming a package that
does not exist. Because the only input is "which binary is on PATH",
`engine/tests/test_packages.py` covers every branch by faking a distribution
with a temp directory of empty executables — no container needed.

It then warns when `wmctrl` is missing (optional), asks
`linux_session.check()` for the session verdict and prints the message and
remedy when it is negative (the same detector the engine uses, so the two can
never disagree), and checks pynput with `importlib.util.find_spec` rather than
importing it — importing pynput on Linux connects to the X server at once and
fails without `DISPLAY`, which says nothing about whether the install worked.
Both paths check `python3 >= 3.9` and reuse an existing `.venv`.

`run.sh` refuses without `.venv`, refuses if the port is already bound, starts
the engine in the background, polls `/status` for up to 10 s, opens the console
unless `--no-open` (`open` on macOS, `xdg-open` on Linux, silently skipped when
neither exists — a headless box has neither), and `wait`s on the engine so
Ctrl-C reaches both. A trap sends the engine SIGTERM on any exit, which the
engine handles exactly like Ctrl-C (see Shutdown above).

Both of its probes fall back to `.venv/bin/python`, because **neither `curl`
nor `lsof` is installed on a stock Ubuntu desktop**. Without the fallback the
readiness loop printed `curl: command not found` on every attempt and then
"the engine did not answer" — about an engine that was up and healthy. The
port probe sets `SO_REUSEADDR` to match `http.server`'s `allow_reuse_address`,
or a socket still in `TIME_WAIT` from an engine that just exited reads as
"port in use" for a minute after every stop.

`install.ps1` and `run.ps1` are the Windows equivalents, written for Windows
PowerShell 5.1 with no external modules. `install.ps1` finds Python through
`py -3` first (the launcher is immune to the Microsoft Store's `python` alias,
which only opens the Store) then plain `python`, requires 3.9+, creates `.venv`
and pip-installs into it. `run.ps1` refuses without `.venv`, refuses a bound
port using `netstat -ano` (printing the `netstat -ano | findstr :4320` hint),
activates the venv, starts `engine\engine.py`, polls `/status` with a 1 s
timeout up to 10 times, opens the browser with `Start-Process` unless
`-NoOpen`, and keeps the engine in the foreground so Ctrl-C stops it. Windows
may need `Set-ExecutionPolicy -Scope Process Bypass` once per window before an
unsigned `.ps1` will run.

`requirements.txt` carries both libraries behind pip environment markers
(`pyobjc-framework-Quartz ... ; sys_platform == "darwin"` and
`pynput ... ; sys_platform != "darwin"`), so one file serves every platform and
pip installs exactly one of them.

## Why the old Express bridge is gone

`tools/task-notif/src/server/bridge-controller.ts` spawned `python3
engine/mac_engine.py` (now `engine/engine.py`) and proxied three routes. That only ever worked when the
Node server itself ran on a Mac; on the Linux host task-notif deploys to it
could do nothing. Serving the console from the engine removes the proxy, the
`axios` dependency and the Vite `/automation` proxy entry in one go, and the
CORS allow-list covers the case where another dashboard *served from this
Mac* wants to control the engine.
