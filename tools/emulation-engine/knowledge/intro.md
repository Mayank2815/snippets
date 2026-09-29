# Intro — what the emulation engine is

The Core Emulation Engine is a Python script (`engine/engine.py`) that posts
synthetic input on the computer it runs on: curved mouse moves, bursts of
arrow-key and Shift presses, app switches, browser-tab switches and wheel
scrolling, on a cycle of a few seconds to half a minute. How much of that
happens is not left to chance: an **activity governor** (`engine/governor.py`)
holds the engine to a measured band — a tracker scores ten minutes as 60 blocks
of ten seconds, and the engine gives each window a random share of them, never
crosses 65 %, spreads the share across the window, counts the person's own
typing against it, stands down entirely while somebody is using the machine,
and stops the run after three hours. A tiny
built-in HTTP server on `127.0.0.1:4320` lets a web page start and stop it and
read its state, and the same server hands out that web page
(`console/index.html`) at `/`.

It runs on **macOS, Windows and Linux**. The loop, the timings and the console
are identical everywhere; only the layer that actually posts the input differs,
and that lives in `engine/backends/` — Quartz event taps (pyobjc) on macOS,
pynput on Windows and Linux. Linux means an **X11/Xorg** session: on Wayland the
engine refuses to start the loop and says why, rather than reporting RUNNING
while nothing moves.

It began life inside `tools/task-notif` as an "Automation panel": the Node
server spawned the Python script and proxied `/automation/*` calls to it, and
a React panel in the dashboard showed a status pill and a toggle. That coupling
was wrong in both directions — task-notif is deployed to a Linux VM where the
engine can never run, and the engine has nothing to do with Teamwork
reminders — so on 2026-09-28 it was extracted into its own tool. It was
macOS-only until 2026-09-28, when the backend layer was added so teammates on
Windows and Linux could use it too.

Who uses it: teammates who want the engine on their own machine, whatever they
run. The audience is "clone, run two scripts, open the page". There is
deliberately no Node dependency, no build step and no package to install
system-wide; everything lives in the tool folder plus a local `.venv`.

Where things are:

| Path                             | Role                                                     |
|----------------------------------|----------------------------------------------------------|
| `engine/engine.py`               | engine loop + HTTP server + console file serving         |
| `engine/governor.py`             | the activity governor: budget, ceiling, pacing, pausing  |
| `engine/backends/base.py`        | the contract every input backend implements              |
| `engine/backends/quartz.py`      | macOS input (pyobjc Quartz event taps)                   |
| `engine/backends/pynput_backend.py` | Windows and Linux input (pynput)                      |
| `engine/backends/linux_session.py` | can this Linux session receive synthetic input?        |
| `engine/backends/unavailable.py` | stand-in when it cannot, so the console can explain      |
| `engine/backends/fake.py`        | records calls, generates nothing (tests, CI)             |
| `engine/tests/test_engine.py`    | engine tests against the fake backend                    |
| `engine/tests/test_governor.py`  | the governor, and the calibration it promises            |
| `engine/tests/test_linux_session.py` | the session check and the unavailable backend        |
| `engine/test_metrics.py`         | 3-hour activity simulator on a virtual clock (no input)  |
| `console/index.html`             | single-file control page, inline CSS/JS                  |
| `install.sh` / `run.sh`          | venv setup and start/open/stop — macOS and Linux         |
| `install.ps1` / `run.ps1`        | the same for Windows (PowerShell 5.1 compatible)         |
| `Makefile`                       | `install`, `run`, `test`, `bundle` (macOS and Linux)     |
| `requirements.txt`               | pyobjc Quartz on darwin, pynput elsewhere (pip markers)  |
| `knowledge/`                     | this folder: intro, architecture, lessons, cheatsheet    |

Read `architecture.md` for how the pieces fit, `lessons.md` before changing
the engine, and `cheatsheet.md` when you just need a command.
