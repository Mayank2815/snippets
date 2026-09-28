# Lessons — gotchas found in the code and during the extraction

Dated, newest first. Add to this file whenever the engine bites you.

## 2026-09-28 — review findings: the double-loop race and the CORS hole

- **Stop-then-Start could run two loops at once.** `/stop` only cleared a
  shared `is_running` boolean; the worker checked it between events, but its
  end-of-cycle `time.sleep(9.5–12.5)` did not. Press Stop, then Start again
  inside that window: `/start` saw `is_running == False`, set it back to
  `True` and spawned a second thread, and the *old* thread woke from its
  sleep, saw `True` and kept going. Reproduced with a Quartz-stubbed copy of
  the engine: events arrived from two thread ids. The fix is a
  `threading.Event` handed to each worker at start; `/stop` sets that
  worker's Event and nothing ever clears one, so a stopped worker cannot be
  revived. While the old thread is still alive `/status` says `STOPPING`,
  the console shows "Stopping…" with both buttons off, and `/start` answers
  `409 {"success": false, "message": "stopping, try again in a moment"}`. The
  long pause is now `stop.wait(...)`, so Stop also takes effect at once
  instead of up to 12.5 s later.

- **`Access-Control-Allow-Origin: *` was not made safe by binding to
  loopback.** The user's own browser is on loopback too, so any web page they
  visited could `POST /start` from JavaScript — a bare POST is a "simple
  request" that needs no preflight — and drive their mouse. Now the Origin
  is reflected only when it is `http://127.0.0.1[:port]` or
  `http://localhost[:port]` (no Origin, i.e. curl, is fine too); every other
  Origin gets no CORS headers, and `POST /start` / `/stop` also require
  `X-Engine-Control: 1` (403 without it), which forces a preflight the
  disallowed page fails. The console sends the header itself. Consequence: a
  page served from a remote host (the team workbench on its VM) can no longer
  read `/status` cross-origin — it must iframe the engine's own console or use
  an opaque `no-cors` probe.

- **Smaller things fixed in the same pass.** `osascript` now has
  `timeout=5` (the Automation permission dialog used to be able to hang the
  worker so `/stop` never took effect); SIGTERM from `run.sh`'s trap is handled
  like Ctrl-C (stop, join 2 s, Command key-up so a synthetic modifier is never
  left held, "Shutdown complete."); `POST /start?x=1` no longer 404s; stdout is
  line-buffered so a redirected log fills in live; `make bundle` uses the
  folder's real name so a renamed copy still bundles; `install.sh` checks for
  the Command Line Tools before trusting `python3` (Apple's stub used to have
  its "xcode-select: note" printed as if it were a version).

## 2026-09-28 — the extraction from task-notif

- **The source file was two copies of itself.** `mac_engine.py` in task-notif
  was ~245 lines of a commented-out "V19.0" copy followed by the live "V20.0"
  code. The only real differences were the end-of-cycle sleep (11–14 s in
  V19, 9.5–12.5 s in V20) and the banner. Only the live code was kept; if you
  ever need V19's timing, it is in git history at commit `11fe7e4`.

- **The `sys.path` bootstrap exists because of how it used to be launched.**
  The old Express bridge ran `spawn('python3', [script])`, i.e. the bare
  system interpreter, on a machine where pyobjc had been installed with
  `pip install --user` into `~/Library/Python/3.9/lib/python/site-packages`.
  The script therefore force-inserted that path (and the Command Line Tools
  site-packages) at the front of `sys.path`. Inside the `.venv` that
  `install.sh` creates this is unnecessary and slightly dangerous — an old
  user-site pyobjc would have shadowed the venv's. The bootstrap is now a
  fallback that only runs if `Quartz` fails to import, and it appends rather
  than prepends. Running via `./run.sh` never triggers it.

- **A venv disables user site-packages.** `python3 -m venv` writes
  `include-system-site-packages = false`, so `~/Library/Python/3.9/...` is
  invisible inside `.venv`. That is why `install.sh` must install
  `pyobjc-framework-Quartz` into the venv even on a Mac that already has
  pyobjc for the system Python.

