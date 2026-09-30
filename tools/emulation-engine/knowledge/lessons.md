# Lessons — gotchas found in the code and during the extraction

Dated, newest first. Add to this file whenever the engine bites you.

## 2026-09-29 — one apt line for every distribution, and x86_64 at last

Everything below this entry was proven on **aarch64 only** (Colima on Apple
Silicon) and on **Debian/Ubuntu only**. A colleague is about to run this on an
unknown Linux box that is almost certainly x86_64 and may not be Ubuntu, so both
gaps were closed by measurement.

### x86_64 changes nothing — including the part we hoped it would

Ubuntu 24.04.5 under `--platform linux/amd64` (qemu), non-root user, Xvfb,
Openbox, two xterms, the documented `./install.sh` then `./run.sh --no-open`
path. `uname -m` = `x86_64`, Python 3.12.3. Result: **identical to aarch64 in
every respect** — same preflight output, same three apt packages, same
`{"status": "IDLE", "backend": "pynput", "platform": "linux",
"inputWorking": true}`, and after `POST /start` the pointer went from
`x:640 y:400` to `x:359 y:276` with 40 RawMotion, 43 RawKeyPress and 2
RawButton from `xinput test-xi2 --root`. Nothing about the backend, the timings
or the install is architecture-specific, and nothing had to change.

**`evdev` has no x86_64 wheel either.** This was the one thing that might have
made the build-tools preflight unnecessary on the common architecture, so it was
tested directly rather than assumed:

```
$ .venv/bin/pip download --no-deps --only-binary=:all: -d /tmp/wheelprobe evdev
ERROR: Could not find a version that satisfies the requirement evdev (from versions: none)
ERROR: No matching distribution found for evdev
```

`from versions: none` under `--only-binary` means PyPI publishes an sdist and
nothing else, for every platform. Arch on x86_64 independently confirmed it by
failing the same evdev compile for a missing C compiler. So the compiler
preflight is needed on every Linux machine, on every architecture — as the
2026-09-28 entry claimed, now actually checked on x86_64.

### The apt line was not merely unhelpful elsewhere — it was wrong

On a fresh Fedora 44 container with python3 but no toolchain, `./install.sh`
detected the missing pieces correctly and then said:

```
    build-essential — a C compiler, to build pynput's evdev dependency
    python3-dev     — Python.h, which evdev needs to build
install.sh: install the equivalents for your distribution, then re-run if the install below fails.
...
install.sh: compiler/header (gcc, Python.h, linux/input.h), install the build tools with:
    sudo apt install build-essential python3-dev
```

Neither package name exists on Fedora, and the command names a package manager
the machine does not have. It then carried on to the evdev compile, which failed
with a 53-line C-extension traceback. openSUSE Tumbleweed printed the same two
wrong names; Arch printed `build-essential` alone (its unsplit `python` package
already supplies `Python.h`, so only the compiler check fired). In every case the
user is left reading a compiler error with an instruction that cannot work.

**The fix is `packages.sh`** — a sourced, side-effect-free bash file with three
lookups: which manager is on PATH, what each abstract requirement is called
there, and how to phrase the whole command. `install.sh` now asks for `venv`,
`compiler`, `kernel_headers` and `python_headers` rather than for Debian package
names, in all five places it used to hard-code apt (the preflight, the
`python3`-missing message, the venv-creation failure, the pip failure and the
wmctrl note).

Measured afterwards, same containers:

| Distribution | First run said | Now says |
|---|---|---|
| Ubuntu 24.04 x86_64, Debian 12 | `sudo apt install build-essential python3-dev python3-venv` | **byte-identical** |
| Fedora 44 | `sudo apt install build-essential python3-dev` | `sudo dnf install gcc python3-devel` |
| Arch (x86_64) | `sudo apt install build-essential` | `sudo pacman -S base-devel` |
| openSUSE Tumbleweed | `sudo apt install build-essential python3-dev` | `sudo zypper install gcc python3-devel` |

Following the new instruction and re-running got Fedora, Arch, openSUSE and
Debian 12 to a working engine that moved the pointer — 38/37, 38/35, 38/40 and
39/43 RawMotion/RawKeyPress respectively, all with `POINTER: MOVED`.

