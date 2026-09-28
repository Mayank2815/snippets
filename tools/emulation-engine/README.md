# Core Emulation Engine Control Console

A small tool that generates realistic mouse movement, navigation keystrokes,
app/tab switches and scrolling on the computer it runs on, with a web page to
start and stop it. It is a Python script plus a single HTML page — no Node, no
build step, nothing installed system-wide.

It runs on **macOS, Windows and Linux**. The loop, the timings and the console
page are the same everywhere; only the layer that posts the input differs.

It lives at `tools/emulation-engine/` in the `snippets` repo and is completely
self-contained: nothing else in the repo is needed to run it.

## Platforms

| Platform | Input backend | Install | Run | Notes |
|----------|---------------|---------|-----|-------|
| macOS    | `quartz` (pyobjc Quartz event taps) | `./install.sh` | `./run.sh` | needs the Accessibility permission (below) |
| Windows  | `pynput` (SendInput)                | `.\install.ps1` | `.\run.ps1` | no permission step; PowerShell may need an execution-policy line (below) |
| Linux    | `pynput` (X11 / XTest)              | `./install.sh` | `./run.sh` | **X11 only** — on Wayland the engine refuses to start and says why (below) |

The backend is chosen automatically from the platform. `GET /status` and the
console header both report which one is active, e.g. `backend: quartz on darwin`.

Verified end to end on macOS (Quartz), Ubuntu 22.04 and Ubuntu 24.04 (pynput on
X11, on a desktop with a real window manager).

## Quick start

**macOS and Linux**

```bash
git clone https://github.com/Mayank2815/snippets.git
cd snippets/tools/emulation-engine
./install.sh
./run.sh
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/Mayank2815/snippets.git
cd snippets\tools\emulation-engine
.\install.ps1
.\run.ps1
```

The install script creates a private Python environment in `.venv` and installs
the one input library this platform needs. The run script starts the engine,
waits for it to answer, and opens the console at <http://127.0.0.1:4320/> in
your browser. Both are safe to run again at any time.

You need Python 3.9 or newer. On macOS the Xcode Command Line Tools version
(`xcode-select --install`) is enough; on Windows install it from python.org and
tick "Add python.exe to PATH"; on Debian/Ubuntu you need
`build-essential python3-dev python3-venv` (all three — see the Linux section
for why). `install.sh` checks first and prints one `sudo apt install` line with
everything that is missing, so you never have to guess.

## macOS: the one manual step — Accessibility permission

macOS will not let a program move the mouse or press keys until you allow it.
The permission is granted to the *terminal app* that launches the engine, not
to the script:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Turn on the terminal app you run `./run.sh` from — Terminal, iTerm, Warp,
   the VS Code terminal, whichever you use. If it is not in the list, press
   **+** and add it (Terminal is in `/System/Applications/Utilities`, the
   others in `/Applications`).
3. If the engine reports RUNNING but nothing on screen moves, repeat the same
   for **Privacy & Security → Input Monitoring**.
4. Quit and reopen the terminal app afterwards — the toggle only applies to
   newly started processes.

`install.sh` prints these same steps at the end.

## Windows: the execution-policy prompt

Windows blocks unsigned `.ps1` files by default, so `.\install.ps1` may fail
with "running scripts is disabled on this system". Allow scripts for that one
PowerShell window and run it again:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

`-Scope Process` only affects the window you typed it in; nothing is changed
permanently. You may also see a SmartScreen "Windows protected your PC" prompt
the first time — choose **More info → Run anyway**. No Accessibility-style
permission is needed: the engine drives input through SendInput.

## Linux

### Step 1 — check which kind of session you are in

This is the only thing that can stop the tool working on Linux, so check it
first. In a terminal:

```bash
echo $XDG_SESSION_TYPE
```

- **`x11`** — you are good. Skip to step 2.
- **`wayland`** — the engine cannot drive your computer as things stand. See
  "If you are on Wayland" below.
- **nothing at all** — you are probably in a plain text console or connected
  over SSH. Run the engine from the desktop of the computer you want it to
  drive.

### Step 2 — install the system packages

Ubuntu and Debian do not ship these by default. `./install.sh` checks for them
and prints this exact line if any are missing, so you can also just run it and
see:

```bash
sudo apt install build-essential python3-dev python3-venv
```

- `python3-venv` — Ubuntu splits the `venv` module out of `python3`, and
  without it `./install.sh` cannot create its `.venv` folder.
- `build-essential` and `python3-dev` — pynput depends on a package called
  `evdev`, which is published as source only (there is no prebuilt version for
  **any** processor), so pip has to compile it and needs a C compiler and the
  Python and Linux headers.

