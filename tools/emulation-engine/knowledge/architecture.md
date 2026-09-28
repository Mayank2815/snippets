# Architecture

One process, two threads, one HTML file.

```
 browser (console/index.html, or the team workbench on a VM)
      │  GET /status every 2 s, POST /start, POST /stop   (CORS: *)
      ▼
 ┌──────────────────────────── mac_engine.py ────────────────────────────┐
 │  main thread: HTTPServer on 127.0.0.1:4320  (EngineBridgeHandler)     │
 │      GET  /          -> reads ../console/index.html, serves it        │
 │      GET  /status    -> {"status": "IDLE" | "RUNNING"}                │
 │      POST /start     -> sets is_running, starts loop_worker thread    │
 │      POST /stop      -> clears is_running; loop exits after its step  │
 │      OPTIONS *       -> 200 + CORS headers (preflight)                │
 │                                                                       │
 │  engine thread (daemon): loop_worker() while is_running               │
 │      Quartz CGEventPost -> kCGHIDEventTap -> macOS input              │
 └───────────────────────────────────────────────────────────────────────┘
```

## The HTTP server

`http.server.HTTPServer` with a `BaseHTTPRequestHandler` subclass. It is
single-threaded, which is fine: every request is answered in microseconds and
the slow work happens on the engine thread. `log_message` is silenced so the
2-second status poll does not spam the terminal.

`BIND_HOST` is hard-wired to `127.0.0.1`. The engine drives *this* machine's
input, so it must never be reachable from the network; that is also why
`Access-Control-Allow-Origin: *` is acceptable — the only thing that can
connect is a browser on the same Mac. `PORT` can be overridden through the
`PORT` environment variable (run.sh passes it through) for the rare case
4320 is taken; the bind address cannot.

CORS headers go on every response, including the 404s and the console page.
The reason is the team workbench: that page is served from a VM, but the
engine it controls runs on the viewer's own Mac, so the browser sees a
cross-origin request from `https://<vm>` to `http://127.0.0.1:4320` and blocks
it unless the engine says it is allowed. `OPTIONS` is handled for the
preflight the browser sends before a `POST`.

## State

Three module globals: `is_running` (the loop's on/off switch), `engine_thread`
(the current worker, if any) and `thread_lock` (guards the start/stop
transitions so two quick clicks cannot start two loops). `app_cycle_index`
remembers how many Tabs the next Cmd+Tab should press so successive switches
walk deeper into the app list instead of bouncing between the same two apps.

Stop is cooperative: `/stop` only clears the flag. Every loop and inner loop
checks `is_running`, so the worker exits within one step — at worst the
9.5–12.5 s pause at the end of a cycle. The thread is a daemon, so Ctrl-C on
the server never waits for it.

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
5. Sleep 9.5–12.5 s.

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
- **`get_total_visible_apps_count()`** — the AppleScript query above; falls
  back to 5 when the query fails (no Automation permission, odd output).

None of the emulation numbers changed during the extraction; each one now has
a `WHY` comment next to it in the source.

## The console page

`console/index.html` is one file with inline CSS and JavaScript so it needs no
build and can be served by `http.server` as-is. The engine resolves it
relative to its own file (`../console/index.html`) so it works from any
current directory. The page polls `/status` every 2 s, renders OFFLINE / IDLE /
RUNNING, enables Start only when IDLE and Stop only when RUNNING, and shows the
last error under the buttons. It reads the engine address from
`location.origin` (normal case), or from `?engine=http://127.0.0.1:4321` if
opened from disk or pointed at a different port. The palette is copied from the
task-notif dashboard so the tools look related.

## The scripts

`install.sh` refuses on anything but Darwin, checks `python3 >= 3.9`, creates
`.venv`, installs `requirements.txt`, proves `import Quartz.CoreGraphics`
works from the venv, and prints the Accessibility steps. `run.sh` refuses
without `.venv`, refuses if the port is already bound (with the `lsof` line to
find the culprit), starts the engine in the background, polls `/status` for up
to 10 s, `open`s the console unless `--no-open`, and `wait`s on the engine so
Ctrl-C reaches both. A trap kills the engine on any exit.

## Why the old Express bridge is gone

`tools/task-notif/src/server/bridge-controller.ts` spawned `python3
engine/mac_engine.py` and proxied three routes. That only ever worked when the
Node server itself ran on a Mac; on the Linux host task-notif deploys to it
could do nothing. Serving the console from the engine removes the proxy, the
`axios` dependency and the Vite `/automation` proxy entry in one go, and the
CORS headers cover the case where another dashboard wants to control the
engine.