### Things that only show up once you leave Debian

- **The package names differ more than the commands do.** The verb is the easy
  part; `python3-dev` vs `python3-devel` vs `python` is what actually breaks
  people. Arch folds *three* of our four requirements into one `python` package,
  so `pkg_install_command` has to sort and deduplicate or it prints
  `sudo pacman -S base-devel python python` — which is why that dedup has a test
  of its own.
- **Only Debian and Ubuntu split out `venv`.** Fedora ships ensurepip inside
  `python3-libs`, Arch and openSUSE inside their Python package. So the Fedora
  and openSUSE lines are two packages, not three, and the missing-venv branch
  never fires there at all. A lookup table that insisted on naming a venv package
  everywhere would have printed something that does not exist.
- **Detect by "is the binary on PATH", not by `/etc/os-release`.** Mint, Pop!_OS,
  Zorin, Nobara, EndeavourOS and Manjaro are all derivatives that keep their
  parent's package manager, and there is no end to that list. Which is also why
  Mint and Pop!_OS were not tested separately here: they are Ubuntu with a
  different theme, and `apt` is `apt`.
- **apt is checked first on purpose.** A machine with both (someone installed dnf
  on Ubuntu) must keep taking the Debian branch, whose wording is the one that
  was already proven. There is a test for exactly that.
- **Keep the Debian output byte-identical, and prove it.** The old and new
  preflight output were diffed on Ubuntu 24.04 x86_64 and Debian 12 aarch64:
  zero difference. The exact string
  `sudo apt install build-essential python3-dev python3-venv` is quoted in the
  README and in this file, so a test pins it.
- **`readarray` is bash 4.** The original dedup used it, which was fine because
  it only ran on Linux — but the new tests source `packages.sh` on whatever
  machine runs `make test`, and macOS still ships bash 3.2. A `while read` loop
  does the same job everywhere.

### The tests fake a distro with a directory

`engine/tests/test_packages.py` builds a temp directory holding empty executable
files named `dnf` or `pacman`, sets `PATH` to **only** that directory, and
sources `packages.sh`. That is a whole Fedora or Arch machine as far as the
lookup is concerned, so all 35 new tests run in milliseconds on a Mac with no
container, no root and no distro. Two details make it work: `PATH` is replaced
rather than prepended, so a real `apt` on the test host cannot leak into an
"this is Arch" case; and `sort` — the one external command the lookup uses — is
symlinked into the fake bin, while `bash` itself has to be launched by absolute
path because the replaced `PATH` cannot find it.

### Container quirks that are not product bugs