- **`pip install pyobjc-framework-Quartz` is all you need.** It depends on
  `pyobjc-core` and `pyobjc-framework-Cocoa` and pulls them in. The full
  `pyobjc` meta-package is 100+ frameworks and takes minutes; don't.

- **The old handler returned nothing for unknown GET paths.** `do_GET` only
  handled `/status`; any other path closed the socket without a response, so
  `curl http://127.0.0.1:4320/` just printed "Empty reply". `/` now serves the
  console and everything else returns a JSON 404. `POST` to an unknown path
  used to answer `200 {"success": true}`; it now returns 404 too.

- **`test_metrics.py` does not model the current engine.** Its comments were
  in Hindi (now English) and its numbers — 2.0–2.5 s of action then 14–24 s
  of sleep — describe an older "V7" cadence. The live engine does roughly
  3–5 s of keystrokes plus a switch/scroll, then sleeps 9.5–12.5 s. The
  simulator logic was left untouched on purpose (it is a historical
  calibration tool); update both if you retune the engine.

- **A stale engine can sit on port 4320 for days.** The old bridge spawned the
  engine `detached` with `unref()`, so it outlived the Node server that
  started it. During the extraction one such orphan (started from a path that
  no longer existed) was still bound to 4320. `run.sh` now refuses to start
  when the port is taken and prints the `lsof` line to find the culprit.

- **Removing the panel from task-notif touches lines next to KeepAlivePanel.**
  In `dashboard/src/App.tsx` the AutomationPanel import (line 3) and block
  (lines 226–229) sit right beside the KeepAlivePanel import (line 4) and
  element (line 224), which a sibling branch removes. Whichever PR lands
  second needs a trivial rebase.

## Behaviour of the engine itself

- **Accessibility permission is per launching app, not per script.** macOS
  grants "may control this computer" to the process that owns the TTY —
  Terminal, iTerm, VS Code — and only to processes started *after* the toggle.
  Symptom of a missing grant: `/status` says RUNNING, the terminal prints the
  "[Scroll Active]" / "[System Shift]" lines, and nothing on screen happens,
  with no error anywhere. Quit and reopen the terminal after toggling.

- **`osascript` may prompt the first time.** `get_total_visible_apps_count()`
  asks System Events for the app count, which needs the Automation permission
  for your terminal. macOS shows a one-time dialog; decline it and the count
  silently falls back to 5 (so Cmd+Tab still cycles, just not adaptively).

- **Cmd+Tab needs real hold times.** The 80 ms after Command-down, 180 ms
  between Tabs and 300 ms before Command-up are not decoration: shorter values
  make the app switcher collapse the presses into one or never appear.

- **Modifier flags must be set on both the down and the up event.** A Tab-up
  without the Command flag is interpreted as a plain Tab release; the switcher
  then does not activate the highlighted app.

- **Flag values are Quartz masks, not key codes.** `1048576` is
  `kCGEventFlagMaskCommand` (0x100000); `1572864` is Command|Alternate
  (0x180000). Key codes are the separate `kVK_*` table (55 = Command, 56 =
  Shift, 48 = Tab, 123–126 = arrows).

- **The target rectangle assumes a 1280×800-point display.** The pointer is
  clamped to x 200–1100, y 200–650, which on a larger external display keeps
  it in the top-left region. Widen the clamp if that matters.

- **Stop is not instant, but it is quick.** `/stop` sets the worker's Event;
  the worker checks it between events and the end-of-cycle pause waits on it,
  so the loop ends within the current step — at worst one Cmd+Tab sequence
  (about 0.6 s). Until the thread has really ended `/status` says `STOPPING`
  and `/start` answers 409. Ctrl-C / SIGTERM on the server waits up to 2 s for
  the worker, then exits regardless because the worker is a daemon thread.

- **The server is single-threaded.** Fine for a 2 s poll, but do not put slow
  work in a handler — it would block `/stop`.
