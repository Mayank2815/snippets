# Core Emulation Engine Control Console

A small tool that generates realistic mouse movement, navigation keystrokes,
app/tab switches and scrolling on the computer it runs on, with a web page to
start and stop it. It is a Python script plus a single HTML page — no Node, no
build step, nothing installed system-wide.

It holds its own activity to a **measured band**, gets out of the way the
moment you start using the computer, and **stops by itself after three hours**.
Those three things are what the [Activity](#activity-what-the-engine-promises)
section is about, and they are the reason it will sometimes sit still for
minutes at a time on purpose.

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

Verified end to end on macOS (Quartz) and, for pynput on X11 with a real window
manager, on Ubuntu 22.04 and 24.04, Debian 12, Fedora 44, Arch and openSUSE
Tumbleweed — on both **x86_64** and **aarch64**. See
[knowledge/lessons.md](knowledge/lessons.md) for what differed between them
(almost nothing) and what is still unproven (a real GNOME or KDE desktop
session, as opposed to a bare window manager).

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
tick "Add python.exe to PATH"; on Linux you need a C compiler, the Python
headers and (on Debian and Ubuntu only) Python's venv package — see the Linux
section for why. `install.sh` checks first and prints one install command, in
your own distribution's package manager, listing everything that is missing, so
you never have to guess.

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

No distribution ships all of these by default. **You do not have to work out
which ones you need or what they are called** — run `./install.sh`, and it
checks first and prints the exact command for your distribution:

| Your distribution | What `./install.sh` prints |
|---|---|
| Debian, Ubuntu, Mint, Pop!_OS, Zorin | `sudo apt install build-essential python3-dev python3-venv` |
| Fedora, RHEL, CentOS Stream, Rocky, Alma, Nobara | `sudo dnf install gcc python3-devel` |
| Arch, Manjaro, EndeavourOS | `sudo pacman -S base-devel python` |
| openSUSE Leap and Tumbleweed, SLES | `sudo zypper install gcc python3-devel` |
| anything else | the three requirements in plain words, and no command — see below |

It only lists what is actually missing, so your line may be shorter than the
one above. The three things being asked for are always the same:

- **A C compiler.** pynput depends on a package called `evdev`, which is
  published as source only — there is no prebuilt version for **any**
  processor, x86_64 included — so pip has to compile it.
- **The Python development headers** (`Python.h`), which that compile needs.
  This is the package whose name differs most: `python3-dev` on Debian and
  Ubuntu, `python3-devel` on Fedora and openSUSE, and part of plain `python`
  on Arch.
- **Python's `venv` support**, so `./install.sh` can create its `.venv` folder.
  Only Debian and Ubuntu split this into a separate package (`python3-venv`);
  Fedora, Arch and openSUSE include it in Python itself, so their line does not
  mention it.

If your distribution is not one of the four above, `./install.sh` says so
plainly and names those three requirements in words rather than guessing a
package name that does not exist on your system. Install them however your
distribution does it, then carry on.

Then:

```bash
./install.sh
./run.sh
```

### Optional: `wmctrl`

```bash
sudo apt install wmctrl      # or dnf / pacman -S / zypper install — the
                             # package is called wmctrl everywhere, and
                             # install.sh prints the right command for you
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
desktop alongside what you have and pick it at the login screen — Xfce is the
usual choice (`sudo apt install xfce4`, `sudo dnf install @xfce-desktop`,
`sudo pacman -S xfce4`, `sudo zypper install -t pattern xfce`).

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

## Activity: what the engine promises

Activity trackers do not measure how hard you worked. They cut ten minutes into
**60 blocks of ten seconds** and tick a block if *anything* happened in it — one
keystroke ticks a block exactly as hard as a hundred. Your score is the
percentage of blocks ticked.

The engine knows that, and holds itself to three rules:

| Rule | What it means |
|---|---|
| **Never above 65%** | No ten minutes may ever have more than **39 of the 60 blocks** ticked. Not a target — a limit the loop cannot cross, checked on every single action, and checked over *any* ten minutes rather than only the ones lined up with the engine's own clock. |
| **A different share every window** | Each ten-minute window draws its own budget, between **21 and 34 blocks (35%–57%)**. Two windows in a row genuinely differ. |
| **38%–47% over three hours** | The long-run average. Measured across 2,000 simulated three-hour runs: average **43.1%**, worst run 39.8%, best 45.5%, **none outside the band** and none over the ceiling. |

Three more things follow from those:

- **It spreads its share across the whole window.** Spending the budget in the
  first three minutes and lying dead for seven is its own pattern, and a
  tracker reading a rolling ten minutes would see the spike anyway. The engine
  compares what it has used against the share due for the part of the window
  that has actually elapsed, and goes quiet whenever it is ahead.
- **Your own typing counts against the same budget.** If you are working, the
  engine is not adding to your score — it is *sharing* it. Without this it
  would pile its activity on top of yours and the combined number would sail
  past the ceiling, which is the whole reason the rule exists.
- **So it will sit still, sometimes for minutes.** That is the feature working.
  The console says which of the reasons it is (see below), so a deliberate
  quiet stretch never reads as a hang.

`engine/test_metrics.py` is the simulator those numbers come from. It runs
three hours of the real loop against the real governor on a virtual clock, in
under a second, and `make test` fails if the calibration has drifted:

```bash
python3 engine/test_metrics.py              # one run in detail, then 200 more
python3 engine/test_metrics.py --trials 500 # a wider sweep
python3 engine/test_metrics.py --human      # and with a person working alongside it
```

## It pauses while you are using the computer

The engine used to fight you: it would yank the pointer away and press arrow
keys into whatever you were typing. Now it watches how long it has been since
*anything* happened on the machine, compares that against when it last acted
itself, and stands down completely when the input was not its own. The console
says **"Paused — you are using this computer"**, and it starts again once you
have been quiet for **45 seconds**.

Your keystrokes still count towards the activity budget while it is paused, so
the score stays where it should be either way.

On Linux this needs the X server's XScreenSaver extension (present on every
normal desktop) or the `xprintidle` command. On a box with neither, the engine
says so — `/status` reports `userInputVisible: false` — and simply never
pauses, rather than guessing.

## Clicking is off by default

One cycle in five used to end with a left click wherever the pointer happened
to be. Everything else the engine does is reversible — an arrow key moves a
caret, a scroll scrolls back — but a click is not: it can hit a link, a **Send**
button, a tab's close box, or **Delete** in a confirmation dialog, in whatever
window happens to be in front.

So clicking is **off unless you ask for it**:

```bash
ENGINE_ALLOW_CLICK=1 ./run.sh       # macOS and Linux
$env:ENGINE_ALLOW_CLICK = 1; .\run.ps1   # Windows
```

The console header and `GET /status` both say which it is. Only turn it on for
a screen you have deliberately parked on something blank.

## It stops on its own

A run ends after **three hours**, whether or not anyone remembered it. Forgetting
the engine used to mean it drove the machine all night; the sister tool
`tools/keep-alive` solved the same problem the same way, and for the same
reason — an idle limit exists to save something, and a forgotten toggle should
not defeat it. Three hours is also the span the activity band above is measured
over.

```bash
ENGINE_MAX_HOURS=8 ./run.sh    # a longer run
ENGINE_MAX_HOURS=0 ./run.sh    # no limit at all, if you have decided you want that
```

The console shows the time left ("stops in 2h 41m") and `GET /status` carries it
as `runSecondsRemaining`.

## Where the pointer goes

The target area is worked out from your actual screen: 10% in from each edge,
never less than 60 pixels, which clears the macOS menu bar and Dock, the Windows
taskbar and every desktop's hot corners. On a 1470x956 Mac that is
x 147–1323, y 95–861 — about **64% of the screen** — and the hops between
targets scale with it, so the pointer crosses a 4K display as readily as a
laptop one.

It used to be a fixed `x 200–1100, y 200–650`, written for a 13" 1280x800
display. On anything bigger the pointer never left the top-left corner: 29% of a
1470x956 screen, and less on a 4K one. A pointer that only ever lives in one
corner is its own tell.

## Using the console

Open <http://127.0.0.1:4320/> (the run script does this for you). The page is
served by the engine itself and shows:

- a status dot and label, refreshed every 2 seconds from `GET /status`:
  **OFFLINE** (engine not answering), **IDLE** (up, waiting), **RUNNING**
  (driving input right now) or **Stopping…** (you pressed Stop and the loop is
  finishing its current step — both buttons stay off until it has);
- while it runs, *why* it is doing whatever it is doing, in words — the
  behaviour profile of the current cycle, or "Paused — you are using this
  computer", or "Waiting — it has used its share of this 10-minute window" — so
  a deliberate quiet minute never reads as a hang;
- a line reading **"This 10-minute window: 18 of 26 blocks used"**, with a bar
  showing where that sits against the 65% ceiling, the running average against
  the 38–47% band, and how long is left before the run stops itself;
- which backend is active, e.g. `backend: pynput on win32`;
- **Start** and **Stop** buttons, which call `POST /start` and `POST /stop`;
- a "Last error" line if a call fails;
- when it last checked.

Once you press Start, the engine takes over the pointer and keyboard of the
window in front: a curved mouse move, a burst of arrow/Shift presses, then one
of an app switch (Cmd+Tab on macOS, Alt+Tab elsewhere), a browser-tab switch
(Cmd+Option+Right on macOS, Ctrl+Tab elsewhere) or a scroll, and an occasional
click. Press **Stop** to end the loop; the current step finishes first, so
allow a couple of seconds.

### Behaviour profiles

A person does not work at one constant rhythm, so neither does the loop. Every
cycle draws a profile at random, and the profile decides how much typing there
is and how long the quiet afterwards lasts:

| Profile | Keystrokes | Gap between keys | Quiet after the cycle | How often |
|---|---|---|---|---|
| **BURST** | 24–36 | 0.08–0.18 s | 3–6 s | 2 in 10 |
| **STANDARD** | 14–20 | 0.12–0.28 s | 5–9 s | 3 in 10 |
| **READING** | 5–10 | 0.30–0.60 s | 8–14 s | 4 in 10 |
| **THINKING** | none | — | 40–70 s away | 1 in 10 |

The profiles decide the *texture* of a cycle. Whether a cycle runs at all is the
activity governor's decision, and that is why the quiet stretches inside the
profiles are shorter than they used to be: holding the activity rate down was
their job before the governor existed, and it is not any more. If the loop's own
natural pace sat near the target, the governor would stop being the thing in
control and the activity rate would go back to being an accident of the profile
mix.

**THINKING does nothing on purpose.** For up to 70 seconds nothing moves, as if
you had stepped away or taken a call. The console says so in plain words while
it happens, so it is never mistaken for a crash, and **Stop** still responds
immediately — it does not wait the pause out. It is rarer than it used to be
(one draw in ten, not one in six) because the governor now supplies most of the
quiet; what THINKING still adds is one *long* unmistakably human gap, where the
governor's own quiet is an even trickle.

The profile is also in `GET /status` as `mode`, and `null` whenever the loop is
not running.

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
| `make test`    | runs the engine tests on the fake backend, then `engine/test_metrics.py`, which simulates 200 three-hour runs and fails if the activity calibration has drifted; neither generates any input |
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
├── engine/governor.py       the activity governor: budget, ceiling, pacing, pausing
├── engine/backends/         one file per platform's input layer
│   ├── base.py              the contract every backend implements
│   ├── quartz.py            macOS (pyobjc Quartz event taps)
│   ├── pynput_backend.py    Windows and Linux (pynput)
│   ├── linux_session.py     can this Linux session receive input at all?
│   ├── unavailable.py       stand-in when it cannot, so the console can explain
│   └── fake.py              records calls, generates nothing (tests)
├── engine/tests/            engine tests against the fake backend
├── engine/test_metrics.py   3-hour activity simulator, on a virtual clock (make test)
├── console/index.html       the control page the engine serves at /
├── install.sh  run.sh       macOS and Linux
├── packages.sh              which package manager this Linux box uses, and the
│                            package names for it (sourced by install.sh)
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
- **`install.sh` fails** — it prints one install command for your own
  distribution (`apt`, `dnf`, `pacman` or `zypper`) with everything that is
  missing. Run it and re-run `./install.sh`. If it says your package manager was
  not recognised, it lists the requirements in words instead — install those
  however your distribution does it.
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
- **It does not click unless you set `ENGINE_ALLOW_CLICK=1`.** A click is the
  one thing it does that cannot be undone. See "Clicking is off by default".
- It stops by itself after three hours (`ENGINE_MAX_HOURS` changes that), so a
  run you walk away from does not carry on all night.