- **Arch's pacman fails under qemu** with `error restricting syscalls via
  seccomp: 22` — its sandbox cannot initialise in x86_64 emulation on an arm64
  host. `--disable-sandbox` works around it. That flag is a property of the test
  rig, not of the instruction we print; on real Arch hardware
  `sudo pacman -S base-devel` is the whole command.
- **Arch publishes no official arm64 image**, so Arch was necessarily tested
  under `--platform linux/amd64` — which incidentally gave a second x86_64
  data point for free.
- The 2026-09-28 note still holds everywhere: `xvfb-run` hangs, start
  `Xvfb :99` by hand; and `xev -root` sees zero keypresses for a healthy engine,
  so count with `xinput test-xi2 --root`.

### Still unproven

**A real GNOME or KDE desktop session on Xorg.** Every run above used Openbox
inside a container, which is a genuine window manager but not a full desktop:
no GNOME Shell, no KWin, no session manager, no compositing, no
`org.gnome.Settings` keyboard grabs. A desktop environment can take exclusive
grabs and intercept the Alt+Tab and Ctrl+Tab chords before the focused window
sees them, so the switcher steps in particular are the ones most likely to
behave differently there. That cannot be reproduced in a container and was not
attempted. Also unproven, unchanged from before: Windows on real hardware, and
the macOS input path (`/start` is still never sent to a Quartz engine here).

## 2026-09-29 — the Wayland silent failure, and what a real Linux desktop showed

The 2026-09-28 Linux support below was only ever proven inside a bare
`python:3.12-slim` container: root, `Xvfb` started by hand, no window manager,
no desktop packages. That is not a machine anyone uses. Re-running the
documented path as a **non-root user on Ubuntu 22.04 and 24.04, with Openbox
and real windows**, found the following.

### The silent failure was worse than recorded — and a warning nobody reads is not a fix

The previous entry says the Wayland case is handled because `install.sh` and
the backend constructor print a warning. Measured, that was not enough in three
distinct ways:

1. **The warning does not reach the person.** `run.sh` opens the console in a
   browser, so the terminal is behind it. `GET /status` returned
   `{"status": "RUNNING", "backend": "pynput", "platform": "linux"}` — with
   nothing about Wayland in it — so the console had no way to know, and showed
   a green dot and "the pointer and keys are being driven". Measured directly.
2. **One of the two Wayland signals was not checked at all.** The check was
   `XDG_SESSION_TYPE == "wayland"`. A session that sets `WAYLAND_DISPLAY` and
   leaves `XDG_SESSION_TYPE` unset or `tty` — sway and Hyprland launched from a
   text console do exactly this — printed **no warning anywhere**.
3. **With no `DISPLAY` at all, the error actively misled.** pynput's import
   failed with an X-connection error, which the engine reported as
   "pynput is not installed for this interpreter. Run ./install.sh" — sending
   the user to reinstall a package that was already installed.

**What was done.** The session check moved into `backends/linux_session.py`, a
pure function of the environment (so every branch is unit-tested with no X
server anywhere), and it now treats *either* Wayland signal as decisive. When
it says no, `get_backend()` returns an `UnavailableBackend` instead of the real
one: the server still starts, so the console — the thing the user is actually
looking at — can explain it. `/status` gained `inputWorking` and `warning`,
`POST /start` answers **503** with the explanation, and the console paints a red
"INPUT UNAVAILABLE" panel with Start disabled. A false positive here is a
visible, overridable annoyance; the opposite mistake is invisible, which is why
the check errs towards refusing.

**`ENGINE_ALLOW_WAYLAND=1`** exists for someone who genuinely only drives old
XWayland apps. It downgrades the refusal to an amber caveat that `/status` and
the console still show — it never downgrades it to silence.

### Why not ydotool

`ydotool` injects through `/dev/uinput`, below the compositor, so it does work
on Wayland. It was rejected, for reasons that are unlikely to change:

- **It cannot read the pointer position.** Wayland exposes no way to ask where
  the cursor is, and `backend.mouse_position()` is called at the top of every
  cycle to start the Bezier curve from where the pointer actually is. We would
  have to track it internally and would be wrong the moment the user touched
  their own mouse.
- **It needs a root daemon.** `ydotoold` plus a udev rule plus group
  membership — a much bigger ask than picking "Ubuntu on Xorg" once at the
  login screen, which takes thirty seconds and makes the existing, tested path
  work properly.
- **Its own failure mode is silent.** A daemon that is not running, or a socket
  the user cannot write to, produces exactly the "reports fine, moves nothing"
  bug this whole entry is about. Adding it would add a second silent-failure
  surface in exchange for a worse result.

So the answer on Wayland is a clear refusal plus precise instructions. The
README spells out the login-screen steps and is honest that Ubuntu 25.10+ and
recent Fedora GNOME have dropped the Xorg session entirely, where the only
options are another machine or a lighter X11 desktop.

### A real desktop broke three things the slim container could not

- **`run.sh` hard-failed without `curl`.** Ubuntu Desktop does not ship it. The
  readiness loop printed `curl: command not found` twenty-seven times and then
  `the engine did not answer on http://127.0.0.1:4320/status within 10 s` —
  while the engine was up and perfectly healthy. An error message that names
  the wrong thing is worse than a crash. `lsof` is missing too, which silently
  disabled the port-in-use check. Both now fall back to `.venv/bin/python`,
  the one interpreter guaranteed to exist by that point.
- **The port probe must set `SO_REUSEADDR`.** The first version of that
  fallback reported "port already in use" for about a minute after every stop,
  with nothing listening — a socket in `TIME_WAIT`. `http.server` sets
  `allow_reuse_address`, so the probe has to as well, or it answers a different
  question from the one that matters.