These are the same on Ubuntu 22.04 and 24.04. Then:

```bash
./install.sh
./run.sh
```

### Optional: `wmctrl`

```bash
sudo apt install wmctrl
```

The engine uses it to count your open windows, so its Alt+Tab presses cycle a
realistic number of apps. Without it the count falls back to 5 — everything
else works exactly the same, and `install.sh` tells you it is missing.

### If you are on Wayland

Ubuntu has used Wayland by default since 21.04. Under Wayland, the desktop
simply ignores input that comes from a program rather than from real hardware —
it does not report an error, it discards it. Older X11 apps running through the
XWayland compatibility layer still see it, but nothing else does, which in
practice means almost nothing on a modern desktop.

**The engine refuses to run in this state** rather than report RUNNING while
your pointer sits still. The console page shows a red "INPUT UNAVAILABLE"
panel, Start is disabled, and `POST /start` answers HTTP 503.

To fix it, switch to an Xorg session — it takes about thirty seconds:

1. Log out.
2. At the login screen, click your name.
3. Click the **gear icon** at the bottom right.
4. Choose **"Ubuntu on Xorg"** (on other desktops: "GNOME on Xorg",
   "Plasma (X11)").
5. Type your password and log in.
6. Check it worked: `echo $XDG_SESSION_TYPE` must now print `x11`.
7. Run `./run.sh` again.

The choice sticks, so you only do this once.

**If your login screen has no Xorg option**, your desktop has dropped it —
Ubuntu 25.10 and later, and recent Fedora GNOME, ship Wayland only. There is
then no way to make this tool work on that desktop, and the honest options are
to run it on a machine that still offers Xorg, or to install a lighter X11
desktop (for example `sudo apt install xfce4`, then pick Xfce at the login
screen) alongside what you have.

**We looked at `ydotool` and decided against it** — see
[knowledge/lessons.md](knowledge/lessons.md) for the reasoning. The short
version: it works on Wayland but cannot tell us where the pointer currently is,
which is the one thing this engine's movement depends on, and it needs a
background service running as root.

**If you only care about old X11 apps** (a legacy application running under
XWayland) you can override the refusal:

```bash
ENGINE_ALLOW_WAYLAND=1 ./run.sh
```

The engine then runs, and the console still shows an amber warning explaining
that input reaches XWayland windows only. It is deliberately not silent.

## Using the console

Open <http://127.0.0.1:4320/> (the run script does this for you). The page is
served by the engine itself and shows:

- a status dot and label, refreshed every 2 seconds from `GET /status`:
  **OFFLINE** (engine not answering), **IDLE** (up, waiting), **RUNNING**
  (driving input right now) or **Stopping…** (you pressed Stop and the loop is
  finishing its current step — both buttons stay off until it has);
- which backend is active, e.g. `backend: pynput on win32`;
- **Start** and **Stop** buttons, which call `POST /start` and `POST /stop`;
- a "Last error" line if a call fails;
- when it last checked.

Once you press Start, the engine takes over the pointer and keyboard of the
window in front: a curved mouse move, a short burst of arrow/Shift presses,
then one of an app switch (Cmd+Tab on macOS, Alt+Tab elsewhere), a browser-tab
switch (Cmd+Option+Right on macOS, Ctrl+Tab elsewhere) or a scroll, an
occasional click, and a 9.5–12.5 second pause before the next cycle. Press
**Stop** to end the loop; the current step finishes first, so allow a couple of
seconds.

## Stopping it

- **Stop** in the console stops the loop but leaves the server up.
- **Ctrl-C** in the terminal running the run script stops everything (so does
  `kill <pid>`: the engine treats SIGTERM like Ctrl-C, waits up to 2 s for the
  loop to finish its step and releases the held modifier before exiting).
- From another shell: `lsof -nP -iTCP:4320 -sTCP:LISTEN` shows the PID, then
  `kill <pid>`. On Windows: `netstat -ano | findstr :4320`, then
  `Stop-Process -Id <pid>`.

## Makefile targets (macOS and Linux)

| Target         | What it does                                                             |
|----------------|--------------------------------------------------------------------------|
| `make install` | same as `./install.sh`                                                   |
| `make run`     | same as `./run.sh`                                                       |
| `make test`    | runs the engine tests on the fake backend, then `engine/test_metrics.py`, an offline simulator of the action cadence; neither generates any input |
| `make bundle`  | zips this folder (without `.venv`) as `emulation-engine-<date>.zip`     |

Windows has no `make`: use `.\install.ps1` and `.\run.ps1` directly, and run
the tests with
`.\.venv\Scripts\python -m unittest discover -s engine/tests -v`.

