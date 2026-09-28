# Intro — what the emulation engine is

The Core Emulation Engine is a Python script (`engine/mac_engine.py`) that
uses macOS Quartz event taps to post synthetic input on the Mac it runs on:
curved mouse moves, bursts of arrow-key and Shift presses, Cmd+Tab app
switches, Cmd+Option+Right browser-tab switches, wheel scrolling and the
occasional click, on a roughly 13–17 second cycle. A tiny built-in HTTP server
on `127.0.0.1:4320` lets a web page start and stop it and read its state, and
the same server hands out that web page (`console/index.html`) at `/`.

It began life inside `tools/task-notif` as an "Automation panel": the Node
server spawned the Python script and proxied `/automation/*` calls to it, and
a React panel in the dashboard showed a status pill and a toggle. That coupling
was wrong in both directions — task-notif is deployed to a Linux VM where the
engine can never run, and the engine has nothing to do with Teamwork
reminders — so on 2026-09-28 it was extracted into its own tool.

Who uses it: teammates on macOS who want the engine on their own machine. The
audience is "clone, run two scripts, open the page". There is deliberately no
Node dependency, no build step and no package to install system-wide;
everything lives in the tool folder plus a local `.venv`.

Where things are:

| Path                       | Role                                                     |
|----------------------------|----------------------------------------------------------|
| `engine/mac_engine.py`     | engine loop + HTTP server + console file serving         |
| `engine/test_metrics.py`   | offline simulator of the action cadence (no input)       |
| `console/index.html`       | single-file control page, inline CSS/JS                  |
| `install.sh` / `run.sh`    | venv setup and start/open/stop                           |
| `Makefile`                 | `install`, `run`, `test`, `bundle`                       |
| `requirements.txt`         | `pyobjc-framework-Quartz` only                           |
| `knowledge/`               | this folder: intro, architecture, lessons, cheatsheet    |

Read `architecture.md` for how the pieces fit, `lessons.md` before changing
the engine, and `cheatsheet.md` when you just need a command.