- **A second SIGTERM printed a traceback instead of releasing the modifier.**
  `run.sh` traps both INT and TERM, so a Ctrl-C there can deliver two. The
  second arrived while `shutdown_engine()` was inside
  `backend.release_modifiers()`, and `KeyboardInterrupt` is not an `Exception`,
  so it sailed past that method's `except Exception` and skipped the release —
  leaving Alt held for the rest of the session, which taints every later click
  and keystroke. `_on_sigterm` now ignores every signal after the first.

### Installing needs all three apt packages, always

`evdev` — pynput's Linux dependency — publishes **an sdist and no wheels for
any architecture**, so every Linux user compiles it, not just unusual ones.
Combined with Ubuntu splitting out `python3-venv`, a stock desktop failed
`./install.sh` twice in a row with a different apt line each time. `install.sh`
now checks `ensurepip`, a C compiler, `Python.h` and `linux/input.h` up front
and prints one line:
`sudo apt install build-essential python3-dev python3-venv`. It only insists
on Debian/Ubuntu, where those names are right; elsewhere it names what is
missing and carries on.

### How it was proven

Ubuntu 22.04 and 24.04 containers with a non-root sudo user, Openbox, two
xterms and an xclock. Input was verified with tools that are not our code:
`xdotool getmouselocation` sampled over time for the pointer, and
`xinput test-xi2 --root` — which sees raw device events regardless of which
window has focus — for keystrokes. That last detail matters: `xev -root` reports
**zero** KeyPress events for a working engine, because XTest keys go to the
focused window and `xev -root` only sees them when focus is on the root window.
That looks exactly like a total failure and is not one.

Measured on 24.04: 18 RawKeyPress and 19 RawMotion in one burst;
`app_switch(2)` produced `Alt_L↓ Tab↓↑ Tab↓↑ Alt_L↑` and `browser_tab_next()`
produced `Control_L↓ Tab↓↑ Control_L↑`, read back as keysyms from the server's
own keymap.

## 2026-09-28 — running on Windows and Linux: the backend layer

- **Why a backend layer and not a second script.** The obvious move — copy
  `mac_engine.py` to `win_engine.py` — would have duplicated the HTTP server,
  the CORS allow-list, the STOPPING/409 state machine and every calibrated
  timing constant, and the two copies would have drifted on the first bug fix.
  Instead `engine.py` keeps all of that exactly as it was and calls ten methods
  on a backend object; `engine/backends/` holds one small file per platform.
  The split line is "does this touch the operating system?" — cadence,
  probabilities and the target rectangle stayed in the loop, key codes and
  event posting moved out. The loop now names keys (`'left'`, `'shift'`) and
  each backend maps them to its own codes, which is what let the macOS key-code
  table leave `engine.py` without any behaviour change.

- **Wayland silently eats synthetic input.** pynput posts through the X11 XTest
  extension. On a Wayland session it connects to XWayland and every call
  *succeeds* — no error, no exception — but the compositor never delivers those
  events to native Wayland windows. The symptom is identical to the macOS
  missing-Accessibility case: `/status` says RUNNING, the log prints its
  "[Scroll Active]" lines, nothing on screen moves. Both `install.sh` and the
  backend constructor now check `XDG_SESSION_TYPE` and print a warning, because
  nothing downstream can detect it.
  **Superseded on 2026-09-29** — a printed warning was not enough, and one of
  the two Wayland signals was not checked at all. See the 2026-09-29 entry
  above; the engine now refuses to start and says so in the console.

- **Do not `import pynput` to check that the install worked.** On Linux the
  import connects to the X server immediately and raises without `DISPLAY`
  (over SSH, in a container, in CI) — which says nothing about whether pip
  succeeded. `install.sh` uses `importlib.util.find_spec("pynput")` instead.

- **pynput drags in a compiler on Linux.** It depends on `evdev`, a C extension
  with no binary wheel, so pip needs gcc and the Python and kernel headers.
  `install.sh` catches the failure and prints
  `sudo apt install build-essential python3-dev`. Debian and Ubuntu also ship
  `python3` without the venv module, hence the separate `python3-venv` hint.

- **`xvfb-run` hangs in a slim container.** Its "server is ready" handshake
  relies on a SIGUSR1 from Xvfb that never arrived in `python:3.12-slim`, so the
  proof run sat there until it was killed; it also needs `xauth`, which the slim
  image lacks. Starting `Xvfb :99` by hand and exporting `DISPLAY=:99` is what
  works, and is what the cheatsheet documents.