`./run.sh --no-open` (or `.\run.ps1 -NoOpen`) starts the engine without opening
a browser, and `PORT=4321 ./run.sh` (or `$env:PORT = 4321; .\run.ps1`) moves it
to another port — the console follows.

## For developers: running without touching real input

```bash
ENGINE_BACKEND=fake .venv/bin/python engine/engine.py
```

`ENGINE_BACKEND` overrides the platform choice and takes `fake`, `quartz` or
`pynput`. The `fake` backend records every call and generates nothing, so the
whole engine — HTTP server, loop, shutdown — can run on a machine whose mouse
must not move. That is how `engine/tests/test_engine.py` works; it also sets
`ENGINE_FAST=1`, which scales every sleep by 0.01 so a full loop cycle takes
milliseconds instead of ~15 seconds.

## Sharing with someone who has no repo access

Run `make bundle`, send them the resulting `emulation-engine-<date>.zip`. They
unzip it anywhere, then run the install and run scripts for their platform —
the same steps as above minus the clone.

## Layout

```
emulation-engine/
├── engine/engine.py         the engine loop + HTTP server (port 4320)
├── engine/backends/         one file per platform's input layer
│   ├── base.py              the contract every backend implements
│   ├── quartz.py            macOS (pyobjc Quartz event taps)
│   ├── pynput_backend.py    Windows and Linux (pynput)
│   ├── linux_session.py     can this Linux session receive input at all?
│   ├── unavailable.py       stand-in when it cannot, so the console can explain
│   └── fake.py              records calls, generates nothing (tests)
├── engine/tests/            engine tests against the fake backend
├── engine/test_metrics.py   offline cadence simulator (make test)
├── console/index.html       the control page the engine serves at /
├── install.sh  run.sh       macOS and Linux
├── install.ps1  run.ps1     Windows
├── Makefile  requirements.txt
└── knowledge/               intro, architecture, lessons, cheatsheet
```

## Troubleshooting

**`ImportError` / "Module load failed" for Quartz or pynput** — the engine is
not running from `.venv`. Run the install script again and start it with the
run script, not with a bare `python3`.

**Start does nothing / RUNNING but nothing moves or types** — on macOS, the
terminal app is not allowed under Accessibility (see above); toggle it on, quit
and reopen the terminal, run again, and if it still does nothing add the same
app under Input Monitoring.

On Linux this state should no longer be possible: if the engine cannot deliver
input it says so in red on the console and refuses to start. So:

- **The console shows a red "INPUT UNAVAILABLE" panel** — read it, it names the
  problem and the fix. Nearly always it is a Wayland session; see the Linux
  section above.
- **The Start button is greyed out and the dot is red** — same thing.
- **Start works, the dot goes green, and still nothing moves** — that is a real
  bug, not a known state. Check `echo $XDG_SESSION_TYPE` prints `x11`, then
  confirm the X server is accepting synthetic input at all:
  `xdotool mousemove 500 500` should jump your pointer. If that does nothing
  either, the problem is below this tool.
- **`install.sh` fails** — it prints one `sudo apt install ...` line with
  everything that is missing. Run it and re-run `./install.sh`.
- **`./install.sh: Permission denied`** — the executable bit was lost, which
  happens when the folder arrives as a zip downloaded from a browser. Fix with
  `chmod +x install.sh run.sh`, or run `bash install.sh` instead.

**"port 4320 is already in use"** — an older engine is still running. Find it
with `lsof -nP -iTCP:4320 -sTCP:LISTEN` (`netstat -ano | findstr :4320` on
Windows), stop it, or run on another port with `PORT=4321 ./run.sh`.

**"running scripts is disabled on this system" (Windows)** — see the
execution-policy line above.

**The console says OFFLINE** — the engine process is not up, or you opened the
page from somewhere other than the run script while the engine was down. Start
it again; the page recovers on its next 2-second poll.

**Cmd+Tab does not switch apps (macOS)** — the engine asks System Events how
many apps are open; the first time, macOS may prompt to allow your terminal to
control "System Events". Click Allow. If you declined, re-enable it under
Privacy & Security → Automation.

## Safety notes

- The server binds to `127.0.0.1` only. Nothing on your network can reach it.
- Your own browser *is* on `127.0.0.1`, so the engine also refuses `POST /start`
  and `/stop` unless they carry an `X-Engine-Control: 1` header and come from a
  page served on this computer (`http://127.0.0.1` or `http://localhost`, any
  port). A random web page you visit cannot start it from JavaScript.
- It never types letters or digits — only arrow keys, Shift, the app-switcher
  chord, the next-tab chord and scroll — but it will act on whatever window is
  in front. Do not leave it running over an open chat box or a form.
