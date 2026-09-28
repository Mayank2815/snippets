# Architecture

One process, two threads, one HTML file.

```
 browser (console/index.html, or another page served from this Mac)
      │  GET /status every 2 s, POST /start, POST /stop
      │  (CORS: Origin reflected only for http://127.0.0.1 / http://localhost;
      │   POSTs must carry X-Engine-Control: 1)
      ▼
 ┌──────────────────────────── mac_engine.py ────────────────────────────┐
 │  main thread: HTTPServer on 127.0.0.1:4320  (EngineBridgeHandler)     │
 │      GET  /          -> reads ../console/index.html, serves it        │
 │      GET  /status    -> {"status": "IDLE" | "RUNNING" | "STOPPING"}   │
 │      POST /start     -> new stop Event + loop_worker thread, or 409   │
 │                         while the previous worker is still alive      │
 │      POST /stop      -> sets the worker's Event; loop exits after     │
 │                         its current step                              │
 │      OPTIONS *       -> 200 + CORS headers (preflight)                │
 │      SIGTERM / Ctrl-C-> stop, join worker 2 s, Command key-up, exit   │
 │                                                                       │
 │  engine thread (daemon): loop_worker(stop) while not stop.is_set()    │
 │      Quartz CGEventPost -> kCGHIDEventTap -> macOS input              │
 └───────────────────────────────────────────────────────────────────────┘
```

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
**RUNNING**; a live thread whose Event is set is **STOPPING**.

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
post a Command key-up regardless — if the worker was killed in the middle of
a Cmd+Tab, the synthetic Command modifier would otherwise stay held for the
rest of the login session. The thread is a daemon, so a worker that has not
finished in 2 s never blocks the exit.

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
  stops gently, posting `kCGEventMouseMoved` 5–10 ms apart.
- **`post_dense_keystroke(code)`** — key down, hold 12–25 ms, key up, through
  `CGEventCreateKeyboardEvent`. Only virtual key codes 123–126 (arrows) and 56
  (Shift) are ever used here.
- **`simulate_real_app_switch()`** — asks System Events (via `osascript`) how
  many non-background apps are open, holds Command (key 55), presses Tab (48)
  `app_cycle_index` times with the Command flag (`0x100000`) on each event,
  waits 300 ms and releases Command. The index then advances, wrapping when it
  reaches the app count.
- **`hardware_browser_tab_switch()`** — one Right-arrow press carrying the
  Command|Option flags (`0x180000`), which Safari and Chrome bind to "next tab".
- **`simulate_vertical_scrolling()`** — 4–8 `CGEventCreateScrollWheelEvent`
  line-unit events in a random direction, 150–300 ms apart.
- **`get_total_visible_apps_count()`** — the AppleScript query above, with a
  5 s timeout so the Automation permission dialog cannot hang the worker;
  falls back to 5 when the query fails (no permission, timeout, odd output).

None of the emulation numbers changed during the extraction; each one now has
a `WHY` comment next to it in the source.

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

`install.sh` refuses on anything but Darwin, checks that the Command Line
Tools are present when `python3` is only Apple's stub (and says to run
`xcode-select --install`), checks `python3 >= 3.9`, creates `.venv`, installs
`requirements.txt`, proves `import Quartz.CoreGraphics` works from the venv,
and prints the Accessibility steps. `run.sh` refuses without `.venv`, refuses
if the port is already bound (with the `lsof` line to find the culprit),
starts the engine in the background, polls `/status` for up to 10 s, `open`s
the console unless `--no-open`, and `wait`s on the engine so Ctrl-C reaches
both. A trap sends the engine SIGTERM on any exit, which the engine handles
exactly like Ctrl-C (see Shutdown above).

## Why the old Express bridge is gone

`tools/task-notif/src/server/bridge-controller.ts` spawned `python3
engine/mac_engine.py` and proxied three routes. That only ever worked when the
Node server itself ran on a Mac; on the Linux host task-notif deploys to it
could do nothing. Serving the console from the engine removes the proxy, the
`axios` dependency and the Vite `/automation` proxy entry in one go, and the
CORS allow-list covers the case where another dashboard *served from this
Mac* wants to control the engine.
