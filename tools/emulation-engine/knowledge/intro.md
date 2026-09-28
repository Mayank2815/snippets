# Intro — what the emulation engine is

The Core Emulation Engine is a Python script (`engine/engine.py`) that posts
synthetic input on the computer it runs on: curved mouse moves, bursts of
arrow-key and Shift presses, app switches, browser-tab switches, wheel
scrolling and the occasional click, on a roughly 13–17 second cycle. A tiny
built-in HTTP server on `127.0.0.1:4320` lets a web page start and stop it and
read its state, and the same server hands out that web page
(`console/index.html`) at `/`.

It runs on **macOS, Windows and Linux**. The loop, the timings and the console
are identical everywhere; only the layer that actually posts the input differs,
and that lives in `engine/backends/` — Quartz event taps (pyobjc) on macOS,
pynput on Windows and Linux.

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
| `engine/backends/base.py`        | the contract every input backend implements              |
| `engine/backends/quartz.py`      | macOS input (pyobjc Quartz event taps)                   |
| `engine/backends/pynput_backend.py` | Windows and Linux input (pynput)                      |
| `engine/backends/fake.py`        | records calls, generates nothing (tests, CI)             |
| `engine/tests/test_engine.py`    | engine tests against the fake backend                    |
| `engine/test_metrics.py`         | offline simulator of the action cadence (no input)       |
| `console/index.html`             | single-file control page, inline CSS/JS                  |
| `install.sh` / `run.sh`          | venv setup and start/open/stop — macOS and Linux         |
| `install.ps1` / `run.ps1`        | the same for Windows (PowerShell 5.1 compatible)         |
| `Makefile`                       | `install`, `run`, `test`, `bundle` (macOS and Linux)     |
| `requirements.txt`               | pyobjc Quartz on darwin, pynput elsewhere (pip markers)  |
| `knowledge/`                     | this folder: intro, architecture, lessons, cheatsheet    |

Read `architecture.md` for how the pieces fit, `lessons.md` before changing
the engine, and `cheatsheet.md` when you just need a command.