- **A fake backend is what makes the engine testable at all.** Before this there
  was no way to exercise `/start` on a developer's machine — the previous review
  had to hand-stub Quartz in a copy of the file. `ENGINE_BACKEND=fake` now runs
  the real server, the real loop and the real shutdown path while recording
  calls to a list, and `ENGINE_FAST=1` scales every sleep by 0.01 so a full
  cycle takes milliseconds instead of 13–17 s. Ten tests cover status, the
  console, start/stop, the STOPPING→409 race, the header guard and the CORS
  allow-list, and they run in about 2.5 s.

- **Testing the STOPPING window needs a gate, not a sleep.** With fast sleeps a
  cycle finishes so quickly that a `/stop` is usually already IDLE by the time
  the next request lands, making a 409 test flaky. The fake backend has an
  optional `gate` Event: every call blocks on it, which parks the worker inside
  a step so STOPPING is observable deterministically.

- **`sys.path` matters for `engine/tests/`.** The tests insert the `engine/`
  directory at the front of `sys.path` so `import engine` finds `engine.py` and
  not a namespace package named after the `engine/` folder. `engine.py` itself
  imports `backends` as a top-level package for the same reason — it is run as
  a script, not as part of a package.

- **What was tested where.** macOS: the real Quartz backend was started on a
  spare port and checked over HTTP only (`/status` reports
  `backend: quartz, platform: darwin`, the console renders, CORS and the header
  guard behave) — `/start` was never sent to a Quartz engine, so the macOS
  *input* path is unchanged-by-inspection, not re-proven. Linux: fully
  exercised in Docker under a headless Xvfb — install, `/status` reporting
  `pynput`/`linux`, `/start`, RUNNING, `/stop`, IDLE, clean SIGTERM exit, and a
  direct backend check where `move_mouse(321, 654)` read back as exactly
  `(321, 654)`. **Windows: not executed — no Windows machine was available.**
  Its code path is covered only by the fake-backend tests (which exercise the
  shared loop and server), by a PowerShell 7 parse check of `install.ps1` and
  `run.ps1`, and by review. `EnumWindows`, `SendInput` through pynput, the
  `netstat` port check and `Start-Process` all remain unverified on real
  hardware.

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

- **The source file was two copies of itself.** `mac_engine.py` (renamed to
  `engine.py` on 2026-09-28) in task-notif
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

- **Accessibility permission is per launching app, not per script.** (macOS.) macOS
  grants "may control this computer" to the process that owns the TTY —
  Terminal, iTerm, VS Code — and only to processes started *after* the toggle.
  Symptom of a missing grant: `/status` says RUNNING, the terminal prints the
  "[Scroll Active]" / "[System Shift]" lines, and nothing on screen happens,
  with no error anywhere. Quit and reopen the terminal after toggling.

- **`osascript` may prompt the first time.** (macOS.) `visible_app_count()`
  asks System Events for the app count, which needs the Automation permission
  for your terminal. macOS shows a one-time dialog; decline it and the count
  silently falls back to 5 (so Cmd+Tab still cycles, just not adaptively).

- **Cmd+Tab needs real hold times.** (Alt+Tab too — the pynput backend copies
  the same numbers.) The 80 ms after Command-down, 180 ms
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

- **2026-09-29. The loop ran at one fixed cadence, which is its own tell.** Every
  cycle was 16-20 keystrokes and a 9.5-12.5 s pause, so the gaps between actions
  were near-constant — the easiest possible pattern to spot. Replaced with four
  profiles drawn per cycle (BURST, STANDARD, READING, THINKING) so the rhythm
  varies the way a person's does. Two things this changed that were not obvious
  up front: (1) `THINKING` does nothing for 45-75 s, which on a console that only
  said "RUNNING" is indistinguishable from a hang, so the profile is now reported
  in `/status` and named in plain words on the page; (2) the old "41-44 % of
  ten-second windows" calibration no longer describes the loop as a whole, and
  `test_metrics.py` models the old single cadence only. The unit tests pin the
  pool to the working profiles, because one draw in six doing nothing made any
  test that waited for a backend call flaky on timing alone.
