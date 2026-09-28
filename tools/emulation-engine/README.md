# Core Emulation Engine Control Console

A small Mac-only tool that generates realistic mouse movement, navigation
keystrokes, app/tab switches and scrolling on the Mac it runs on, with a web
page to start and stop it. It is a Python script plus a single HTML page — no
Node, no build step, nothing installed system-wide.

It lives at `tools/emulation-engine/` in the `snippets` repo and is completely
self-contained: nothing else in the repo is needed to run it.

## Quick start (any teammate's Mac)

```bash
git clone https://github.com/Mayank2815/snippets.git
cd snippets/tools/emulation-engine
./install.sh
./run.sh
```

`install.sh` creates a private Python environment in `.venv` and installs the
Quartz bindings. `run.sh` starts the engine, waits for it to answer, and opens
the console at <http://127.0.0.1:4320/> in your browser. Both are safe to run
again at any time.

You need macOS with Python 3.9 or newer — the Xcode Command Line Tools version
that every Mac gets with `xcode-select --install` is enough.

## The one manual step: Accessibility permission

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

## Using the console

Open <http://127.0.0.1:4320/> (run.sh does this for you). The page is served by
the engine itself and shows:

- a status dot and label, refreshed every 2 seconds from `GET /status`:
  **OFFLINE** (engine not answering), **IDLE** (up, waiting), **RUNNING**
  (driving input right now) or **Stopping…** (you pressed Stop and the loop is
  finishing its current step — both buttons stay off until it has);
- **Start** and **Stop** buttons, which call `POST /start` and `POST /stop`;
- a "Last error" line if a call fails;
- when it last checked.

Once you press Start, the engine takes over the pointer and keyboard of the
window in front: a curved mouse move, a short burst of arrow/Shift presses,
then one of Cmd+Tab, Cmd+Option+Right or a scroll, an occasional click, and a
9.5–12.5 second pause before the next cycle. Press **Stop** to end the loop; the
current step finishes first, so allow a couple of seconds.

## Stopping it

- **Stop** in the console stops the loop but leaves the server up.
- **Ctrl-C** in the terminal running `./run.sh` stops everything (so does
  `kill <pid>`: the engine treats SIGTERM like Ctrl-C, waits up to 2 s for the
  loop to finish its step and releases the Command key before exiting).
- From another shell: `lsof -nP -iTCP:4320 -sTCP:LISTEN` shows the PID, then
  `kill <pid>`.

## Makefile targets

| Target         | What it does                                                             |
|----------------|--------------------------------------------------------------------------|
| `make install` | same as `./install.sh`                                                   |
| `make run`     | same as `./run.sh`                                                       |
| `make test`    | runs `engine/test_metrics.py`, an offline simulator of the action cadence over ten minutes; it generates no input |
| `make bundle`  | zips this folder (without `.venv`) as `emulation-engine-<date>.zip`     |

`./run.sh --no-open` starts the engine without opening a browser, and
`PORT=4321 ./run.sh` moves it to another port (the console follows).

## Sharing with someone who has no repo access

Run `make bundle`, send them the resulting `emulation-engine-<date>.zip`. They
unzip it anywhere, then run `./install.sh` and `./run.sh` inside the folder —
the same three steps as above minus the clone.

## Layout

```
emulation-engine/
├── engine/mac_engine.py     the engine and its HTTP server (port 4320)
├── engine/test_metrics.py   offline cadence simulator (make test)
├── console/index.html       the control page the engine serves at /
├── install.sh  run.sh  Makefile  requirements.txt
└── knowledge/               intro, architecture, lessons, cheatsheet
```

## Troubleshooting

**`ImportError: No module named Quartz` (or "Module load failed")** — the
engine is not running from `.venv`. Run `./install.sh` again and start it with
`./run.sh`, not with a bare `python3`.

**RUNNING but nothing moves or types** — the terminal app is not allowed under
Accessibility (see above). Toggle it on, quit and reopen the terminal, run
again. If it still does nothing, add the same app under Input Monitoring.

**"port 4320 is already in use"** — an older engine is still running. Find it
with `lsof -nP -iTCP:4320 -sTCP:LISTEN`, stop it with `kill <pid>`, or run on
another port with `PORT=4321 ./run.sh`.

**The console says OFFLINE** — the engine process is not up, or you opened the
page from somewhere other than `run.sh` while the engine was down. Start
`./run.sh`; the page recovers on its next 2-second poll.

**Cmd+Tab does not switch apps** — the engine asks System Events how many apps
are open; the first time, macOS may prompt to allow your terminal to control
"System Events". Click Allow. If you declined, re-enable it under Privacy &
Security → Automation.

## Safety notes

- The server binds to `127.0.0.1` only. Nothing on your network can reach it.
- Your own browser *is* on `127.0.0.1`, so the engine also refuses `POST /start`
  and `/stop` unless they carry an `X-Engine-Control: 1` header and come from a
  page served on this Mac (`http://127.0.0.1` or `http://localhost`, any port).
  A random web page you visit cannot start it from JavaScript.
- It never types letters or digits — only arrow keys, Shift, Cmd+Tab,
  Cmd+Option+Right and scroll — but it will act on whatever window is in
  front. Do not leave it running over an open chat box or a form.
