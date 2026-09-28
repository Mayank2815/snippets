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
 |                          "backend": "quartz", "platform": "darwin"}  |
 |      POST /start     -> new stop Event + loop_worker thread, or 409  |
 |                         while the previous worker is still alive     |
 |      POST /stop      -> sets the worker's Event; loop exits after    |
 |                         its current step                             |
 |      OPTIONS *       -> 200 + CORS headers (preflight)               |
 |      SIGTERM/Ctrl-C  -> stop, join worker 2 s, release modifiers     |
 |                                                                      |
 |  engine thread (daemon): loop_worker(stop) while not stop.is_set()   |
 |      every OS call goes through the backend object ------------+     |
 +----------------------------------------------------------------|-----+
                                                                  v
 +--------------------- engine/backends/ (get_backend()) ---------------+
 |  base.py    the contract: mouse_position, screen_size, move_mouse,   |
 |             click, scroll, tap_key, app_switch, browser_tab_next,    |
 |             visible_app_count, release_modifiers, name, platform_note|
 |  quartz.py  darwin -> CGEventPost -> kCGHIDEventTap -> macOS input   |
 |  pynput_backend.py  win32 -> SendInput | linux -> X11/XTest          |
 |  fake.py    records every call, generates nothing (tests, CI)        |
 +----------------------------------------------------------------------+
```

## The backend layer

`engine.py` holds the loop — the cadence, the probabilities, the target
rectangle, every timing constant — and never talks to the operating system
directly. Everything platform-specific sits behind one object obtained once at
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
call `backend.release_modifiers()` regardless — if the worker was killed in
the middle of an app switch, the synthetic modifier (Command on macOS,
Alt/Ctrl elsewhere) would otherwise stay held for the rest of the login
session. The thread is a daemon, so a worker that has not finished in 2 s never
blocks the exit. `shutdown_engine()` is a named function precisely so the tests
can call the same path the signal handler does.

## The loop (`loop_worker`)

Each cycle:

1. `get_mouse_pos()` reads the pointer, picks a target within ±300 px clamped
   to a central rectangle (x 200–1100, y 200–650) and calls
   `move_humanlike_adaptive()`.
2. 16–20 `post_dense_keystroke()` calls, 120–280 ms apart: 78 % a random
   arrow key, 22 % a bare Shift.
3. One of three, by a dice roll: `simulate_real_app_switch()` (30 %),
   `hardware_browser_tab_switch()` (30 %), `simulate_vertical_scrolling()` (40 %).
4. 22 % chance of a left click where the pointer already is.
5. Wait 9.5–12.5 s on the stop Event (returns early when `/stop` sets it).

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
the last error under the buttons. It reads the engine address from
`location.origin` (normal case), or from `?engine=http://127.0.0.1:4321` if
opened from disk or pointed at a different port. The palette is copied from the
task-notif dashboard so the tools look related.

## The scripts

`install.sh` serves **macOS and Linux** and refuses anything else (pointing at
`install.ps1`). On macOS it checks that the Command Line Tools are present when
`python3` is only Apple's stub (and says to run `xcode-select --install`),
proves `import Quartz.CoreGraphics` works from the venv, and prints the
Accessibility steps. On Linux it prints `sudo apt install python3-venv` when
`python3 -m venv` fails, `sudo apt install build-essential python3-dev` when
pip cannot build pynput's `evdev` dependency, warns when `wmctrl` is missing
(optional) and when `XDG_SESSION_TYPE` is `wayland`, and checks pynput with
`importlib.util.find_spec` rather than importing it — importing pynput on Linux
connects to the X server at once and fails without `DISPLAY`, which says
nothing about whether the install worked. Both paths check `python3 >= 3.9` and
reuse an existing `.venv`.

`run.sh` refuses without `.venv`, refuses if the port is already bound (with
the `lsof` line to find the culprit), starts the engine in the background,
polls `/status` for up to 10 s, opens the console unless `--no-open` (`open` on
macOS, `xdg-open` on Linux, silently skipped when neither exists — a headless
box has neither), and `wait`s on the engine so Ctrl-C reaches both. A trap
sends the engine SIGTERM on any exit, which the engine handles exactly like
Ctrl-C (see Shutdown above).

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
